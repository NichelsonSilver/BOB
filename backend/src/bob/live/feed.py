"""Puente entre el feed de Binance y el dashboard.

`data/` no conoce al frontend y `api/` no conoce a Binance: este módulo es el
único que ve a los dos. Toma los eventos del `MarketDataHub`, los traduce a
frames del WS del dashboard y mantiene vivo el snapshot periódico de derivados.

Latencia (regla 6): el camino tick → dashboard es memoria pura. Lo único que
toca disco es la persistencia de la vela cerrada, y eso corre en un thread
aparte dentro del hub.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from loguru import logger

from bob.data.binance_ws import (
    AggTradeEvent,
    KlineEvent,
    MarketDataHub,
    MarketEvent,
    MarkPriceEvent,
)
from bob.data.snapshots import snapshot_loop

Publisher = Callable[[str, Any], Awaitable[None]]

#: El stream de klines actualiza cada ~250ms y el de aggTrade mucho más
#: seguido. Un tick cada 250ms por símbolo ya está muy por debajo del segundo
#: que exige la regla 6, y evita ahogar al navegador.
MIN_TICK_INTERVAL_S = 0.25

#: Cada cuánto se revisa si cambió el estado de conexión. Solo se publica
#: cuando cambia: un `conn.status` por segundo sería ruido.
STATUS_POLL_S = 5.0


class LiveDataService:
    """Ciclo de vida de todo lo que corre en vivo: stream, snapshots y estado.

    Se arma en el `lifespan` del backend. Recibe el `publish` del hub de
    broadcast por inyección, así el test lo reemplaza por una lista.
    """

    def __init__(
        self,
        symbols: Sequence[str],
        timeframe: str,
        *,
        publish: Publisher,
        hub: MarketDataHub | None = None,
        snapshot_period: str = "15m",
        snapshot_interval_s: float = 1800.0,
        feed_mode: str = "auto",
        status_poll_s: float = STATUS_POLL_S,
        min_tick_interval_s: float = MIN_TICK_INTERVAL_S,
        snapshots_enabled: bool = True,
    ) -> None:
        self.symbols = [s.upper() for s in symbols]
        self.timeframe = timeframe
        self._publish = publish
        self.hub = hub or MarketDataHub(self.symbols, timeframe, mode=feed_mode)
        self._snapshot_period = snapshot_period
        self._snapshot_interval_s = snapshot_interval_s
        self._snapshots_enabled = snapshots_enabled
        self._status_poll_s = status_poll_s
        self._min_tick_interval_s = min_tick_interval_s
        self._last_tick_sent: dict[str, float] = {}
        self._last_status: dict[str, Any] | None = None
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self.hub.add_listener(self._on_event)

    async def start(self) -> None:
        self._stop.clear()
        await self.hub.start()
        self._tasks = [asyncio.create_task(self._status_watcher(), name="conn-status")]
        if self._snapshots_enabled:
            self._tasks.append(
                asyncio.create_task(
                    snapshot_loop(
                        self.symbols,
                        self._snapshot_period,
                        self._snapshot_interval_s,
                        stop=self._stop,
                    ),
                    name="derivatives-snapshot",
                )
            )
        logger.info(
            "live: feed arriba — {} {} (snapshots {})",
            ", ".join(self.symbols),
            self.timeframe,
            "on" if self._snapshots_enabled else "off",
        )

    async def stop(self) -> None:
        self._stop.set()
        await self.hub.stop()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        logger.info("live: feed detenido")

    # -- Traducción de eventos ------------------------------------------ #

    async def _on_event(self, event: MarketEvent) -> None:
        if isinstance(event, KlineEvent):
            if event.is_closed:
                # La vela cerrada es la única que el modelo puede mirar: se
                # publica siempre, sin throttle.
                await self._publish("market.candle", self._candle_payload(event))
            elif self._should_send_tick(event.symbol):
                await self._publish("market.tick", self._tick_payload(event))
        elif isinstance(event, MarkPriceEvent):
            await self._publish(
                "market.tick",
                {
                    "symbol": event.symbol,
                    "mark_price": event.mark_price,
                    "funding_rate": event.funding_rate,
                    "next_funding_time": event.next_funding_time,
                    "event_time": event.event_time,
                },
            )
        elif isinstance(event, AggTradeEvent):
            # Los trades individuales no viajan al navegador: se agregan en el
            # hub y salen resumidos en el payload de cada tick.
            return

    def _should_send_tick(self, symbol: str) -> bool:
        now = time.monotonic()
        last = self._last_tick_sent.get(symbol, 0.0)
        if now - last < self._min_tick_interval_s:
            return False
        self._last_tick_sent[symbol] = now
        return True

    def _tick_payload(self, event: KlineEvent) -> dict[str, Any]:
        state = self.hub.state.get(event.symbol)
        return {
            "symbol": event.symbol,
            "timeframe": event.timeframe,
            "open_time": event.kline.open_time,
            "price": event.kline.close,
            "high": event.kline.high,
            "low": event.kline.low,
            "volume": event.kline.volume,
            "closed": False,
            "taker_buy_qty": state.taker_buy_qty if state else 0.0,
            "taker_sell_qty": state.taker_sell_qty if state else 0.0,
            "event_time": event.event_time,
        }

    def _candle_payload(self, event: KlineEvent) -> dict[str, Any]:
        k = event.kline
        return {
            "symbol": event.symbol,
            "timeframe": event.timeframe,
            "open_time": k.open_time,
            "close_time": k.close_time,
            "open": k.open,
            "high": k.high,
            "low": k.low,
            "close": k.close,
            "volume": k.volume,
            "taker_buy_volume": k.taker_buy_volume,
            "n_trades": k.n_trades,
            "closed": True,
        }

    # -- Estado de conexión ---------------------------------------------- #

    async def _status_watcher(self) -> None:
        """Publica `conn.status` cuando cambia algo que el usuario deba ver.

        Regla 8: el dashboard muestra el estado de cada conexión. Un feed caído
        sin aviso es peor que no tener feed — el usuario creería que el precio
        que está mirando es el de ahora.
        """
        while not self._stop.is_set():
            await self._publish_status_if_changed()
            with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._status_poll_s)

    async def _publish_status_if_changed(self) -> None:
        status = self.hub.status.as_dict()
        watched = {
            "source": self.hub.source_name,
            "connected": status["connected"],
            "reconnects": status["reconnects"],
            "last_error": status["last_error"],
        }
        if watched == self._last_status:
            return
        self._last_status = watched
        await self._publish(
            "conn.status",
            {"source": self.hub.source_name, "symbols": self.symbols, **status},
        )
