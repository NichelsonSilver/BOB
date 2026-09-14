"""Tests del techo de la sigma: modo cuantílico y escalado adaptativo.

El que más vale es `test_el_adaptativo_no_usa_el_futuro`: el corrector en
línea se alimenta de resultados ya ocurridos, y si se alimentara del resultado
de la propia barra calibraría perfecto en el backtest y sería imposible en
vivo. Esa es exactamente la forma de lookahead que la regla 5 persigue.
"""

from __future__ import annotations

import numpy as np
import pytest

from bob.backtest import sigma_tail_runner as st
from bob.models.forecast import VolatilityModel


def _datos(n: int = 6_000, seed: int = 0):
    rng = np.random.default_rng(seed)
    X = rng.normal(0, 1, (n, 4))
    # La volatilidad depende de la primera columna: hay señal que aprender.
    vol = 0.005 * np.exp(0.6 * X[:, 0] + rng.normal(0, 0.3, n))
    return X, vol


# --------------------------------------------------------------------------- #
# Modo cuantílico
# --------------------------------------------------------------------------- #
def test_el_cuantilico_queda_por_encima_de_la_media():
    X, vol = _datos()
    idx = np.arange(4_000)
    media = VolatilityModel(kind="gbm", seed=1).fit(X, vol, idx).predict(X[4_000:])
    alto = (
        VolatilityModel(kind="gbm", seed=1, quantile=0.90)
        .fit(X, vol, idx)
        .predict(X[4_000:])
    )
    assert (alto > media).mean() > 0.9


def test_el_cuantilico_no_aplica_la_correccion_de_jensen():
    """Los cuantiles son invariantes ante transformaciones monótonas.

    Aplicarle Jensen a un modelo cuantílico lo inflaría por un factor que no
    corresponde a nada, y el error sería invisible: seguiría pareciendo una
    sigma razonable, solo que sistemáticamente alta.
    """
    X, vol = _datos()
    idx = np.arange(4_000)
    m = VolatilityModel(kind="gbm", seed=1, quantile=0.90).fit(X, vol, idx)
    esperado = np.exp(m._predict_log(X[4_000:4_010]))
    np.testing.assert_allclose(m.predict(X[4_000:4_010]), esperado)
    # La media sí la aplica, y por eso son caminos distintos.
    mm = VolatilityModel(kind="gbm", seed=1).fit(X, vol, idx)
    assert mm._resid_var > 0
    assert not np.allclose(mm.predict(X[4_000:4_010]), np.exp(mm._predict_log(X[4_000:4_010])))


def test_el_cuantilico_exige_gbm_y_un_q_valido():
    X, vol = _datos(n=2_000)
    idx = np.arange(1_500)
    with pytest.raises(ValueError, match="gbm"):
        VolatilityModel(kind="xgb", quantile=0.9).fit(X, vol, idx)
    with pytest.raises(ValueError, match="quantile"):
        VolatilityModel(kind="gbm", quantile=1.5).fit(X, vol, idx)


# --------------------------------------------------------------------------- #
# Escalado adaptativo
# --------------------------------------------------------------------------- #
def test_el_adaptativo_converge_a_la_excedencia_nominal():
    """Con una sigma sistemáticamente corta, el corrector la levanta."""
    rng = np.random.default_rng(5)
    n = 20_000
    mean_pred = np.full(n, 0.01)
    # Realizada con el doble de escala: la media estática se queda corta.
    realized = 0.02 * rng.lognormal(0, 0.4, n)

    out = st._adaptive_scale(realized, mean_pred, q=0.90, horizon=16)
    ok = np.isfinite(out)
    # Se mide la segunda mitad: la primera es la convergencia.
    tail = slice(n // 2, n)
    exceedance = (realized[tail] > out[tail]).mean()
    assert exceedance == pytest.approx(0.10, abs=0.03)
    assert ok.all()


def test_el_adaptativo_no_usa_el_futuro():
    """Mutar el futuro no puede cambiar lo que el corrector ya emitió.

    El resultado de la barra t recién se conoce H barras después. Si el
    corrector se alimentara del resultado de la propia barra, calibraría
    perfecto en backtest y en vivo no existiría.
    """
    rng = np.random.default_rng(6)
    n = 2_000
    mean_pred = np.full(n, 0.01)
    realized = 0.012 * rng.lognormal(0, 0.3, n)

    base = st._adaptive_scale(realized, mean_pred, q=0.90, horizon=16)

    i = 1_000
    mutado = realized.copy()
    mutado[i:] *= 10.0
    got = st._adaptive_scale(mutado, mean_pred, q=0.90, horizon=16)

    # Hasta i el corrector solo pudo ver resultados anteriores a i.
    np.testing.assert_allclose(got[: i], base[: i])


def test_el_adaptativo_respeta_el_retraso_del_horizonte():
    """Con horizonte H, la barra t no puede haber usado la barra t−H+1."""
    n, H = 500, 16
    mean_pred = np.full(n, 0.01)
    realized = np.full(n, 0.005)  # nunca excede

    base = st._adaptive_scale(realized, mean_pred, q=0.90, horizon=H)
    mutado = realized.copy()
    mutado[300] = 10.0  # una excedencia gigante en 300
    got = st._adaptive_scale(mutado, mean_pred, q=0.90, horizon=H)

    # Su efecto no puede aparecer antes de 300 + H.
    np.testing.assert_allclose(got[: 300 + H], base[: 300 + H])
    assert got[300 + H + 1] != base[300 + H + 1]


def test_pinball_es_la_perdida_del_cuantil():
    y = np.array([1.0, 2.0, 3.0])
    # Un pronóstico exacto da pérdida 0.
    assert st.pinball_loss(y, y, 0.9) == pytest.approx(0.0)
    # Sub-pronosticar se penaliza con peso q; sobre-pronosticar con 1−q.
    bajo = st.pinball_loss(y, y - 1.0, 0.9)
    alto = st.pinball_loss(y, y + 1.0, 0.9)
    assert bajo == pytest.approx(0.9)
    assert alto == pytest.approx(0.1)
    assert bajo > alto


def test_evaluate_reporta_los_tres_candidatos():
    rng = np.random.default_rng(7)
    n = 5_000
    realized = 0.01 * rng.lognormal(0, 0.4, n)
    mean_pred = np.full(n, 0.01)
    model = np.full(n, 0.014)
    baseline = np.full(n, 0.015)
    adaptive = st._adaptive_scale(realized, mean_pred, q=0.90, horizon=16)

    r = st.evaluate_quantile(
        realized, model, baseline, mean_pred, adaptive, 0.90, 16, n_boot=200, seed=0
    )
    for k in (
        "exceedance_model",
        "exceedance_baseline",
        "exceedance_adaptive",
        "pinball_model",
        "pinball_baseline",
        "pinball_adaptive",
        "tail_deficit_mean",
        "tail_deficit_model",
        "adopted",
    ):
        assert k in r
    assert r["n"] == n


def test_cli_corre_end_to_end(monkeypatch, tmp_path, capsys):
    from tests.conftest import synthetic_series

    serie = synthetic_series(n=4_000, seed=4)
    monkeypatch.setattr(st, "load_series", lambda *a, **k: serie)
    monkeypatch.setattr(st, "load_derivatives", lambda *a, **k: None)
    monkeypatch.setattr(st, "load_book_depth", lambda *a, **k: None)
    monkeypatch.setattr(st, "ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(st, "QUANTILES", (0.90,))
    monkeypatch.setattr(
        "sys.argv",
        [
            "sigma_tail_runner",
            "--features", "price",
            "--vol-model", "gbm",
            "--folds", "2",
            "--horizon", "8",
            "--n-boot", "100",
            "--artifact",
        ],
    )

    st.main()

    out = capsys.readouterr().out
    assert "TECHO DE LA SIGMA" in out
    assert "VEREDICTO" in out
    # El reporte tiene que decir siempre qué NO decide.
    assert "no habilita el KPI 1" in out.lower() or "KPI 1" in out
    assert len(list(tmp_path.iterdir())) == 2


def test_cli_falla_claro_sin_velas(monkeypatch):
    from bob.data.store import OHLCVSeries

    vacia = OHLCVSeries(
        symbol="X", timeframe="15m",
        open_time=np.array([], dtype=np.int64),
        **{k: np.array([]) for k in ("open", "high", "low", "close", "volume",
                                     "quote_volume", "taker_buy_volume")},
        n_trades=np.array([], dtype=np.int64),
    )
    monkeypatch.setattr(st, "load_series", lambda *a, **k: vacia)
    monkeypatch.setattr("sys.argv", ["sigma_tail_runner"])
    with pytest.raises(SystemExit):
        st.main()
