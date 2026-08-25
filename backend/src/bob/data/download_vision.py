"""Ingesta del archivo histórico de Binance a SQLite — derivados y libro.

Uso:
    uv run python -m bob.data.download_vision --symbol ETHUSDT --days 720
    uv run python -m bob.data.download_vision --datasets metrics --days 30
    uv run python -m bob.data.download_vision --status

Es la contraparte de `download.py` (klines) para las dos familias que la Fase 2
había dado por imposibles: derivados y microestructura. Los tres datasets:

- `metrics`   — OI y ratios, grilla 5m, ~12 KB por día. 720 días son ~20 MB.
- `bookDepth` — profundidad del libro, **agregada a la grilla del timeframe al
  ingerir**: el crudo son ~2 GB por año y lo que el modelo necesita son unas
  pocas columnas por barra.
- `funding`   — vía REST, que sí tiene historia completa. Se guarda en la misma
  tabla bajo `period="funding"` porque su grilla es de 8h, no de 5m.

Idempotente por día UTC: un día ya completo en DB no se vuelve a bajar.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from loguru import logger

from bob.data.binance_rest import INTERVAL_MS, BinanceRestClient
from bob.data.snapshots import DerivativePoint
from bob.data.store import (
    DAY_MS,
    book_depth_coverage,
    book_depth_day_counts,
    coverage,
    derivative_day_counts,
    derivatives_coverage,
    upsert_book_depth,
    upsert_derivatives,
)
from bob.data.vision import (
    DATASET_START,
    VisionClient,
    aggregate_book_depth,
    daterange,
    parse_book_depth_csv,
    parse_metrics_csv,
)
from bob.db.session import init_db
from bob.utils.console import enable_utf8_stdout

#: Grilla nativa del archivo `metrics`: un punto cada 5 minutos, 288 por día.
METRICS_PERIOD = "5m"
METRICS_ROWS_PER_DAY = 288

#: `period` bajo el que se guarda el funding. No es un timeframe de velas: es
#: una etiqueta de grilla, y mezclarlo con los puntos de 5m rompería el
#: `_validate_monotonic` de la serie de derivados.
FUNDING_PERIOD = "funding"

#: Fracción del día que debe estar en DB para saltarlo. No se exige el 100%
#: porque hay días en que Binance publicó el archivo incompleto: reintentarlos
#: en cada corrida sería bajar los mismos bytes para siempre.
COMPLETE_RATIO = 0.99

#: Días por lote. Acota el pico de memoria (un lote de bookDepth son ~30 MB
#: de CSV descomprimido) y hace que la ingesta commitee seguido: cortarla a la
#: mitad no pierde lo ya bajado.
BATCH_DAYS = 30


@dataclass
class IngestReport:
    """Qué pasó con una ingesta. Se imprime al final, sin maquillaje."""

    dataset: str
    days_requested: int = 0
    days_skipped: int = 0  # ya estaban completos en DB
    days_written: int = 0
    days_absent: int = 0  # 404: el archivo no existe para ese día
    days_failed: int = 0  # fallo de red: el día SIGUE pendiente
    days_empty: int = 0  # bajado y parseado, pero sin filas utilizables
    rows_written: int = 0
    absent_days: list[date] = field(default_factory=list)
    empty_days: list[date] = field(default_factory=list)

    #: El funding no se baja por día (viene paginado por filas), así que su
    #: reporte omite el conteo de días en vez de imprimir ceros que parecen bug.
    by_day: bool = True

    def render(self) -> str:
        lines = [f"  {self.dataset}"]
        if self.by_day:
            lines += [
                f"    días pedidos     : {self.days_requested}",
                f"    ya completos     : {self.days_skipped}",
                f"    escritos         : {self.days_written}",
                f"    ausentes (404)   : {self.days_absent}",
                f"    fallidos (red)   : {self.days_failed}",
                f"    sin filas útiles : {self.days_empty}",
            ]
        lines.append(f"    filas escritas   : {self.rows_written:,}")
        if self.absent_days:
            muestra = ", ".join(d.isoformat() for d in self.absent_days[:5])
            extra = f" ... y {len(self.absent_days) - 5} más" if len(self.absent_days) > 5 else ""
            lines.append(f"    huecos del archivo: {muestra}{extra}")
        if self.empty_days:
            # Un día que llegó completo y no produjo nada es casi siempre un
            # cambio de formato del archivo, no un día vacío de mercado. Se
            # nombra aparte porque mezclarlo con los 404 lo vuelve invisible:
            # así se perdieron 508 días de libro sin que el reporte lo dijera.
            muestra = ", ".join(d.isoformat() for d in self.empty_days[:5])
            extra = f" ... y {len(self.empty_days) - 5} más" if len(self.empty_days) > 5 else ""
            lines.append(f"    ⚠ bajados pero sin filas: {muestra}{extra}")
            lines.append("      (revisar si cambió el formato del archivo)")
        if self.days_failed:
            # No es un detalle: una corrida con la red caída termina en exit 0
            # y sin esta línea parecería una ingesta completa.
            lines.append(
                f"    ⚠ {self.days_failed} día(s) NO se pudieron bajar por red — "
                "quedan pendientes, reejecutar"
            )
        return "\n".join(lines)

    @property
    def incomplete(self) -> bool:
        """Quedó trabajo sin hacer por causas nuestras, no del archivo.

        Un día bajado que no produjo filas cuenta acá: el archivo cumplió, el
        que no supo leerlo fue este código.
        """
        return self.days_failed > 0 or self.days_empty > 0


def _epoch_day(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000) // DAY_MS


def _pending_days(
    start: date, end: date, have: dict[int, int], expected_rows: int
) -> list[date]:
    """Días del rango que aún no están completos en DB."""
    threshold = int(expected_rows * COMPLETE_RATIO)
    return [d for d in daterange(start, end) if have.get(_epoch_day(d), 0) < threshold]


def _batched(days: list[date], size: int) -> list[list[date]]:
    return [days[i : i + size] for i in range(0, len(days), size)]


def _clamp_start(dataset: str, start: date) -> date:
    """No pedir días anteriores al primer día publicado del dataset."""
    floor = DATASET_START[dataset]
    return max(start, floor)


async def ingest_metrics(
    symbol: str,
    start: date,
    end: date,
    *,
    client: VisionClient | None = None,
) -> IngestReport:
    """Baja `metrics/` día a día y lo persiste como puntos de derivados."""
    init_db()
    report = IngestReport(dataset="metrics")
    start = _clamp_start("metrics", start)
    if start > end:
        return report

    pending = _pending_days(
        start, end, derivative_day_counts(symbol, METRICS_PERIOD), METRICS_ROWS_PER_DAY
    )
    report.days_requested = (end - start).days + 1
    report.days_skipped = report.days_requested - len(pending)

    owns = client is None
    vision = client or VisionClient()
    try:
        for batch in _batched(pending, BATCH_DAYS):
            for fetched in await vision.fetch_days("metrics", symbol, batch):
                if fetched.failed:
                    report.days_failed += 1
                    continue
                raw = fetched.payload
                if raw is None:
                    report.days_absent += 1
                    report.absent_days.append(fetched.day)
                    continue
                points = parse_metrics_csv(raw, symbol)
                if not points:
                    report.days_empty += 1
                    report.empty_days.append(fetched.day)
                    continue
                report.rows_written += upsert_derivatives(symbol, METRICS_PERIOD, points)
                report.days_written += 1
            logger.info(
                "metrics {}: {}/{} días ingeridos", symbol, report.days_written, len(pending)
            )
    finally:
        if owns:
            await vision.aclose()
    return report


async def ingest_book_depth(
    symbol: str,
    timeframe: str,
    start: date,
    end: date,
    *,
    client: VisionClient | None = None,
) -> IngestReport:
    """Baja `bookDepth/`, lo agrega a la grilla del timeframe y lo persiste."""
    init_db()
    report = IngestReport(dataset="bookDepth")
    start = _clamp_start("bookDepth", start)
    if start > end:
        return report

    bars_per_day = DAY_MS // INTERVAL_MS[timeframe]
    pending = _pending_days(
        start, end, book_depth_day_counts(symbol, timeframe), bars_per_day
    )
    report.days_requested = (end - start).days + 1
    report.days_skipped = report.days_requested - len(pending)

    owns = client is None
    vision = client or VisionClient()
    try:
        for batch in _batched(pending, BATCH_DAYS):
            for fetched in await vision.fetch_days("bookDepth", symbol, batch):
                if fetched.failed:
                    report.days_failed += 1
                    continue
                raw = fetched.payload
                if raw is None:
                    report.days_absent += 1
                    report.absent_days.append(fetched.day)
                    continue
                bars = aggregate_book_depth(parse_book_depth_csv(raw), timeframe)
                if not bars:
                    report.days_empty += 1
                    report.empty_days.append(fetched.day)
                    continue
                report.rows_written += upsert_book_depth(symbol, timeframe, bars)
                report.days_written += 1
            logger.info(
                "bookDepth {}: {}/{} días ingeridos", symbol, report.days_written, len(pending)
            )
    finally:
        if owns:
            await vision.aclose()
    return report


async def ingest_funding(
    symbol: str, start_ms: int, *, client: BinanceRestClient | None = None
) -> IngestReport:
    """Baja el funding histórico por REST y lo persiste bajo period="funding".

    A diferencia de `/futures/data/*`, `/fapi/v1/fundingRate` no tiene ventana
    de 30 días: devuelve desde el listado del contrato. Pagina de a 1000 filas,
    que a 3 cobros por día son ~11 meses por request.

    El corte de la paginación mira `len(rows) < 1000` **y** que el cursor
    avance. Solo lo primero deja un bucle infinito el día que Binance devuelva
    una página llena que empieza donde terminó la anterior; solo lo segundo
    gasta un request de más en cada corrida.
    """
    init_db()
    report = IngestReport(dataset="funding", by_day=False)
    cursor = start_ms
    seen: set[int] = set()

    owns = client is None
    rest = client or BinanceRestClient()
    try:
        while True:
            rows = await rest.funding_history(symbol, start_time=cursor, limit=1000)
            if not rows:
                break
            points = [
                DerivativePoint(
                    timestamp=int(row["fundingTime"]),
                    funding_rate=str(row["fundingRate"]),
                )
                for row in rows
                if int(row["fundingTime"]) not in seen
            ]
            if not points:
                break  # página repetida: el cursor no avanza, cortar
            seen.update(p.timestamp for p in points)
            report.rows_written += upsert_derivatives(symbol, FUNDING_PERIOD, points)
            if len(rows) < 1000:
                break
            cursor = max(p.timestamp for p in points) + 1
    finally:
        if owns:
            await rest.aclose()
    return report


def _fmt_ms(ms: int) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def print_status(symbol: str, timeframe: str) -> None:
    """Cobertura de las tres fuentes contra la de klines — que es la que manda.

    Lo que importa no es cuántos puntos hay sino **si cubren el mismo tramo que
    las velas**: una familia de features que empieza dos años después que el
    precio no entrena sobre el mismo backtest.
    """
    candles = coverage(symbol, timeframe)
    metrics = derivatives_coverage(symbol, METRICS_PERIOD)
    funding = derivatives_coverage(symbol, FUNDING_PERIOD)
    book = book_depth_coverage(symbol, timeframe)

    print(f"\n{symbol} — cobertura por fuente")
    print(f"  klines {timeframe:<4} : {candles['n_candles']:>8,}  "
          f"{_fmt_ms(candles['first_open_time'])} .. {_fmt_ms(candles['last_open_time'])}")
    print(f"  metrics 5m   : {metrics['n_points']:>8,}  "
          f"{_fmt_ms(metrics['first_timestamp'])} .. {_fmt_ms(metrics['last_timestamp'])}")
    print(f"  funding 8h   : {funding['n_points']:>8,}  "
          f"{_fmt_ms(funding['first_timestamp'])} .. {_fmt_ms(funding['last_timestamp'])}")
    print(f"  bookDepth    : {book['n_bars']:>8,}  "
          f"{_fmt_ms(book['first_open_time'])} .. {_fmt_ms(book['last_open_time'])}")

    if candles["n_candles"]:
        first = candles["first_open_time"]
        for label, got in (
            ("metrics", metrics["first_timestamp"]),
            ("funding", funding["first_timestamp"]),
            ("bookDepth", book["first_open_time"]),
        ):
            if not got:
                print(f"  ⚠ {label}: sin datos — esa familia de features sale NaN entera")
            elif got > first:
                dias = (got - first) / DAY_MS
                print(f"  ⚠ {label}: arranca {dias:.0f} días después que las velas")
    print()


async def run(symbol: str, timeframe: str, days: int, datasets: list[str]) -> None:
    end = datetime.now(UTC).date() - timedelta(days=1)  # el archivo del día D sale D+1
    start = end - timedelta(days=days - 1)
    reports: list[IngestReport] = []

    async with VisionClient() as vision:
        if "metrics" in datasets:
            reports.append(await ingest_metrics(symbol, start, end, client=vision))
        if "bookDepth" in datasets:
            reports.append(await ingest_book_depth(symbol, timeframe, start, end, client=vision))

    if "funding" in datasets:
        start_ms = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp() * 1000)
        reports.append(await ingest_funding(symbol, start_ms))

    print(f"\nIngesta {symbol} — {start.isoformat()} .. {end.isoformat()}")
    for report in reports:
        print(report.render())

    if any(r.incomplete for r in reports):
        print(
            "\n⚠ La ingesta quedó INCOMPLETA. Es idempotente: reejecutar el mismo "
            "comando retoma solo lo que falta."
        )


def main() -> None:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="Ingesta del archivo histórico de Binance (derivados + libro)"
    )
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--timeframe", default="15m", choices=sorted(INTERVAL_MS))
    parser.add_argument("--days", type=int, default=720)
    parser.add_argument(
        "--datasets",
        default="metrics,bookDepth,funding",
        help="lista separada por comas: metrics, bookDepth, funding",
    )
    parser.add_argument("--status", action="store_true", help="solo reporta cobertura")
    args = parser.parse_args()

    if args.status:
        print_status(args.symbol, args.timeframe)
        return

    datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]
    unknown = set(datasets) - {"metrics", "bookDepth", "funding"}
    if unknown:
        parser.error(f"datasets desconocidos: {sorted(unknown)}")

    asyncio.run(run(args.symbol, args.timeframe, args.days, datasets))
    print_status(args.symbol, args.timeframe)


if __name__ == "__main__":
    main()
