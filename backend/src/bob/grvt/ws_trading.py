"""Trading WebSocket hub — fills / order updates / positions.

Thin sibling of MarketDataHub focused on authenticated streams.

GRVT exposes the trading feed at `wss://trades.<env>.grvt.io/ws/full`. The
underlying `GrvtCcxtWS` instance multiplexes both market and trading
endpoints but we keep a dedicated Hub here so Phase-5 concerns (fills,
order acks) are isolated from market data distribution.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from pysdk.grvt_ccxt_env import GrvtEnv
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


class TradingHub:
    """Manages the trading WS connection and distributes events to queues.

    The hub subscribes once per (stream, selector) pair at GRVT and then
    fans out to multiple internal asyncio.Queues so that several bots can
    independently consume their fills / order updates.
    """

    def __init__(self) -> None:
        self._ws: GrvtCcxtWS | None = None
        # (stream, selector) -> list[Queue]
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
        logger.info("TradingHub started")

    async def stop(self) -> None:
        if self._ws:
            for ep_type in self._ws.endpoint_types:
                await self._ws._close_connection(ep_type)
        self._started = False
        logger.info("TradingHub stopped")

    async def subscribe_fills(
        self, symbol: str | None = None, queue: asyncio.Queue | None = None
    ) -> asyncio.Queue:
        """Stream user fills. Pass symbol=None for every instrument."""
        params: dict[str, Any] = {"sub_account_id": settings.grvt_trading_account_id}
        if symbol:
            params["instrument"] = symbol
        selector = symbol or "*"
        return await self._subscribe("fill", selector, params, queue)

    async def subscribe_orders(
        self, symbol: str | None = None, queue: asyncio.Queue | None = None
    ) -> asyncio.Queue:
        """Stream order state updates (accepted, partially_filled, cancelled…)."""
        params: dict[str, Any] = {"sub_account_id": settings.grvt_trading_account_id}
        if symbol:
            params["instrument"] = symbol
        selector = symbol or "*"
        return await self._subscribe("order", selector, params, queue)

    async def subscribe_state(
        self, queue: asyncio.Queue | None = None
    ) -> asyncio.Queue:
        """Stream sub-account state (positions, balances)."""
        params = {"sub_account_id": settings.grvt_trading_account_id}
        return await self._subscribe("state", "account", params, queue)

    def unsubscribe(self, stream: str, selector: str, queue: asyncio.Queue) -> None:
        key = (stream, selector)
        if key in self._subscribers:
            self._subscribers[key] = [q for q in self._subscribers[key] if q is not queue]

    async def _subscribe(
        self,
        stream: str,
        selector: str,
        params: dict,
        queue: asyncio.Queue | None,
    ) -> asyncio.Queue:
        if not self._started or not self._ws:
            raise RuntimeError("TradingHub not started")

        if queue is None:
            queue = asyncio.Queue(maxsize=512)

        key = (stream, selector)
        already_subscribed = len(self._subscribers[key]) > 0
        self._subscribers[key].append(queue)

        if not already_subscribed:

            async def _callback(message: dict[str, Any]) -> None:
                feed = message.get("feed", message)
                for q in self._subscribers[key]:
                    try:
                        q.put_nowait(feed)
                    except asyncio.QueueFull:
                        # Drop oldest — trading feed is critical but if the
                        # consumer is this far behind, the bot will re-sync
                        # via fetch_open_orders.
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        q.put_nowait(feed)

            await self._ws.subscribe(stream=stream, callback=_callback, params=params)
            logger.info("TradingHub subscribed to %s selector=%s", stream, selector)

        return queue


trading_hub = TradingHub()
