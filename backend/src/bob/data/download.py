"""Descarga de histórico a SQLite — insumo del backtest y del entrenamiento ML.

Uso:
    uv run python -m bob.data.download --symbol ETHUSDT --timeframe 15m --months 24
    uv run python -m bob.data.download --status

Idempotente: reejecutar continúa desde la última vela persistida.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from datetime import UTC, datetime

from loguru import logger

from bob.data.binance_rest import INTERVAL_MS, BinanceRestClient
from bob.data.store import coverage, load_series, upsert_klines
from bob.db.session import init_db
from bob.utils.console import enable_utf8_stdout


def _fmt_ms(ms: int) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


async def download_history(
    symbol: str,
    timeframe: str,
    months: int,
    *,
    resume: bool = True,
) -> int:
    """Descarga `months` meses hacia atrás y los persiste. Devuelve velas escritas."""
    init_db()
    step = INTERVAL_MS[timeframe]
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - months * 30 * 86_400_000

    have = coverage(symbol, timeframe)
    if resume and have["n_candles"] and have["last_open_time"] >= start_ms:
        start_ms = have["last_open_time"] + step
        logger.info(
            "reanudando {} {} desde {} ({} velas ya en DB)",
            symbol,
            timeframe,
            _fmt_ms(start_ms),
            have["n_candles"],
        )
        if start_ms >= now_ms:
            logger.info("nada nuevo que descargar")
            return 0
    else:
        logger.info("descarga completa de {} {} desde {}", symbol, timeframe, _fmt_ms(start_ms))

    async with BinanceRestClient() as client:
        klines = await client.fetch_klines(symbol, timeframe, start_ms, only_closed=True)

    if not klines:
        logger.warning("Binance no devolvió velas para el rango pedido")
        return 0

    written = upsert_klines(symbol, timeframe, klines)
    logger.info(
        "{} velas escritas — rango {} .. {}",
        written,
        _fmt_ms(klines[0].open_time),
        _fmt_ms(klines[-1].open_time),
    )
    return written


def print_status(symbol: str, timeframe: str) -> None:
    have = coverage(symbol, timeframe)
    print(f"\n{symbol} {timeframe}")
    print(f"  velas persistidas : {have['n_candles']:,}")
    print(f"  primera           : {_fmt_ms(have['first_open_time'])}")
    print(f"  última            : {_fmt_ms(have['last_open_time'])}")
    if have["n_candles"]:
        series = load_series(symbol, timeframe)
        gaps = series.gaps
        span_days = (have["last_open_time"] - have["first_open_time"]) / 86_400_000
        expected = int(span_days * 86_400_000 / INTERVAL_MS[timeframe]) + 1
        print(f"  cobertura         : {span_days:.1f} días")
        print(f"  completitud       : {100 * len(series) / max(expected, 1):.2f}%")
        print(f"  huecos            : {len(gaps)}")
        for a, b in gaps[:5]:
            missing = (b - a) // INTERVAL_MS[timeframe] - 1
            print(f"      {_fmt_ms(a)} -> {_fmt_ms(b)}  ({missing} velas)")
        if len(gaps) > 5:
            print(f"      ... y {len(gaps) - 5} más")
    print()


def main() -> None:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(description="Descarga histórico de Binance Futures a SQLite")
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--timeframe", default="15m", choices=sorted(INTERVAL_MS))
    parser.add_argument("--months", type=int, default=24)
    parser.add_argument("--no-resume", action="store_true", help="ignora lo ya persistido")
    parser.add_argument("--status", action="store_true", help="solo reporta qué hay en DB")
    args = parser.parse_args()

    if args.status:
        print_status(args.symbol, args.timeframe)
        return

    asyncio.run(
        download_history(args.symbol, args.timeframe, args.months, resume=not args.no_resume)
    )
    print_status(args.symbol, args.timeframe)


if __name__ == "__main__":
    main()
