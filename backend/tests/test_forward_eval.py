"""Tests de los baselines forward y del intervalo por bloques.

Lo que protegen no es que el reporte imprima: es que sus dos números
principales no se lean al revés. Sin baselines, un R² que cae por muestra
corta se confunde con un modelo degradado; sin corregir por solapamiento, la
cobertura del cono se lee con un error estándar cuatro veces más chico del
real.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from bob.paper import forward_eval as fe
from bob.paper.tracker import CoverageReport, render_coverage, write_artifact

from .conftest import TF_MS, synthetic_series  # type: ignore[attr-defined]


# --------------------------------------------------------------------- #
# Baselines
# --------------------------------------------------------------------- #


def test_los_baselines_no_miran_el_futuro() -> None:
    """La invariante de la regla 5, aplicada a los baselines forward.

    Mutar las velas POSTERIORES a la última barra evaluada no puede cambiar
    ninguna predicción. Es donde un baseline mal alineado se escondería mejor:
    un GARCH ajustado sobre la serie entera o un HAR con el target corrido
    darían métricas hermosas e irreproducibles en vivo.
    """
    series = synthetic_series(n=2000, seed=3)
    close = np.asarray(series.close, dtype=np.float64)
    idx = np.arange(1500, 1600, dtype=np.int64)

    antes = fe.baseline_predictions(close, idx, horizon=16, timeframe_ms=TF_MS)

    mutada = close.copy()
    mutada[1601:] *= 3.0  # el futuro de la última barra evaluada, irreconocible
    despues = fe.baseline_predictions(mutada, idx, horizon=16, timeframe_ms=TF_MS)

    for name in ("ewma", "garch", "har"):
        np.testing.assert_allclose(antes[name], despues[name], rtol=0, atol=0)


def test_los_baselines_devuelven_uno_por_barra_y_son_positivos() -> None:
    series = synthetic_series(n=1200, seed=5)
    close = np.asarray(series.close, dtype=np.float64)
    idx = np.arange(900, 1000, dtype=np.int64)

    preds = fe.baseline_predictions(close, idx, horizon=16, timeframe_ms=TF_MS)

    assert set(preds) == {"ewma", "garch", "har"}
    for name, v in preds.items():
        assert v.shape == idx.shape, name
        assert np.all(np.isfinite(v)), name
        assert np.all(v > 0.0), name  # son volatilidades


def test_sin_indices_es_error_explicito() -> None:
    close = np.asarray(synthetic_series(n=300, seed=1).close, dtype=np.float64)
    with pytest.raises(ValueError):
        fe.baseline_predictions(close, np.array([], dtype=np.int64), 16, TF_MS)


# --------------------------------------------------------------------- #
# Intervalo por bloques
# --------------------------------------------------------------------- #


def test_el_intervalo_es_determinista_con_la_misma_semilla() -> None:
    """El artefacto tiene que poder reproducirse; un IC aleatorio no lo sería."""
    rng = np.random.default_rng(0)
    hits = rng.random(300) < 0.9
    a = fe.coverage_interval(hits, block=16, n_boot=2000, seed=7)
    b = fe.coverage_interval(hits, block=16, n_boot=2000, seed=7)
    assert a == b


def test_el_bootstrap_por_bloques_solo_ensancha_si_hay_dependencia() -> None:
    """Los dos lados de la corrección, que son igual de importantes.

    Con aciertos i.i.d. el bootstrap por bloques **no** debe ensanchar nada:
    si lo hiciera, estaría inflando el IC por construcción y volviendo
    inconcluyente cualquier medición. Con aciertos correlacionados en rachas
    —que es lo que produce el solapamiento de horizontes, donde dos
    pronósticos consecutivos comparten 15 de 16 barras— sí tiene que
    ensanchar, porque ahí el IC i.i.d. está subestimando el error.
    """
    rng = np.random.default_rng(1)

    iid = rng.random(320) < 0.9
    ancho_ind = np.subtract(*reversed(fe.coverage_interval(iid, 1, n_boot=4000, seed=0)))
    ancho_dep = np.subtract(*reversed(fe.coverage_interval(iid, 16, n_boot=4000, seed=0)))
    assert ancho_dep == pytest.approx(ancho_ind, abs=0.03)

    # Rachas: el acierto se decide una vez por bloque de 16, no barra a barra.
    rachas = np.repeat(rng.random(20) < 0.9, 16)
    ancho_ind = np.subtract(*reversed(fe.coverage_interval(rachas, 1, n_boot=4000, seed=0)))
    ancho_dep = np.subtract(*reversed(fe.coverage_interval(rachas, 16, n_boot=4000, seed=0)))
    assert ancho_dep > ancho_ind * 1.5


def test_cobertura_perfecta_da_intervalo_degenerado() -> None:
    hits = np.ones(64, dtype=bool)
    lo, hi = fe.coverage_interval(hits, block=16, n_boot=500, seed=0)
    assert lo == pytest.approx(1.0) and hi == pytest.approx(1.0)


def test_muestra_vacia_no_revienta() -> None:
    lo, hi = fe.coverage_interval(np.array([], dtype=bool), block=16)
    assert np.isnan(lo) and np.isnan(hi)


def test_bloques_efectivos() -> None:
    assert fe.effective_blocks(288, 16) == pytest.approx(18.0)
    assert fe.effective_blocks(10, 0) == pytest.approx(10.0)  # no divide por cero


# --------------------------------------------------------------------- #
# Artefacto
# --------------------------------------------------------------------- #


def test_el_artefacto_escribe_txt_y_json_verificables(tmp_path) -> None:
    """El veredicto de la fase tiene que salir de un archivo, no de un resumen.

    Misma razón que los artefactos del gate: un número escrito a mano no se
    puede volver a verificar. El `.json` debe traer las cifras crudas, no solo
    el texto renderizado.
    """
    report = CoverageReport(
        symbol="TESTUSDT",
        timeframe="15m",
        n_resolved=288,
        n_open=16,
        n_gap=0,
        date_from="2026-08-31 15:30",
        date_to="2026-09-04 04:45",
        n_blocks=18.0,
        model_versions=["bob-forecast-0.1.0+vol=gbm"],
        cone_ci={0.05: (0.795, 0.976)},
    )
    text = render_coverage(report)
    paths = write_artifact(report, text, str(tmp_path))

    assert len(paths) == 2
    txt, js = paths
    assert txt.exists() and js.exists()
    assert "n288" in txt.name  # el n identifica el corte
    assert txt.read_text(encoding="utf-8").startswith("=" * 72)

    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["n_resolved"] == 288
    assert data["model_versions"] == ["bob-forecast-0.1.0+vol=gbm"]
    assert data["cone_ci"]["0.05"] == [0.795, 0.976]


def test_una_muestra_mixta_se_marca_en_el_reporte() -> None:
    """Dos model_version promediados en silencio invalidan la comparación."""
    report = CoverageReport(
        symbol="TESTUSDT", timeframe="15m", n_resolved=10, n_open=0, n_gap=0,
        date_from="-", date_to="-",
        model_versions=["bob-forecast-0.1.0", "bob-forecast-0.1.0+vol=gbm"],
    )
    assert "MUESTRA MIXTA" in render_coverage(report)
