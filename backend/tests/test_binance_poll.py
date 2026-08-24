"""Tests de la fuente REST en vivo y de la degradación por stream.

Existe porque desde la red del usuario `fstream.binance.com` entrega unos
streams y calla otros sobre la misma conexión (ver `data/binance_poll.py`). Lo
que se protege acá: que la misma vela cerrada no se emita dos veces, que el hub
distinga "socket caído" de "stream mudo", que rellene por REST **solo** lo que
el WS no da, y que no cuente el flujo taker dos veces cuando los dos streams de
trades están vivos a la vez.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from bob.data.binance_poll import BinancePollSource
from bob.data.binance_rest import INTERVAL_MS, BinanceRestClient
from bob.data.binance_ws import (
    AggTradeEvent,
    BinanceMarketStream,
    ConnectionStatus,
    KlineEvent,
    MarketDataHub,
    MarkPriceEvent,
)

from .test_binance_ws import (
    FakeConnector,
    FakeSocket,
    _agg_trade_frame,
    _kline_frame,
    _mark_price_frame,
    _trade_frame,
)

TF = "15m"
STEP = INTERVAL_MS[TF]
T0 = 1_700_000_000_000


def _kline_row(open_time: int, close: float = 2500.0) -> list[Any]:
    return [
        open_time,
        f"{close:.2f}",
        f"{close + 5:.2f}",
        f"{close - 5:.2f}",
        f"{close:.2f}",
        "10.5",
        open_time + STEP - 1,
        "26250.0",
        42,
        "6.0",
        "15000.0",
        "0",
    ]


def _premium() -> dict[str, Any]:
    return {
        "symbol": "ETHUSDT",
        "markPrice": "2501.10",
        "indexPrice": "2500.90",
        "lastFundingRate": "0.00010000",
        "nextFundingTime": T0 + 8 * 3_600_000,
        "time": T0 + 1000,
    }


class Backend:
    """Binance simulado: se le mueve la última vela a voluntad."""

    def __init__(self) -> None:
        self.last_closed = T0
        self.fail = False
        self.calls = 0

    def handler(self, request: Request) -> Response:
        self.calls += 1
        if self.fail:
            return Response(500, text="boom")
        if "klines" in request.url.path:
            return Response(
                200,
                json=[_kline_row(self.last_closed), _kline_row(self.last_closed + STEP)],
            )
        return Response(200, json=_premium())


def _source(backend: Backend, **kwargs: Any) -> tuple[BinancePollSource, list[Any]]:
    http = AsyncClient(
        base_url="https://fapi.binance.com", transport=MockTransport(backend.handler)
    )
    client = BinanceRestClient(client=http, max_retries=1)
    source = BinancePollSource(["ETHUSDT"], TF, client=client, **kwargs)
    recibidos: list[Any] = []

    async def listener(event: Any) -> None:
        recibidos.append(event)

    source.add_listener(listener)
    return source, recibidos


class TestPollSource:
    async def test_emite_la_vela_cerrada_y_la_en_curso(self) -> None:
        source, recibidos = _source(Backend())
        await source._poll_symbol("ETHUSDT")

        klines = [e for e in recibidos if isinstance(e, KlineEvent)]
        assert [k.is_closed for k in klines] == [True, False]
        assert klines[0].kline.open_time == T0
        assert klines[1].kline.open_time == T0 + STEP

    async def test_emite_mark_price_y_funding(self) -> None:
        source, recibidos = _source(Backend())
        await source._poll_symbol("ETHUSDT")

        mark = next(e for e in recibidos if isinstance(e, MarkPriceEvent))
        assert mark.mark_price == "2501.10"
        assert mark.funding_rate == "0.00010000"

    async def test_no_repite_la_misma_vela_cerrada(self) -> None:
        """El polling ve el mismo cierre muchas veces: emitirlo dos veces
        duplicaría la vela en DB y en el gráfico."""
        source, recibidos = _source(Backend())
        await source._poll_symbol("ETHUSDT")
        await source._poll_symbol("ETHUSDT")

        cerradas = [e for e in recibidos if isinstance(e, KlineEvent) and e.is_closed]
        assert len(cerradas) == 1

    async def test_emite_el_cierre_nuevo_cuando_avanza_la_barra(self) -> None:
        backend = Backend()
        source, recibidos = _source(backend)
        await source._poll_symbol("ETHUSDT")
        backend.last_closed += STEP
        await source._poll_symbol("ETHUSDT")

        cerradas = [e for e in recibidos if isinstance(e, KlineEvent) and e.is_closed]
        assert [k.kline.open_time for k in cerradas] == [T0, T0 + STEP]

    async def test_un_error_de_red_no_mata_la_fuente(self) -> None:
        backend = Backend()
        backend.fail = True
        source, recibidos = _source(backend)
        await source._poll_symbol("ETHUSDT")

        assert recibidos == []
        assert source.status.connected is False
        assert source.status.last_error is not None

        backend.fail = False
        await source._poll_symbol("ETHUSDT")
        assert source.status.connected is True
        assert source.status.last_error is None

    async def test_el_loop_arranca_y_para(self) -> None:
        source, recibidos = _source(Backend(), interval_s=0.01)
        await source.start()
        await source.start()  # idempotente
        await asyncio.sleep(0.4)  # el limiter espacia los requests ~50ms
        await source.stop()

        assert recibidos, "no emitió nada"
        assert source.status.messages >= 1

    async def test_stop_sin_start_no_revienta(self) -> None:
        source, _ = _source(Backend())
        await source.stop()


class FakePoll:
    """`BinancePollSource` de mentira: registra con qué la construyeron.

    Lo que interesa afirmar es que el hub pide **solo** lo que falta: un
    relleno que además trajera lo que el WS ya entrega haría que el dashboard
    alternara entre un precio fresco y uno de hace segundos.
    """

    source_name = "binance_rest_poll"
    creados: list[FakePoll] = []

    def __init__(self, symbols: Any, timeframe: str, **kwargs: Any) -> None:
        self.symbols = symbols
        self.timeframe = timeframe
        self.kwargs = kwargs
        self.listeners: list[Any] = []
        self.started = False
        self.status_obj = ConnectionStatus()
        FakePoll.creados.append(self)

    @property
    def status(self) -> Any:
        return self.status_obj

    def add_listener(self, listener: Any) -> None:
        self.listeners.append(listener)

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False


@pytest.fixture
def sin_red(monkeypatch: pytest.MonkeyPatch) -> type[FakePoll]:
    """Ningún test de este bloque puede salir a Binance de verdad."""
    FakePoll.creados = []
    monkeypatch.setattr("bob.data.binance_poll.BinancePollSource", FakePoll)
    return FakePoll


def _ws(frames: list[str]) -> BinanceMarketStream:
    return BinanceMarketStream(
        ["ETHUSDT"],
        TF,
        connect=FakeConnector([FakeSocket(frames, after="hang")]),
        connection_ttl=1e9,
        recv_timeout=5.0,
    )


class TestSocketMudo:
    """El WS conecta y no entrega NADA: se reemplaza la fuente entera."""

    async def test_cambia_a_rest_cuando_el_ws_no_entrega(
        self, sin_red: type[FakePoll]
    ) -> None:
        hub = MarketDataHub(
            ["ETHUSDT"], TF, persist=False, stream=_ws([]), mode="auto", ws_probe_s=0.05
        )
        vistos: list[Any] = []

        async def consumidor(event: Any) -> None:
            vistos.append(event)

        hub.add_listener(consumidor)
        await hub.start()
        await asyncio.sleep(0.2)

        assert sin_red.creados, "no cayó a REST"
        assert hub.source_name == "binance_rest_poll"
        assert sin_red.creados[0].started is True

        # El consumidor sigue enganchado después del cambio de fuente.
        await sin_red.creados[0].listeners[0](
            KlineEvent(
                symbol="ETHUSDT", timeframe=TF, kline=_fake_kline(), is_closed=True, event_time=1
            )
        )
        assert len(vistos) == 1
        await hub.stop()


class TestStreamsMudos:
    """El caso real medido el 2026-08-24: el socket entrega `@trade` y calla
    `@kline`, `@markPrice` y `@aggTrade`."""

    async def test_rellena_por_rest_solo_lo_que_falta(self, sin_red: type[FakePoll]) -> None:
        hub = MarketDataHub(
            ["ETHUSDT"],
            TF,
            persist=False,
            stream=_ws([_trade_frame()]),
            mode="auto",
            ws_probe_s=0.05,
        )
        await hub.start()
        await asyncio.sleep(0.2)

        assert len(sin_red.creados) == 1
        assert sin_red.creados[0].kwargs["klines"] is True
        assert sin_red.creados[0].kwargs["mark_price"] is True
        # El WS NO se apaga: sigue sirviendo el flujo taker a <100ms.
        assert isinstance(hub.stream, BinanceMarketStream)
        assert hub.source_name == "binance_ws+rest_fill(kline,markPrice)"
        await hub.stop()

    async def test_no_rellena_si_todos_los_streams_entregan(
        self, sin_red: type[FakePoll]
    ) -> None:
        hub = MarketDataHub(
            ["ETHUSDT"],
            TF,
            persist=False,
            stream=_ws([_kline_frame(), _mark_price_frame(), _agg_trade_frame(), _trade_frame()]),
            mode="auto",
            ws_probe_s=0.05,
        )
        await hub.start()
        await asyncio.sleep(0.2)

        assert sin_red.creados == []
        assert hub.source_name == "binance_ws"
        await hub.stop()

    async def test_no_inventa_relleno_para_lo_que_el_rest_no_da(
        self, sin_red: type[FakePoll]
    ) -> None:
        """Sin flujo taker en vivo no hay relleno posible a esta cadencia: se
        reporta y se sigue con el volumen taker que trae la vela cerrada."""
        hub = MarketDataHub(
            ["ETHUSDT"],
            TF,
            persist=False,
            stream=_ws([_kline_frame(), _mark_price_frame()]),
            mode="auto",
            ws_probe_s=0.05,
        )
        await hub.start()
        await asyncio.sleep(0.2)

        assert sin_red.creados == []
        assert hub.source_name == "binance_ws"
        await hub.stop()

    async def test_reporta_los_mudos_en_el_estado_de_conexion(self) -> None:
        """Regla 8: el dashboard tiene que poder decir qué stream no llega."""
        stream = _ws([_trade_frame()])
        await stream.start()
        await asyncio.sleep(0.1)

        estado = stream.status.as_dict()
        assert estado["stream_messages"] == {"ethusdt@trade": 1}
        assert set(estado["mute_streams"]) == {
            "ethusdt@kline_15m",
            "ethusdt@markPrice@1s",
            "ethusdt@aggTrade",
        }
        await stream.stop()


class TestDedupDeFlujoTaker:
    """`@aggTrade` y `@trade` son el mismo flujo: uno agregado por orden
    agresora y el otro fill por fill. Sumarlos duplicaría el volumen."""

    async def test_descarta_el_crudo_cuando_el_agregado_esta_vivo(self) -> None:
        hub = MarketDataHub(["ETHUSDT"], TF, persist=False, stream=_ws([]), mode="ws")

        await hub._on_event(_trade("2.0", aggregated=True))
        await hub._on_event(_trade("2.0", aggregated=False))

        assert hub.state["ETHUSDT"].taker_buy_qty == 2.0

    async def test_cuenta_el_crudo_mientras_el_agregado_no_aparezca(self) -> None:
        hub = MarketDataHub(["ETHUSDT"], TF, persist=False, stream=_ws([]), mode="ws")

        await hub._on_event(_trade("2.0", aggregated=False))
        await hub._on_event(_trade("1.0", aggregated=False))

        assert hub.state["ETHUSDT"].taker_buy_qty == 3.0


def _trade(qty: str, *, aggregated: bool) -> AggTradeEvent:
    return AggTradeEvent(
        symbol="ETHUSDT",
        price="2500.0",
        quantity=qty,
        is_buyer_maker=False,
        trade_time=1,
        event_time=1,
        aggregated=aggregated,
    )


class TestRellenoSelectivo:
    async def test_puede_traer_solo_el_mark_price(self) -> None:
        source, recibidos = _source(Backend(), klines=False)
        await source._poll_symbol("ETHUSDT")

        assert all(isinstance(e, MarkPriceEvent) for e in recibidos)
        assert recibidos, "no emitió el mark price"

    async def test_puede_traer_solo_las_velas(self) -> None:
        source, recibidos = _source(Backend(), mark_price=False)
        await source._poll_symbol("ETHUSDT")

        assert all(isinstance(e, KlineEvent) for e in recibidos)
        assert recibidos, "no emitió velas"

    async def test_rechaza_una_fuente_que_no_emitiria_nada(self) -> None:
        with pytest.raises(ValueError, match="nada que emitir"):
            BinancePollSource(["ETHUSDT"], TF, klines=False, mark_price=False)


class TestModosDelHub:
    async def test_modo_rest_no_toca_el_websocket(self) -> None:
        hub = MarketDataHub(["ETHUSDT"], TF, persist=False, mode="rest")
        assert hub.source_name == "binance_rest_poll"

    async def test_modo_desconocido_falla_temprano(self) -> None:
        with pytest.raises(ValueError, match="modo de feed"):
            MarketDataHub(["ETHUSDT"], TF, mode="telepatia")


def _fake_kline() -> Any:
    from bob.data.binance_rest import Kline

    return Kline.from_row(_kline_row(T0))
