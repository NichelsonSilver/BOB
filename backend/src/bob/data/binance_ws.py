"""Stream WebSocket de Binance USDⓈ-M Futures — el feed en vivo del asistente.

Junto a `binance_rest.py`, el único lugar con I/O de mercado (regla 3 de
CLAUDE.md). Market data público: sin API key, sin credenciales, sin órdenes.

Tres decisiones gobiernan el diseño:

1. **La reconexión es el caso normal, no el edge case.** Binance corta cada
   conexión a las 24h por diseño (docs/DATA_SOURCES.md). El loop reconecta
   con backoff exponencial + jitter y resuscribe todo; el contador de
   intentos se resetea en cuanto llega el primer frame bueno.
2. **La vela en curso no es una vela.** Solo se persiste una kline con
   `k.x == true`. Computar features sobre la vela abierta es lookahead en
   producción que el backtest jamás vería (regla 5).
3. **Un stream mudo no es una conexión caída.** Medición de campo del
   2026-08-24 (evidencia en docs/DATA_SOURCES.md): desde la red del usuario,
   `fstream.binance.com` entrega `@trade`, `@bookTicker` y `@depth*` con
   normalidad, y calla `@aggTrade`, `@kline_*`, `@markPrice`, `@ticker`,
   `@miniTicker` y `@forceOrder` — todo sobre la MISMA conexión TLS, así que
   no es la red: es Binance a nivel de aplicación. Por eso la salud se lleva
   **por stream** y no por socket, y lo que falta se rellena por REST.

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
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from loguru import logger

from bob.data.binance_rest import INTERVAL_MS, Kline

FSTREAM_BASE: Final = "wss://fstream.binance.com/stream"

#: Binance corta la conexión a las 24h. Reciclamos antes, en un momento
#: elegido por nosotros, para que el corte no caiga justo en un cierre de vela.
CONNECTION_TTL_S: Final = 23 * 3600.0

#: Si no llega ningún frame en este lapso, la conexión se da por muerta. Con
#: `@trade` activo (decenas de frames por segundo en ETHUSDT) 60s de silencio
#: es anomalía, no calma. Ojo: mide el socket completo, no cada stream — un
#: stream mudo con otros vivos NO se detecta acá sino en `mute_streams()`.
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
    """Un trade taker. `is_buyer_maker=True` ⇒ el agresor fue el vendedor: así
    se separa el volumen taker comprador del vendedor.

    `aggregated` dice de qué stream vino: `@aggTrade` (True, varios fills de
    una misma orden agresora colapsados en uno) o `@trade` (False, fill por
    fill). Para volumen y delta ambos suman idéntico — verificado contra
    `/fapi/v1/aggTrades` sobre la misma ventana, diferencia 0.000% — pero el
    hub necesita el flag para no contar dos veces si los dos streams reviven
    a la vez.
    """

    symbol: str
    price: str
    quantity: str
    is_buyer_maker: bool
    trade_time: int
    event_time: int
    aggregated: bool = True


MarketEvent = KlineEvent | MarkPriceEvent | AggTradeEvent


@dataclass
class ConnectionStatus:
    """Estado de la conexión, para el evento `conn.status` del dashboard.

    `subscribed` + `stream_messages` son lo que permite distinguir "el socket
    está caído" de "el socket está vivo pero este stream no llega nunca".
    Sin esa distinción el dashboard mostraría un feed en verde mientras el
    precio que exhibe es de hace media hora.
    """

    connected: bool = False
    connected_since_ms: int | None = None
    last_message_ms: int | None = None
    reconnects: int = 0
    messages: int = 0
    last_error: str | None = None
    #: Streams pedidos en el handshake.
    subscribed: list[str] = field(default_factory=list)
    #: Frames recibidos por stream, acumulado entre reconexiones.
    stream_messages: dict[str, int] = field(default_factory=dict)

    @property
    def mute_streams(self) -> list[str]:
        """Suscritos que no entregaron ni un frame. Solo es concluyente si la
        conexión ya lleva un rato arriba — de ahí el `ws_probe_s` del hub."""
        return [name for name in self.subscribed if not self.stream_messages.get(name)]

    def record_stream(self, name: str) -> None:
        self.stream_messages[name] = self.stream_messages.get(name, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "connected_since_ms": self.connected_since_ms,
            "last_message_ms": self.last_message_ms,
            "reconnects": self.reconnects,
            "messages": self.messages,
            "last_error": self.last_error,
            "subscribed": list(self.subscribed),
            "stream_messages": dict(self.stream_messages),
            "mute_streams": self.mute_streams,
        }


# --------------------------------------------------------------------- #
# Armado de la URL y parseo (puros)
# --------------------------------------------------------------------- #


def stream_kind(name: str) -> str:
    """Familia de un stream (`ethusdt@kline_15m` → `kline`).

    El hub decide qué rellenar por REST a partir de la familia, no del nombre
    completo: así da igual el símbolo y el timeframe.
    """
    tail = name.split("@", 1)[1] if "@" in name else name
    return tail.split("@", 1)[0].split("_", 1)[0]


def stream_names(
    symbols: Iterable[str],
    timeframe: str,
    *,
    klines: bool = True,
    mark_price: bool = True,
    agg_trades: bool = True,
    trades: bool = True,
    book_ticker: bool = False,
    depth: str | None = None,
) -> list[str]:
    """Nombres de stream combinados para la watchlist.

    Los símbolos van en minúscula: con mayúscula Binance acepta el handshake
    y después no manda nada, así que el bug parece de red y no lo es.

    Se piden `@aggTrade` **y** `@trade` a propósito. Son redundantes por
    diseño: donde Binance entrega los dos, el hub se queda con el agregado y
    descarta el crudo; donde `@aggTrade` está mudo (ver el docstring del
    módulo), `@trade` sostiene el flujo taker sin pedirle nada al usuario. Un
    stream de sobra cuesta un nombre en la URL; quedarse sin flujo cuesta el
    KPI de microestructura entero.

    `book_ticker` y `depth` (p. ej. `depth="depth20@100ms"`) quedan apagados:
    entregan bien desde esta red y son el enganche de Fase 2b, pero hoy no
    tienen consumidor y `@bookTicker` solo son ~425 frames/s por símbolo que
    nadie lee.
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
        if trades:
            names.append(f"{sym}@trade")
        if book_ticker:
            names.append(f"{sym}@bookTicker")
        if depth:
            names.append(f"{sym}@{depth}")
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


#: Binance intercala en `@trade` frames de relleno con `p=q=0` y `X="NA"`
#: (~0,6% de los frames). No son trades: sumarlos no cambia el volumen pero
#: sí ensucia el conteo. Se filtran por tamaño, no por `X`: que el precio y la
#: cantidad sean positivos es una invariante de lo que es un trade, mientras
#: que `X` es un enum sin documentar que Binance puede ampliar cuando quiera.
#: Medido: los dos filtros seleccionan exactamente el mismo conjunto y su
#: volumen coincide al decimal con `/fapi/v1/aggTrades`.


def load_frame(raw: str | bytes) -> dict[str, Any] | None:
    """`json.loads` tolerante. Un frame malformado NO puede tumbar el loop."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("ws: frame no-JSON descartado ({} bytes)", len(raw))
        return None
    return msg if isinstance(msg, dict) else None


def frame_stream(msg: dict[str, Any]) -> str | None:
    """Nombre del stream que originó el frame, para la contabilidad de salud.

    El endpoint combinado lo trae en `stream`. En el crudo (un solo stream por
    conexión) no viene, así que se reconstruye desde el tipo de evento — basta
    para no perder la cuenta cuando alguien conecta a `/ws/<stream>`.
    """
    name = msg.get("stream")
    if isinstance(name, str) and name:
        return name
    data = msg.get("data", msg)
    if not isinstance(data, dict):
        return None
    event, symbol = data.get("e"), data.get("s")
    if isinstance(event, str) and isinstance(symbol, str):
        return f"{symbol.lower()}@{event}"
    return None


def event_from_message(msg: dict[str, Any]) -> MarketEvent | None:
    """Mapea un frame ya parseado a evento tipado. `None` si no interesa."""
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
                aggregated=True,
            )
        if event == "trade":
            price, qty = str(data["p"]), str(data["q"])
            if float(price) <= 0.0 or float(qty) <= 0.0:
                return None  # frame de relleno, ver la nota sobre `X="NA"`
            return AggTradeEvent(
                symbol=str(data["s"]).upper(),
                price=price,
                quantity=qty,
                is_buyer_maker=bool(data["m"]),
                trade_time=int(data.get("T", 0)),
                event_time=int(data.get("E", 0)),
                aggregated=False,
            )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("ws: evento {} malformado ({}) — descartado", event, exc)
        return None

    return None  # pong, respuestas de suscripción, streams no cableados


def parse_frame(raw: str | bytes) -> MarketEvent | None:
    """Convierte un frame crudo en evento tipado. `None` si no interesa.

    Acepta el formato combinado (`{"stream": ..., "data": {...}}`) y el crudo
    de un stream único. El loop de recepción no la usa —parsea una sola vez
    con `load_frame` para poder contabilizar el stream—, pero es la puerta
    limpia para testear el mapeo sin red.
    """
    msg = load_frame(raw)
    return None if msg is None else event_from_message(msg)


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
        trades: bool = True,
        book_ticker: bool = False,
        depth: str | None = None,
        recv_timeout: float = RECV_TIMEOUT_S,
        connection_ttl: float = CONNECTION_TTL_S,
        rng: random.Random | None = None,
    ) -> None:
        self._streams = stream_names(
            symbols,
            timeframe,
            klines=klines,
            mark_price=mark_price,
            agg_trades=agg_trades,
            trades=trades,
            book_ticker=book_ticker,
            depth=depth,
        )
        self._url = combined_url(self._streams, base_url)
        self._connect = connect or _default_connect
        self._recv_timeout = recv_timeout
        self._connection_ttl = connection_ttl
        self._rng = rng
        self._listeners: list[Listener] = []
        self._status = ConnectionStatus(subscribed=list(self._streams))
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

    def mute_streams(self) -> list[str]:
        """Streams suscritos que nunca entregaron un frame.

        Solo tiene sentido consultarlo después de un rato de conexión: antes,
        todo está mudo simplemente porque no ha pasado nada.
        """
        return self._status.mute_streams

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
                msg = load_frame(raw)
                if msg is None:
                    continue
                name = frame_stream(msg)
                if name is not None:
                    self._status.record_stream(name)
                event = event_from_message(msg)
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
      "auto" — arranca con el WS y, pasado `ws_probe_s`, mira **stream por
               stream** qué llegó:
                 · nada de nada           → cae entero al polling REST
                 · faltan kline/markPrice → los rellena por REST y deja el WS
                   sirviendo lo que sí entrega (el flujo taker, que es lo que
                   de verdad necesita latencia)
                 · todo llega             → no hace nada
               Nace de un hallazgo real: Binance calla ciertos streams por
               IP/PoP mientras entrega otros sobre el mismo socket (ver el
               docstring del módulo). Un feed mudo sin aviso es la peor falla
               posible en un asistente de trading; uno que se degrada a medias
               y lo declara es aceptable.

    Si los streams estándar reviven —el hallazgo parece un servicio caído del
    lado de Binance, no una política— basta reiniciar el backend para volver al
    camino de baja latencia: `stream_names` los sigue pidiendo siempre.
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
        fill_interval_s: float = 3.0,
    ) -> None:
        if mode not in ("auto", "ws", "rest"):
            raise ValueError(f"modo de feed desconocido: {mode}")
        self.symbols = [s.upper() for s in symbols]
        self.timeframe = timeframe
        self.persist = persist
        self.mode = mode
        self._ws_probe_s = ws_probe_s
        self._fill_interval_s = fill_interval_s
        self._listeners: list[Listener] = []
        self._watchdog: asyncio.Task[None] | None = None
        #: Fuente REST que cubre los streams mudos, en paralelo al WS.
        self._fill: MarketSource | None = None
        self._fill_label: str = ""
        #: ¿Llegó alguna vez un `@aggTrade`? Si sí, los `@trade` crudos se
        #: descartan: sumar los dos contaría cada fill dos veces.
        self._agg_seen = False
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
        """Qué está alimentando al hub. Viaja tal cual al `conn.status` del
        dashboard, así que describe el híbrido y no solo la fuente principal:
        `binance_ws+rest_fill(kline,markPrice)` es información operativa, no un
        detalle interno."""
        if self._fill is None:
            return self.stream.source_name
        return f"{self.stream.source_name}+{self._fill_label}"

    def add_listener(self, listener: Listener) -> None:
        """Engancha un consumidor externo (broadcast al dashboard, analista…).

        Los listeners viven en el hub, no en la fuente: si el feed cambia de WS
        a REST en caliente, los consumidores no se enteran ni se pierden nada.
        """
        self._listeners.append(listener)

    async def start(self) -> None:
        await self.stream.start()
        if self.mode == "auto" and isinstance(self.stream, BinanceMarketStream):
            self._watchdog = asyncio.create_task(self._watch_streams(), name="feed-watchdog")

    async def stop(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog
            self._watchdog = None
        if self._fill is not None:
            await self._fill.stop()
            self._fill = None
        await self.stream.stop()

    async def _watch_streams(self) -> None:
        """Diagnostica el WS pasado el periodo de prueba y decide qué hacer.

        Corre una sola vez. Si el WS se cae después, de eso se encarga la
        reconexión; y si un stream mudo revive, el conteo de salud lo refleja
        aunque el relleno ya esté puesto — el relleno de más no corrompe nada,
        solo gasta peso de REST.
        """
        await asyncio.sleep(self._ws_probe_s)
        stream = self.stream
        if not isinstance(stream, BinanceMarketStream):
            return

        if stream.status.messages == 0:
            await self._fallback_to_rest()
            return

        mute = stream.mute_streams()
        if mute:
            await self._fill_mute_streams(mute)

    async def _fallback_to_rest(self) -> None:
        """El WS conecta y no entrega NADA: se reemplaza entero."""
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

    async def _fill_mute_streams(self, mute: Sequence[str]) -> None:
        """El WS entrega algunos streams y calla otros: se rellena por REST.

        Solo se rellena lo que el REST sabe dar (velas y mark price/funding).
        Si lo mudo es el flujo taker no hay relleno posible a esta cadencia
        —el volumen taker por barra igual viaja dentro de la vela cerrada— y lo
        único honesto es decirlo fuerte.
        """
        kinds = {stream_kind(name) for name in mute}
        need_klines = "kline" in kinds
        need_mark = "markPrice" in kinds
        logger.warning(
            "feed: el WS está mudo en {} — {}",
            ", ".join(sorted(mute)),
            "se rellena por REST" if (need_klines or need_mark) else "sin relleno posible",
        )
        if kinds >= {"aggTrade", "trade"}:
            logger.error(
                "feed: sin flujo taker en vivo (aggTrade y trade mudos) — la "
                "microestructura intrabarra queda ciega; solo queda el volumen "
                "taker que trae cada vela cerrada"
            )
        if not (need_klines or need_mark):
            return

        from bob.data.binance_poll import BinancePollSource

        filled = sorted(k for k in ("kline", "markPrice") if k in kinds)
        fill = BinancePollSource(
            self.symbols,
            self.timeframe,
            klines=need_klines,
            mark_price=need_mark,
            interval_s=self._fill_interval_s,
        )
        fill.add_listener(self._on_event)
        self._fill = fill
        self._fill_label = "rest_fill(" + ",".join(filled) + ")"
        await fill.start()
        logger.info("feed: relleno REST activo para {}", ", ".join(filled))

    @property
    def status(self) -> ConnectionStatus:
        return self.stream.status

    async def _on_event(self, event: MarketEvent) -> None:
        if isinstance(event, AggTradeEvent):
            if event.aggregated:
                self._agg_seen = True
            elif self._agg_seen:
                # `@aggTrade` está vivo: el `@trade` crudo es exactamente el
                # mismo flujo contado fill por fill. Dejar pasar los dos
                # duplicaría el volumen taker y con él todo lo que dependa del
                # delta comprador/vendedor.
                return
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
