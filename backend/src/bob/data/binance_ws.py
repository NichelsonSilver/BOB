"""Stream WebSocket de Binance USDⓈ-M Futures — el feed en vivo del asistente.

Junto a `binance_rest.py`, el único lugar con I/O de mercado (regla 3 de
CLAUDE.md). Market data público: sin API key, sin credenciales, sin órdenes.

Dos decisiones gobiernan el diseño:

1. **La reconexión es el caso normal, no el edge case.** Binance corta cada
   conexión a las 24h por diseño (docs/DATA_SOURCES.md). El loop reconecta
   con backoff exponencial + jitter y resuscribe todo; el contador de
   intentos se resetea en cuanto llega el primer frame bueno.
2. **La vela en curso no es una vela.** Solo se persiste una kline con
   `k.x == true`. Computar features sobre la vela abierta es lookahead en
   producción que el backtest jamás vería (regla 5).

El parseo es puro y está separado del transporte: `parse_frame` se testea sin
red, y el loop se testea con una fábrica de conexión falsa.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Final, Protocol

from loguru import logger

from bob.data.binance_rest import INTERVAL_MS, Kline

FSTREAM_BASE: Final = "wss://fstream.binance.com/stream"

#: Binance corta la conexión a las 24h. Reciclamos antes, en un momento
#: elegido por nosotros, para que el corte no caiga justo en un cierre de vela.
CONNECTION_TTL_S: Final = 23 * 3600.0

#: Si no llega ningún frame en este lapso, la conexión se da por muerta. Con
#: markPrice@1s y aggTrade activos, 60s de silencio es anomalía, no calma.
RECV_TIMEOUT_S: Final = 60.0

#: Techo del backoff entre reintentos.
MAX_BACKOFF_S: Final = 60.0

#: Piso entre sesiones, incluso cuando la reconexión es "inmediata" (cierre
#: limpio por TTL). Sin él, una conexión que muere en el handshake sin ceder
#: el control deja al loop girando en vacío y se come el event loop entero.
RECONNECT_FLOOR_S: Final = 0.1

#: Límite documentado de streams por conexión. Con la watchlist v1 sobra, pero
#: si alguien mete 100 símbolos conviene enterarse acá y no en un close code
#: opaco de Binance.
MAX_STREAMS_PER_CONNECTION: Final = 200


# --------------------------------------------------------------------- #
# Eventos (inmutables, sin dependencia del transporte)
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class KlineEvent:
    """Una vela del stream. `is_closed=False` es la vela EN CURSO."""

    symbol: str
    timeframe: str
    kline: Kline
    is_closed: bool
    event_time: int  # epoch ms UTC


@dataclass(frozen=True)
class MarkPriceEvent:
    """Mark price + funding corriente. Alimenta el KPI in-live y la distancia
    a liquidación. Precios como `str` (convención de db/models.py)."""

    symbol: str
    mark_price: str
    index_price: str
    funding_rate: str
    next_funding_time: int
    event_time: int


@dataclass(frozen=True)
class AggTradeEvent:
    """Trade agregado. `is_buyer_maker=True` ⇒ el agresor fue el vendedor: así
    se separa el volumen taker comprador del vendedor."""

    symbol: str
    price: str
    quantity: str
    is_buyer_maker: bool
    trade_time: int
    event_time: int


MarketEvent = KlineEvent | MarkPriceEvent | AggTradeEvent


@dataclass
class ConnectionStatus:
    """Estado de la conexión, para el evento `conn.status` del dashboard."""

    connected: bool = False
    connected_since_ms: int | None = None
    last_message_ms: int | None = None
    reconnects: int = 0
    messages: int = 0
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "connected_since_ms": self.connected_since_ms,
            "last_message_ms": self.last_message_ms,
            "reconnects": self.reconnects,
            "messages": self.messages,
            "last_error": self.last_error,
        }


# --------------------------------------------------------------------- #
# Armado de la URL y parseo (puros)
# --------------------------------------------------------------------- #


def stream_names(
    symbols: Iterable[str],
    timeframe: str,
    *,
    klines: bool = True,
    mark_price: bool = True,
    agg_trades: bool = True,
) -> list[str]:
    """Nombres de stream combinados para la watchlist.

    Los símbolos van en minúscula: con mayúscula Binance acepta el handshake
    y después no manda nada, así que el bug parece de red y no lo es.
    """
    if timeframe not in INTERVAL_MS:
        raise ValueError(f"timeframe no soportado: {timeframe}")
    names: list[str] = []
    for raw in symbols:
        sym = raw.strip().lower()
        if not sym:
            continue
        if klines:
            names.append(f"{sym}@kline_{timeframe}")
        if mark_price:
            names.append(f"{sym}@markPrice@1s")
        if agg_trades:
            names.append(f"{sym}@aggTrade")
    return names


def combined_url(streams: Sequence[str], base_url: str = FSTREAM_BASE) -> str:
    """URL del endpoint combinado `/stream?streams=a/b/c`."""
    if not streams:
        raise ValueError("no hay streams a los que suscribirse")
    if len(streams) > MAX_STREAMS_PER_CONNECTION:
        raise ValueError(
            f"{len(streams)} streams supera el máximo por conexión "
            f"({MAX_STREAMS_PER_CONNECTION}): multiplexar en varias conexiones"
        )
    joined = "/".join(streams)
    return f"{base_url.rstrip('/')}?streams={joined}"


def _kline_from_payload(k: dict[str, Any]) -> Kline:
    """Mapea el objeto `k` del stream al mismo `Kline` que devuelve el REST.

    Que ambos caminos produzcan el mismo tipo es lo que permite persistir la
    vela en vivo con el `upsert_klines` del histórico, sin código paralelo.
    """
    return Kline(
        open_time=int(k["t"]),
        open=str(k["o"]),
        high=str(k["h"]),
        low=str(k["l"]),
        close=str(k["c"]),
        volume=str(k["v"]),
        close_time=int(k["T"]),
        quote_volume=str(k["q"]),
        n_trades=int(k["n"]),
        taker_buy_volume=str(k["V"]),
        taker_buy_quote_volume=str(k["Q"]),
    )


def parse_frame(raw: str | bytes) -> MarketEvent | None:
    """Convierte un frame en evento tipado. `None` si no interesa o no parsea.

    Acepta el formato combinado (`{"stream": ..., "data": {...}}`) y el crudo
    de un stream único. Un frame malformado NO puede tumbar el loop: se loguea
    y se descarta.
    """
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("ws: frame no-JSON descartado ({} bytes)", len(raw))
        return None
    if not isinstance(msg, dict):
        return None

    data = msg.get("data", msg)
    if not isinstance(data, dict):
        return None

    event = data.get("e")
    try:
        if event == "kline":
            k = data["k"]
            return KlineEvent(
                symbol=str(data["s"]).upper(),
                timeframe=str(k["i"]),
                kline=_kline_from_payload(k),
                is_closed=bool(k.get("x", False)),
                event_time=int(data.get("E", 0)),
            )
        if event == "markPriceUpdate":
            return MarkPriceEvent(
                symbol=str(data["s"]).upper(),
                mark_price=str(data["p"]),
                index_price=str(data.get("i", "0")),
                funding_rate=str(data.get("r", "0")),
                next_funding_time=int(data.get("T", 0)),
                event_time=int(data.get("E", 0)),
            )
        if event == "aggTrade":
            return AggTradeEvent(
                symbol=str(data["s"]).upper(),
                price=str(data["p"]),
                quantity=str(data["q"]),
                is_buyer_maker=bool(data["m"]),
                trade_time=int(data.get("T", 0)),
                event_time=int(data.get("E", 0)),
            )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("ws: evento {} malformado ({}) — descartado", event, exc)
        return None

    return None  # pong, respuestas de suscripción, streams no cableados


def backoff_delay(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = MAX_BACKOFF_S,
    rng: random.Random | None = None,
) -> float:
    """Backoff exponencial con *full jitter*.

    El jitter no es cosmético: sin él, todo lo que se cayó por el mismo corte
    de red vuelve en el mismo milisegundo y Binance lo rechaza en bloque.
    """
    ceiling = min(cap, base * (2.0 ** max(attempt, 0)))
    return (rng or random).uniform(0.0, ceiling)


# --------------------------------------------------------------------- #
# Transporte
# --------------------------------------------------------------------- #


class WebSocketLike(Protocol):
    """Lo mínimo que el loop necesita de una conexión — así el test inyecta
    una falsa sin depender de `websockets` ni de la red."""

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


ConnectFactory = Callable[[str], AbstractAsyncContextManager[WebSocketLike]]
Listener = Callable[["MarketEvent"], Awaitable[None]]


def _default_connect(url: str) -> AbstractAsyncContextManager[WebSocketLike]:
    """Conexión real. El import va adentro para que el módulo se pueda importar
    (y testear el parseo) sin tocar la librería de red."""
    from websockets.asyncio.client import connect

    # El auto-ping de la librería queda activo a propósito: apagarlo es la
    # receta documentada para que Binance corte la conexión en silencio.
    # close_timeout corto: si el server dejó de responder, esperar 10s por el
    # handshake de cierre atrasa el apagado del backend sin ganar nada.
    conn: AbstractAsyncContextManager[WebSocketLike] = connect(
        url, ping_interval=20, ping_timeout=20, close_timeout=5, max_queue=2048
    )
    return conn


def _now_ms() -> int:
    return int(time.time() * 1000)


class BinanceMarketStream:
    """Una conexión multiplexada para toda la watchlist.

    Los consumidores registran listeners async y reciben todos los eventos. Un
    listener que revienta se loguea y NO tumba el stream: perder el feed por un
    bug de un consumidor sería el peor intercambio posible.
    """

    source_name = "binance_ws"

    def __init__(
        self,
        symbols: Sequence[str],
        timeframe: str,
        *,
        base_url: str = FSTREAM_BASE,
        connect: ConnectFactory | None = None,
        klines: bool = True,
        mark_price: bool = True,
        agg_trades: bool = True,
        recv_timeout: float = RECV_TIMEOUT_S,
        connection_ttl: float = CONNECTION_TTL_S,
        rng: random.Random | None = None,
    ) -> None:
        self._streams = stream_names(
            symbols, timeframe, klines=klines, mark_price=mark_price, agg_trades=agg_trades
        )
        self._url = combined_url(self._streams, base_url)
        self._connect = connect or _default_connect
        self._recv_timeout = recv_timeout
        self._connection_ttl = connection_ttl
        self._rng = rng
        self._listeners: list[Listener] = []
        self._status = ConnectionStatus()
        self._stopping = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    # -- API pública ---------------------------------------------------- #

    @property
    def url(self) -> str:
        return self._url

    @property
    def streams(self) -> list[str]:
        return list(self._streams)

    @property
    def status(self) -> ConnectionStatus:
        return self._status

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    async def start(self) -> None:
        """Lanza el loop en background. Idempotente."""
        if self._task is not None and not self._task.done():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self.run(), name="binance-ws")

    async def stop(self) -> None:
        """Pide el corte y espera a que el loop termine."""
        self._stopping.set()
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def run(self) -> None:
        """Loop de conexión + reconexión. Corre hasta que se llame `stop()`."""
        attempt = 0
        logger.info("ws: {} streams — {}", len(self._streams), ", ".join(self._streams[:6]))
        while not self._stopping.is_set():
            try:
                await self._session()
                attempt = 0  # cierre limpio por TTL: reconectar de inmediato
            except asyncio.CancelledError:
                self._mark_disconnected()
                raise
            except Exception as exc:
                self._status.last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("ws: conexión caída ({})", self._status.last_error)
                attempt += 1
            self._mark_disconnected()

            if self._stopping.is_set():
                break
            self._status.reconnects += 1
            delay = max(
                backoff_delay(attempt, rng=self._rng) if attempt else 0.0, RECONNECT_FLOOR_S
            )
            if attempt:
                logger.info("ws: reconectando en {:.1f}s (intento {})", delay, attempt)
            # Dormir esperando el stop: si llega, no se aguanta el backoff
            # completo para apagar el proceso.
            with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=delay)
        logger.info("ws: loop detenido")

    # -- Interno --------------------------------------------------------- #

    async def _session(self) -> None:
        """Una conexión, desde el handshake hasta el corte o el TTL."""
        deadline = time.monotonic() + self._connection_ttl
        async with self._connect(self._url) as ws:
            self._status.connected = True
            self._status.connected_since_ms = _now_ms()
            logger.info("ws: conectado")
            while not self._stopping.is_set():
                if time.monotonic() >= deadline:
                    logger.info("ws: TTL cumplido — reciclando antes del corte de 24h")
                    await ws.close()
                    return
                raw = await asyncio.wait_for(ws.recv(), timeout=self._recv_timeout)
                self._status.messages += 1
                self._status.last_message_ms = _now_ms()
                # Un frame bueno = la conexión sirve: se olvida el error viejo.
                self._status.last_error = None
                event = parse_frame(raw)
                if event is not None:
                    await self._dispatch(event)

    def _mark_disconnected(self) -> None:
        self._status.connected = False
        self._status.connected_since_ms = None

    async def _dispatch(self, event: MarketEvent) -> None:
        for listener in self._listeners:
            try:
                await listener(event)
            except Exception as exc:
                logger.exception("ws: listener falló ({}) — el stream sigue", exc)


# --------------------------------------------------------------------- #
# Hub: stream + persistencia de velas cerradas + último estado en memoria
# --------------------------------------------------------------------- #


@dataclass
class SymbolState:
    """Última foto conocida de un símbolo. La lee el hot path del dashboard;
    nunca dispara I/O."""

    last_kline: Kline | None = None
    last_closed_open_time: int | None = None
    mark_price: str | None = None
    funding_rate: str | None = None
    next_funding_time: int | None = None
    updated_ms: int | None = None
    #: Volumen taker de la barra EN CURSO, separado por lado de la agresión.
    #: Se resetea en cada cierre de vela.
    taker_buy_qty: float = 0.0
    taker_sell_qty: float = 0.0


class MarketSource(Protocol):
    """Lo que el hub necesita de una fuente de datos en vivo.

    Lo implementan el WS (`BinanceMarketStream`) y el polling REST
    (`binance_poll.BinancePollSource`): el hub, el dashboard y el analista
    trabajan igual con cualquiera de las dos.
    """

    source_name: str

    @property
    def status(self) -> ConnectionStatus: ...

    def add_listener(self, listener: Listener) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class MarketDataHub:
    """Fachada de datos en vivo: conecta la fuente, persiste cada vela cerrada
    y mantiene el último estado por símbolo.

    No conoce ni a la API ni al frontend: los consumidores se enganchan con
    `add_listener`. Es lo que mantiene a `data/` como única capa con red.

    `mode` elige la fuente:
      "ws"   — solo WebSocket (lo correcto donde Binance sí entrega el feed)
      "rest" — solo polling REST
      "auto" — arranca con el WS y, si en `ws_probe_s` no llegó ni un frame,
               cae al REST y lo dice. Nace de un hallazgo real: hay redes donde
               el WS de futuros acepta la suscripción y no manda nada nunca
               (ver el docstring de `binance_poll.py`). Un feed mudo sin aviso
               es la peor falla posible en un asistente de trading.
    """

    def __init__(
        self,
        symbols: Sequence[str],
        timeframe: str,
        *,
        persist: bool = True,
        stream: MarketSource | None = None,
        mode: str = "auto",
        ws_probe_s: float = 25.0,
    ) -> None:
        if mode not in ("auto", "ws", "rest"):
            raise ValueError(f"modo de feed desconocido: {mode}")
        self.symbols = [s.upper() for s in symbols]
        self.timeframe = timeframe
        self.persist = persist
        self.mode = mode
        self._ws_probe_s = ws_probe_s
        self._listeners: list[Listener] = []
        self._watchdog: asyncio.Task[None] | None = None
        self.state: dict[str, SymbolState] = {s: SymbolState() for s in self.symbols}
        self.stream: MarketSource = stream or self._build_source(mode)
        self.stream.add_listener(self._on_event)

    def _build_source(self, mode: str) -> MarketSource:
        if mode == "rest":
            from bob.data.binance_poll import BinancePollSource

            return BinancePollSource(self.symbols, self.timeframe)
        return BinanceMarketStream(self.symbols, self.timeframe)

    @property
    def source_name(self) -> str:
        return self.stream.source_name

    def add_listener(self, listener: Listener) -> None:
        """Engancha un consumidor externo (broadcast al dashboard, analista…).

        Los listeners viven en el hub, no en la fuente: si el feed cambia de WS
        a REST en caliente, los consumidores no se enteran ni se pierden nada.
        """
        self._listeners.append(listener)

    async def start(self) -> None:
        await self.stream.start()
        if self.mode == "auto" and isinstance(self.stream, BinanceMarketStream):
            self._watchdog = asyncio.create_task(self._watch_ws(), name="feed-watchdog")

    async def stop(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog
            self._watchdog = None
        await self.stream.stop()

    async def _watch_ws(self) -> None:
        """Cae al REST si el WS conecta pero no entrega."""
        await asyncio.sleep(self._ws_probe_s)
        if self.stream.status.messages > 0:
            return
        logger.warning(
            "feed: el WS no entregó un solo frame en {:.0f}s — se cambia a polling REST "
            "(latencia de segundos en vez de <100ms; el dashboard lo reporta)",
            self._ws_probe_s,
        )
        await self.stream.stop()

        from bob.data.binance_poll import BinancePollSource

        source = BinancePollSource(self.symbols, self.timeframe)
        source.add_listener(self._on_event)
        self.stream = source
        await source.start()

    @property
    def status(self) -> ConnectionStatus:
        return self.stream.status

    async def _on_event(self, event: MarketEvent) -> None:
        await self._update_state(event)
        for listener in self._listeners:
            try:
                await listener(event)
            except Exception as exc:
                logger.exception("hub: listener falló ({}) — el feed sigue", exc)

    async def _update_state(self, event: MarketEvent) -> None:
        st = self.state.setdefault(event.symbol, SymbolState())
        st.updated_ms = _now_ms()

        if isinstance(event, KlineEvent):
            st.last_kline = event.kline
            if event.is_closed:
                st.last_closed_open_time = event.kline.open_time
                st.taker_buy_qty = 0.0
                st.taker_sell_qty = 0.0
                if self.persist:
                    await self._persist(event)
        elif isinstance(event, MarkPriceEvent):
            st.mark_price = event.mark_price
            st.funding_rate = event.funding_rate
            st.next_funding_time = event.next_funding_time
        else:
            qty = float(event.quantity)
            # `m=True` ⇒ el comprador era maker ⇒ el agresor fue el vendedor.
            if event.is_buyer_maker:
                st.taker_sell_qty += qty
            else:
                st.taker_buy_qty += qty

    async def _persist(self, event: KlineEvent) -> None:
        """Escribe la vela cerrada en SQLite, fuera del event loop.

        SQLite es síncrono: hacerlo inline bloquea la recepción de frames justo
        en el instante más caro (cierre de barra de toda la watchlist).
        """
        from bob.data.store import upsert_klines

        try:
            await asyncio.to_thread(upsert_klines, event.symbol, event.timeframe, [event.kline])
        except Exception as exc:
            logger.exception("ws: no se pudo persistir la vela de {} ({})", event.symbol, exc)
