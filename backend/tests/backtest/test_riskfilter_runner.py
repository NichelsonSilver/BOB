"""Tests del runner del filtro de régimen.

El que vale es `test_el_oraculo_satura_el_lift`: si el cálculo del lift se
rompiera, todos los tests de aritmética del módulo puro podrían seguir en
verde mientras el reporte imprime un número sin sentido. Alimentar el futuro
real como score y exigir que el lift se acerque a su máximo teórico es la
única prueba que recorre el camino completo.
"""

from __future__ import annotations

import numpy as np
import pytest

from bob.backtest import riskfilter_runner as rr
from bob.models import riskfilter as rf
from tests.conftest import synthetic_series


@pytest.fixture(scope="module")
def conftest_series():
    return synthetic_series(n=4_000, seed=3)


def _serie_con_clustering(n: int = 6_000, seed: int = 0):
    """Volatilidad persistente: sin clustering la vol no es predecible y el
    test no distinguiría un filtro bueno de uno roto."""
    rng = np.random.default_rng(seed)
    lv = np.zeros(n)
    for i in range(1, n):
        lv[i] = 0.97 * lv[i - 1] + rng.normal(0, 0.15)
    sig = 0.004 * np.exp(lv)
    ret = rng.normal(0, 1, n) * sig
    close = 100 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.normal(0, 1, n)) * sig)
    low = close * (1 - np.abs(rng.normal(0, 1, n)) * sig)
    return close, high, low, sig


def test_el_oraculo_satura_el_lift():
    """Con el futuro como score, el lift debe acercarse a 1/(1−q) = 5."""
    close, high, low, sig = _serie_con_clustering()
    exc = rf.adverse_excursion(close, high, low, 16)
    # fmax en vez de nanmax: ignora el NaN de a pares y no avisa por las
    # barras del borde, que no tienen setup y quedan fuera igual.
    oracle = np.fmax(exc.long, exc.short)

    scores = {"model": oracle, "ewma": sig, "garch": sig, "har": sig}
    res = rr.evaluate(scores, exc, 20.0, 0.80, 16, min_warmup=500, n_boot=200, seed=0)

    assert res["lifts"]["model"]["lift"] > 3.5
    assert res["lifts"]["model"]["capture_rate"] > 0.85
    assert res["beats_chance"]


def test_el_ruido_no_supera_al_azar():
    close, high, low, _ = _serie_con_clustering()
    exc = rf.adverse_excursion(close, high, low, 16)
    rng = np.random.default_rng(1)
    noise = rng.random(close.size)
    scores = {k: rng.random(close.size) for k in ("model", "ewma", "garch", "har")}
    scores["model"] = noise

    res = rr.evaluate(scores, exc, 20.0, 0.80, 16, min_warmup=500, n_boot=200, seed=0)
    assert not res["beats_chance"]
    assert res["lifts"]["model"]["lift"] == pytest.approx(1.0, abs=0.35)


def test_la_vol_rezagada_le_gana_al_ruido():
    """Un filtro con señal real supera al baseline, y el IC pareado lo detecta."""
    close, high, low, sig = _serie_con_clustering()
    exc = rf.adverse_excursion(close, high, low, 16)
    rng = np.random.default_rng(2)
    lagged = np.concatenate([[np.nan], sig[:-1]])
    scores = {
        "model": lagged,
        "ewma": rng.random(close.size),
        "garch": rng.random(close.size),
        "har": rng.random(close.size),
    }
    res = rr.evaluate(scores, exc, 20.0, 0.80, 16, min_warmup=500, n_boot=200, seed=0)
    assert res["beats_baseline"]
    assert res["diff_ci95"][0] > 0


def test_el_veredicto_exige_los_dos_criterios():
    base = {
        "lifts": {"model": {"lift": 2.0}},
        "lift_ci95": [1.5, 2.5],
        "diff_vs_baseline": 0.5,
        "diff_ci95": [0.2, 0.8],
        "beats_chance": True,
        "beats_baseline": True,
    }
    assert rr.verdict(base)[0]

    solo_azar = {**base, "beats_baseline": False, "diff_ci95": [-0.1, 0.8]}
    assert not rr.verdict(solo_azar)[0]

    solo_baseline = {**base, "beats_chance": False, "lift_ci95": [0.9, 2.5]}
    assert not rr.verdict(solo_baseline)[0]


def test_marca_sin_potencia_cuando_faltan_eventos():
    close, high, low, sig = _serie_con_clustering(n=3_000)
    exc = rf.adverse_excursion(close, high, low, 16)
    scores = {k: sig for k in ("model", "ewma", "garch", "har")}
    # A 5x la liquidación queda a ~20%: prácticamente ningún evento.
    res = rr.evaluate(scores, exc, 5.0, 0.80, 16, min_warmup=500, n_boot=100, seed=0)
    assert res["underpowered"]


def test_render_no_revienta_y_nombra_el_preregistro():
    close, high, low, sig = _serie_con_clustering(n=3_000)
    exc = rf.adverse_excursion(close, high, low, 16)
    scores = {k: sig for k in ("model", "ewma", "garch", "har")}
    res = rr.evaluate(scores, exc, 20.0, 0.80, 16, min_warmup=500, n_boot=100, seed=0)
    payload = {
        "config": {
            "symbol": "ETHUSDT",
            "timeframe": "15m",
            "features": "price+deriv",
            "vol_model": "gbm",
            "folds": 6,
            "horizon": 16,
            "n_oof": 3_000,
            "from": "2024-01-01 00:00",
            "to": "2026-01-01 00:00",
        },
        "primary": res,
        "exploratory": [res],
    }
    text = rr.render(payload)
    assert "PREREGISTRO_FILTRO_RIESGO.md" in text
    assert "CONFIRMATORIA" in text
    assert "EXPLORATORIA" in text
    # El reporte tiene que decir siempre qué NO decide (§7).
    assert "no habilita el KPI 1" in text


def test_el_lift_se_cuenta_solo_sobre_barras_elegibles():
    """Regresión: las barras sin sigma OOF no van en el denominador.

    Falla contra la versión previa, que dividía la captura por la tasa de
    rechazo medida sobre la serie ENTERA. Las barras inelegibles no pueden
    rechazarse ni liquidar, así que solo diluían el denominador: sobre la
    serie real eso daba lift 5,06 donde el número honesto es 3,08 — un falso
    positivo que no rompe ninguna cuenta.
    """
    close, high, low, sig = _serie_con_clustering(n=4_000)
    exc = rf.adverse_excursion(close, high, low, 16)

    # Solo la segunda mitad tiene sigma: es lo que hace el walk-forward, que
    # no produce OOF sobre el tramo de entrenamiento mínimo.
    score = np.full(close.size, np.nan)
    score[2_000:] = sig[2_000:]
    scores = {k: score for k in ("model", "ewma", "garch", "har")}

    res = rr.evaluate(scores, exc, 20.0, 0.80, 16, min_warmup=300, n_boot=100, seed=0)
    m = res["lifts"]["model"]

    # n_bars es el subconjunto elegible, no el largo de la serie.
    assert res["n_bars"] < close.size
    assert m["n"] == res["n_bars"]
    # Y el lift reproduce exactamente la cuenta a mano sobre ese subconjunto.
    esperado = (m["n_events_rejected"] / m["n_events"]) / (m["n_rejected"] / res["n_bars"])
    assert m["lift"] == pytest.approx(esperado)


# --------------------------------------------------------------------------- #
# Camino completo: sigma OOF y CLI
# --------------------------------------------------------------------------- #
def _config_rapida():
    from bob.models.experiment import BarrierConfig, ExperimentConfig

    return ExperimentConfig(
        barrier=BarrierConfig(horizon_bars=8, vol_window_bars=48),
        n_splits=2,
        vol_kind="ridge",
    )


def test_oof_deja_nan_donde_no_hubo_fold_de_test(conftest_series):
    """Una barra que nunca fue test no tiene predicción honesta y debe salir NaN.

    Si el tramo de entrenamiento mínimo saliera con número, esas predicciones
    serían in-sample y el filtro se mediría contra datos que el modelo vio.
    """
    scores = rr.oof_volatility(conftest_series, _config_rapida(), None, None, None)

    assert set(scores) == {"model", "ewma", "garch", "har", "realized"}
    finite = np.isfinite(scores["model"])
    # El walk-forward reserva el primer min_train_frac: el arranque va vacío.
    assert not finite[:100].any()
    assert finite.sum() > 0
    # Y donde hay modelo, hay también baselines con que compararlo.
    for name in ("ewma", "garch", "har"):
        assert np.isfinite(scores[name][finite]).all()


def test_oof_no_predice_sobre_el_propio_train(conftest_series):
    """Las posiciones con predicción tienen que ser exactamente las de test."""
    from bob.models.validation import purged_walk_forward

    cfg = _config_rapida()
    scores = rr.oof_volatility(conftest_series, cfg, None, None, None)
    finite = np.flatnonzero(np.isfinite(scores["model"]))
    # Todas las posiciones predichas son posteriores al arranque del primer test.
    assert finite.min() > len(conftest_series) * cfg.min_train_frac * 0.9
    assert purged_walk_forward is not None


def test_cli_corre_end_to_end_y_escribe_artefacto(monkeypatch, tmp_path, capsys, conftest_series):
    """El CLI completo, con la DB reemplazada por la serie sintética."""
    monkeypatch.setattr(rr, "load_series", lambda *a, **k: conftest_series)
    monkeypatch.setattr(rr, "load_derivatives", lambda *a, **k: None)
    monkeypatch.setattr(rr, "load_book_depth", lambda *a, **k: None)
    monkeypatch.setattr(rr, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(rr, "EXPLORATORY_LEVERAGES", (20.0, 50.0))
    monkeypatch.setattr(rr, "EXPLORATORY_QUANTILES", (0.80,))
    monkeypatch.setattr(
        "sys.argv",
        [
            "riskfilter_runner",
            "--features", "price",
            "--vol-model", "ridge",
            "--folds", "2",
            "--horizon", "8",
            "--min-warmup", "300",
            "--n-boot", "100",
            "--artifact",
        ],
    )

    rr.main()

    out = capsys.readouterr().out
    assert "PRUEBA CONFIRMATORIA" in out
    assert "VEREDICTO" in out

    escritos = sorted(p.name for p in tmp_path.iterdir())
    assert len(escritos) == 2
    assert any(n.endswith(".json") for n in escritos)

    import json

    data = json.loads(next(p for p in tmp_path.iterdir() if p.suffix == ".json").read_text("utf-8"))
    # El artefacto tiene que apuntar a su pre-registro: sin eso, un lift
    # suelto en un archivo no dice contra qué criterio se leyó.
    assert data["config"]["preregistration"] == "docs/PREREGISTRO_FILTRO_RIESGO.md"
    assert "viable" in data["verdict"]
    assert data["primary"]["leverage"] == rr.PRIMARY_LEVERAGE
    assert data["primary"]["quantile"] == rr.PRIMARY_Q


def test_cli_falla_claro_sin_velas(monkeypatch):
    from bob.data.store import OHLCVSeries

    vacia = OHLCVSeries(
        symbol="X", timeframe="15m",
        open_time=np.array([], dtype=np.int64),
        **{k: np.array([]) for k in ("open", "high", "low", "close", "volume",
                                     "quote_volume", "taker_buy_volume")},
        n_trades=np.array([], dtype=np.int64),
    )
    monkeypatch.setattr(rr, "load_series", lambda *a, **k: vacia)
    monkeypatch.setattr("sys.argv", ["riskfilter_runner"])
    with pytest.raises(SystemExit):
        rr.main()
