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


async def repair_series(symbol: str, timeframe: str) -> dict[str, int]:
    """Rellena los huecos INTERIORES de la serie y la extiende hasta ahora.

    `download_history --resume` avanza desde la última vela que hay en DB: es
    lo correcto para una descarga incremental y es exactamente lo que falla
    después de una pausa del proceso.

    La secuencia que rompe: el equipo se suspende dos horas, vuelve, el feed
    reconecta y escribe la vela **actual**. Ahora la última vela en DB es
    posterior a la pausa, así que `--resume` arranca de ahí y el agujero de dos
    horas queda dentro de la serie para siempre — silencioso, porque nadie
    vuelve a mirar ese rango. Y un hueco interior no es cosmético: las ventanas
    rodantes de `signals/features.py` cuentan barras, no tiempo, así que a
    partir del hueco todas las features de contexto describen una ventana que
    no existió.

    Por eso esta función mira los huecos que reporta `OHLCVSeries.gaps` y pide
    cada rango por separado, y solo después extiende hacia adelante. Es
    idempotente: sobre una serie completa no hace ni un request de relleno.
    """
    init_db()
    series = load_series(symbol, timeframe)
    step = INTERVAL_MS[timeframe]
    filled = 0
    gaps = series.gaps

    async with BinanceRestClient() as client:
        for prev_open, next_open in gaps:
            klines = await client.fetch_klines(
                symbol, timeframe, prev_open + step, next_open - 1, only_closed=True
            )
            if klines:
                filled += upsert_klines(symbol, timeframe, klines)
        if gaps:
            logger.info(
                "{} {}: {} hueco(s) interiores, {} velas recuperadas",
                symbol,
                timeframe,
                len(gaps),
                filled,
            )

        last = int(series.open_time[-1]) if len(series) else None
        extended = 0
        if last is not None:
            nuevas = await client.fetch_klines(symbol, timeframe, last + step, only_closed=True)
            if nuevas:
                extended = upsert_klines(symbol, timeframe, nuevas)

    restante = load_series(symbol, timeframe).gaps
    if restante:
        logger.warning(
            "{} {}: quedan {} hueco(s) que Binance no devolvió — se reportan, "
            "no se rellenan",
            symbol,
            timeframe,
            len(restante),
        )
    return {
        "gaps_found": len(gaps),
        "filled": filled,
        "extended": extended,
        "gaps_remaining": len(restante),
    }


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
    parser.add_argument(
        "--repair",
        action="store_true",
        help="rellena huecos interiores y extiende hasta ahora (usar tras una pausa)",
    )
    args = parser.parse_args()

    if args.status:
        print_status(args.symbol, args.timeframe)
        return

    if args.repair:
        resultado = asyncio.run(repair_series(args.symbol, args.timeframe))
        print(
            f"\nreparación: {resultado['gaps_found']} hueco(s) encontrados, "
            f"{resultado['filled']} velas recuperadas, "
            f"{resultado['extended']} agregadas al final, "
            f"{resultado['gaps_remaining']} hueco(s) sin cerrar"
        )
    else:
        asyncio.run(
            download_history(args.symbol, args.timeframe, args.months, resume=not args.no_resume)
        )
    print_status(args.symbol, args.timeframe)


if __name__ == "__main__":
    main()
