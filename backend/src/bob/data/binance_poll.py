"""Fuente de datos en vivo por REST — tapa los agujeros que deje el WS.

Existe por un hallazgo de campo del 2026-08-24, cuyo diagnóstico se corrigió el
mismo día al medirlo bien (evidencia completa en docs/DATA_SOURCES.md):
`fstream.binance.com` **entrega unos streams y calla otros sobre la misma
conexión TLS**. Vivos: `@trade`, `@bookTicker`, `@depth*`. Mudos: `@aggTrade`,
`@kline_*`, `@markPrice`, `@ticker`, `@miniTicker`, `@forceOrder`. Como un
middlebox no puede descartar mensajes selectivos dentro de un TLS ya
establecido, la causa es Binance a nivel de aplicación —no la red del usuario,
como se creyó primero— y no distingue mainnet de derivados: distingue evento
crudo del motor de matching (llega) de evento derivado o agregado (no llega).

Consecuencia para este módulo: dejó de ser el "plan B que reemplaza al WS" y
pasó a ser **el relleno de lo que el WS no da**. Los flags `klines` y
`mark_price` permiten pedirle solo una parte, que es como lo usa el hub: el
flujo taker sigue viniendo del WS por `@trade` (latencia <100ms, verificado
idéntico al oficial) y por acá entran nada más las velas y el mark price /
funding, que son magnitudes lentas y no pierden nada a cadencia de segundos.
Sigue sirviendo como fuente completa si el WS no entrega absolutamente nada.

Presupuesto de peso: klines(limit=2) = 2 y premiumIndex = 1 por símbolo por
ciclo. A 3s, los dos juntos son 60/min por símbolo contra un techo de 2400
(regla 7, con holgura de sobra).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from typing import Any, Final

from loguru import logger

from bob.data.binance_rest import BinanceRestClient, Kline
from bob.data.binance_ws import (
    ConnectionStatus,
    KlineEvent,
    Listener,
    MarketEvent,
    MarkPriceEvent,
    _now_ms,
)

#: Cadencia por defecto. Más rápido no mejora nada: la vela de 15m no cambia
#: de forma interesante en menos de un segundo, y el peso se acumula.
DEFAULT_POLL_S: Final = 3.0


class BinancePollSource:
    """Emite `KlineEvent` / `MarkPriceEvent` desde el REST, en loop.

    Interfaz idéntica a `BinanceMarketStream` (`add_listener`, `start`, `stop`,
    `status`), así que el hub y el dashboard no saben cuál de las dos fuentes
    los está alimentando — salvo por el nombre que se reporta.

    `klines` y `mark_price` acotan qué emite. Apagar lo que el WS ya entrega
    evita el peor error posible de un feed híbrido: dos fuentes publicando el
    mismo símbolo y el dashboard alternando entre un precio fresco y uno de
    hace tres segundos.
    """

    source_name = "binance_rest_poll"

    def __init__(
        self,
        symbols: Sequence[str],
        timeframe: str,
        *,
        client: BinanceRestClient | None = None,
        interval_s: float = DEFAULT_POLL_S,
        klines: bool = True,
        mark_price: bool = True,
    ) -> None:
        if not (klines or mark_price):
            raise ValueError("la fuente REST no tendría nada que emitir")
        self._symbols = [s.upper() for s in symbols]
        self._timeframe = timeframe
        self._owns_client = client is None
        self._client = client or BinanceRestClient()
        self._interval_s = interval_s
        self._want_klines = klines
        self._want_mark_price = mark_price
        self._listeners: list[Listener] = []
        self._status = ConnectionStatus()
        self._last_closed: dict[str, int] = {}
        self._stopping = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    # -- Misma API que el stream ----------------------------------------- #

    @property
    def status(self) -> ConnectionStatus:
        return self._status

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self.run(), name="binance-poll")

    async def stop(self) -> None:
        self._stopping.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._owns_client:
            await self._client.aclose()
        self._status.connected = False

    async def run(self) -> None:
        logger.info(
            "poll: fuente REST activa — {} {} cada {:.0f}s ({})",
            ", ".join(self._symbols),
            self._timeframe,
            self._interval_s,
            "+".join(
                k
                for k, on in (("klines", self._want_klines), ("markPrice", self._want_mark_price))
                if on
            ),
        )
        while not self._stopping.is_set():
            for symbol in self._symbols:
                if self._stopping.is_set():
                    break
                await self._poll_symbol(symbol)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=self._interval_s)
        self._status.connected = False
        logger.info("poll: fuente REST detenida")

    # -- Interno ---------------------------------------------------------- #

    async def _poll_symbol(self, symbol: str) -> None:
        try:
            # limit=2: la penúltima es la última CERRADA, la última es la que
            # está en curso. `only_closed=False` es correcto acá porque el
            # evento lleva el flag y quien lo consume decide.
            rows = (
                await self._client.klines_page(symbol, self._timeframe, limit=2)
                if self._want_klines
                else []
            )
            premium = await self._client.mark_price(symbol) if self._want_mark_price else None
        except Exception as exc:
            self._status.connected = False
            self._status.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("poll: {} falló ({})", symbol, self._status.last_error)
            return

        self._status.connected = True
        self._status.last_error = None
        self._status.last_message_ms = _now_ms()

        if rows:
            await self._emit_klines(symbol, rows)
        if premium:
            await self._emit_mark_price(symbol, premium)

    async def _emit_klines(self, symbol: str, rows: list[Kline]) -> None:
        in_progress = rows[-1]
        if len(rows) > 1:
            closed = rows[-2]
            # Solo se emite un cierre nuevo: el polling ve la misma vela
            # cerrada muchas veces y duplicarla ensuciaría la DB y el gráfico.
            if self._last_closed.get(symbol, 0) < closed.open_time:
                self._last_closed[symbol] = closed.open_time
                await self._dispatch(
                    KlineEvent(
                        symbol=symbol,
                        timeframe=self._timeframe,
                        kline=closed,
                        is_closed=True,
                        event_time=_now_ms(),
                    )
                )
        await self._dispatch(
            KlineEvent(
                symbol=symbol,
                timeframe=self._timeframe,
                kline=in_progress,
                is_closed=False,
                event_time=_now_ms(),
            )
        )

    async def _emit_mark_price(self, symbol: str, premium: dict[str, Any]) -> None:
        def _int(key: str) -> int:
            try:
                return int(premium.get(key, 0) or 0)
            except (TypeError, ValueError):  # pragma: no cover — formato inesperado
                return 0

        await self._dispatch(
            MarkPriceEvent(
                symbol=symbol,
                mark_price=str(premium.get("markPrice", "0")),
                index_price=str(premium.get("indexPrice", "0")),
                funding_rate=str(premium.get("lastFundingRate", "0")),
                next_funding_time=_int("nextFundingTime"),
                event_time=_int("time"),
            )
        )

    async def _dispatch(self, event: MarketEvent) -> None:
        self._status.messages += 1
        for listener in self._listeners:
            try:
                await listener(event)
            except Exception as exc:
                logger.exception("poll: listener falló ({}) — la fuente sigue", exc)
