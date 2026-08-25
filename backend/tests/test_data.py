"""Tests de la capa de datos: parseo REST, rate limiting y persistencia.

La red se simula con un transport de httpx, así que los tests corren sin
tocar Binance: son deterministas y no dependen de que la API esté arriba ni
consumen rate limit.
"""

from __future__ import annotations

import numpy as np
import pytest
from httpx import AsyncClient, Headers, MockTransport, Request, Response
from sqlmodel import Session, SQLModel, create_engine

from bob.data.binance_rest import (
    INTERVAL_MS,
    BinanceRestClient,
    BinanceRestError,
    Kline,
    WeightLimiter,
)
from bob.data.store import (
    OHLCVSeries,
    coverage,
    load_series,
    series_from_klines,
    upsert_klines,
)

TF = "15m"
STEP = INTERVAL_MS[TF]


def _row(open_time: int, close: float = 100.0) -> list:
    """Fila posicional tal como la devuelve /fapi/v1/klines."""
    return [
        open_time,
        f"{close:.2f}",
        f"{close + 1:.2f}",
        f"{close - 1:.2f}",
        f"{close:.2f}",
        "10.5",
        open_time + STEP - 1,
        "1050.0",
        42,
        "6.0",
        "600.0",
        "0",
    ]


@pytest.fixture
def session():
    """DB en memoria, aislada por test."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class TestKline:
    def test_parsea_el_array_posicional(self) -> None:
        k = Kline.from_row(_row(1_700_000_000_000, 2500.0))
        assert k.open_time == 1_700_000_000_000
        assert k.close == "2500.00"
        assert k.high == "2501.00"
        assert k.n_trades == 42
        assert k.taker_buy_volume == "6.0"

    def test_conserva_precios_como_texto(self) -> None:
        """Convención de db/models.py: nada de float en precios."""
        k = Kline.from_row(_row(0, 1234.56789))
        assert isinstance(k.close, str)


class TestWeightLimiter:
    def test_lee_el_header_de_peso(self) -> None:
        lim = WeightLimiter()
        lim.observe(Headers({"X-MBX-USED-WEIGHT-1M": "1234"}))
        assert lim.used_weight == 1234

    def test_sin_header_incrementa_conservadoramente(self) -> None:
        lim = WeightLimiter()
        lim.observe(Headers({}))
        assert lim.used_weight == 1

    def test_header_corrupto_no_rompe(self) -> None:
        lim = WeightLimiter()
        lim.observe(Headers({"X-MBX-USED-WEIGHT-1M": "no-es-un-numero"}))
        assert lim.used_weight == 1

    async def test_acquire_no_bloquea_bajo_el_umbral(self) -> None:
        lim = WeightLimiter(limit=2400, min_interval=0.0)
        lim.observe(Headers({"X-MBX-USED-WEIGHT-1M": "10"}))
        await lim.acquire()
        assert lim.used_weight == 10


class TestBinanceRestClient:
    def _client(self, handler) -> BinanceRestClient:
        transport = MockTransport(handler)
        http = AsyncClient(transport=transport, base_url="https://fapi.binance.com")
        limiter = WeightLimiter(min_interval=0.0)
        return BinanceRestClient(client=http, limiter=limiter)

    async def test_server_time(self) -> None:
        def handler(request: Request) -> Response:
            return Response(200, json={"serverTime": 1_700_000_000_000})

        async with self._client(handler) as c:
            assert await c.server_time_ms() == 1_700_000_000_000

    async def test_exchange_filters(self) -> None:
        def handler(request: Request) -> Response:
            return Response(
                200,
                json={
                    "symbols": [
                        {
                            "symbol": "ETHUSDT",
                            "filters": [
                                {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                                {"filterType": "LOT_SIZE", "stepSize": "0.001"},
                            ],
                        }
                    ]
                },
            )

        async with self._client(handler) as c:
            out = await c.exchange_filters("ETHUSDT")
        assert out["tick_size"] == "0.01"
        assert out["step_size"] == "0.001"

    async def test_simbolo_desconocido(self) -> None:
        def handler(request: Request) -> Response:
            return Response(200, json={"symbols": []})

        async with self._client(handler) as c:
            with pytest.raises(BinanceRestError, match="desconocido"):
                await c.exchange_filters("NOEXISTE")

    async def test_pagina_hasta_agotar_el_rango(self) -> None:
        """Binance devuelve máx 1500 por request: hay que encadenar páginas."""
        start = 1_700_000_000_000
        total = 3200
        llamadas: list[int] = []

        def handler(request: Request) -> Response:
            desde = int(request.url.params["startTime"])
            llamadas.append(desde)
            primero = (desde - start) // STEP
            filas = [
                _row(start + (primero + i) * STEP)
                for i in range(min(1500, total - primero))
            ]
            return Response(200, json=filas)

        async with self._client(handler) as c:
            out = await c.fetch_klines(
                "ETHUSDT", TF, start, end_time=start + total * STEP, now_ms=start + 10**12
            )
        assert len(out) == total
        assert len(llamadas) >= 3
        assert [k.open_time for k in out] == sorted(k.open_time for k in out)

    async def test_descarta_la_vela_en_curso(self) -> None:
        """Incluirla es lookahead en producción: su high/low aún puede cambiar."""
        start = 1_700_000_000_000
        ahora = start + 3 * STEP + 100  # la 4ª vela no cerró

        def handler(request: Request) -> Response:
            return Response(200, json=[_row(start + i * STEP) for i in range(4)])

        async with self._client(handler) as c:
            out = await c.fetch_klines("ETHUSDT", TF, start, now_ms=ahora)
        assert len(out) == 3

    async def test_no_duplica_al_repetir_pagina(self) -> None:
        start = 1_700_000_000_000

        def handler(request: Request) -> Response:
            return Response(200, json=[_row(start + i * STEP) for i in range(5)])

        async with self._client(handler) as c:
            out = await c.fetch_klines(
                "ETHUSDT", TF, start, end_time=start + 100 * STEP, now_ms=start + 10**12
            )
        assert len({k.open_time for k in out}) == len(out)

    async def test_timeframe_no_soportado(self) -> None:
        async with self._client(lambda r: Response(200, json=[])) as c:
            with pytest.raises(ValueError, match="no soportado"):
                await c.fetch_klines("ETHUSDT", "7m", 0)

    async def test_respuesta_vacia(self) -> None:
        async with self._client(lambda r: Response(200, json=[])) as c:
            assert await c.fetch_klines("ETHUSDT", TF, 0, end_time=STEP * 10) == []

    async def test_error_4xx_es_explicito(self) -> None:
        def handler(request: Request) -> Response:
            return Response(400, text="Invalid symbol.")

        async with self._client(handler) as c:
            with pytest.raises(BinanceRestError, match="HTTP 400"):
                await c.klines_page("BAD", TF)

    async def test_reintenta_tras_un_5xx(self) -> None:
        estado = {"n": 0}

        def handler(request: Request) -> Response:
            estado["n"] += 1
            if estado["n"] == 1:
                return Response(503, text="mantención")
            return Response(200, json=[_row(0)])

        async with self._client(handler) as c:
            out = await c.klines_page("ETHUSDT", TF)
        assert len(out) == 1
        assert estado["n"] == 2

    async def test_agota_reintentos(self) -> None:
        def handler(request: Request) -> Response:
            return Response(500, text="siempre caído")

        transport = MockTransport(handler)
        http = AsyncClient(transport=transport, base_url="https://fapi.binance.com")
        c = BinanceRestClient(
            client=http, limiter=WeightLimiter(min_interval=0.0), max_retries=2
        )
        with pytest.raises(BinanceRestError, match="agotó"):
            await c.klines_page("ETHUSDT", TF)
        await c.aclose()

    async def test_endpoints_de_derivados(self) -> None:
        def handler(request: Request) -> Response:
            return Response(200, json=[{"valor": 1}])

        async with self._client(handler) as c:
            assert await c.funding_history("ETHUSDT") == [{"valor": 1}]
            assert await c.open_interest_hist("ETHUSDT") == [{"valor": 1}]
            assert await c.long_short_ratio("ETHUSDT") == [{"valor": 1}]
            assert await c.taker_ratio("ETHUSDT") == [{"valor": 1}]


class TestStore:
    def test_series_from_klines(self) -> None:
        klines = [Kline.from_row(_row(i * STEP, 100.0 + i)) for i in range(10)]
        s = series_from_klines("ETHUSDT", TF, klines)
        assert len(s) == 10
        assert s.close[3] == pytest.approx(103.0)
        assert s.interval_ms == STEP

    def test_ordena_klines_desordenadas(self) -> None:
        klines = [Kline.from_row(_row(i * STEP)) for i in (3, 1, 2, 0)]
        s = series_from_klines("ETHUSDT", TF, klines)
        assert np.all(np.diff(s.open_time) > 0)

    def test_rechaza_open_time_duplicado(self) -> None:
        klines = [Kline.from_row(_row(0)), Kline.from_row(_row(0))]
        with pytest.raises(ValueError, match="creciente"):
            series_from_klines("ETHUSDT", TF, klines)

    def test_upsert_es_idempotente(self, session: Session) -> None:
        klines = [Kline.from_row(_row(i * STEP)) for i in range(20)]
        upsert_klines("ETHUSDT", TF, klines, session)
        upsert_klines("ETHUSDT", TF, klines, session)
        assert len(load_series("ETHUSDT", TF, session=session)) == 20

    def test_upsert_actualiza_valores(self, session: Session) -> None:
        upsert_klines("ETHUSDT", TF, [Kline.from_row(_row(0, 100.0))], session)
        upsert_klines("ETHUSDT", TF, [Kline.from_row(_row(0, 999.0))], session)
        s = load_series("ETHUSDT", TF, session=session)
        assert len(s) == 1
        assert s.close[0] == pytest.approx(999.0)

    def test_upsert_vacio(self, session: Session) -> None:
        assert upsert_klines("ETHUSDT", TF, [], session) == 0

    def test_separa_por_simbolo_y_timeframe(self, session: Session) -> None:
        upsert_klines("ETHUSDT", TF, [Kline.from_row(_row(0))], session)
        upsert_klines("BTCUSDT", TF, [Kline.from_row(_row(0))], session)
        upsert_klines("ETHUSDT", "1h", [Kline.from_row(_row(0))], session)
        assert len(load_series("ETHUSDT", TF, session=session)) == 1
        assert len(load_series("BTCUSDT", TF, session=session)) == 1

    def test_filtro_por_rango(self, session: Session) -> None:
        upsert_klines(
            "ETHUSDT", TF, [Kline.from_row(_row(i * STEP)) for i in range(10)], session
        )
        s = load_series("ETHUSDT", TF, start_time=3 * STEP, end_time=6 * STEP, session=session)
        assert len(s) == 4

    def test_coverage(self, session: Session) -> None:
        upsert_klines(
            "ETHUSDT", TF, [Kline.from_row(_row(i * STEP)) for i in range(7)], session
        )
        cov = coverage("ETHUSDT", TF, session)
        assert cov["n_candles"] == 7
        assert cov["first_open_time"] == 0
        assert cov["last_open_time"] == 6 * STEP

    def test_coverage_vacio(self, session: Session) -> None:
        assert coverage("NADA", TF, session)["n_candles"] == 0

    def test_detecta_huecos_sin_rellenarlos(self, session: Session) -> None:
        """Inventar velas es inventar retornos: los huecos se reportan."""
        indices = [0, 1, 2, 7, 8]
        upsert_klines(
            "ETHUSDT", TF, [Kline.from_row(_row(i * STEP)) for i in indices], session
        )
        s = load_series("ETHUSDT", TF, session=session)
        assert len(s) == 5  # no se rellenó
        assert s.gaps == [(2 * STEP, 7 * STEP)]

    def test_serie_contigua_no_tiene_huecos(self, session: Session) -> None:
        upsert_klines(
            "ETHUSDT", TF, [Kline.from_row(_row(i * STEP)) for i in range(30)], session
        )
        assert load_series("ETHUSDT", TF, session=session).gaps == []

    def test_slice_posicional(self) -> None:
        klines = [Kline.from_row(_row(i * STEP, 100.0 + i)) for i in range(20)]
        s = series_from_klines("ETHUSDT", TF, klines)
        sub = s.slice(5, 10)
        assert len(sub) == 5
        assert sub.close[0] == pytest.approx(105.0)
        assert sub.symbol == s.symbol

    def test_serie_vacia_no_tiene_huecos(self) -> None:
        s: OHLCVSeries = series_from_klines("ETHUSDT", TF, [])
        assert len(s) == 0
        assert s.gaps == []


# --------------------------------------------------------------------- #
# repair_series — lo que hace segura una pausa del proceso
# --------------------------------------------------------------------- #


class _FakeRestClient:
    """Cliente REST de mentira que devuelve las velas de un rango pedido."""

    def __init__(self, disponibles):
        self.disponibles = disponibles
        self.pedidos = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetch_klines(self, symbol, interval, start_time, end_time=None, **kw):
        self.pedidos.append((start_time, end_time))
        return [
            k
            for k in self.disponibles
            if k.open_time >= start_time and (end_time is None or k.open_time <= end_time)
        ]


def _kline(open_time: int, step: int, price: float = 100.0):
    from bob.data.binance_rest import Kline

    return Kline(
        open_time=open_time, open=str(price), high=str(price), low=str(price),
        close=str(price), volume="1", close_time=open_time + step - 1,
        quote_volume="100", n_trades=1, taker_buy_volume="0.5",
        taker_buy_quote_volume="50",
    )


async def test_repair_cierra_un_hueco_interior(in_memory_engine, monkeypatch):
    """La secuencia que rompe: el equipo se suspende, el feed vuelve y escribe
    la vela actual, así que la descarga incremental salta el rango caído."""
    from bob.data import download as mod
    from bob.data.binance_rest import INTERVAL_MS
    from bob.data.store import load_series, upsert_klines

    step = INTERVAL_MS["15m"]
    base = 1_700_000_000_000
    todas = [_kline(base + i * step, step) for i in range(10)]
    # En DB: las 3 primeras y las 3 últimas. Falta el medio (la pausa).
    upsert_klines("ETHUSDT", "15m", todas[:3] + todas[7:])
    assert len(load_series("ETHUSDT", "15m").gaps) == 1

    fake = _FakeRestClient(todas)
    monkeypatch.setattr(mod, "BinanceRestClient", lambda: fake)

    resultado = await mod.repair_series("ETHUSDT", "15m")

    assert resultado["gaps_found"] == 1
    assert resultado["filled"] == 4  # las velas 3..6
    assert resultado["gaps_remaining"] == 0
    serie = load_series("ETHUSDT", "15m")
    assert len(serie) == 10
    assert serie.gaps == []


async def test_repair_es_idempotente_sobre_una_serie_completa(
    in_memory_engine, monkeypatch
):
    from bob.data import download as mod
    from bob.data.binance_rest import INTERVAL_MS
    from bob.data.store import upsert_klines

    step = INTERVAL_MS["15m"]
    base = 1_700_000_000_000
    todas = [_kline(base + i * step, step) for i in range(5)]
    upsert_klines("ETHUSDT", "15m", todas)

    fake = _FakeRestClient(todas)
    monkeypatch.setattr(mod, "BinanceRestClient", lambda: fake)
    resultado = await mod.repair_series("ETHUSDT", "15m")

    assert resultado == {"gaps_found": 0, "filled": 0, "extended": 0, "gaps_remaining": 0}
    # Sin huecos, el único request es el de extensión hacia adelante.
    assert len(fake.pedidos) == 1


async def test_repair_reporta_el_hueco_que_binance_no_devuelve(
    in_memory_engine, monkeypatch
):
    """No se rellena lo que la fuente no entrega: se reporta y se sigue."""
    from bob.data import download as mod
    from bob.data.binance_rest import INTERVAL_MS
    from bob.data.store import load_series, upsert_klines

    step = INTERVAL_MS["15m"]
    base = 1_700_000_000_000
    todas = [_kline(base + i * step, step) for i in range(6)]
    upsert_klines("ETHUSDT", "15m", [todas[0], todas[1], todas[4], todas[5]])

    fake = _FakeRestClient([])  # Binance no tiene esas velas
    monkeypatch.setattr(mod, "BinanceRestClient", lambda: fake)
    resultado = await mod.repair_series("ETHUSDT", "15m")

    assert resultado["gaps_found"] == 1
    assert resultado["filled"] == 0
    assert resultado["gaps_remaining"] == 1
    assert len(load_series("ETHUSDT", "15m").gaps) == 1
