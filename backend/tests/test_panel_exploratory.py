"""Tests de las variantes exploratorias declaradas en el pre-registro §4.

Son exploratorias, no confirmatorias — pero el código que las produce tiene
que ser tan correcto como el de la primaria, o el reporte diría cosas falsas
sobre el efecto de cada variante.
"""

from __future__ import annotations

import numpy as np
import pytest

from bob.models.forecast import BarrierProbabilityModel
from bob.models.panel_experiment import PanelConfig, run_panel_experiment
from bob.signals.cross_section import panel_uniqueness_weights, relative_target

from .test_panel_validation import _synthetic_panel

# --------------------------------------------------------------------------- #
# Target por tercil
# --------------------------------------------------------------------------- #


def test_tercil_deja_el_medio_sin_etiqueta() -> None:
    """El tercio del medio no se etiqueta: es la diferencia con la mediana."""
    forward = np.arange(9, dtype=np.float64).reshape(9, 1)
    y, _ = relative_target(forward, cut="tercile")
    labeled = y[:, 0] >= 0
    assert labeled.sum() == 6, "deberían quedar 6 de 9 filas"
    assert (y[:3, 0] == 0).all(), "el tercil inferior es 0"
    assert (y[6:, 0] == 1).all(), "el tercil superior es 1"
    assert (y[3:6, 0] == -1).all(), "el medio queda sin etiqueta"


def test_tercil_mantiene_tasa_base_un_medio() -> None:
    rng = np.random.default_rng(0)
    forward = rng.normal(size=(60, 40))
    y, _ = relative_target(forward, cut="tercile")
    ok = y >= 0
    assert float(y[ok].mean()) == pytest.approx(0.5, abs=0.02)


def test_el_exceso_se_mide_contra_la_mediana_en_los_dos_cortes() -> None:
    """El spread del par es la misma magnitud, cambie o no el corte.

    Si el exceso siguiera al corte, los spreads de las variantes no serían
    comparables entre sí y la tabla exploratoria mentiría por construcción.
    """
    rng = np.random.default_rng(1)
    forward = rng.normal(size=(30, 12))
    _, e_med = relative_target(forward, cut="median")
    _, e_ter = relative_target(forward, cut="tercile")
    np.testing.assert_allclose(e_med, e_ter, equal_nan=True)


def test_corte_invalido_falla_temprano() -> None:
    with pytest.raises(ValueError, match="cut debe ser"):
        relative_target(np.zeros((5, 2)), cut="cuartil")


# --------------------------------------------------------------------------- #
# Pesos por unicidad
# --------------------------------------------------------------------------- #


def test_unicidad_se_calcula_por_simbolo_y_no_mezclando() -> None:
    """Dos símbolos con las mismas fechas deben recibir los mismos pesos.

    Si la concurrencia se contara sobre el panel entero, tener dos símbolos
    en vez de uno partiría los pesos a la mitad — y estaría midiendo cuántos
    símbolos cotizan ese día, no cuánto se solapan los labels en el tiempo.
    """
    dates = np.arange(20)
    sym = np.concatenate([np.zeros(20, dtype=np.int64), np.ones(20, dtype=np.int64)])
    dat = np.concatenate([dates, dates])
    w = panel_uniqueness_weights(sym, dat, horizon=5, n_dates=20)
    np.testing.assert_allclose(w[:20], w[20:])


def test_unicidad_tiene_media_uno() -> None:
    sym = np.repeat(np.arange(5), 40)
    dat = np.tile(np.arange(40), 5)
    w = panel_uniqueness_weights(sym, dat, horizon=5, n_dates=40)
    assert float(w.mean()) == pytest.approx(1.0)
    assert (w > 0).all()


def test_unicidad_con_horizonte_fijo_es_casi_constante() -> None:
    """Fija por qué esta exploratoria tiene efecto chico POR CONSTRUCCIÓN.

    Con horizonte fijo la concurrencia es H en casi todas las fechas, así que
    el peso sale casi uniforme y un peso uniforme no cambia un boosting. La
    variación vive solo en los bordes de la vida del símbolo. Sin este test,
    un resultado exploratorio nulo se leería como "no aporta" en vez de como
    "no podía aportar".
    """
    sym = np.zeros(200, dtype=np.int64)
    dat = np.arange(200)
    w = panel_uniqueness_weights(sym, dat, horizon=5, n_dates=200)
    interior = w[10:-10]
    assert interior.std() / interior.mean() < 0.02


def test_unicidad_ignora_simbolos_de_una_sola_fila() -> None:
    sym = np.array([0, 1, 1], dtype=np.int64)
    dat = np.array([0, 0, 1], dtype=np.int64)
    w = panel_uniqueness_weights(sym, dat, horizon=2, n_dates=2)
    assert np.isfinite(w).all() and (w > 0).all()


# --------------------------------------------------------------------------- #
# XGBoost como clasificador
# --------------------------------------------------------------------------- #


def test_xgb_clasificador_entrena_y_calibra() -> None:
    rng = np.random.default_rng(3)
    n = 800
    X = rng.normal(size=(n, 4))
    y = (X[:, 0] + rng.normal(scale=0.5, size=n) > 0).astype(np.int64)
    span = np.minimum(np.arange(n) + 5, n - 1)
    train = np.arange(600)

    model = BarrierProbabilityModel(kind="xgb", seed=0).fit(X, y, train, span)
    p = model.predict_proba(X[600:])
    assert p.shape == (200,)
    assert ((p >= 0.0) & (p <= 1.0)).all()
    # Debe distinguir algo sobre una señal fuerte: si no, el cableado está mal.
    assert p[X[600:, 0] > 1.0].mean() > p[X[600:, 0] < -1.0].mean()


def test_xgb_y_gbm_son_estimadores_distintos() -> None:
    """Si dieran lo mismo, la exploratoria no estaría midiendo nada."""
    rng = np.random.default_rng(4)
    n = 600
    X = rng.normal(size=(n, 3))
    y = (X[:, 0] + rng.normal(scale=0.8, size=n) > 0).astype(np.int64)
    span = np.minimum(np.arange(n) + 5, n - 1)
    train = np.arange(450)

    a = BarrierProbabilityModel(kind="gbm", seed=0).fit(X, y, train, span)
    b = BarrierProbabilityModel(kind="xgb", seed=0).fit(X, y, train, span)
    assert not np.allclose(a.predict_proba(X[450:]), b.predict_proba(X[450:]))


# --------------------------------------------------------------------------- #
# Integración: las variantes corren end-to-end
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "overrides",
    [
        {"target_cut": "tercile"},
        {"uniqueness_weights": True},
        {"model_kind": "xgb"},
        {"portfolio_quantile": 0.20},
    ],
)
def test_variantes_corren_end_to_end(overrides: dict[str, object]) -> None:
    features, open_, close, eligible = _synthetic_panel(signal=0.0, n_dates=400)
    cfg = PanelConfig(horizon=3, n_splits=3, n_boot=300, **overrides)  # type: ignore[arg-type]
    result = run_panel_experiment(features, open_, close, eligible, cfg)
    assert result.n_rows > 0
    assert np.isfinite(result.auc)
    # Sigue siendo ruido: ninguna variante puede fabricar edge.
    assert not result.viable


# --------------------------------------------------------------------------- #
# Comparador de variantes
# --------------------------------------------------------------------------- #


def _write_artifact(directory, variant: str, *, viable: bool, ic: float):  # noqa: ANN001, ANN202
    import json

    payload = {
        "n_rows": 1000,
        "n_features": 111,
        "auc": 0.56,
        "bss": 0.01,
        "model_mean_ic": ic,
        "model_spread_net": 0.014,
        "oracle_capture": 0.05,
        "turnover": 0.44,
        "intervals": {"spread_net": [0.009, 0.018]},
        "criteria": {"viable": viable},
    }
    path = directory / f"panel-{variant}-20260923120000.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_comparador_separa_primaria_placebo_y_exploratorias(tmp_path) -> None:  # noqa: ANN001
    from bob.backtest.panel_compare import load_runs, render_comparison

    _write_artifact(tmp_path, "1d-H5-xs", viable=True, ic=0.107)
    _write_artifact(tmp_path, "1d-H10-xs", viable=True, ic=0.112)
    _write_artifact(tmp_path, "1d-H5-xs-PLACEBO", viable=False, ic=0.002)

    runs = load_runs(tmp_path)
    assert len(runs) == 3
    assert sum(r.is_primary for r in runs) == 1
    assert sum(r.is_placebo for r in runs) == 1

    text = render_comparison(runs)
    assert "PRIMARIA" in text and "PLACEBO" in text
    assert "con los TRES criterios       : 2" in text
    assert "placebo NO viable (debe ser) : True" in text


def test_comparador_toma_el_run_mas_reciente_por_variante(tmp_path) -> None:  # noqa: ANN001
    """Un run viejo de la misma variante refleja código que ya no existe."""
    import json

    from bob.backtest.panel_compare import load_runs

    for stamp, ic in (("20260101120000", 0.01), ("20260923120000", 0.99)):
        path = tmp_path / f"panel-1d-H5-xs-{stamp}.json"
        path.write_text(json.dumps({"model_mean_ic": ic}), encoding="utf-8")

    runs = load_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0].get("model_mean_ic") == pytest.approx(0.99)


def test_comparador_ignora_json_corrupto(tmp_path) -> None:  # noqa: ANN001
    from bob.backtest.panel_compare import load_runs

    (tmp_path / "panel-roto-20260923120000.json").write_text("{no es json", encoding="utf-8")
    _write_artifact(tmp_path, "1d-H5-xs", viable=True, ic=0.1)
    assert len(load_runs(tmp_path)) == 1
