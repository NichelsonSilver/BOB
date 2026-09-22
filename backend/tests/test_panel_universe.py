"""Tests de la regla de pertenencia y de las features transversales.

Los que importan son los de **causalidad**: fallan contra una implementación
que mira el futuro, que es el único bug capaz de invalidar la Fase 10 entera
sin que nada más se rompa.
"""

from __future__ import annotations

import numpy as np
import pytest

from bob.signals.cross_section import (
    cross_sectional_rank,
    forward_path_return,
    market_factor,
    panel_log_returns,
    relative_target,
    rolling_beta,
)
from bob.signals.universe import (
    UniverseRule,
    eligibility_mask,
    prior_history,
    trailing_dollar_volume,
)

# --------------------------------------------------------------------------- #
# Regla de pertenencia
# --------------------------------------------------------------------------- #


def test_prior_history_no_cuenta_la_barra_actual() -> None:
    """La barra de hoy no puede contar para decidir si hoy soy elegible."""
    observed = np.array([[True, True, True, True]])
    assert prior_history(observed).tolist() == [[0, 1, 2, 3]]


def test_trailing_volume_ignora_la_fecha_actual() -> None:
    qvol = np.array([[1.0, 2.0, 3.0, 1000.0]])
    out = trailing_dollar_volume(qvol, lookback=2)
    # En j=3 la ventana es [2.0, 3.0]: el 1000 de hoy NO entra.
    assert out[0, 3] == pytest.approx(2.5)
    assert np.isnan(out[0, 0])


def test_trailing_volume_no_confunde_sin_dato_con_volumen_cero() -> None:
    qvol = np.array([[np.nan, np.nan, 10.0, 10.0]])
    observed = np.array([[False, False, True, True]])
    out = trailing_dollar_volume(qvol, lookback=3, observed=observed)
    # En j=3 la ventana tiene un solo dato real: la mediana es 10, no 3,33.
    assert out[0, 3] == pytest.approx(10.0)
    assert np.isnan(out[0, 1])


def test_eligibilidad_no_mira_el_futuro() -> None:
    """EL test de la fase: mutar el futuro no puede cambiar el pasado.

    El fixture está construido para que el corte de liquidez sea **vinculante**
    y la mutación pueda voltearlo. El símbolo está seco en la primera mitad y
    líquido en la segunda: una implementación que calcule el corte sobre la
    muestra completa lo declara elegible **desde el día uno**, porque el
    volumen que todavía no ocurrió le levanta la mediana.

    Ese es el lookahead de la regla 5 en su forma de panel: no falla nada, no
    deja NaN, y el resultado sale mejor de lo que es. Con un fixture donde el
    filtro nunca muerde, este test pasa contra código roto y no vale nada —
    verificado.
    """
    rng = np.random.default_rng(0)
    close = 100.0 + np.cumsum(rng.normal(size=(2, 300)), axis=1)
    qvol = np.empty((2, 300))
    qvol[:, :150] = 1e6  # seco: por debajo del corte de 5e6
    qvol[:, 150:] = 5e7  # líquido
    rule = UniverseRule(min_history_bars=30, min_dollar_volume=5e6, volume_lookback=10)

    base = eligibility_mask(close, qvol, rule)
    assert not base[:, :150].any(), "el fixture no discrimina: el filtro no muerde"
    assert base[:, 165:].all()

    close2, qvol2 = close.copy(), qvol.copy()
    close2[:, 200:] *= 7.0
    qvol2[:, 200:] = 1e12
    mutated = eligibility_mask(close2, qvol2, rule)

    assert np.array_equal(base[:, :200], mutated[:, :200])


def test_eligibilidad_rechaza_lo_iliquido_y_lo_joven() -> None:
    close = np.full((2, 100), 100.0)
    qvol = np.full((2, 100), 1e7)
    qvol[1] = 1e3  # el segundo símbolo no tiene volumen
    rule = UniverseRule(min_history_bars=50, min_dollar_volume=5e6, volume_lookback=10)
    mask = eligibility_mask(close, qvol, rule)

    assert not mask[0, :50].any(), "no puede ser elegible antes de tener historia"
    assert mask[0, 60:].all()
    assert not mask[1].any(), "sin volumen nunca entra"


def test_eligibilidad_valida_formas() -> None:
    with pytest.raises(ValueError):
        eligibility_mask(np.zeros((2, 3)), np.zeros((2, 4)))
    with pytest.raises(ValueError):
        eligibility_mask(np.zeros(3), np.zeros(3))


def test_universe_rule_valida_parametros() -> None:
    with pytest.raises(ValueError):
        UniverseRule(min_history_bars=0)
    with pytest.raises(ValueError):
        UniverseRule(volume_lookback=0)
    with pytest.raises(ValueError):
        UniverseRule(min_dollar_volume=-1.0)


# --------------------------------------------------------------------------- #
# Transversales
# --------------------------------------------------------------------------- #


def test_rank_transversal_usa_solo_la_fecha() -> None:
    values = np.array([[1.0, 9.0], [2.0, 8.0], [3.0, 7.0]])
    ranks = cross_sectional_rank(values)
    # Columna 0 ascendente, columna 1 descendente: el rango se invierte.
    assert ranks[0, 0] < ranks[1, 0] < ranks[2, 0]
    assert ranks[0, 1] > ranks[1, 1] > ranks[2, 1]
    # Extremos abiertos: nunca 0 ni 1.
    assert ranks.min() > 0.0 and ranks.max() < 1.0


def test_rank_transversal_promedia_empates() -> None:
    values = np.array([[5.0], [5.0], [9.0]])
    ranks = cross_sectional_rank(values)
    assert ranks[0, 0] == pytest.approx(ranks[1, 0])
    assert ranks[2, 0] > ranks[0, 0]


def test_rank_transversal_ignora_no_elegibles() -> None:
    values = np.array([[1.0], [2.0], [3.0]])
    eligible = np.array([[True], [True], [False]])
    ranks = cross_sectional_rank(values, eligible)
    assert np.isnan(ranks[2, 0])
    assert np.isfinite(ranks[0, 0]) and np.isfinite(ranks[1, 0])


def test_factor_de_mercado_es_la_mediana() -> None:
    returns = np.array([[0.01], [0.02], [0.30]])
    # La mediana resiste el outlier; una media daría 0.11.
    assert market_factor(returns)[0] == pytest.approx(0.02)


def test_log_returns_no_puentea_huecos() -> None:
    close = np.array([[100.0, np.nan, 110.0]])
    r = panel_log_returns(close)
    assert np.isnan(r[0, 1]) and np.isnan(r[0, 2]), "un hueco no genera retorno"


def test_rolling_beta_es_causal() -> None:
    rng = np.random.default_rng(1)
    factor = rng.normal(scale=0.02, size=200)
    returns = np.vstack([1.5 * factor, 0.5 * factor]) + rng.normal(scale=1e-4, size=(2, 200))
    beta = rolling_beta(returns, factor, window=60)
    assert beta[0, 150] == pytest.approx(1.5, abs=0.1)
    assert beta[1, 150] == pytest.approx(0.5, abs=0.1)

    mutated = returns.copy()
    mutated[:, 160:] *= 20.0
    beta2 = rolling_beta(mutated, factor, window=60)
    np.testing.assert_allclose(beta[:, :160], beta2[:, :160], equal_nan=True)


def test_rolling_beta_valida_ventana() -> None:
    with pytest.raises(ValueError):
        rolling_beta(np.zeros((2, 10)), np.zeros(10), window=1)


# --------------------------------------------------------------------------- #
# Target
# --------------------------------------------------------------------------- #


def test_forward_path_entra_al_open_siguiente() -> None:
    open_ = np.array([[10.0, 20.0, 30.0, 40.0]])
    close = np.array([[11.0, 22.0, 33.0, 44.0]])
    ret, _ = forward_path_return(open_, close, horizon=2)
    # En j=0: entra al open[1]=20, sale al close[2]=33.
    assert ret[0, 0] == pytest.approx(np.log(33.0 / 20.0))


def test_forward_path_resuelve_al_ultimo_precio_si_se_deslista() -> None:
    """Un símbolo que muere dentro del horizonte NO se descarta.

    Descartarlo reintroduciría el sesgo de supervivencia: lo que se muere es
    justo lo que cae, y borrarlo convierte una pérdida real en dato faltante.
    """
    open_ = np.array([[10.0, 20.0, np.nan, np.nan]])
    close = np.array([[11.0, 5.0, np.nan, np.nan]])
    ret, delisted = forward_path_return(open_, close, horizon=3)
    assert ret[0, 0] == pytest.approx(np.log(5.0 / 20.0))
    assert ret[0, 0] < 0, "la caída tiene que quedar registrada como pérdida"
    assert delisted[0, 0]


def test_forward_path_valida_horizonte() -> None:
    with pytest.raises(ValueError):
        forward_path_return(np.zeros((1, 5)), np.zeros((1, 5)), horizon=0)


def test_target_relativo_tiene_tasa_base_un_medio() -> None:
    rng = np.random.default_rng(3)
    forward = rng.normal(size=(50, 40))
    y, excess = relative_target(forward)
    usable = y >= 0
    assert usable.sum() > 0
    # Con S par la mediana parte exactamente por la mitad.
    assert float(y[usable].mean()) == pytest.approx(0.5, abs=0.02)
    np.testing.assert_allclose(np.sign(excess[usable]), np.where(y[usable] == 1, 1.0, -1.0))


def test_target_relativo_cancela_el_factor_comun() -> None:
    """Sumarle lo mismo a todos los símbolos de una fecha no cambia el target."""
    rng = np.random.default_rng(4)
    forward = rng.normal(size=(20, 10))
    y1, _ = relative_target(forward)
    y2, _ = relative_target(forward + 0.15)  # un día verde para todos
    np.testing.assert_array_equal(y1, y2)


def test_target_sin_seccion_suficiente_queda_sin_etiqueta() -> None:
    forward = np.array([[0.1], [0.2]])  # solo 2 símbolos
    y, _ = relative_target(forward)
    assert (y == -1).all(), "una mediana de dos no define arriba ni abajo"
