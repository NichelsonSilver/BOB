"""Tests de los snapshots de derivados (OI, ratio long/short, taker ratio).

Lo que se protege acá es una ventana que no se puede recuperar: Binance guarda
~30 días de esta historia. Un bug que descarte puntos en silencio no se nota
hoy y se paga cuando la Fase 2b quiera entrenar con ellos.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient, MockTransport, Request, Response
from sqlmodel import Session, SQLModel, create_engine

from bob.data.binance_rest import BinanceRestClient
from bob.data.snapshots import (
    DerivativePoint,
    fetch_derivatives,
    merge_derivative_rows,
    snapshot_loop,
    snapshot_once,
)
from bob.data.store import derivatives_coverage, upsert_derivatives

T0 = 1_700_000_000_000
STEP = 900_000


@pytest.fixture
def session():
    """DB en memoria, aislada por test."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _oi_row(ts: int, oi: str = "150000.5") -> dict[str, object]:
    return {
        "symbol": "ETHUSDT",
        "sumOpenInterest": oi,
        "sumOpenInterestValue": "375000000.0",
        "timestamp": ts,
    }


def _ls_row(ts: int, ratio: str = "1.85") -> dict[str, object]:
    return {
        "symbol": "ETHUSDT",
        "longShortRatio": ratio,
        "longAccount": "0.6491",
        "shortAccount": "0.3509",
        "timestamp": ts,
    }


def _taker_row(ts: int, ratio: str = "0.98") -> dict[str, object]:
    return {"buySellRatio": ratio, "buyVol": "1000", "sellVol": "1020", "timestamp": ts}


class TestMerge:
    def test_alinea_las_tres_fuentes_por_timestamp(self) -> None:
        points = merge_derivative_rows([_oi_row(T0)], [_ls_row(T0)], [_taker_row(T0)])
        assert len(points) == 1
        p = points[0]
        assert p.open_interest == "150000.5"
        assert p.long_short_ratio == "1.85"
        assert p.taker_buy_sell_ratio == "0.98"

    def test_conserva_puntos_incompletos(self) -> None:
        """Mejor un punto con OI y sin ratio que ningún punto: el modelo tiene
        que tolerar features faltantes."""
        points = merge_derivative_rows([_oi_row(T0)], [_ls_row(T0 + STEP)], [])
        assert len(points) == 2
        assert points[0].long_short_ratio is None
        assert points[1].open_interest is None

    def test_acepta_timestamp_como_texto(self) -> None:
        points = merge_derivative_rows([{"timestamp": str(T0), "sumOpenInterest": "1"}], [], [])
        assert points[0].timestamp == T0

    def test_descarta_filas_sin_timestamp_usable(self) -> None:
        rows = [{"sumOpenInterest": "1"}, {"timestamp": "no-num", "sumOpenInterest": "2"}]
        assert merge_derivative_rows(rows, [], []) == []

    def test_devuelve_en_orden_cronologico(self) -> None:
        points = merge_derivative_rows(
            [_oi_row(T0 + 2 * STEP), _oi_row(T0), _oi_row(T0 + STEP)], [], []
        )
        assert [p.timestamp for p in points] == [T0, T0 + STEP, T0 + 2 * STEP]


class TestPersistencia:
    def test_persiste_y_cuenta(self, session: Session) -> None:
        points = merge_derivative_rows([_oi_row(T0)], [_ls_row(T0)], [_taker_row(T0)])
        assert upsert_derivatives("ETHUSDT", "15m", points, session) == 1
        have = derivatives_coverage("ETHUSDT", "15m", session)
        assert have == {"n_points": 1, "first_timestamp": T0, "last_timestamp": T0}

    def test_es_idempotente(self, session: Session) -> None:
        """El solape entre ciclos es deliberado: no puede duplicar filas."""
        points = merge_derivative_rows([_oi_row(T0)], [], [])
        upsert_derivatives("ETHUSDT", "15m", points, session)
        upsert_derivatives("ETHUSDT", "15m", points, session)
        assert derivatives_coverage("ETHUSDT", "15m", session)["n_points"] == 1

    def test_no_borra_lo_que_el_ciclo_nuevo_no_trae(self, session: Session) -> None:
        """Si un endpoint falla, el dato viejo de los otros sobrevive."""
        upsert_derivatives("ETHUSDT", "15m", merge_derivative_rows([], [_ls_row(T0)], []), session)
        upsert_derivatives("ETHUSDT", "15m", merge_derivative_rows([_oi_row(T0)], [], []), session)

        from sqlmodel import select

        from bob.db.models import DerivativeSnapshot

        record = session.exec(select(DerivativeSnapshot)).one()
        assert record.long_short_ratio == "1.85"
        assert record.open_interest == "150000.5"

    def test_actualiza_el_valor_cuando_binance_lo_corrige(self, session: Session) -> None:
        upsert_derivatives("ETHUSDT", "15m", [DerivativePoint(T0, open_interest="1")], session)
        upsert_derivatives("ETHUSDT", "15m", [DerivativePoint(T0, open_interest="2")], session)

        from sqlmodel import select

        from bob.db.models import DerivativeSnapshot

        record = session.exec(select(DerivativeSnapshot)).one()
        assert record.open_interest == "2"

    def test_lista_vacia_no_escribe(self, session: Session) -> None:
        assert upsert_derivatives("ETHUSDT", "15m", [], session) == 0

    def test_coverage_de_simbolo_sin_datos(self, session: Session) -> None:
        assert derivatives_coverage("SOLUSDT", "15m", session)["n_points"] == 0


def _client(handler) -> BinanceRestClient:
    """Cliente REST con la red simulada por un transport de httpx."""
    transport = MockTransport(handler)
    http = AsyncClient(base_url="https://fapi.binance.com", transport=transport)
    return BinanceRestClient(client=http)


class TestFetch:
    async def test_pide_los_cinco_endpoints(self) -> None:
        """Los dos "top" se agregaron después: sin ellos, las 5 columnas de top
        traders quedaban NaN en vivo desde el día en que terminaba el archivo
        (medido: 73 barras NaN de 96)."""
        visitados: list[str] = []

        def handler(request: Request) -> Response:
            visitados.append(request.url.path)
            if "openInterestHist" in request.url.path:
                return Response(200, json=[_oi_row(T0)])
            if "globalLongShortAccountRatio" in request.url.path:
                return Response(200, json=[_ls_row(T0)])
            if "topLongShortAccountRatio" in request.url.path:
                return Response(200, json=[{"timestamp": T0, "longShortRatio": "2.1"}])
            if "topLongShortPositionRatio" in request.url.path:
                return Response(200, json=[{"timestamp": T0, "longShortRatio": "1.4"}])
            return Response(200, json=[_taker_row(T0)])

        client = _client(handler)
        points = await fetch_derivatives(client, "ETHUSDT", "15m", 500)
        await client.aclose()

        assert len(visitados) == 5
        assert points[0].top_trader_account_ratio == "2.1"
        assert points[0].top_trader_position_ratio == "1.4"
        assert len(points) == 1
        assert points[0].open_interest == "150000.5"


class TestSnapshotOnce:
    async def test_persiste_la_watchlist(self, in_memory_engine) -> None:
        def handler(request: Request) -> Response:
            if "openInterestHist" in request.url.path:
                return Response(200, json=[_oi_row(T0), _oi_row(T0 + STEP)])
            if "globalLongShortAccountRatio" in request.url.path:
                return Response(200, json=[_ls_row(T0)])
            return Response(200, json=[_taker_row(T0)])

        client = _client(handler)
        written = await snapshot_once(["ethusdt"], "15m", client=client)
        await client.aclose()

        assert written == {"ETHUSDT": 2}
        assert derivatives_coverage("ETHUSDT", "15m")["n_points"] == 2

    async def test_un_simbolo_caido_no_frena_a_los_demas(self, in_memory_engine) -> None:
        """La ventana de 30 días corre para todos: si ETH falla, BTC igual se
        snapshotea."""

        def handler(request: Request) -> Response:
            if request.url.params.get("symbol") == "ETHUSDT":
                return Response(400, text='{"code":-1121,"msg":"Invalid symbol."}')
            if "openInterestHist" in request.url.path:
                return Response(200, json=[_oi_row(T0)])
            return Response(200, json=[])

        client = _client(handler)
        written = await snapshot_once(["ETHUSDT", "BTCUSDT"], "15m", client=client)
        await client.aclose()

        assert written == {"ETHUSDT": 0, "BTCUSDT": 1}


class TestSnapshotLoop:
    """El loop se prueba por eventos, nunca por reloj de pared.

    La versión anterior dormía 50 ms y asumía que habían pasado >= 2 ciclos.
    Con la máquina cargada el loop alcanzaba a dar uno solo y el test fallaba
    sin que nada estuviera roto — tres falsos positivos en una sesión. Ahora el
    doble avisa por un Event cuando llegó a las N vueltas y el test espera ESO,
    con un timeout amplio que solo salta si el loop de verdad se colgó.
    """

    @staticmethod
    def _loop_hasta(n_ciclos: int, fake):
        """Envuelve un doble para que señale cuando completó `n_ciclos`."""
        listo = asyncio.Event()
        contador = {"n": 0}

        async def envuelto(symbols, period="15m", limit=500, *, client=None):
            contador["n"] += 1
            try:
                return await fake(contador["n"], symbols)
            finally:
                if contador["n"] >= n_ciclos:
                    listo.set()

        return envuelto, listo, contador

    async def test_corre_un_ciclo_y_para_cuando_se_lo_piden(
        self, in_memory_engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ciclos: list[tuple[str, ...]] = []

        async def fake(_n, symbols):
            ciclos.append(tuple(symbols))
            return {}

        envuelto, listo, _ = self._loop_hasta(2, fake)
        monkeypatch.setattr("bob.data.snapshots.snapshot_once", envuelto)
        stop = asyncio.Event()
        task = asyncio.create_task(snapshot_loop(["ETHUSDT"], "15m", 0.01, stop=stop))

        await asyncio.wait_for(listo.wait(), timeout=5.0)
        stop.set()
        await asyncio.wait_for(task, timeout=1.0)

        assert len(ciclos) >= 2
        assert all(c == ("ETHUSDT",) for c in ciclos)

    async def test_un_ciclo_que_revienta_no_mata_el_loop(
        self, in_memory_engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake(n, _symbols):
            if n == 1:
                raise RuntimeError("Binance devolvió basura")
            return {}

        envuelto, listo, intentos = self._loop_hasta(2, fake)
        monkeypatch.setattr("bob.data.snapshots.snapshot_once", envuelto)
        stop = asyncio.Event()
        task = asyncio.create_task(snapshot_loop(["ETHUSDT"], "15m", 0.01, stop=stop))

        # Si el error matara el loop, el segundo ciclo nunca llega y esto expira.
        await asyncio.wait_for(listo.wait(), timeout=5.0)
        stop.set()
        await asyncio.wait_for(task, timeout=1.0)

        assert intentos["n"] >= 2
