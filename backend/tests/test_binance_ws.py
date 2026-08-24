"""Tests del stream WebSocket de Binance.

Nada de red: el parseo es puro y el loop recibe una fábrica de conexión falsa
que entrega frames desde una lista y puede simular cortes. Así se testea lo que
importa de verdad — que reconecte y que no emita la vela en curso — sin esperar
a que Binance se caiga.
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any

import pytest

from bob.data.binance_ws import (
    MAX_STREAMS_PER_CONNECTION,
    AggTradeEvent,
    BinanceMarketStream,
    KlineEvent,
    MarketDataHub,
    MarkPriceEvent,
    backoff_delay,
    combined_url,
    frame_stream,
    parse_frame,
    stream_kind,
    stream_names,
)

TF = "15m"


def _kline_frame(
    *,
    open_time: int = 1_700_000_000_000,
    close: str = "2500.10",
    closed: bool = True,
    symbol: str = "ETHUSDT",
) -> str:
    """Frame combinado tal como llega de `<sym>@kline_15m`."""
    return json.dumps(
        {
            "stream": f"{symbol.lower()}@kline_{TF}",
            "data": {
                "e": "kline",
                "E": open_time + 900_000,
                "s": symbol,
                "k": {
                    "t": open_time,
                    "T": open_time + 899_999,
                    "s": symbol,
                    "i": TF,
                    "o": "2490.00",
                    "c": close,
                    "h": "2505.00",
                    "l": "2488.00",
                    "v": "1234.5",
                    "n": 4321,
                    "x": closed,
                    "q": "3080000.0",
                    "V": "700.0",
                    "Q": "1750000.0",
                },
            },
        }
    )


def _mark_price_frame(symbol: str = "ETHUSDT") -> str:
    return json.dumps(
        {
            "stream": f"{symbol.lower()}@markPrice@1s",
            "data": {
                "e": "markPriceUpdate",
                "E": 1_700_000_000_500,
                "s": symbol,
                "p": "2500.55",
                "i": "2500.40",
                "r": "0.00010000",
                "T": 1_700_028_800_000,
            },
        }
    )


def _agg_trade_frame(*, qty: str = "1.5", is_buyer_maker: bool = False) -> str:
    return json.dumps(
        {
            "stream": "ethusdt@aggTrade",
            "data": {
                "e": "aggTrade",
                "E": 1_700_000_000_700,
                "s": "ETHUSDT",
                "p": "2500.30",
                "q": qty,
                "m": is_buyer_maker,
                "T": 1_700_000_000_690,
            },
        }
    )


def _trade_frame(
    *,
    qty: str = "1.5",
    price: str = "2500.30",
    is_buyer_maker: bool = False,
    order_type: str = "MARKET",
) -> str:
    """Frame de `<sym>@trade`: el fill crudo, sin agregar.

    Es el stream que sostiene el flujo taker donde `@aggTrade` está mudo.
    """
    return json.dumps(
        {
            "stream": "ethusdt@trade",
            "data": {
                "e": "trade",
                "E": 1_700_000_000_700,
                "T": 1_700_000_000_690,
                "s": "ETHUSDT",
                "t": 8_625_057_383,
                "p": price,
                "q": qty,
                "X": order_type,
                "m": is_buyer_maker,
            },
        }
    )


class FakeSocket:
    """Conexión falsa: entrega frames y después hace lo que le pidan.

    `after` decide qué pasa cuando se acaban los frames — levantar un error
    (corte de red) o quedarse esperando (conexión viva pero silenciosa).
    """

    def __init__(self, frames: list[str], after: str = "raise") -> None:
        self._frames = list(frames)
        self._after = after
        self.closed = False

    async def recv(self) -> str | bytes:
        if self._frames:
            return self._frames.pop(0)
        if self._after == "raise":
            raise ConnectionError("boom: conexión cortada por el server")
        await asyncio.sleep(3600)  # nunca llega: el test lo cancela
        raise AssertionError("unreachable")  # pragma: no cover

    async def close(self) -> None:
        self.closed = True


class FakeConnector:
    """Fábrica de conexiones: una `FakeSocket` por sesión, en orden."""

    def __init__(self, sockets: list[FakeSocket]) -> None:
        self._sockets = list(sockets)
        self.urls: list[str] = []
        self.opened: list[FakeSocket] = []

    def __call__(self, url: str) -> Any:
        self.urls.append(url)
        socket = self._sockets.pop(0) if self._sockets else FakeSocket([], after="hang")
        self.opened.append(socket)
        class _Ctx:
            async def __aenter__(self) -> FakeSocket:
                return socket

            async def __aexit__(self, *exc: object) -> None:
                await socket.close()

        return _Ctx()


class ZeroJitter(random.Random):
    """Jitter apagado: el backoff queda en el piso y el test es determinista."""

    def uniform(self, a: float, b: float) -> float:
        return 0.0


class TestStreamNames:
    def test_baja_los_simbolos_a_minuscula(self) -> None:
        names = stream_names(["ETHUSDT"], TF)
        assert names == [
            "ethusdt@kline_15m",
            "ethusdt@markPrice@1s",
            "ethusdt@aggTrade",
            "ethusdt@trade",
        ]

    def test_pide_aggtrade_y_trade_a_la_vez(self) -> None:
        """Redundancia deliberada: donde Binance calla `@aggTrade`, `@trade`
        sostiene el flujo taker sin que el usuario toque nada."""
        names = stream_names(["ETHUSDT"], TF)
        assert "ethusdt@aggTrade" in names and "ethusdt@trade" in names

    def test_multiplexa_toda_la_watchlist(self) -> None:
        names = stream_names(["ETHUSDT", "BTCUSDT"], TF)
        assert len(names) == 8
        assert any(n.startswith("btcusdt@") for n in names)

    def test_permite_apagar_streams(self) -> None:
        names = stream_names(["ETHUSDT"], TF, mark_price=False, agg_trades=False, trades=False)
        assert names == ["ethusdt@kline_15m"]

    def test_book_ticker_y_depth_estan_apagados_por_defecto(self) -> None:
        """Entregan bien desde esta red, pero son el enganche de Fase 2b: hoy
        no tienen consumidor y `@bookTicker` son ~425 frames/s por símbolo."""
        assert not [n for n in stream_names(["ETHUSDT"], TF) if "bookTicker" in n or "depth" in n]

    def test_engancha_book_ticker_y_depth_cuando_se_piden(self) -> None:
        names = stream_names(["ETHUSDT"], TF, book_ticker=True, depth="depth20@100ms")
        assert "ethusdt@bookTicker" in names
        assert "ethusdt@depth20@100ms" in names

    def test_ignora_entradas_vacias(self) -> None:
        assert stream_names(["", "  "], TF) == []

    def test_rechaza_timeframe_desconocido(self) -> None:
        with pytest.raises(ValueError, match="timeframe"):
            stream_names(["ETHUSDT"], "7m")


class TestStreamKind:
    """La familia es lo que decide qué se rellena por REST: tiene que salir
    igual para cualquier símbolo y cualquier timeframe."""

    @pytest.mark.parametrize(
        ("name", "kind"),
        [
            ("ethusdt@kline_15m", "kline"),
            ("btcusdt@kline_1m", "kline"),
            ("ethusdt@markPrice@1s", "markPrice"),
            ("ethusdt@markPrice", "markPrice"),
            ("ethusdt@aggTrade", "aggTrade"),
            ("ethusdt@trade", "trade"),
            ("ethusdt@depth20@100ms", "depth20"),
            ("ethusdt@bookTicker", "bookTicker"),
        ],
    )
    def test_extrae_la_familia(self, name: str, kind: str) -> None:
        assert stream_kind(name) == kind


class TestFrameStream:
    def test_usa_el_nombre_del_formato_combinado(self) -> None:
        assert frame_stream(json.loads(_kline_frame())) == "ethusdt@kline_15m"

    def test_lo_reconstruye_en_el_formato_crudo(self) -> None:
        """En `/ws/<stream>` no viene el nombre: sin esto, la contabilidad de
        salud quedaría en cero y el hub creería que todo está mudo."""
        raw = json.loads(_mark_price_frame())["data"]
        assert frame_stream(raw) == "ethusdt@markPriceUpdate"

    def test_devuelve_none_si_no_hay_de_donde_sacarlo(self) -> None:
        assert frame_stream({"result": None, "id": 1}) is None


class TestCombinedUrl:
    def test_arma_el_endpoint_combinado(self) -> None:
        url = combined_url(["a@kline_15m", "b@aggTrade"])
        assert url.endswith("/stream?streams=a@kline_15m/b@aggTrade")

    def test_rechaza_lista_vacia(self) -> None:
        with pytest.raises(ValueError, match="no hay streams"):
            combined_url([])

    def test_rechaza_pasarse_del_limite_por_conexion(self) -> None:
        demasiados = [f"s{i}@aggTrade" for i in range(MAX_STREAMS_PER_CONNECTION + 1)]
        with pytest.raises(ValueError, match="multiplexar"):
            combined_url(demasiados)


class TestParseFrame:
    def test_parsea_kline_cerrada(self) -> None:
        event = parse_frame(_kline_frame())
        assert isinstance(event, KlineEvent)
        assert event.is_closed is True
        assert event.symbol == "ETHUSDT"
        assert event.timeframe == TF
        assert event.kline.close == "2500.10"
        assert event.kline.taker_buy_volume == "700.0"
        assert event.kline.n_trades == 4321

    def test_marca_la_vela_en_curso(self) -> None:
        """`k.x == false` es la vela abierta: quien la use hace lookahead."""
        event = parse_frame(_kline_frame(closed=False))
        assert isinstance(event, KlineEvent)
        assert event.is_closed is False

    def test_parsea_mark_price(self) -> None:
        event = parse_frame(_mark_price_frame())
        assert isinstance(event, MarkPriceEvent)
        assert event.mark_price == "2500.55"
        assert event.funding_rate == "0.00010000"
        assert event.next_funding_time == 1_700_028_800_000

    def test_parsea_agg_trade(self) -> None:
        event = parse_frame(_agg_trade_frame(is_buyer_maker=True))
        assert isinstance(event, AggTradeEvent)
        assert event.is_buyer_maker is True
        assert event.quantity == "1.5"

    def test_parsea_trade_crudo(self) -> None:
        event = parse_frame(_trade_frame(qty="0.75", is_buyer_maker=True))
        assert isinstance(event, AggTradeEvent)
        assert event.aggregated is False
        assert event.quantity == "0.75"
        assert event.is_buyer_maker is True

    def test_agg_trade_queda_marcado_como_agregado(self) -> None:
        event = parse_frame(_agg_trade_frame())
        assert isinstance(event, AggTradeEvent)
        assert event.aggregated is True

    def test_descarta_el_relleno_de_precio_cero_de_trade(self) -> None:
        """Binance intercala en `@trade` frames con `p=q=0` y `X="NA"` (~0,6%).
        No son trades: contarlos ensucia el conteo de agresiones."""
        assert parse_frame(_trade_frame(qty="0", price="0", order_type="NA")) is None

    def test_no_filtra_por_el_enum_de_tipo_sino_por_el_tamano(self) -> None:
        """`X` es un enum sin documentar que Binance puede ampliar; que precio
        y cantidad sean positivos sí es una invariante de lo que es un trade."""
        event = parse_frame(_trade_frame(qty="2.0", order_type="LIMIT"))
        assert isinstance(event, AggTradeEvent)
        assert event.quantity == "2.0"

    def test_acepta_formato_crudo_sin_envoltorio(self) -> None:
        raw = json.loads(_mark_price_frame())["data"]
        event = parse_frame(json.dumps(raw))
        assert isinstance(event, MarkPriceEvent)

    def test_descarta_frame_no_json(self) -> None:
        assert parse_frame("no soy json") is None

    def test_descarta_json_que_no_es_objeto(self) -> None:
        assert parse_frame("[1, 2, 3]") is None
        assert parse_frame('{"data": 42}') is None

    def test_descarta_evento_desconocido(self) -> None:
        assert parse_frame('{"e": "forceOrder", "s": "ETHUSDT"}') is None

    def test_descarta_kline_malformada_sin_reventar(self) -> None:
        """Un frame roto no puede tumbar el loop del feed."""
        assert parse_frame('{"e": "kline", "s": "ETHUSDT", "k": {"t": "no-num"}}') is None


class TestBackoff:
    def test_crece_exponencialmente(self) -> None:
        rng = random.Random(0)
        techos = [backoff_delay(i, rng=random.Random(1)) for i in range(4)]
        assert all(d >= 0 for d in techos)
        # Con jitter completo el valor es aleatorio, pero el techo no lo es.
        assert backoff_delay(0, rng=rng) <= 1.0
        assert backoff_delay(3, rng=rng) <= 8.0

    def test_respeta_el_tope(self) -> None:
        assert backoff_delay(50, rng=random.Random(7)) <= 60.0

    def test_tiene_jitter(self) -> None:
        """Sin jitter, todo lo caído por el mismo corte vuelve a la vez."""
        rng = random.Random(3)
        muestras = {backoff_delay(5, rng=rng) for _ in range(20)}
        assert len(muestras) > 1


class TestStreamLoop:
    async def test_despacha_eventos_al_listener(self) -> None:
        connector = FakeConnector([FakeSocket([_kline_frame(), _mark_price_frame()])])
        stream = BinanceMarketStream(
            ["ETHUSDT"], TF, connect=connector, connection_ttl=1e9, recv_timeout=1.0
        )
        recibidos: list[object] = []

        async def listener(event: object) -> None:
            recibidos.append(event)
            if len(recibidos) == 2:
                await stream.stop()

        stream.add_listener(listener)
        await stream.start()
        await asyncio.sleep(0.05)
        await stream.stop()

        assert [type(e).__name__ for e in recibidos] == ["KlineEvent", "MarkPriceEvent"]
        assert stream.status.messages == 2

    async def test_reconecta_tras_un_corte(self) -> None:
        """El corte a las 24h de Binance es el caso normal, no la excepción."""
        connector = FakeConnector(
            [
                FakeSocket([_kline_frame()], after="raise"),
                FakeSocket([_kline_frame(open_time=1_700_000_900_000)], after="hang"),
            ]
        )
        stream = BinanceMarketStream(
            ["ETHUSDT"],
            TF,
            connect=connector,
            connection_ttl=1e9,
            recv_timeout=1.0,
            rng=ZeroJitter(),
        )
        vistos: list[object] = []
        stream.add_listener(lambda e: _collect(vistos, e))

        await stream.start()
        for _ in range(60):
            await asyncio.sleep(0.02)
            if len(vistos) == 2:
                break
        await stream.stop()

        assert len(connector.urls) == 2, "no reconectó"
        assert stream.status.reconnects >= 1
        assert len(vistos) == 2

    async def test_el_ttl_recicla_la_conexion_sin_esperar_backoff(self) -> None:
        connector = FakeConnector([FakeSocket([], after="hang"), FakeSocket([], after="hang")])
        stream = BinanceMarketStream(
            ["ETHUSDT"], TF, connect=connector, connection_ttl=0.0, recv_timeout=1.0
        )
        await stream.start()
        await asyncio.sleep(0.35)
        await stream.stop()

        assert len(connector.urls) >= 2
        assert connector.opened[0].closed is True

    async def test_un_listener_roto_no_tumba_el_stream(self) -> None:
        connector = FakeConnector([FakeSocket([_kline_frame(), _mark_price_frame()], after="hang")])
        stream = BinanceMarketStream(
            ["ETHUSDT"], TF, connect=connector, connection_ttl=1e9, recv_timeout=1.0
        )
        buenos: list[object] = []

        async def roto(event: object) -> None:
            raise RuntimeError("el consumidor tiene un bug")

        stream.add_listener(roto)
        stream.add_listener(lambda e: _collect(buenos, e))

        await stream.start()
        await asyncio.sleep(0.05)
        await stream.stop()

        assert len(buenos) == 2
        assert stream.status.messages == 2

    async def test_stop_es_seguro_sin_haber_arrancado(self) -> None:
        stream = BinanceMarketStream(["ETHUSDT"], TF, connect=FakeConnector([]))
        await stream.stop()  # no debe reventar

    async def test_start_es_idempotente(self) -> None:
        connector = FakeConnector([FakeSocket([], after="hang")])
        stream = BinanceMarketStream(["ETHUSDT"], TF, connect=connector, connection_ttl=1e9)
        await stream.start()
        await stream.start()
        await asyncio.sleep(0.02)
        await stream.stop()
        assert len(connector.urls) == 1

    async def test_el_silencio_prolongado_cuenta_como_caida(self) -> None:
        """Sin frames no hay feed: mejor reconectar que mostrar precio viejo."""
        connector = FakeConnector([FakeSocket([], after="hang"), FakeSocket([], after="hang")])
        stream = BinanceMarketStream(
            ["ETHUSDT"],
            TF,
            connect=connector,
            connection_ttl=1e9,
            recv_timeout=0.01,
            rng=ZeroJitter(),
        )
        await stream.start()
        await asyncio.sleep(0.35)
        await stream.stop()

        assert len(connector.urls) >= 2
        assert stream.status.last_error is not None


async def _collect(sink: list[object], event: object) -> None:
    sink.append(event)


class TestMarketDataHub:
    async def test_solo_persiste_velas_cerradas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        escritas: list[tuple[str, str, int]] = []

        def fake_upsert(symbol: str, timeframe: str, klines: list[Any]) -> int:
            escritas.extend((symbol, timeframe, k.open_time) for k in klines)
            return len(klines)

        monkeypatch.setattr("bob.data.store.upsert_klines", fake_upsert)

        hub = MarketDataHub(["ETHUSDT"], TF)
        abierta = parse_frame(_kline_frame(closed=False))
        cerrada = parse_frame(_kline_frame(closed=True))
        assert abierta is not None and cerrada is not None
        await hub._on_event(abierta)
        await hub._on_event(cerrada)

        assert escritas == [("ETHUSDT", TF, 1_700_000_000_000)]

    async def test_separa_el_volumen_taker_por_lado(self) -> None:
        hub = MarketDataHub(["ETHUSDT"], TF, persist=False)
        compra = parse_frame(_agg_trade_frame(qty="2.0", is_buyer_maker=False))
        venta = parse_frame(_agg_trade_frame(qty="0.5", is_buyer_maker=True))
        assert compra is not None and venta is not None
        await hub._on_event(compra)
        await hub._on_event(venta)

        state = hub.state["ETHUSDT"]
        assert state.taker_buy_qty == pytest.approx(2.0)
        assert state.taker_sell_qty == pytest.approx(0.5)

    async def test_el_cierre_de_vela_resetea_el_acumulado_taker(self) -> None:
        hub = MarketDataHub(["ETHUSDT"], TF, persist=False)
        trade = parse_frame(_agg_trade_frame(qty="3.0"))
        cerrada = parse_frame(_kline_frame(closed=True))
        assert trade is not None and cerrada is not None
        await hub._on_event(trade)
        await hub._on_event(cerrada)

        state = hub.state["ETHUSDT"]
        assert state.taker_buy_qty == 0.0
        assert state.last_closed_open_time == 1_700_000_000_000

    async def test_guarda_mark_price_y_funding(self) -> None:
        hub = MarketDataHub(["ETHUSDT"], TF, persist=False)
        event = parse_frame(_mark_price_frame())
        assert event is not None
        await hub._on_event(event)

        state = hub.state["ETHUSDT"]
        assert state.mark_price == "2500.55"
        assert state.funding_rate == "0.00010000"

    async def test_acepta_simbolos_fuera_de_la_watchlist_inicial(self) -> None:
        hub = MarketDataHub(["ETHUSDT"], TF, persist=False)
        event = parse_frame(_mark_price_frame(symbol="BTCUSDT"))
        assert event is not None
        await hub._on_event(event)
        assert "BTCUSDT" in hub.state

    async def test_status_expone_el_estado_para_el_dashboard(self) -> None:
        hub = MarketDataHub(["ETHUSDT"], TF, persist=False)
        assert hub.status.as_dict()["connected"] is False
