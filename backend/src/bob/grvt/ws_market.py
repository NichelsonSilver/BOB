"""Market data WebSocket hub.

Wraps GrvtCcxtWS to provide asyncio.Queue-based distribution of market data
to multiple consumers (bots, API endpoints, etc.).
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from pysdk.grvt_ccxt_env import GrvtEnv, GrvtWSEndpointType
from pysdk.grvt_ccxt_ws import GrvtCcxtWS

from bob.config import settings

logger = logging.getLogger(__name__)


def _get_grvt_env() -> GrvtEnv:
    env_map = {
        "testnet": GrvtEnv.TESTNET,
        "prod": GrvtEnv.PROD,
        "mainnet": GrvtEnv.PROD,
        "dev": GrvtEnv.DEV,
        "staging": GrvtEnv.STAGING,
    }
    return env_map[settings.grvt_env.lower()]


class MarketDataHub:
    """Singleton-ish hub that manages one WS connection for all market data.

    Consumers subscribe to (stream, symbol) pairs and receive updates
    via asyncio.Queue instances.
    """

    def __init__(self) -> None:
        self._ws: GrvtCcxtWS | None = None
        # (stream, symbol) -> list of queues
        self._subscribers: dict[tuple[str, str], list[asyncio.Queue]] = defaultdict(list)
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        loop = asyncio.get_running_loop()
        self._ws = GrvtCcxtWS(
            env=_get_grvt_env(),
            loop=loop,
            logger=logger,
            parameters={
                "trading_account_id": settings.grvt_trading_account_id,
                "private_key": settings.grvt_private_key.get_secret_value(),
                "api_key": settings.grvt_api_key.get_secret_value(),
            },
        )
        await self._ws.initialize()
        self._started = True
        logger.info("MarketDataHub started")

    async def stop(self) -> None:
        if self._ws:
            for ep_type in self._ws.endpoint_types:
                await self._ws._close_connection(ep_type)
        self._started = False
        logger.info("MarketDataHub stopped")

    async def subscribe_ticker(
        self, symbol: str, queue: asyncio.Queue | None = None
    ) -> asyncio.Queue:
        """Subscribe to ticker snapshots for a symbol."""
        return await self._subscribe("ticker.s", symbol, {"instrument": symbol}, queue)

    async def subscribe_mini_ticker(
        self, symbol: str, queue: asyncio.Queue | None = None
    ) -> asyncio.Queue:
        return await self._subscribe("mini.s", symbol, {"instrument": symbol}, queue)

    async def subscribe_candle(
        self,
        symbol: str,
        interval: str = "CI_1_M",
        queue: asyncio.Queue | None = None,
    ) -> asyncio.Queue:
        """Subscribe to candlestick stream for a symbol."""
        return await self._subscribe(
            "candle",
            symbol,
            {"instrument": symbol, "interval": interval, "type": "TRADE"},
            queue,
        )

    async def subscribe_orderbook(
        self, symbol: str, depth: int = 10, queue: asyncio.Queue | None = None
    ) -> asyncio.Queue:
        return await self._subscribe(
            "book.s", symbol, {"instrument": symbol, "depth": str(depth)}, queue
        )

    async def subscribe_trades(
        self, symbol: str, queue: asyncio.Queue | None = None
    ) -> asyncio.Queue:
        return await self._subscribe(
            "trade", symbol, {"instrument": symbol}, queue
        )

    def unsubscribe(self, stream: str, symbol: str, queue: asyncio.Queue) -> None:
        """Remove a queue from subscribers for a given stream/symbol."""
        key = (stream, symbol)
        if key in self._subscribers:
            self._subscribers[key] = [q for q in self._subscribers[key] if q is not queue]

    async def _subscribe(
        self,
        stream: str,
        symbol: str,
        params: dict,
        queue: asyncio.Queue | None = None,
    ) -> asyncio.Queue:
        if not self._started or not self._ws:
            raise RuntimeError("MarketDataHub not started")

        if queue is None:
            queue = asyncio.Queue(maxsize=256)

        key = (stream, symbol)
        already_subscribed = len(self._subscribers[key]) > 0
        self._subscribers[key].append(queue)

        if not already_subscribed:

            async def _callback(message: dict[str, Any]) -> None:
                feed = message.get("feed", {})
                for q in self._subscribers[key]:
                    try:
                        q.put_nowait(feed)
                    except asyncio.QueueFull:
                        # Drop oldest to keep up
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        q.put_nowait(feed)

            await self._ws.subscribe(stream=stream, callback=_callback, params=params)
            logger.info(f"Subscribed to {stream} for {symbol}")

        return queue


# Module-level singleton
market_data_hub = MarketDataHub()
