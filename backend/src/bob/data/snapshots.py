"""Snapshots periódicos de derivados — OI, ratio long/short y taker ratio.

Por qué existe este módulo y no espera a la Fase 2b: **Binance solo conserva
~30 días** de `openInterestHist` y de los ratios de posicionamiento
(docs/DATA_SOURCES.md). El histórico de klines se puede pedir cuando sea; el
de derivados, no. Cada día que el asistente corre sin snapshotear es un día
de entrenamiento que ya no se puede recuperar a ningún precio.

La cadencia es de minutos, nunca del hot path: los `/futures/data/*` tienen
límites más estrictos que `/fapi/*` y no se rafaguean (regla 7).
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from bob.data.binance_rest import BinanceRestClient
from bob.data.store import derivatives_coverage, upsert_derivatives
from bob.db.session import init_db
from bob.utils.console import enable_utf8_stdout

#: Grilla de 5 minutos, la MISMA que publica el archivo diario `metrics/`
#: (data/vision.py). No es un detalle de resolución: si las dos fuentes usaran
#: periods distintos escribirían dos series que nunca se tocan, y la familia de
#: derivados se cortaría justo el día en que termina el archivo.
#:
#: Cada request trae hasta 500 puntos: a 5m son ~41 horas de historia. Con
#: snapshots cada 30 min el solape sigue siendo enorme, y el solape es
#: justamente lo que hace la ingesta inmune a que el proceso esté caído un rato.
DEFAULT_PERIOD = "5m"
DEFAULT_LIMIT = 500
DEFAULT_INTERVAL_S = 1800.0


@dataclass(frozen=True)
class DerivativePoint:
    """Un punto de derivados ya alineado por timestamp.

    Los campos son opcionales a propósito: los tres endpoints no siempre
    devuelven la misma grilla, y es preferible guardar un punto con OI y sin
    ratio que descartarlo. El modelo tiene que tolerar features faltantes.
    """

    timestamp: int  # epoch ms UTC
    open_interest: str | None = None
    open_interest_value: str | None = None
    long_short_ratio: str | None = None
    long_account_pct: str | None = None
    short_account_pct: str | None = None
    taker_buy_sell_ratio: str | None = None
    #: Solo los llena el archivo histórico (data/vision.py); el REST no los da
    #: en el mismo endpoint y no vale la pena un request extra en vivo.
    top_trader_account_ratio: str | None = None
    top_trader_position_ratio: str | None = None
    #: Solo en las filas de period="funding"; el resto lo deja en None.
    funding_rate: str | None = None


def _ts(row: dict[str, Any]) -> int | None:
    """El timestamp de los `/futures/data/*` llega a veces como str."""
    raw = row.get("timestamp")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _text(row: dict[str, Any], key: str) -> str | None:
    value = row.get(key)
    return None if value is None else str(value)


def merge_derivative_rows(
    oi_rows: Iterable[dict[str, Any]],
    ls_rows: Iterable[dict[str, Any]],
    taker_rows: Iterable[dict[str, Any]],
) -> list[DerivativePoint]:
    """Alinea las tres respuestas por timestamp. Puro: se testea sin red.

    Las filas sin timestamp usable se descartan — un punto sin su instante no
    sirve para nada aguas arriba, y colarlo con timestamp 0 sería peor.
    """
    merged: dict[int, dict[str, str | None]] = {}

    for row in oi_rows:
        ts = _ts(row)
        if ts is None:
            continue
        entry = merged.setdefault(ts, {})
        entry["open_interest"] = _text(row, "sumOpenInterest")
        entry["open_interest_value"] = _text(row, "sumOpenInterestValue")

    for row in ls_rows:
        ts = _ts(row)
        if ts is None:
            continue
        entry = merged.setdefault(ts, {})
        entry["long_short_ratio"] = _text(row, "longShortRatio")
        entry["long_account_pct"] = _text(row, "longAccount")
        entry["short_account_pct"] = _text(row, "shortAccount")

    for row in taker_rows:
        ts = _ts(row)
        if ts is None:
            continue
        entry = merged.setdefault(ts, {})
        entry["taker_buy_sell_ratio"] = _text(row, "buySellRatio")

    return [
        DerivativePoint(
            timestamp=ts,
            open_interest=fields.get("open_interest"),
            open_interest_value=fields.get("open_interest_value"),
            long_short_ratio=fields.get("long_short_ratio"),
            long_account_pct=fields.get("long_account_pct"),
            short_account_pct=fields.get("short_account_pct"),
            taker_buy_sell_ratio=fields.get("taker_buy_sell_ratio"),
        )
        for ts, fields in sorted(merged.items())
    ]


async def fetch_derivatives(
    client: BinanceRestClient,
    symbol: str,
    period: str = DEFAULT_PERIOD,
    limit: int = DEFAULT_LIMIT,
) -> list[DerivativePoint]:
    """Pide los tres endpoints y devuelve los puntos alineados.

    Secuencial, no en paralelo: los `/futures/data/*` comparten un límite
    estrecho y el limiter del cliente los espacia. Son tres requests cada
    media hora — la latencia acá no le importa a nadie.
    """
    oi = await client.open_interest_hist(symbol, period=period, limit=limit)
    ls = await client.long_short_ratio(symbol, period=period, limit=limit)
    taker = await client.taker_ratio(symbol, period=period, limit=limit)
    return merge_derivative_rows(oi, ls, taker)


async def snapshot_once(
    symbols: Sequence[str],
    period: str = DEFAULT_PERIOD,
    limit: int = DEFAULT_LIMIT,
    *,
    client: BinanceRestClient | None = None,
) -> dict[str, int]:
    """Un ciclo de snapshot para la watchlist. Devuelve filas escritas por símbolo.

    Idempotente: los puntos se identifican por (symbol, period, timestamp), así
    que el solape entre ciclos reescribe lo mismo en vez de duplicarlo.
    """
    init_db()
    owns = client is None
    client = client or BinanceRestClient()
    written: dict[str, int] = {}
    try:
        for symbol in symbols:
            sym = symbol.upper()
            try:
                points = await fetch_derivatives(client, sym, period, limit)
            except Exception as exc:
                # Que un símbolo falle no puede dejar sin snapshot a los demás:
                # la ventana de 30 días corre para todos por igual.
                logger.warning("snapshot: {} falló ({}) — sigue la watchlist", sym, exc)
                written[sym] = 0
                continue
            rows = await asyncio.to_thread(upsert_derivatives, sym, period, points)
            written[sym] = rows
            logger.info("snapshot: {} {} — {} puntos persistidos", sym, period, rows)
    finally:
        if owns:
            await client.aclose()
    return written


async def snapshot_loop(
    symbols: Sequence[str],
    period: str = DEFAULT_PERIOD,
    interval_s: float = DEFAULT_INTERVAL_S,
    *,
    stop: asyncio.Event | None = None,
) -> None:
    """Corre `snapshot_once` cada `interval_s` hasta que se pida el corte.

    Se lanza como task en el lifespan del backend. No usa APScheduler a
    propósito: es un solo job periódico y una task de asyncio no tiene ni
    scheduler que arrancar ni estado que persistir.
    """
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            await snapshot_once(symbols, period)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("snapshot: ciclo falló ({}) — reintenta al próximo", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            continue


def _fmt_ms(ms: int) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def print_status(symbol: str, period: str) -> None:
    have = derivatives_coverage(symbol, period)
    print(f"\n{symbol} derivados {period}")
    print(f"  puntos persistidos : {have['n_points']:,}")
    print(f"  primero            : {_fmt_ms(have['first_timestamp'])}")
    print(f"  último             : {_fmt_ms(have['last_timestamp'])}")
    print("  ventana de Binance : ~30 días (lo anterior ya no es recuperable)\n")


def main() -> None:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="Snapshot de OI y ratios de posicionamiento a SQLite"
    )
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--period", default=DEFAULT_PERIOD)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--status", action="store_true", help="solo reporta qué hay en DB")
    parser.add_argument(
        "--loop",
        action="store_true",
        help=f"queda corriendo, un ciclo cada {DEFAULT_INTERVAL_S / 60:.0f} min",
    )
    args = parser.parse_args()

    if args.status:
        print_status(args.symbol, args.period)
        return

    symbols = [s.strip() for s in args.symbol.split(",") if s.strip()]
    if args.loop:
        asyncio.run(snapshot_loop(symbols, args.period))
    else:
        asyncio.run(snapshot_once(symbols, args.period, args.limit))
        print_status(symbols[0], args.period)


if __name__ == "__main__":
    main()
