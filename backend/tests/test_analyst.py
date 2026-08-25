"""Tests del analista en vivo — el loop vela → features → modelo → proyección.

El analista es la única pieza de la Fase 5 que junta I/O, asincronía y
modelos, así que lo que se prueba acá es el pegamento: que solo reaccione a
la vela cerrada, que persista sin duplicar, que no se calle sin explicar, y
que un reajuste fallido no lo deje mudo.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlmodel import select

from bob.data.binance_rest import Kline
from bob.data.binance_ws import AggTradeEvent, KlineEvent
from bob.db.models import ForecastRecord
from bob.db.session import get_session
from bob.live.analyst import LiveAnalyst, _append_candle
from bob.models.experiment import ExperimentConfig
from bob.models.labeling import BarrierConfig

from .conftest import TF_MS, synthetic_series  # type: ignore[attr-defined]


def _kline(open_time: int, close: float = 2000.0) -> Kline:
    return Kline(
        open_time=open_time,
        open=str(close),
        high=str(close * 1.001),
        low=str(close * 0.999),
        close=str(close),
        volume="100",
        close_time=open_time + TF_MS - 1,
        quote_volume="200000",
        n_trades=500,
        taker_buy_volume="50",
        taker_buy_quote_volume="100000",
    )


class _Recorder:
    """Captura lo que el analista publica, en vez de un WebSocket."""

    def __init__(self) -> None:
        self.frames: list[tuple[str, dict]] = []

    async def __call__(self, topic: str, payload: dict) -> None:
        self.frames.append((topic, payload))

    def of(self, topic: str) -> list[dict]:
        return [p for t, p in self.frames if t == topic]


#: Estimadores livianos a propósito. Lo que se prueba acá es el pegamento
#: —qué se publica, qué se persiste, qué pasa cuando algo falla—, no la
#: capacidad del modelo, que la mide el gate. Con boosting cada test costaba
#: ~10 ajustes sobre 3.000 barras y la suite se volvía impracticable.
CFG = ExperimentConfig(
    barrier=BarrierConfig(horizon_bars=8, vol_window_bars=48),
    conformal_alphas=(0.20,),
    model_kind="logistic",
    vol_kind="ridge",
)


def _analyst(publish, **kw) -> LiveAnalyst:
    return LiveAnalyst(
        "TESTUSDT", "15m", publish=publish, config=CFG, feature_set="price", **kw
    )


@pytest.fixture
def listo(monkeypatch):
    """Un analista ya ajustado sobre serie sintética, sin tocar la DB de velas."""
    series = synthetic_series(n=3000, seed=5, symbol="TESTUSDT")
    rec = _Recorder()
    analyst = _analyst(rec)

    def _fake_load(self):
        from bob.live.analyst import AnalystInputs

        return AnalystInputs(series=series, derivatives=None, funding=None, book=None)

    monkeypatch.setattr(LiveAnalyst, "_load_inputs", _fake_load)
    return analyst, rec, series


# --------------------------------------------------------------------- #
# Configuración
# --------------------------------------------------------------------- #


def test_una_variante_de_features_desconocida_falla_al_construir() -> None:
    with pytest.raises(ValueError, match="feature_set desconocido"):
        LiveAnalyst("TESTUSDT", "15m", publish=_Recorder(), feature_set="inventada")


def test_la_variante_manda_sobre_las_familias_del_config() -> None:
    """`--features price` tiene que apagar derivados y libro de verdad."""
    a = _analyst(_Recorder())
    assert a.config.use_derivatives is False
    assert a.config.use_book is False
    b = LiveAnalyst("TESTUSDT", "15m", publish=_Recorder(), feature_set="full")
    assert (b.config.use_derivatives, b.config.use_book) == (True, True)
    assert b.config.use_book_near is False


# --------------------------------------------------------------------- #
# El camino feliz
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_arranca_ajusta_y_publica_la_proyeccion(listo, in_memory_engine) -> None:
    analyst, rec, series = listo
    await analyst.start()

    frames = rec.of("analysis.forecast")
    assert len(frames) == 1
    p = frames[0]
    assert p["symbol"] == "TESTUSDT"
    assert p["open_time"] == int(series.open_time[-1])
    assert p["sigma_forecast"] > 0
    assert p["barrier_sigma_source"] == "forecast"
    # Regla 2: el KPI 1 viaja, pero marcado.
    assert p["probability_calibrated"] is False
    # El vector de features NO viaja al navegador; sí a la DB.
    assert "features" not in p
    assert {"long", "short"} <= set(p["projections"])
    # A 1x un long no se liquida (el precio tendría que llegar a 0) y un short
    # sí, arriba de la entrada. Que el número refleje eso es justo lo que le
    # importa a un usuario que ya pasó por una liquidación.
    assert p["projections"]["long"]["liquidation_price"] == 0.0
    assert p["projections"]["short"]["liquidation_price"] > p["reference_price"]


@pytest.mark.asyncio
async def test_persiste_el_vector_completo_y_no_duplica(listo, in_memory_engine) -> None:
    """Regla 10: sin el feature vector no hay post-mortem. Y un id, un registro."""
    analyst, rec, series = listo
    await analyst.start()
    await analyst.analyze_latest()  # misma barra otra vez

    with get_session() as s:
        rows = list(s.exec(select(ForecastRecord)).all())
    assert len(rows) == 1
    feats = json.loads(rows[0].features_json)
    assert len(feats) == 55  # las 55 de precio
    assert rows[0].status == "open"
    assert rows[0].horizon_bars == CFG.barrier.horizon_bars
    assert json.loads(rows[0].cones_json)[0]["alpha"] == pytest.approx(0.20)


@pytest.mark.asyncio
async def test_una_vela_nueva_produce_un_analisis_nuevo(listo, in_memory_engine) -> None:
    analyst, rec, series = listo
    await analyst.start()
    siguiente = int(series.open_time[-1]) + TF_MS
    await analyst.on_closed_candle(_kline(siguiente, close=float(series.close[-1])))

    frames = rec.of("analysis.forecast")
    assert len(frames) == 2
    assert frames[1]["open_time"] == siguiente
    assert frames[1]["bars_since_fit"] == 1


# --------------------------------------------------------------------- #
# Lo que el analista NO debe hacer
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ignora_la_vela_en_curso(listo, in_memory_engine) -> None:
    """Todo el proyecto es causal sobre velas cerradas: la en curso no se mira."""
    analyst, rec, series = listo
    await analyst.start()
    n = len(rec.of("analysis.forecast"))

    abierta = KlineEvent(
        symbol="TESTUSDT", timeframe="15m",
        kline=_kline(int(series.open_time[-1]) + TF_MS),
        is_closed=False, event_time=0,
    )
    await analyst.on_event(abierta)
    assert len(rec.of("analysis.forecast")) == n


@pytest.mark.asyncio
async def test_ignora_otros_simbolos_y_otros_eventos(listo, in_memory_engine) -> None:
    analyst, rec, series = listo
    await analyst.start()
    n = len(rec.of("analysis.forecast"))

    await analyst.on_event(
        KlineEvent(symbol="OTROUSDT", timeframe="15m",
                   kline=_kline(int(series.open_time[-1]) + TF_MS),
                   is_closed=True, event_time=0)
    )
    await analyst.on_event(
        AggTradeEvent(symbol="TESTUSDT", price="1", quantity="1",
                      is_buyer_maker=True, trade_time=0, event_time=0)
    )
    assert len(rec.of("analysis.forecast")) == n


@pytest.mark.asyncio
async def test_una_vela_repetida_o_atrasada_no_se_agrega(listo, in_memory_engine) -> None:
    """El relleno por REST solapa: agregar esa vela rompería la monotonía."""
    analyst, rec, series = listo
    await analyst.start()
    n = len(rec.of("analysis.forecast"))

    await analyst.on_closed_candle(_kline(int(series.open_time[-1])))  # repetida
    await analyst.on_closed_candle(_kline(int(series.open_time[-5])))  # atrasada
    assert len(rec.of("analysis.forecast")) == n


def test_append_candle_rechaza_lo_que_no_avanza() -> None:
    series = synthetic_series(n=100, seed=1)
    assert _append_candle(series, _kline(int(series.open_time[-1]))) is None
    nueva = _append_candle(series, _kline(int(series.open_time[-1]) + TF_MS))
    assert nueva is not None
    assert len(nueva) == len(series) + 1
    assert nueva.open_time[-1] == series.open_time[-1] + TF_MS


# --------------------------------------------------------------------- #
# Fallos: el analista explica en vez de callarse
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_un_fallo_al_analizar_se_publica_como_error(listo, in_memory_engine) -> None:
    """Regla 8 aplicada al modelo: un silencio sin causa engaña al usuario."""
    analyst, rec, _ = listo
    await analyst.start()

    def _explota(self):
        raise ValueError("faltan features densos de prueba")

    LiveAnalyst._compute_latest, original = _explota, LiveAnalyst._compute_latest
    try:
        assert await analyst.analyze_latest() is None
    finally:
        LiveAnalyst._compute_latest = original

    errores = rec.of("analysis.error")
    assert errores and "faltan features densos" in errores[-1]["detail"]


@pytest.mark.asyncio
async def test_una_vela_antes_del_ajuste_no_revienta(in_memory_engine) -> None:
    rec = _Recorder()
    analyst = _analyst(rec)
    await analyst.on_closed_candle(_kline(1_000_000))  # nunca arrancó
    assert rec.frames == []


@pytest.mark.asyncio
async def test_un_reajuste_fallido_deja_vivo_el_bundle_previo(
    listo, in_memory_engine, monkeypatch
) -> None:
    """Un modelo de ayer es mucho mejor que ningún modelo."""
    analyst, rec, series = listo
    await analyst.start()
    bundle_previo = analyst.bundle

    def _falla(self):
        raise RuntimeError("la DB se cayó durante el reajuste")

    monkeypatch.setattr(LiveAnalyst, "_load_and_fit", _falla)
    await analyst._refit()

    assert analyst.bundle is bundle_previo
    errores = rec.of("analysis.error")
    assert errores[-1]["still_serving"] is True
    # Y sigue emitiendo.
    n = len(rec.of("analysis.forecast"))
    await analyst.analyze_latest()
    assert len(rec.of("analysis.forecast")) == n + 1


@pytest.mark.asyncio
async def test_persistencia_apagada_no_escribe(in_memory_engine, monkeypatch) -> None:
    series = synthetic_series(n=3000, seed=5, symbol="TESTUSDT")

    def _fake_load(self):
        from bob.live.analyst import AnalystInputs

        return AnalystInputs(series=series, derivatives=None, funding=None, book=None)

    monkeypatch.setattr(LiveAnalyst, "_load_inputs", _fake_load)
    rec = _Recorder()
    analyst = _analyst(rec, persist=False)
    await analyst.start()

    with get_session() as s:
        assert list(s.exec(select(ForecastRecord)).all()) == []
    assert len(rec.of("analysis.forecast")) == 1


@pytest.mark.asyncio
async def test_stop_es_seguro_sin_haber_arrancado() -> None:
    await _analyst(_Recorder()).stop()


# --------------------------------------------------------------------- #
# Enganche al hub, carga desde DB y reajuste programado
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_attach_se_registra_como_listener_del_hub(listo, in_memory_engine) -> None:
    """El analista escucha por el hub, no por la fuente: si el feed cae de WS a
    REST en caliente, no se entera ni se pierde una vela."""

    class _Hub:
        def __init__(self) -> None:
            self.listeners: list = []

        def add_listener(self, listener) -> None:
            self.listeners.append(listener)

    analyst, rec, series = listo
    hub = _Hub()
    analyst.attach(hub)
    assert hub.listeners == [analyst.on_event]

    await analyst.start()
    n = len(rec.of("analysis.forecast"))
    # Lo que llegue por el hub debe recorrer el mismo camino.
    await hub.listeners[0](
        KlineEvent(
            symbol="TESTUSDT", timeframe="15m",
            kline=_kline(int(series.open_time[-1]) + TF_MS,
                         close=float(series.close[-1])),
            is_closed=True, event_time=0,
        )
    )
    assert len(rec.of("analysis.forecast")) == n + 1


def test_sin_velas_persistidas_dice_que_falta_descargar(in_memory_engine) -> None:
    """El error tiene que nombrar el comando, no fallar con un IndexError."""
    analyst = _analyst(_Recorder())
    with pytest.raises(ValueError, match="bob.data.download"):
        analyst._load_inputs()


def test_load_inputs_trae_derivados_solo_si_la_variante_los_usa(
    in_memory_engine, monkeypatch
) -> None:
    from bob.live import analyst as mod

    series = synthetic_series(n=50, seed=2, symbol="TESTUSDT")
    monkeypatch.setattr(mod, "load_series", lambda *a, **k: series)
    pedidos: list[str] = []

    def _deriv(symbol, period):
        pedidos.append(period)
        return None

    monkeypatch.setattr(mod, "load_derivatives", _deriv)
    monkeypatch.setattr(mod, "load_book_depth", lambda *a, **k: "libro")

    solo_precio = _analyst(_Recorder())
    assert solo_precio._load_inputs().derivatives is None
    assert pedidos == []

    completo = LiveAnalyst("TESTUSDT", "15m", publish=_Recorder(), config=CFG,
                           feature_set="full")
    entradas = completo._load_inputs()
    # La grilla de derivados debe ser la del archivo histórico (5m): con
    # periods distintos, archivo y REST escriben series que nunca se tocan.
    assert pedidos == ["5m", "funding"]
    assert entradas.book == "libro"


@pytest.mark.asyncio
async def test_no_pronostica_sobre_una_barra_con_huecos(listo, in_memory_engine) -> None:
    """Un NaN en una columna densa es un hueco de datos: inventar sería peor."""
    analyst, rec, _ = listo
    await analyst.start()

    original = analyst._assemble

    def _con_hueco(inputs):
        X, names, sparse, fams = original(inputs)
        X = X.copy()
        X[-1, 0] = float("nan")
        return X, names, sparse, fams

    analyst._assemble = _con_hueco
    assert await analyst.analyze_latest() is None
    assert "no se pronostica sobre un hueco" in rec.of("analysis.error")[-1]["detail"]


@pytest.mark.asyncio
async def test_una_matriz_que_cambio_de_forma_no_se_pronostica(
    listo, in_memory_engine
) -> None:
    """Cambiar las familias sin reajustar daría un número sobre otro modelo."""
    analyst, rec, _ = listo
    await analyst.start()

    original = analyst._assemble
    analyst._assemble = lambda inputs: (
        original(inputs)[0], ["otra_cosa"], set(), {}
    )
    assert await analyst.analyze_latest() is None
    assert "dejó de coincidir" in rec.of("analysis.error")[-1]["detail"]


@pytest.mark.asyncio
async def test_el_reajuste_se_programa_al_cumplirse_las_barras(
    listo, in_memory_engine
) -> None:
    analyst, rec, series = listo
    analyst._refit_every_bars = 2
    await analyst.start()

    t = int(series.open_time[-1])
    for k in range(1, 3):
        await analyst.on_closed_candle(_kline(t + k * TF_MS, close=float(series.close[-1])))

    assert analyst._refit_task is not None
    await analyst._refit_task
    assert analyst.bundle is not None
    assert analyst._bars_since_fit == 0  # el ajuste nuevo reinicia el contador
    await analyst.stop()


@pytest.mark.asyncio
async def test_stop_cancela_un_reajuste_en_curso(listo, in_memory_engine) -> None:
    analyst, _, _ = listo
    await analyst.start()

    async def _eterno() -> None:
        await asyncio.sleep(3600)

    analyst._refit_task = asyncio.create_task(_eterno())
    await analyst.stop()
    assert analyst._refit_task is None
