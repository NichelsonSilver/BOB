"""Tests del filtro de régimen.

Los dos que importan de verdad son los de no-lookahead: el umbral del filtro
sale de un cuantil, y un cuantil calculado sobre la muestra completa se ve
perfecto y es trampa. Si esos dos pasan, el resto es aritmética.
"""

from __future__ import annotations

import numpy as np
import pytest

from bob.models import riskfilter as rf
from bob.models.projection import DEFAULT_MMR, LeverageProfile, liquidation_price
from bob.paper.forward_eval import coverage_interval


# --------------------------------------------------------------------------- #
# Excursión adversa
# --------------------------------------------------------------------------- #
def test_mae_se_calcula_a_mano():
    # Entrada al open de la barra 1 = 100. Horizonte 2 → barras 1 y 2.
    open_ = np.array([99.0, 100.0, 101.0, 102.0, 103.0])
    high = np.array([99.0, 104.0, 106.0, 102.0, 103.0])
    low = np.array([99.0, 97.0, 95.0, 102.0, 103.0])

    exc = rf.adverse_excursion(open_, high, low, horizon=2)

    # long: el mínimo de low[1..2] es 95 → 1 − 95/100
    assert exc.long[0] == pytest.approx(0.05)
    # short: el máximo de high[1..2] es 106 → 106/100 − 1
    assert exc.short[0] == pytest.approx(0.06)
    assert exc.entry[0] == pytest.approx(100.0)
    assert exc.valid[0]


def test_mae_no_mira_mas_alla_del_horizonte():
    """Mutar el futuro posterior a i+H no puede cambiar el MAE de i."""
    rng = np.random.default_rng(7)
    n, h = 200, 16
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    open_ = close.copy()
    high = close * 1.001
    low = close * 0.999

    base = rf.adverse_excursion(open_, high, low, h)

    i = 50
    low2, high2, open2 = low.copy(), high.copy(), open_.copy()
    low2[i + h + 1 :] *= 0.5  # colapso posterior al horizonte
    high2[i + h + 1 :] *= 2.0
    mutated = rf.adverse_excursion(open2, high2, low2, h)

    assert mutated.long[i] == pytest.approx(base.long[i])
    assert mutated.short[i] == pytest.approx(base.short[i])


def test_mae_marca_invalidas_las_barras_del_borde():
    open_ = np.arange(10, dtype=float) + 100
    exc = rf.adverse_excursion(open_, open_ + 1, open_ - 1, horizon=4)
    # Se necesita open[i+1] y hasta i+4 → la última válida es i = 10-4-1 = 5.
    assert exc.valid[:6].all()
    assert not exc.valid[6:].any()


def test_mae_propaga_nan_del_origen():
    open_ = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
    high = np.array([100.0, 101.0, np.nan, 100.0, 100.0])
    low = np.full(5, 99.0)
    exc = rf.adverse_excursion(open_, high, low, horizon=2)
    assert not exc.valid[0]
    assert np.isnan(exc.short[0])


# --------------------------------------------------------------------------- #
# Liquidación
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("leverage", [5.0, 20.0, 50.0])
def test_distancia_de_liquidacion_coincide_con_projection(direction, leverage):
    """No puede divergir del número que el dashboard le muestra al usuario."""
    entry = 2500.0
    profile = LeverageProfile(leverage=leverage, maintenance_margin_rate=DEFAULT_MMR)
    liq = liquidation_price(entry, direction, profile)
    esperado = abs(liq - entry) / entry
    assert rf.liquidation_distance(leverage, direction) == pytest.approx(esperado)


def test_liquidacion_es_un_umbral_sobre_el_mae():
    d = rf.liquidation_distance(20.0, "long")
    mae = np.array([d * 0.99, d, d * 1.01, np.nan])
    got = rf.liquidates(mae, 20.0, "long")
    assert list(got) == [False, True, True, False]


def test_mas_leverage_liquida_mas_seguido():
    rng = np.random.default_rng(3)
    mae = np.abs(rng.normal(0, 0.02, 5000))
    counts = [rf.liquidates(mae, L, "long").sum() for L in (10, 20, 50)]
    assert counts[0] < counts[1] < counts[2]


# --------------------------------------------------------------------------- #
# Umbral causal — el test que impide la trampa
# --------------------------------------------------------------------------- #
def test_umbral_causal_usa_solo_el_pasado_estricto():
    rng = np.random.default_rng(11)
    v = rng.random(300)
    th = rf.causal_quantile_threshold(v, 0.8, min_warmup=50)
    for i in (50, 120, 299):
        assert th[i] == pytest.approx(np.quantile(v[:i], 0.8))


def test_umbral_causal_no_cambia_si_se_muta_el_futuro():
    rng = np.random.default_rng(12)
    v = rng.random(300)
    base = rf.causal_quantile_threshold(v, 0.8, min_warmup=50)

    i = 150
    v2 = v.copy()
    v2[i:] = 99.0  # el futuro se vuelve gigante
    mutated = rf.causal_quantile_threshold(v2, 0.8, min_warmup=50)

    np.testing.assert_allclose(mutated[:i], base[:i])


def test_umbral_causal_es_nan_en_el_warmup():
    th = rf.causal_quantile_threshold(np.arange(100.0), 0.9, min_warmup=30)
    assert np.isnan(th[:30]).all()
    assert np.isfinite(th[30:]).all()


def test_umbral_causal_valida_argumentos():
    with pytest.raises(ValueError):
        rf.causal_quantile_threshold(np.arange(10.0), 1.5, 2)
    with pytest.raises(ValueError):
        rf.causal_quantile_threshold(np.arange(10.0), 0.8, 0)


# --------------------------------------------------------------------------- #
# Lift
# --------------------------------------------------------------------------- #
def test_filtro_aleatorio_da_lift_uno():
    """El listón del pre-registro: rechazar al azar da exactamente 1.0."""
    rng = np.random.default_rng(5)
    n = 200_000
    events = rng.random(n) < 0.02
    rejected = rng.random(n) < 0.20
    res = rf.filter_lift(rejected, events)
    assert res.lift == pytest.approx(1.0, abs=0.05)


def test_filtro_perfecto_da_lift_inverso_a_la_tasa_de_rechazo():
    n = 1000
    events = np.zeros(n, dtype=bool)
    events[:50] = True
    rejected = np.zeros(n, dtype=bool)
    rejected[:200] = True  # rechaza el 20% e incluye todos los eventos
    res = rf.filter_lift(rejected, events)
    assert res.capture_rate == pytest.approx(1.0)
    assert res.lift == pytest.approx(5.0)
    assert res.beats_chance


def test_lift_reporta_el_costo_del_filtro():
    n = 100
    events = np.zeros(n, dtype=bool)
    events[:10] = True
    rejected = np.zeros(n, dtype=bool)
    rejected[:20] = True
    res = rf.filter_lift(rejected, events)
    # De las 90 barras que no liquidaban, rechazó 10.
    assert res.false_reject_rate == pytest.approx(10 / 90)
    assert res.n_events == 10
    assert res.n_events_rejected == 10


def test_lift_es_nan_sin_eventos():
    res = rf.filter_lift(np.ones(10, dtype=bool), np.zeros(10, dtype=bool))
    assert np.isnan(res.lift)


def test_lift_exige_largos_iguales():
    with pytest.raises(ValueError):
        rf.filter_lift(np.ones(5, dtype=bool), np.ones(4, dtype=bool))


# --------------------------------------------------------------------------- #
# Bootstrap por bloques
# --------------------------------------------------------------------------- #
def test_el_bootstrap_reproduce_el_de_la_fase_5():
    """Mismo objeto estadístico que `coverage_interval`, no uno parecido.

    Si los dos ICs del proyecto se calcularan con convenciones distintas, el
    forward y este reporte no serían comparables y nadie lo notaría.
    """
    rng = np.random.default_rng(1)
    hits = rng.random(500) < 0.9
    block, n_boot, seed = 16, 300, 0

    esperado = coverage_interval(hits, block, n_boot=n_boot, seed=seed)

    draws = []
    for idx in rf.block_index_samples(500, block, n_boot, seed, chunk=n_boot):
        draws.append(hits[idx].mean(axis=1))
    got = np.percentile(np.concatenate(draws), [2.5, 97.5])

    assert got[0] == pytest.approx(esperado[0])
    assert got[1] == pytest.approx(esperado[1])


def test_el_loteo_no_cambia_el_resultado():
    """`chunk` es memoria, no estadística: no puede mover una cifra del reporte."""
    a = np.concatenate(list(rf.block_index_samples(100, 8, 64, 0, chunk=64)))
    b = np.concatenate(list(rf.block_index_samples(100, 8, 64, 0, chunk=7)))
    np.testing.assert_array_equal(a, b)


def test_ic_del_lift_contiene_uno_para_un_filtro_aleatorio():
    rng = np.random.default_rng(9)
    n = 20_000
    events = rng.random(n) < 0.05
    rejected = rng.random(n) < 0.20
    lo, hi = rf.lift_interval(rejected, events, block=16, n_boot=500)
    assert lo < 1.0 < hi


def test_diferencia_pareada_contra_si_mismo_es_cero():
    rng = np.random.default_rng(13)
    n = 5_000
    events = rng.random(n) < 0.05
    rejected = rng.random(n) < 0.2
    lo, hi = rf.lift_difference_interval(rejected, rejected, events, block=16, n_boot=200)
    assert lo == pytest.approx(0.0)
    assert hi == pytest.approx(0.0)


def test_diferencia_pareada_exige_largos_iguales():
    with pytest.raises(ValueError):
        rf.lift_difference_interval(
            np.ones(5, dtype=bool), np.ones(5, dtype=bool), np.ones(4, dtype=bool), 2
        )


# --------------------------------------------------------------------------- #
# apply_filter
# --------------------------------------------------------------------------- #
def test_apply_filter_no_rechaza_donde_no_tiene_umbral():
    score = np.array([1.0, 5.0, 5.0, np.nan])
    threshold = np.array([np.nan, 2.0, 2.0, 2.0])
    eligible = np.array([True, True, False, True])
    got = rf.apply_filter(score, threshold, eligible)
    # [0] sin umbral, [2] no elegible, [3] sin score → ninguna se rechaza.
    assert list(got) == [False, True, False, False]


# --------------------------------------------------------------------------- #
# Direcciones agrupadas — la configuración confirmatoria del pre-registro
# --------------------------------------------------------------------------- #
def test_agrupar_direcciones_cuenta_los_dos_setups_de_la_barra():
    n = 100
    rejected = np.zeros(n, dtype=bool)
    rejected[:20] = True
    events = np.zeros((n, 2), dtype=bool)
    events[:5, 0] = True   # 5 liquidaciones long, todas en zona rechazada
    events[10:15, 1] = True  # 5 short, también rechazadas
    events[50:55, 0] = True  # 5 long fuera de la zona rechazada

    res = rf.filter_lift(rejected, events)
    assert res.n == n
    assert res.n_events == 15
    assert res.n_events_rejected == 10
    assert res.reject_rate == pytest.approx(0.20)
    assert res.capture_rate == pytest.approx(10 / 15)
    assert res.lift == pytest.approx((10 / 15) / 0.20)


def test_agrupado_de_una_columna_coincide_con_el_vector_plano():
    rng = np.random.default_rng(21)
    n = 2_000
    rejected = rng.random(n) < 0.3
    events = rng.random(n) < 0.05
    plano = rf.filter_lift(rejected, events)
    columna = rf.filter_lift(rejected, events[:, None])
    assert plano.lift == pytest.approx(columna.lift)
    assert plano.false_reject_rate == pytest.approx(columna.false_reject_rate)


def test_el_ic_agrupado_remuestrea_barras_y_no_setups():
    """La barra es el eje de bloques: las dos direcciones viajan juntas."""
    rng = np.random.default_rng(22)
    n = 5_000
    rejected = rng.random(n) < 0.2
    events = rng.random((n, 2)) < 0.05
    lo, hi = rf.lift_interval(rejected, events, block=16, n_boot=300)
    assert lo < 1.0 < hi  # filtro independiente de los eventos → lift 1


def test_events_con_forma_invalida_falla():
    with pytest.raises(ValueError):
        rf.filter_lift(np.ones(5, dtype=bool), np.ones((4, 2), dtype=bool))
