"""Tests del paper tracking — la medición forward de lo que sí pasó el gate.

El riesgo que cubren no es que el tracker se caiga: es que mida algo
levemente distinto de lo que midió el backtest y que la comparación
"forward vs backtest" —la razón de ser de la Fase 5— quede sin sentido.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from bob.db.models import CandleRecord, ForecastRecord
from bob.db.session import get_session
from bob.models.labeling import (
    BarrierConfig,
    forward_volatility,
    resolve_setup_path,
    triple_barrier_labels,
)
from bob.paper.tracker import (
    coverage_report,
    render_coverage,
    resolve_pending,
    resolve_record,
)

from .conftest import TF_MS, synthetic_series  # type: ignore[attr-defined]

BARRIER = BarrierConfig(horizon_bars=4, vol_window_bars=48)


# --------------------------------------------------------------------- #
# La convención compartida con el etiquetado
# --------------------------------------------------------------------- #


def test_resolve_setup_path_coincide_con_el_etiquetado_fila_por_fila() -> None:
    """La duplicación está permitida; la divergencia silenciosa no.

    `resolve_setup_path` reimplementa el recorrido de barreras que hace
    `triple_barrier_labels`, porque refactorizar ese bucle pondría en riesgo un
    gate que se reproduce bit a bit. El precio de esa decisión es este test:
    para cada barra etiquetada, las dos implementaciones tienen que coincidir
    en resolución, barras aguantadas y retorno neto.
    """
    series = synthetic_series(n=1500, seed=11)
    cfg = BarrierConfig(horizon_bars=12, vol_window_bars=48)

    for direction in ("long", "short"):
        labels = triple_barrier_labels(
            series.high, series.low, series.close, series.open, cfg, TF_MS, direction
        )
        idx = np.flatnonzero(labels.usable)
        assert idx.size > 500  # que el test realmente recorra casos
        comparados = 0
        for i in idx[::7]:  # muestreo: 1500 barras × 2 direcciones es de más
            res = resolve_setup_path(
                series.high[i + 1 : i + 1 + cfg.horizon_bars],
                series.low[i + 1 : i + 1 + cfg.horizon_bars],
                series.close[i + 1 : i + 1 + cfg.horizon_bars],
                entry_price=float(labels.entry_price[i]),
                tp_level=float(labels.tp_price[i]),
                sl_level=float(labels.sl_price[i]),
                direction=direction,
                config=cfg,
                timeframe_ms=TF_MS,
            )
            assert res.resolution == labels.resolution[i], f"barra {i}"
            assert res.bars_held == labels.touch_idx[i] - i, f"barra {i}"
            assert res.net_return == pytest.approx(
                labels.net_return[i], abs=1e-12
            ), f"barra {i}"
            comparados += 1
        assert comparados > 50


def test_el_empate_intrabarra_se_resuelve_contra_el_trader() -> None:
    """Si el high toca el TP y el low toca el SL en la misma vela, gana el SL."""
    high = np.array([110.0])
    low = np.array([90.0])
    close = np.array([100.0])
    res = resolve_setup_path(
        high, low, close, entry_price=100.0, tp_level=105.0, sl_level=95.0,
        direction="long", config=BARRIER, timeframe_ms=TF_MS,
    )
    assert res.status == "sl_hit"
    assert res.exit_price == 95.0


def test_sin_toque_expira_en_la_vertical() -> None:
    high = np.array([101.0, 101.0, 101.0, 101.0])
    low = np.array([99.0, 99.0, 99.0, 99.0])
    close = np.array([100.0, 100.5, 100.2, 100.7])
    res = resolve_setup_path(
        high, low, close, entry_price=100.0, tp_level=105.0, sl_level=95.0,
        direction="long", config=BARRIER, timeframe_ms=TF_MS,
    )
    assert res.status == "expired"
    assert res.bars_held == 4
    assert res.exit_price == 100.7


def test_resolve_setup_path_valida_sus_entradas() -> None:
    a = np.array([1.0])
    with pytest.raises(ValueError, match="direction"):
        resolve_setup_path(a, a, a, entry_price=1.0, tp_level=2.0, sl_level=0.5,
                           direction="arriba", config=BARRIER, timeframe_ms=TF_MS)
    with pytest.raises(ValueError, match="entrada"):
        resolve_setup_path(a, a, a, entry_price=0.0, tp_level=2.0, sl_level=0.5,
                           direction="long", config=BARRIER, timeframe_ms=TF_MS)


# --------------------------------------------------------------------- #
# Resolución de un registro
# --------------------------------------------------------------------- #


def _record(open_time: int, ref_price: float, *, cones=((0.20, -0.02, 0.02),),
            tp: float = 105.0, sl: float = 95.0) -> ForecastRecord:
    return ForecastRecord(
        forecast_id=f"TESTUSDT-15m-{open_time}",
        symbol="TESTUSDT",
        timeframe="15m",
        open_time=open_time,
        reference_price=str(ref_price),
        sigma_forecast=0.01,
        sigma_backward=0.011,
        horizon_bars=BARRIER.horizon_bars,
        cones_json=json.dumps(
            [
                {"alpha": a, "nominal": 1 - a, "ret_lo": lo, "ret_hi": hi,
                 "price_lo": ref_price * np.exp(lo), "price_hi": ref_price * np.exp(hi)}
                for a, lo, hi in cones
            ]
        ),
        projections_json=json.dumps(
            {
                "long": {"take_profit": tp, "stop_loss": sl, "net_ev_pct": 0.001,
                         "roe_pct": 0.001, "leverage": 1.0},
            }
        ),
    )


def _candles(start: int, closes: list[float], *, opens=None, step: int = TF_MS,
             skip: int = 0) -> list[CandleRecord]:
    out = []
    t = start
    for i, c in enumerate(closes):
        if skip and i == 1:
            t += step  # se salta una barra: hueco del feed
        o = opens[i] if opens else c
        out.append(
            CandleRecord(
                symbol="TESTUSDT", timeframe="15m", open_time=t,
                close_time=t + step - 1, open=str(o), high=str(max(o, c) + 0.5),
                low=str(min(o, c) - 0.5), close=str(c), volume="10",
                quote_volume="1000", taker_buy_volume="5", n_trades=10,
            )
        )
        t += step
    return out


def test_la_volatilidad_realizada_es_la_misma_del_etiquetado() -> None:
    """`resolve_record` debe reproducir `labeling.forward_volatility`.

    Si midiera la volatilidad de otra forma, el R² forward y el del backtest
    no serían comparables, que es exactamente lo que la fase quiere comparar.
    """
    closes = [100.0, 101.0, 100.5, 102.0, 101.5]  # la primera es la de decisión
    rec = _record(1_000_000, closes[0])
    candles = _candles(1_000_000 + TF_MS, closes[1:])
    out = resolve_record(rec, candles, BARRIER)

    esperado = forward_volatility(np.array(closes), BARRIER.horizon_bars)[0]
    assert out.realized_vol == pytest.approx(esperado, rel=1e-12)
    assert out.realized_return == pytest.approx(np.log(closes[-1] / closes[0]))


def test_la_entrada_se_mide_al_open_real_no_al_precio_de_referencia() -> None:
    """El hueco de apertura tiene que aparecer en el resultado, no desaparecer."""
    rec = _record(1_000_000, 100.0)
    # El precio abre 2% arriba de la referencia: quien entró se comió el gap.
    candles = _candles(1_000_000 + TF_MS, [102.0, 102.0, 102.0, 102.0],
                       opens=[102.0, 102.0, 102.0, 102.0])
    out = resolve_record(rec, candles, BARRIER)
    assert out.outcomes["long"]["entry_price"] == pytest.approx(102.0)
    # Contra la referencia habría sido ganancia; contra la entrada real, no.
    assert out.outcomes["long"]["gross_return"] < 0.005


def test_el_cono_registra_su_acierto_y_su_fallo() -> None:
    dentro = _record(1_000_000, 100.0, cones=((0.20, -0.05, 0.05),))
    out = resolve_record(dentro, _candles(1_000_000 + TF_MS, [100.0] * 4), BARRIER)
    assert out.cone_hits[0.20] is True

    fuera = _record(2_000_000, 100.0, cones=((0.20, -0.001, 0.001),))
    out2 = resolve_record(fuera, _candles(2_000_000 + TF_MS, [110.0] * 4), BARRIER)
    assert out2.cone_hits[0.20] is False


# --------------------------------------------------------------------- #
# El camino con DB
# --------------------------------------------------------------------- #


def test_un_horizonte_incompleto_sigue_abierto(in_memory_engine) -> None:
    with get_session() as s:
        s.add(_record(1_000_000, 100.0))
        for c in _candles(1_000_000 + TF_MS, [100.0, 101.0]):  # faltan 2
            s.add(c)
        s.commit()

    assert resolve_pending("TESTUSDT", "15m") == []
    with get_session() as s:
        rec = s.exec(_select_records()).first()
        assert rec.status == "open"


def test_un_horizonte_con_huecos_se_marca_gap_y_no_entra_en_la_cobertura(
    in_memory_engine,
) -> None:
    """Rellenar la vela faltante inflaría la cobertura del cono. Se descarta."""
    with get_session() as s:
        s.add(_record(1_000_000, 100.0))
        for c in _candles(1_000_000 + TF_MS, [100.0, 101.0, 100.5, 102.0], skip=1):
            s.add(c)
        s.commit()

    assert resolve_pending("TESTUSDT", "15m") == []
    rep = coverage_report("TESTUSDT", "15m")
    assert rep.n_gap == 1
    assert rep.n_resolved == 0


def test_resolver_escribe_el_resultado_y_no_repite(in_memory_engine) -> None:
    with get_session() as s:
        s.add(_record(1_000_000, 100.0))
        for c in _candles(1_000_000 + TF_MS, [100.0, 101.0, 100.5, 102.0]):
            s.add(c)
        s.commit()

    resueltos = resolve_pending("TESTUSDT", "15m")
    assert len(resueltos) == 1
    with get_session() as s:
        rec = s.exec(_select_records()).first()
        assert rec.status == "resolved"
        assert rec.realized_vol is not None
        assert rec.vol_ratio == pytest.approx(rec.realized_vol / rec.sigma_forecast)
        assert json.loads(rec.outcomes_json)["long"]["status"] in {
            "tp_hit",
            "sl_hit",
            "expired",
        }

    # Segunda pasada: ya no queda nada abierto.
    assert resolve_pending("TESTUSDT", "15m") == []


def test_el_tracker_realimenta_el_aci_del_cono_vivo(in_memory_engine) -> None:
    """Sin esta realimentación el cono en vivo se congela en el alpha del ajuste."""
    from bob.models.production import OnlineConformalCone

    class _Stub:
        gamma = 0.05

        def predict_interval(self, X):
            n = X.shape[0]
            return np.full(n, -0.001), np.full(n, 0.001)

    cone = OnlineConformalCone(model=_Stub(), alpha=0.20, gamma=0.05)  # type: ignore[arg-type]
    with get_session() as s:
        s.add(_record(1_000_000, 100.0, cones=((0.20, -0.001, 0.001),)))
        for c in _candles(1_000_000 + TF_MS, [110.0] * 4):  # se sale del cono
            s.add(c)
        s.commit()

    resolve_pending("TESTUSDT", "15m", cones={0.20: cone})
    assert cone.n_observed == 1
    assert cone.n_covered == 0
    assert cone.alpha_t < 0.20  # el fallo ensancha el próximo intervalo


def test_el_reporte_dice_cuando_no_hay_nada_medido(in_memory_engine) -> None:
    rep = coverage_report("TESTUSDT", "15m")
    assert rep.n_resolved == 0
    texto = render_coverage(rep)
    assert "no hay nada resuelto" in texto.lower()


def test_el_reporte_completo_cita_cobertura_y_ev(in_memory_engine) -> None:
    with get_session() as s:
        for k in range(6):
            t = 1_000_000 + k * 10 * TF_MS
            s.add(_record(t, 100.0, cones=((0.20, -0.05, 0.05),)))
            for c in _candles(t + TF_MS, [100.0, 101.0, 100.5, 100.2]):
                s.add(c)
        s.commit()

    resolve_pending("TESTUSDT", "15m")
    rep = coverage_report("TESTUSDT", "15m")
    assert rep.n_resolved == 6
    assert rep.volatility is not None
    assert 0.20 in rep.cones
    assert rep.cones[0.20].n == 6
    assert "long" in rep.setups

    texto = render_coverage(rep)
    assert "CONO CONFORMAL" in texto
    assert "pasó el gate (regla 2)" in texto
    # El dict serializable no revienta con métricas presentes.
    assert rep.to_dict()["n_resolved"] == 6


def _select_records():
    from sqlmodel import select

    return select(ForecastRecord)


# --------------------------------------------------------------------- #
# Loop y CLI
# --------------------------------------------------------------------- #


async def test_el_loop_resuelve_y_para_cuando_se_lo_pide(in_memory_engine) -> None:
    import asyncio

    from bob.paper.tracker import tracker_loop

    with get_session() as s:
        s.add(_record(1_000_000, 100.0))
        for c in _candles(1_000_000 + TF_MS, [100.0, 101.0, 100.5, 102.0]):
            s.add(c)
        s.commit()

    stop = asyncio.Event()
    task = asyncio.create_task(tracker_loop("TESTUSDT", "15m", 0.01, stop=stop))
    for _ in range(50):
        await asyncio.sleep(0.01)
        with get_session() as s:
            if s.exec(_select_records()).first().status == "resolved":
                break
    stop.set()
    await task

    with get_session() as s:
        assert s.exec(_select_records()).first().status == "resolved"


async def test_el_loop_pide_los_conos_en_cada_vuelta(in_memory_engine) -> None:
    """El analista reemplaza el bundle al reajustar: capturar los conos una vez
    dejaría el ACI realimentando objetos que ya no emiten."""
    import asyncio

    from bob.paper.tracker import tracker_loop

    llamadas: list[int] = []
    stop = asyncio.Event()

    def _provider():
        llamadas.append(1)
        if len(llamadas) >= 2:
            stop.set()
        return None

    await tracker_loop("TESTUSDT", "15m", 0.01, stop=stop, cones_provider=_provider)
    assert len(llamadas) >= 2


async def test_el_loop_sobrevive_a_un_fallo(in_memory_engine, monkeypatch) -> None:
    """Un tracker caído no puede tumbar el backend ni detenerse en el primer error."""
    import asyncio

    from bob.paper import tracker as mod

    vueltas: list[int] = []
    stop = asyncio.Event()

    def _explota(*a, **k):
        vueltas.append(1)
        if len(vueltas) >= 2:
            stop.set()
        raise RuntimeError("la DB no responde")

    monkeypatch.setattr(mod, "resolve_pending", _explota)
    await mod.tracker_loop("TESTUSDT", "15m", 0.01, stop=stop)
    assert len(vueltas) >= 2


def test_la_cli_reporta_sin_resolver_nada(in_memory_engine, capsys) -> None:
    from bob.paper.tracker import main

    with get_session() as s:
        s.add(_record(1_000_000, 100.0))
        for c in _candles(1_000_000 + TF_MS, [100.0, 101.0, 100.5, 102.0]):
            s.add(c)
        s.commit()

    assert main(["--symbol", "testusdt", "--report-only"]) == 0
    assert "PAPER TRACKING" in capsys.readouterr().out
    with get_session() as s:
        assert s.exec(_select_records()).first().status == "open"  # no tocó nada


def test_la_cli_resuelve_y_reporta(in_memory_engine, capsys) -> None:
    from bob.paper.tracker import main

    with get_session() as s:
        s.add(_record(1_000_000, 100.0))
        for c in _candles(1_000_000 + TF_MS, [100.0, 101.0, 100.5, 102.0]):
            s.add(c)
        s.commit()

    assert main(["--symbol", "TESTUSDT"]) == 0
    assert "VOLATILIDAD PRONOSTICADA" in capsys.readouterr().out
    with get_session() as s:
        assert s.exec(_select_records()).first().status == "resolved"
