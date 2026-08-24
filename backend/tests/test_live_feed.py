"""Tests del puente feed → dashboard.

Se prueba la traducción de eventos (qué llega al navegador y qué no) y el
reporte de estado de conexión, que es lo que evita que el usuario mire un
precio congelado creyendo que es el de ahora.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from bob.data.binance_rest import Kline
from bob.data.binance_ws import (
    AggTradeEvent,
    KlineEvent,
    MarketDataHub,
    MarkPriceEvent,
)
from bob.live.feed import LiveDataService

TF = "15m"
T0 = 1_700_000_000_000


def _kline(*, close: str = "2500.10") -> Kline:
    return Kline(
        open_time=T0,
        open="2490.00",
        high="2505.00",
        low="2488.00",
        close=close,
        volume="1234.5",
        close_time=T0 + 899_999,
        quote_volume="3080000.0",
        n_trades=4321,
        taker_buy_volume="700.0",
        taker_buy_quote_volume="1750000.0",
    )


def _service(**kwargs: Any) -> tuple[LiveDataService, list[tuple[str, Any]]]:
    """Servicio con un `publish` que solo acumula: nada de red ni de WS."""
    publicados: list[tuple[str, Any]] = []

    async def publish(event: str, payload: Any) -> None:
        publicados.append((event, payload))

    hub = MarketDataHub(["ETHUSDT"], TF, persist=False)
    service = LiveDataService(
        ["ETHUSDT"],
        TF,
        publish=publish,
        hub=hub,
        snapshots_enabled=False,
        **kwargs,
    )
    return service, publicados


class TestTraduccionDeEventos:
    async def test_la_vela_cerrada_sale_como_market_candle(self) -> None:
        service, publicados = _service()
        await service._on_event(
            KlineEvent(symbol="ETHUSDT", timeframe=TF, kline=_kline(), is_closed=True, event_time=1)
        )

        assert len(publicados) == 1
        event, payload = publicados[0]
        assert event == "market.candle"
        assert payload["closed"] is True
        assert payload["close"] == "2500.10"
        assert payload["taker_buy_volume"] == "700.0"

    async def test_la_vela_en_curso_sale_como_tick_y_marcada_abierta(self) -> None:
        service, publicados = _service()
        await service._on_event(
            KlineEvent(
                symbol="ETHUSDT", timeframe=TF, kline=_kline(), is_closed=False, event_time=1
            )
        )

        event, payload = publicados[0]
        assert event == "market.tick"
        assert payload["closed"] is False
        assert payload["price"] == "2500.10"

    async def test_los_ticks_se_estrangulan_pero_la_vela_cerrada_no(self) -> None:
        """El stream actualiza cada ~250ms; el cierre de barra se publica siempre."""
        service, publicados = _service(min_tick_interval_s=10.0)
        abierta = KlineEvent(
            symbol="ETHUSDT", timeframe=TF, kline=_kline(), is_closed=False, event_time=1
        )
        cerrada = KlineEvent(
            symbol="ETHUSDT", timeframe=TF, kline=_kline(), is_closed=True, event_time=2
        )
        await service._on_event(abierta)
        await service._on_event(abierta)
        await service._on_event(cerrada)
        await service._on_event(cerrada)

        eventos = [e for e, _ in publicados]
        assert eventos == ["market.tick", "market.candle", "market.candle"]

    async def test_el_mark_price_viaja_con_su_funding(self) -> None:
        service, publicados = _service()
        await service._on_event(
            MarkPriceEvent(
                symbol="ETHUSDT",
                mark_price="2500.55",
                index_price="2500.40",
                funding_rate="0.0001",
                next_funding_time=T0,
                event_time=1,
            )
        )

        event, payload = publicados[0]
        assert event == "market.tick"
        assert payload["mark_price"] == "2500.55"
        assert payload["funding_rate"] == "0.0001"

    async def test_los_trades_sueltos_no_llegan_al_navegador(self) -> None:
        """Se agregan en el hub y salen resumidos en el tick."""
        service, publicados = _service()
        await service._on_event(
            AggTradeEvent(
                symbol="ETHUSDT",
                price="2500.30",
                quantity="1.5",
                is_buyer_maker=False,
                trade_time=1,
                event_time=1,
            )
        )
        assert publicados == []

    async def test_el_tick_lleva_el_desbalance_taker_acumulado(self) -> None:
        service, publicados = _service()
        await service.hub._on_event(
            AggTradeEvent(
                symbol="ETHUSDT",
                price="2500.30",
                quantity="2.0",
                is_buyer_maker=False,
                trade_time=1,
                event_time=1,
            )
        )
        await service._on_event(
            KlineEvent(
                symbol="ETHUSDT", timeframe=TF, kline=_kline(), is_closed=False, event_time=2
            )
        )

        _, payload = publicados[0]
        assert payload["taker_buy_qty"] == pytest.approx(2.0)
        assert payload["taker_sell_qty"] == pytest.approx(0.0)


class TestEstadoDeConexion:
    async def test_publica_el_estado_la_primera_vez(self) -> None:
        service, publicados = _service()
        await service._publish_status_if_changed()

        event, payload = publicados[0]
        assert event == "conn.status"
        assert payload["source"] == "binance_ws"
        assert payload["connected"] is False

    async def test_no_repite_el_estado_si_no_cambio(self) -> None:
        service, publicados = _service()
        await service._publish_status_if_changed()
        await service._publish_status_if_changed()
        assert len(publicados) == 1

    async def test_avisa_cuando_la_conexion_cae(self) -> None:
        service, publicados = _service()
        await service._publish_status_if_changed()

        service.hub.status.connected = True
        await service._publish_status_if_changed()
        service.hub.status.connected = False
        service.hub.status.reconnects = 1
        service.hub.status.last_error = "ConnectionError: boom"
        await service._publish_status_if_changed()

        estados = [p["connected"] for _, p in publicados]
        assert estados == [False, True, False]
        assert publicados[-1][1]["last_error"] == "ConnectionError: boom"


class TestCicloDeVida:
    async def test_arranca_y_para_sin_dejar_tasks_colgadas(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, publicados = _service(status_poll_s=0.01)

        arrancado = asyncio.Event()

        async def fake_start() -> None:
            arrancado.set()

        monkeypatch.setattr(service.hub, "start", fake_start)
        monkeypatch.setattr(service.hub, "stop", _noop)

        await service.start()
        await asyncio.sleep(0.03)
        await service.stop()

        assert arrancado.is_set()
        assert any(e == "conn.status" for e, _ in publicados)
        assert service._tasks == []

    async def test_con_snapshots_encendidos_lanza_su_task(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        publicados: list[tuple[str, Any]] = []

        async def publish(event: str, payload: Any) -> None:
            publicados.append((event, payload))

        ciclos = {"n": 0}

        async def fake_once(symbols, period="15m", limit=500, *, client=None):
            ciclos["n"] += 1
            return {}

        monkeypatch.setattr("bob.data.snapshots.snapshot_once", fake_once)

        hub = MarketDataHub(["ETHUSDT"], TF, persist=False)
        monkeypatch.setattr(hub, "start", _noop)
        monkeypatch.setattr(hub, "stop", _noop)
        service = LiveDataService(
            ["ETHUSDT"],
            TF,
            publish=publish,
            hub=hub,
            snapshots_enabled=True,
            snapshot_interval_s=0.01,
            status_poll_s=0.01,
        )

        await service.start()
        await asyncio.sleep(0.05)
        await service.stop()

        assert ciclos["n"] >= 1


async def _noop() -> None:
    return None
