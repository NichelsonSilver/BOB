"""Filtro de régimen leído con stop: eventos, calibración y simulación."""

from __future__ import annotations

import numpy as np
import pytest

from bob.models import riskfilter as rf
from bob.models.projection import prob_touch
from bob.models.stopfilter import (
    bracket_outcomes,
    calibration,
    hit_rate_ratio,
    masked_mean_interval,
    predicted_touch,
    stop_events,
)


def _walk(n: int, seed: int = 0, vol: float = 0.003) -> tuple[np.ndarray, ...]:
    rng = np.random.default_rng(seed)
    c = 2500.0 * np.exp(np.cumsum(rng.normal(0.0, vol, n)))
    o = np.concatenate([[2500.0], c[:-1]])
    w = np.abs(rng.normal(0.0, vol / 2, (2, n)))
    h = np.maximum(o, c) * (1 + w[0])
    lo = np.minimum(o, c) * (1 - w[1])
    return o, h, lo, c


def test_un_stop_es_una_liquidacion_a_otra_distancia() -> None:
    """El evento de stop tiene que ser el mismo objeto que la liquidación de
    la Fase 5-bis: misma excursión, solo cambia el umbral."""
    o, h, lo, _ = _walk(3000)
    exc = rf.adverse_excursion(o, h, lo, 16)
    d_long = rf.liquidation_distance(20.0, "long")
    d_short = rf.liquidation_distance(20.0, "short")
    ev_l = stop_events(exc, d_long)[:, 0]
    ev_s = stop_events(exc, d_short)[:, 1]
    assert np.array_equal(ev_l, rf.liquidates(exc.long, 20.0, "long"))
    assert np.array_equal(ev_s, rf.liquidates(exc.short, 20.0, "short"))


def test_stop_events_nan_no_cuenta() -> None:
    o, h, lo, _ = _walk(100)
    exc = rf.adverse_excursion(o, h, lo, 16)
    ev = stop_events(exc, 0.001)
    assert not ev[~exc.valid].any()
    with pytest.raises(ValueError):
        stop_events(exc, 0.0)


def test_predicted_touch_es_la_funcion_de_la_calculadora() -> None:
    sig = np.array([0.01, np.nan, 0.0, 0.02])
    p = predicted_touch(sig, 0.01)
    assert p[0] == prob_touch(0.01, 0.01) and p[3] == prob_touch(0.01, 0.02)
    assert np.isnan(p[1]) and np.isnan(p[2])


def test_calibracion_sin_brecha_contiene_cero_y_con_brecha_la_detecta() -> None:
    rng = np.random.default_rng(3)
    n = 20_000
    pred = np.full(n, 0.4)
    mask = np.ones(n, dtype=bool)
    honest = rng.random((n, 2)) < 0.4
    biased = rng.random((n, 2)) < 0.5
    ok = calibration(pred, honest, mask, 16, label="x", n_boot=500)
    bad = calibration(pred, biased, mask, 16, label="x", n_boot=500)
    assert ok.gap_ci95[0] < 0 < ok.gap_ci95[1] and not ok.gap_excludes_zero
    assert bad.gap == pytest.approx(0.1, abs=0.01) and bad.gap_excludes_zero
    assert bad.ratio == pytest.approx(1.25, abs=0.03)


def test_calibracion_respeta_la_mascara() -> None:
    n = 1000
    pred = np.full(n, 0.2)
    ev = np.zeros((n, 2), dtype=bool)
    ev[:500] = True
    mask = np.zeros(n, dtype=bool)
    mask[:500] = True
    c = calibration(pred, ev, mask, 16, label="x", n_boot=200)
    assert c.n_bars == 500 and c.observed == 1.0 and c.gap == pytest.approx(0.8)


def test_hit_rate_ratio() -> None:
    ev = np.zeros((10, 2), dtype=bool)
    ev[:5, 0] = True  # mitad de setups en barras marcadas
    flag = np.zeros(10, dtype=bool)
    flag[:5] = True
    eligible = np.ones(10, dtype=bool)
    assert np.isnan(hit_rate_ratio(ev, flag, eligible))  # tasa normal = 0
    ev[5, 0] = True
    assert hit_rate_ratio(ev, flag, eligible) == pytest.approx(0.5 / 0.1)


def test_bracket_outcomes_caso_conocido() -> None:
    # Entra a 100; la barra siguiente baja a 98,9: stop a 1% salta → −1 R − costo.
    o = np.array([100.0, 100.0, 99.5, 99.0, 99.0])
    h = np.array([100.0, 100.2, 99.8, 99.2, 99.1])
    lo = np.array([99.9, 99.6, 98.9, 98.8, 98.9])
    c = np.array([100.0, 99.7, 99.0, 99.0, 99.0])
    out = bracket_outcomes(o, h, lo, c, 0.01, 2.0, 2, 0.001)
    assert out[0, 0] == pytest.approx(-1.0 - 0.1)
    # El short no toca stop (101) ni objetivo (98): sale por tiempo al cierre 99.
    assert out[0, 1] == pytest.approx((0.01 - 0.001) / 0.01)
    assert np.isnan(out[-1]).all()


def test_bracket_en_paseo_aleatorio_pierde_el_costo() -> None:
    """Sin edge, la expectancy es ≈ −costo/riesgo en R, por álgebra.

    No exactamente: las convenciones conservadoras del simulador (si una vela
    toca stop y objetivo, cuenta el stop) restan ~0,01 R más. El test fija las
    dos cosas: el bruto queda apenas por debajo de 0 y nunca por encima.
    """
    o, h, lo, c = _walk(30_000, seed=9)
    net = bracket_outcomes(o, h, lo, c, 0.01, 2.0, 16, 0.0014)
    gross = bracket_outcomes(o, h, lo, c, 0.01, 2.0, 16, 0.0)
    everything = np.ones(c.size, dtype=bool)
    g_mean, _, g_hi = masked_mean_interval(gross, everything, 16, n_boot=500)
    n_mean, _, _ = masked_mean_interval(net, everything, 16, n_boot=500)
    assert -0.03 < g_mean <= 0.0 and g_hi < 0.02
    assert n_mean == pytest.approx(g_mean - 0.14, abs=1e-9)


def test_masked_mean_interval_vacio() -> None:
    v = np.full((10, 2), np.nan)
    assert all(np.isnan(x) for x in masked_mean_interval(v, np.ones(10, bool), 4))
