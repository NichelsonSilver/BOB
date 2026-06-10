"""Fase 5 — smoke test en testnet con 1 bot real.

Uso:
    cd backend
    .venv/Scripts/python.exe -m scripts.phase5_live_smoke \
        --symbol BTC_USDT_Perp \
        --low 94000 --high 96000 --grids 8 \
        --investment 100 --leverage 3 \
        --direction neutral --minutes 60

Qué hace:
    - Confirma conexión GRVT (testnet)
    - Arranca MarketDataHub + TradingHub
    - Crea 1 GridBot en modo "live" vía BotManager
    - Loguea estado cada 30 s
    - Al SIGINT (o al llegar al tiempo objetivo) llama stop_all → cancela
      todas las órdenes abiertas en GRVT

Validación esperada:
    - El primer round de órdenes aparece en GRVT UI (estado Working)
    - Cada fill se refleja en bob.db tabla FillRecord con mode='live'
    - Al terminar, fetch_open_orders devuelve [] para el símbolo
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from decimal import Decimal

from bob.config import settings
from bob.db.session import init_db
from bob.grid.engine import BotConfig
from bob.grid.manager import BotManager
from bob.grvt.client import check_grvt_connection, create_grvt_client
from bob.grvt.rest import GrvtRestClient
from bob.grvt.ws_market import market_data_hub
from bob.grvt.ws_trading import trading_hub

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("phase5")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC_USDT_Perp")
    ap.add_argument("--low", required=True, type=str)
    ap.add_argument("--high", required=True, type=str)
    ap.add_argument("--grids", type=int, default=8)
    ap.add_argument("--investment", type=str, default="100")
    ap.add_argument("--leverage", type=int, default=3)
    ap.add_argument(
        "--direction",
        choices=["long", "short", "neutral"],
        default="neutral",
    )
    ap.add_argument("--spacing", choices=["arithmetic", "geometric"], default="arithmetic")
    ap.add_argument("--tick", type=str, default="0.1")
    ap.add_argument("--lot", type=str, default="0.001")
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument(
        "--bot-id",
        default="phase5-smoke",
        help="Internal bot id (no debe colisionar con otras corridas)",
    )
    return ap.parse_args()


async def run(args: argparse.Namespace) -> None:
    assert settings.grvt_env.lower() == "testnet", (
        f"Smoke test debe correr en testnet, actual={settings.grvt_env!r}"
    )

    init_db()

    grvt_client = create_grvt_client()
    await grvt_client.load_markets()
    rest_client = GrvtRestClient(grvt_client)

    instrument = grvt_client.markets.get(args.symbol)
    if instrument is None:
        raise RuntimeError(f"Unknown symbol {args.symbol!r}")
    min_notional = Decimal(instrument.get("min_notional", "0"))
    min_size = Decimal(instrument.get("min_size", args.lot))
    logger.info(
        "Instrument %s | min_size=%s | min_notional=%s",
        args.symbol,
        min_size,
        min_notional,
    )

    avg_price = (Decimal(args.low) + Decimal(args.high)) / 2
    qty_est = (Decimal(args.investment) * args.leverage) / (
        Decimal(args.grids) * avg_price
    )
    qty_est_quantized = (qty_est // Decimal(args.lot)) * Decimal(args.lot)
    notional_est = qty_est_quantized * avg_price
    logger.info(
        "Estimated qty/grid=%s → notional ≈ %s (min=%s)",
        qty_est_quantized,
        notional_est,
        min_notional,
    )
    if qty_est_quantized < min_size:
        raise RuntimeError(
            f"qty/grid {qty_est_quantized} < min_size {min_size} — aumentá investment o bajá grids"
        )
    if notional_est < min_notional:
        raise RuntimeError(
            f"notional {notional_est} < min_notional {min_notional} — aumentá investment/leverage o bajá grids"
        )

    health = await check_grvt_connection(grvt_client)
    logger.info("GRVT health: %s", health)
    if not health.get("authenticated"):
        raise RuntimeError("GRVT auth failed — revisá tu .env")

    await market_data_hub.start()
    await trading_hub.start()

    manager = BotManager(
        market_data_hub=market_data_hub,
        trading_hub=trading_hub,
        rest_client=rest_client,
    )

    config = BotConfig(
        symbol=args.symbol,
        direction=args.direction,
        price_low=Decimal(args.low),
        price_high=Decimal(args.high),
        n_grids=args.grids,
        investment_usdt=Decimal(args.investment),
        leverage=args.leverage,
        spacing=args.spacing,
        tick_size=Decimal(args.tick),
        lot_size=Decimal(args.lot),
    )

    await manager.create_and_start(args.bot_id, config, mode="live")

    stop_event = asyncio.Event()

    def _on_signal() -> None:
        logger.warning("SIGINT — stopping bots")
        stop_event.set()

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _on_signal)
    except NotImplementedError:
        # Windows: signal handlers via loop unsupported, fallback to
        # KeyboardInterrupt propagation via the wait below.
        pass

    deadline = loop.time() + args.minutes * 60
    try:
        while not stop_event.is_set() and loop.time() < deadline:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass
            status = manager.get_status(args.bot_id)
            logger.info(
                "STATUS | state=%s price=%s trades=%s pnl=%s live_orders=%s",
                status.get("state"),
                status.get("last_price"),
                status.get("grid_trades_count"),
                status.get("realized_pnl"),
                status.get("live_orders_count"),
            )
    except KeyboardInterrupt:
        logger.warning("KeyboardInterrupt — stopping")
    finally:
        await manager.stop_all(reason="smoke_end")
        await trading_hub.stop()
        await market_data_hub.stop()

        # Sanity: confirmar que no quedaron órdenes abiertas
        try:
            remaining = await rest_client.fetch_open_orders(symbol=args.symbol)
            logger.info("open orders remaining: %d", len(remaining))
        except Exception as e:  # pragma: no cover
            logger.warning("could not fetch open orders at end: %s", e)


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
