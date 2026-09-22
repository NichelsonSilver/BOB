"""Tests del walk-forward purgado por fecha y del experimento del panel."""

from __future__ import annotations

import numpy as np
import pytest

from bob.models.panel_experiment import (
    PanelConfig,
    _evaluate_score,
    _paired_difference,
    _rankdata,
    _spearman,
    run_panel_experiment,
)
from bob.models.panel_validation import (
    DateSplit,
    assert_no_date_leakage,
    purged_walk_forward_by_date,
    rows_for_dates,
)
from bob.signals.cross_section import PanelFeatures

# --------------------------------------------------------------------------- #
# Splits por fecha
# --------------------------------------------------------------------------- #


def test_folds_no_comparten_fechas() -> None:
    splits = purged_walk_forward_by_date(1000, horizon=5, n_splits=4)
    for s in splits:
        assert np.intersect1d(s.train_dates, s.test_dates).size == 0


def test_el_train_termina_horizon_antes_del_test() -> None:
    """La purga tiene que abrir un hueco de al menos `horizon` fechas.

    Sin él, el label de la última fecha de train mira hasta dentro del test:
    es entrenar con el resultado que se está evaluando.
    """
    horizon = 7
    splits = purged_walk_forward_by_date(1000, horizon=horizon, n_splits=4)
    for s in splits:
        gap = int(s.test_dates.min()) - int(s.train_dates.max())
        assert gap > horizon, f"{s.name}: hueco de {gap} para horizonte {horizon}"
    assert_no_date_leakage(splits, horizon)


def test_assert_no_date_leakage_detecta_la_fuga() -> None:
    bad = [
        DateSplit(
            train_dates=np.arange(0, 100),
            test_dates=np.arange(101, 150),
            name="pegado",
        )
    ]
    with pytest.raises(AssertionError, match="toca el test"):
        assert_no_date_leakage(bad, horizon=10)


def test_splits_validan_parametros() -> None:
    with pytest.raises(ValueError):
        purged_walk_forward_by_date(100, horizon=5, n_splits=0)
    with pytest.raises(ValueError):
        purged_walk_forward_by_date(100, horizon=0)
    with pytest.raises(ValueError):
        purged_walk_forward_by_date(100, horizon=5, min_train_frac=1.5)
    with pytest.raises(ValueError, match="folds"):
        purged_walk_forward_by_date(10, horizon=1, n_splits=8, min_train_frac=0.9)


def test_rows_for_dates() -> None:
    date_idx = np.array([0, 0, 1, 1, 2])
    mask = rows_for_dates(date_idx, np.array([1, 2]))
    assert mask.tolist() == [False, False, True, True, True]


def test_split_rechaza_solapamiento() -> None:
    with pytest.raises(ValueError, match="comparten fechas"):
        DateSplit(np.array([1, 2, 3]), np.array([3, 4]), "malo")


# --------------------------------------------------------------------------- #
# Estadística del experimento
# --------------------------------------------------------------------------- #


def test_rankdata_promedia_empates() -> None:
    assert _rankdata(np.array([5.0, 5.0, 9.0])).tolist() == [1.5, 1.5, 3.0]


def test_spearman_captura_monotonia_no_lineal() -> None:
    x = np.arange(1.0, 21.0)
    assert _spearman(x, x**3) == pytest.approx(1.0)
    assert _spearman(x, -(x**3)) == pytest.approx(-1.0)


def test_spearman_sin_variacion_es_nan_no_cero() -> None:
    """Un score constante no tiene correlación *definida*; 0 sería inventarla."""
    assert np.isnan(_spearman(np.ones(10), np.arange(10.0)))
    assert np.isnan(_spearman(np.arange(2.0), np.arange(2.0)))


def test_evaluate_score_el_oraculo_da_ic_uno() -> None:
    rng = np.random.default_rng(0)
    n_dates, n_sym = 12, 30
    date_idx = np.repeat(np.arange(n_dates), n_sym)
    forward = rng.normal(size=n_dates * n_sym)
    ev = _evaluate_score("o", forward, forward, date_idx, np.arange(n_dates), 0.10)
    np.testing.assert_allclose(ev.ic, 1.0)
    assert ev.mean_spread_gross > 0


def test_evaluate_score_ruido_da_ic_cero() -> None:
    rng = np.random.default_rng(1)
    n_dates, n_sym = 200, 40
    date_idx = np.repeat(np.arange(n_dates), n_sym)
    forward = rng.normal(size=n_dates * n_sym)
    noise = rng.normal(size=n_dates * n_sym)
    ev = _evaluate_score("r", noise, forward, date_idx, np.arange(n_dates), 0.10)
    assert abs(ev.mean_ic) < 0.05


def test_paired_difference_alinea_por_fecha() -> None:
    a = _fake_eval("a", dates=np.array([1, 2, 3]), ic=np.array([0.2, 0.2, 0.2]))
    b = _fake_eval("b", dates=np.array([2, 3, 4]), ic=np.array([0.1, 0.1, 0.1]))
    mean, lo, hi = _paired_difference(a, b, block=1, n_boot=500, seed=0)
    assert mean == pytest.approx(0.1)
    assert lo <= mean <= hi


def test_paired_difference_sin_fechas_comunes() -> None:
    a = _fake_eval("a", np.array([1, 2]), np.array([0.1, 0.1]))
    b = _fake_eval("b", np.array([8, 9]), np.array([0.1, 0.1]))
    assert np.isnan(_paired_difference(a, b, 1, n_boot=100, seed=0)[0])


def _fake_eval(name: str, dates: np.ndarray, ic: np.ndarray):  # noqa: ANN202
    from bob.models.panel_experiment import ScoreEvaluation

    return ScoreEvaluation(
        name=name,
        dates=dates,
        ic=ic,
        spread_gross=np.zeros_like(ic),
        n_per_date=np.full(ic.size, 10),
    )


# --------------------------------------------------------------------------- #
# Experimento completo, sobre un panel sintético
# --------------------------------------------------------------------------- #


def _synthetic_panel(
    n_sym: int = 40, n_dates: int = 500, seed: int = 0, signal: float = 0.0
) -> tuple[PanelFeatures, np.ndarray, np.ndarray, np.ndarray]:
    """Panel de juguete con una señal transversal inyectable.

    Con `signal=0` el feature es ruido puro: el experimento debe salir **no
    viable**. Es el control que demuestra que la plomería no fabrica edge.
    """
    rng = np.random.default_rng(seed)
    feat = rng.normal(size=(n_sym, n_dates, 1))
    shock = rng.normal(scale=0.03, size=(n_sym, n_dates))
    # El feature de la fecha j mueve el retorno de j+1, que es el primero que
    # el setup captura: se entra al `open` de j+1. Inyectarlo en el retorno de
    # la propia fecha j lo pondría ANTES de la entrada —un retorno ya ocurrido—
    # y el control positivo mediría cero por construcción.
    fwd = shock.copy()
    fwd[:, 1:] += signal * feat[:, :-1, 0] * 0.03
    close = np.exp(np.cumsum(fwd, axis=1)) * 100.0
    open_ = close.copy()
    open_[:, 1:] = close[:, :-1]
    eligible = np.ones((n_sym, n_dates), dtype=bool)
    features = PanelFeatures(
        X=feat,
        names=["senal"],
        symbols=tuple(f"S{i}" for i in range(n_sym)),
        open_time=np.arange(n_dates, dtype=np.int64) * 86_400_000,
        valid=np.ones((n_sym, n_dates), dtype=bool),
    )
    return features, open_, close, eligible


def test_experimento_sobre_ruido_no_es_viable() -> None:
    """El control negativo: sin señal, el veredicto tiene que ser NO VIABLE.

    Si esto pasara, la maquinaria estaría fabricando edge y cualquier
    resultado positivo del panel real sería indistinguible de este.
    """
    features, open_, close, eligible = _synthetic_panel(signal=0.0)
    cfg = PanelConfig(horizon=3, n_splits=3, n_boot=400)
    result = run_panel_experiment(features, open_, close, eligible, cfg)

    assert not result.viable
    assert abs(result.model.mean_ic) < 0.08
    assert 0.40 < result.auc < 0.60


def test_experimento_detecta_una_senal_inyectada() -> None:
    """El control positivo: con señal fuerte, el IC tiene que aparecer.

    Sin este, el control negativo se pasaría con un experimento roto que
    devuelve ceros siempre.
    """
    features, open_, close, eligible = _synthetic_panel(signal=6.0, seed=7)
    cfg = PanelConfig(horizon=3, n_splits=3, n_boot=400)
    result = run_panel_experiment(features, open_, close, eligible, cfg)

    assert result.model.mean_ic > 0.2
    assert result.auc > 0.55
    assert result.oracle_capture > 0.0


def test_experimento_sin_filas_utilizables_falla_claro() -> None:
    features, open_, close, _ = _synthetic_panel(n_sym=5, n_dates=60)
    empty = np.zeros((5, 60), dtype=bool)
    with pytest.raises(ValueError, match="ninguna fila utilizable"):
        run_panel_experiment(features, open_, close, empty, PanelConfig(horizon=2))


def test_pair_cost_es_dos_patas() -> None:
    cfg = PanelConfig(leg_cost=0.001)
    assert cfg.pair_cost == pytest.approx(0.002)
