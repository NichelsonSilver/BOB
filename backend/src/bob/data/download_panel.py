"""CLI de ingesta del panel diario multi-símbolo (Fase 10).

    # 1. descubrir y CONGELAR el universo (toca la red, escribe artefacto)
    uv run python -m bob.data.download_panel --discover

    # 2. bajar las velas diarias de todo el universo cripto
    uv run python -m bob.data.download_panel --ingest

    # 3. ver qué quedó
    uv run python -m bob.data.download_panel --status

Los pasos están separados a propósito. El descubrimiento consulta
`exchangeInfo`, que devuelve algo distinto cada semana; si la ingesta lo
llamara sola, dos corridas del mismo comando trabajarían sobre universos
distintos sin que nada fallara. Es la misma lección que la Fase 4-bis dejó
escrita para `--until`: reproducir un run exige nombrar su insumo.

Idempotente: reejecutar continúa desde la última vela persistida de cada
símbolo y no vuelve a pedir lo que ya está.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger

from bob.data.binance_rest import INTERVAL_MS, BinanceRestClient, BinanceRestError
from bob.data.panel import (
    SymbolMeta,
    discover_universe,
    load_universe,
    panel_coverage,
    save_universe,
)
from bob.data.store import upsert_klines
from bob.data.vision import VisionClient, parse_klines_csv
from bob.db.session import init_db
from bob.utils.console import enable_utf8_stdout

#: Meses mínimos de archivo para que un símbolo valga la pena bajar. No es la
#: regla de pertenencia —esa es causal y vive en `signals/universe.py`— sino
#: un corte de ingesta: con menos de 6 meses el símbolo nunca acumula los 180
#: días de historia previa que la regla pide, así que bajarlo es puro peso.
MIN_MONTHS_TO_INGEST: int = 6


def _fmt_ms(ms: int) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


@dataclass
class IngestReport:
    """Resultado de una corrida, con los fallos separados de las ausencias.

    La distinción no es cosmética y el proyecto ya la pagó una vez: en la
    Fase 2b una corrida atravesó un corte de conectividad y reportó 726 días
    "ausentes del archivo" que existían todos. Un símbolo que Binance no tiene
    y uno que no se pudo pedir **no son lo mismo**, y una corrida con fallos
    de red no está completa aunque termine.
    """

    written: int = 0
    symbols_done: int = 0
    symbols_empty: int = 0
    failures: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.failures is None:
            self.failures = {}

    @property
    def complete(self) -> bool:
        return not self.failures


def _months_between(first: str, last: str) -> list[str]:
    """Meses `YYYY-MM` inclusive entre dos marcas del archivo."""
    y0, m0 = (int(p) for p in first.split("-"))
    y1, m1 = (int(p) for p in last.split("-"))
    out: list[str] = []
    while (y0, m0) <= (y1, m1):
        out.append(f"{y0:04d}-{m0:02d}")
        m0 += 1
        if m0 > 12:
            y0, m0 = y0 + 1, 1
    return out


async def _ingest_from_archive(meta: SymbolMeta, timeframe: str) -> int:
    """Baja las velas del archivo mensual. La vía de los **deslistados**.

    `/fapi/v1/klines` responde `-1121 Invalid symbol` en cuanto el contrato
    deja de cotizar, así que una ingesta solo-REST **no puede traer los
    símbolos cuya ausencia es justamente el sesgo de supervivencia**. Medido
    el 2026-09-22: 4 de los 31 deslistados del universo fallaban así, y uno
    de ellos (`SXPUSDT`) tiene seis años de historia.

    Que el universo esté bien construido no alcanza si la capa de abajo no
    puede materializarlo: el sesgo se colaba una capa más abajo de donde se
    lo estaba vigilando.
    """
    written = 0
    async with VisionClient() as vision:
        for month in _months_between(meta.first_month, meta.last_month):
            raw = await vision.fetch_month_klines(meta.symbol, timeframe, month)
            if raw is None:
                continue  # 404 = ese mes no existe, no es un fallo
            klines = parse_klines_csv(raw)
            if klines:
                written += upsert_klines(meta.symbol, timeframe, klines)
    return written


async def _ingest_symbol(
    client: BinanceRestClient,
    meta: SymbolMeta,
    timeframe: str,
    start_ms: int,
    coverage: dict[str, dict[str, int]],
) -> tuple[int, bool]:
    """Baja y persiste un símbolo. Devuelve (velas escritas, hubo datos)."""
    step = INTERVAL_MS[timeframe]
    symbol = meta.symbol
    have = coverage.get(symbol)
    cursor = start_ms
    if have and have["n_candles"]:
        cursor = max(cursor, have["last_open_time"] + step)

    try:
        klines = await client.fetch_klines(symbol, timeframe, cursor, only_closed=True)
    except BinanceRestError as exc:
        if "Invalid symbol" not in str(exc):
            raise
        logger.info("{}: REST no lo conoce (deslistado) — se baja del archivo", symbol)
        return await _ingest_from_archive(meta, timeframe), True

    if not klines:
        return 0, bool(have and have["n_candles"])
    return upsert_klines(symbol, timeframe, klines), True


async def ingest_panel(
    timeframe: str = "1d",
    *,
    concurrency: int = 6,
    limit: int | None = None,
    min_months: int = MIN_MONTHS_TO_INGEST,
) -> IngestReport:
    """Baja las velas de todo el universo cripto congelado."""
    init_db()
    universe = load_universe(timeframe=timeframe)
    metas = universe.crypto_perps(min_months=min_months)
    if limit is not None:
        metas = metas[:limit]

    symbols = [m.symbol for m in metas]
    logger.info(
        "ingesta de {} símbolos {} (universo congelado el {})",
        len(symbols),
        timeframe,
        universe.discovered_at,
    )
    coverage = panel_coverage(symbols, timeframe)
    report = IngestReport()
    sem = asyncio.Semaphore(concurrency)
    start_ms = 0  # desde el principio: el propio archivo marca dónde empieza

    async with BinanceRestClient() as client:

        async def one(meta: SymbolMeta) -> None:
            symbol = meta.symbol
            async with sem:
                try:
                    written, had = await _ingest_symbol(
                        client, meta, timeframe, start_ms, coverage
                    )
                except Exception as exc:  # noqa: BLE001 — se reporta, no se traga
                    assert report.failures is not None
                    report.failures[symbol] = f"{type(exc).__name__}: {exc}"
                    logger.warning("{}: {}", symbol, exc)
                    return
                report.written += written
                report.symbols_done += 1
                if not had:
                    report.symbols_empty += 1

        await asyncio.gather(*(one(m) for m in metas))

    return report


def print_status(timeframe: str = "1d", min_months: int = MIN_MONTHS_TO_INGEST) -> None:
    """Resumen de lo persistido, contra lo que el universo dice que debería haber."""
    universe = load_universe(timeframe=timeframe)
    metas = universe.crypto_perps(min_months=min_months)
    symbols = [m.symbol for m in metas]
    cov = panel_coverage(symbols, timeframe)

    with_data = {s: c for s, c in cov.items() if c["n_candles"]}
    total_rows = sum(c["n_candles"] for c in with_data.values())
    firsts = [c["first_open_time"] for c in with_data.values()]
    lasts = [c["last_open_time"] for c in with_data.values()]

    print(f"universo congelado   : {universe.discovered_at}")
    print(f"  {universe.summary()}")
    print(f"a ingerir (>= {min_months} meses): {len(symbols)}")
    print(f"con datos en DB      : {len(with_data)}")
    print(f"filas totales        : {total_rows:,}")
    if firsts:
        print(f"rango                : {_fmt_ms(min(firsts))} .. {_fmt_ms(max(lasts))}")
    missing = [s for s in symbols if s not in with_data]
    if missing:
        print(f"SIN datos            : {len(missing)} -> {missing[:10]}")

    delisted = [m.symbol for m in metas if m.is_delisted]
    print(f"deslistados incluidos: {len(delisted)} -> {sorted(delisted)[:8]}")


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Ingesta del panel diario multi-símbolo")
    parser.add_argument("--timeframe", default="1d")
    parser.add_argument(
        "--discover",
        action="store_true",
        help="descubre el universo desde el archivo de Vision y lo congela en artefacto",
    )
    parser.add_argument("--ingest", action="store_true", help="baja las velas del universo")
    parser.add_argument("--status", action="store_true", help="qué hay persistido")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--limit", type=int, default=None, help="solo N símbolos (pruebas)")
    parser.add_argument("--min-months", type=int, default=MIN_MONTHS_TO_INGEST)
    args = parser.parse_args()

    enable_utf8_stdout()

    if args.discover:
        t0 = time.perf_counter()
        universe = await discover_universe(args.timeframe)
        path = save_universe(universe)
        print(f"universo congelado en {path} ({time.perf_counter() - t0:.0f}s)")
        print(f"  {universe.summary()}")
        return

    if args.status:
        print_status(args.timeframe, args.min_months)
        return

    if args.ingest:
        t0 = time.perf_counter()
        report = await ingest_panel(
            args.timeframe,
            concurrency=args.concurrency,
            limit=args.limit,
            min_months=args.min_months,
        )
        print(
            f"{report.written:,} velas escritas — {report.symbols_done} símbolos "
            f"({report.symbols_empty} sin datos nuevos) en {time.perf_counter() - t0:.0f}s"
        )
        assert report.failures is not None
        if report.failures:
            print(f"\n⚠ CORRIDA INCOMPLETA: {len(report.failures)} símbolos fallaron")
            for sym, err in list(report.failures.items())[:10]:
                print(f"    {sym}: {err}")
            print("  Reejecutar --ingest: es idempotente y reintenta solo lo que falta.")
        return

    parser.print_help()


if __name__ == "__main__":
    asyncio.run(_main())
