"""Tests de la fuente REST en vivo y del cambio automático de fuente.

Existe porque en la red del usuario el WS de futuros acepta la suscripción y no
manda un solo frame (ver `data/binance_poll.py`). Lo que se protege acá: que la
misma vela cerrada no se emita dos veces, y que el hub se dé cuenta de que el
WS está mudo y cambie de fuente sin perder consumidores.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from bob.data.binance_poll import BinancePollSource
from bob.data.binance_rest import INTERVAL_MS, BinanceRestClient
from bob.data.binance_ws import BinanceMarketStream, KlineEvent, MarketDataHub, MarkPriceEvent

from .test_binance_ws import FakeConnector, FakeSocket

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


class TestFallbackAutomatico:
    async def test_cambia_a_rest_cuando_el_ws_no_entrega(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """El caso real: el WS conecta, confirma la suscripción y queda mudo."""
        mudo = BinanceMarketStream(
            ["ETHUSDT"],
            TF,
            connect=FakeConnector([FakeSocket([], after="hang")]),
            connection_ttl=1e9,
            recv_timeout=5.0,
        )
        creados: list[Any] = []

        class FakePoll:
            source_name = "binance_rest_poll"

            def __init__(self, symbols: Any, timeframe: str) -> None:
                self.listeners: list[Any] = []
                self.started = False
                self.status_obj = mudo.status
                creados.append(self)

            @property
            def status(self) -> Any:
                return self.status_obj

            def add_listener(self, listener: Any) -> None:
                self.listeners.append(listener)

            async def start(self) -> None:
                self.started = True

            async def stop(self) -> None:
                self.started = False

        monkeypatch.setattr("bob.data.binance_poll.BinancePollSource", FakePoll)

        hub = MarketDataHub(
            ["ETHUSDT"], TF, persist=False, stream=mudo, mode="auto", ws_probe_s=0.05
        )
        vistos: list[Any] = []

        async def consumidor(event: Any) -> None:
            vistos.append(event)

        hub.add_listener(consumidor)

        await hub.start()
        await asyncio.sleep(0.2)

        assert creados, "no cayó a REST"
        assert hub.source_name == "binance_rest_poll"
        assert creados[0].started is True

        # El consumidor sigue enganchado después del cambio de fuente.
        await creados[0].listeners[0](
            KlineEvent(
                symbol="ETHUSDT",
                timeframe=TF,
                kline=_fake_kline(),
                is_closed=True,
                event_time=1,
            )
        )
        assert len(vistos) == 1
        await hub.stop()

    async def test_no_cambia_si_el_ws_entrega(self, monkeypatch: pytest.MonkeyPatch) -> None:
        vivo = BinanceMarketStream(
            ["ETHUSDT"],
            TF,
            connect=FakeConnector([FakeSocket([_MARK_FRAME], after="hang")]),
            connection_ttl=1e9,
            recv_timeout=5.0,
        )
        hub = MarketDataHub(
            ["ETHUSDT"], TF, persist=False, stream=vivo, mode="auto", ws_probe_s=0.05
        )
        await hub.start()
        await asyncio.sleep(0.2)

        assert hub.source_name == "binance_ws"
        await hub.stop()

    async def test_modo_rest_no_toca_el_websocket(self) -> None:
        hub = MarketDataHub(["ETHUSDT"], TF, persist=False, mode="rest")
        assert hub.source_name == "binance_rest_poll"

    async def test_modo_desconocido_falla_temprano(self) -> None:
        with pytest.raises(ValueError, match="modo de feed"):
            MarketDataHub(["ETHUSDT"], TF, mode="telepatia")


_MARK_FRAME = (
    '{"stream":"ethusdt@markPrice@1s","data":{"e":"markPriceUpdate","E":1,'
    '"s":"ETHUSDT","p":"2500.0","i":"2499.0","r":"0.0001","T":2}}'
)


def _fake_kline() -> Any:
    from bob.data.binance_rest import Kline

    return Kline.from_row(_kline_row(T0))
