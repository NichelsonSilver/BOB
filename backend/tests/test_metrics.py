"""Tests de las métricas de evaluación.

Cada métrica se contrasta contra un caso donde el valor correcto se conoce
analíticamente (predictor perfecto, predictor aleatorio, cobertura exacta),
no solo contra sí misma.
"""

from __future__ import annotations

import numpy as np
import pytest

from bob.models import metrics as mx


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(42)


class TestBrierYLogLoss:
    def test_prediccion_perfecta_da_cero(self) -> None:
        y = np.array([1.0, 0.0, 1.0, 0.0])
        assert mx.brier_score(y, y) == pytest.approx(0.0)

    def test_prediccion_maximamente_mala(self) -> None:
        y = np.array([1.0, 0.0])
        assert mx.brier_score(y, 1.0 - y) == pytest.approx(1.0)

    def test_prediccion_indecisa(self) -> None:
        y = np.array([1.0, 0.0, 1.0, 0.0])
        assert mx.brier_score(y, np.full(4, 0.5)) == pytest.approx(0.25)

    def test_log_loss_no_explota_en_extremos(self) -> None:
        y = np.array([1.0, 0.0])
        assert np.isfinite(mx.log_loss(y, np.array([0.0, 1.0])))

    def test_pesos_cambian_el_resultado(self) -> None:
        y = np.array([1.0, 0.0])
        p = np.array([0.9, 0.9])
        sin_peso = mx.brier_score(y, p)
        con_peso = mx.brier_score(y, p, weights=np.array([10.0, 1.0]))
        assert con_peso < sin_peso


class TestAUC:
    def test_separacion_perfecta(self) -> None:
        y = np.array([0, 0, 1, 1])
        assert mx.roc_auc(y, np.array([0.1, 0.2, 0.8, 0.9])) == pytest.approx(1.0)

    def test_orden_invertido(self) -> None:
        y = np.array([0, 0, 1, 1])
        assert mx.roc_auc(y, np.array([0.9, 0.8, 0.2, 0.1])) == pytest.approx(0.0)

    def test_score_constante_da_medio(self) -> None:
        y = np.array([0, 1, 0, 1])
        assert mx.roc_auc(y, np.full(4, 0.5)) == pytest.approx(0.5)

    def test_una_sola_clase_es_indefinido(self) -> None:
        assert np.isnan(mx.roc_auc(np.ones(5, dtype=int), np.linspace(0, 1, 5)))


class TestCalibracion:
    def test_modelo_perfectamente_calibrado(self, rng) -> None:
        """Si p viene de una uniforme y y ~ Bernoulli(p), el ECE debe ser ~0."""
        n = 60_000
        p = rng.uniform(0.05, 0.95, n)
        y = (rng.uniform(size=n) < p).astype(float)
        m = mx.probability_metrics(y, p)
        assert m.ece < 0.02
        assert m.mean_calibration_error_pp < 2.0

    def test_modelo_sistematicamente_optimista(self, rng) -> None:
        n = 20_000
        p_real = rng.uniform(0.1, 0.6, n)
        y = (rng.uniform(size=n) < p_real).astype(float)
        p_inflado = np.clip(p_real + 0.25, 0, 1)
        m = mx.probability_metrics(y, p_inflado)
        assert m.mean_calibration_error_pp > 15.0

    def test_buckets_cubren_las_predicciones(self, rng) -> None:
        n = 5000
        p = rng.uniform(size=n)
        y = (rng.uniform(size=n) < p).astype(float)
        buckets = mx.reliability_curve(y, p, n_buckets=10)
        assert sum(b.n for b in buckets) == n

    def test_bucket_incluye_el_uno_exacto(self) -> None:
        y = np.array([1.0, 1.0])
        buckets = mx.reliability_curve(y, np.array([1.0, 1.0]), n_buckets=10)
        assert sum(b.n for b in buckets) == 2

    def test_error_pp_del_bucket(self) -> None:
        b = mx.ReliabilityBucket(0.7, 0.8, 100, 0.75, 0.60)
        assert b.error_pp == pytest.approx(15.0)

    def test_brier_skill_score_del_baseline_es_cero(self, rng) -> None:
        n = 5000
        y = (rng.uniform(size=n) < 0.3).astype(float)
        # La referencia del BSS es la tasa base OBSERVADA, no la teórica:
        # predecir 0.3 cuando la muestra salió 0.2985 ya es levemente peor.
        m = mx.probability_metrics(y, np.full(n, float(y.mean())))
        assert m.brier_skill_score == pytest.approx(0.0, abs=1e-9)

    def test_summary_es_legible(self, rng) -> None:
        n = 1000
        p = rng.uniform(size=n)
        y = (rng.uniform(size=n) < p).astype(float)
        assert "Brier" in mx.probability_metrics(y, p).summary()


class TestRegresion:
    def test_prediccion_perfecta(self) -> None:
        y = np.array([1.0, 2.0, 3.0, 4.0])
        m = mx.regression_metrics(y, y)
        assert m.rmse == pytest.approx(0.0)
        assert m.r2 == pytest.approx(1.0)

    def test_predecir_la_media_da_r2_cero(self) -> None:
        y = np.array([1.0, 2.0, 3.0, 4.0])
        m = mx.regression_metrics(y, np.full(4, y.mean()))
        assert m.r2 == pytest.approx(0.0)

    def test_r2_vs_baseline_positivo_cuando_el_modelo_gana(self) -> None:
        y = np.array([1.0, 2.0, 3.0, 4.0])
        bueno = y + 0.1
        malo = y + 1.0
        assert mx.regression_metrics(y, bueno, malo).r2_vs_baseline > 0

    def test_qlike_minimo_en_el_valor_correcto(self) -> None:
        y = np.full(100, 0.02)
        exacto = mx.qlike(y, y)
        assert exacto == pytest.approx(0.0, abs=1e-9)
        assert mx.qlike(y, y * 0.5) > exacto
        assert mx.qlike(y, y * 2.0) > exacto

    def test_qlike_castiga_mas_subestimar(self) -> None:
        """La asimetría correcta para quien opera apalancado."""
        y = np.full(100, 0.02)
        subestima = mx.qlike(y, y * 0.5)
        sobreestima = mx.qlike(y, y * 2.0)
        assert subestima > sobreestima

    def test_mincer_zarnowitz_de_forecast_eficiente(self) -> None:
        rng = np.random.default_rng(1)
        f = rng.uniform(0.01, 0.05, 2000)
        y = f + rng.normal(0, 0.001, 2000)
        alpha, beta = mx.mincer_zarnowitz(y, f)
        assert alpha == pytest.approx(0.0, abs=0.002)
        assert beta == pytest.approx(1.0, abs=0.05)

    def test_mincer_zarnowitz_detecta_sesgo(self) -> None:
        rng = np.random.default_rng(2)
        f = rng.uniform(0.01, 0.05, 2000)
        y = f * 0.5  # el forecast duplica sistemáticamente
        _, beta = mx.mincer_zarnowitz(y, f)
        assert beta == pytest.approx(0.5, abs=0.05)

    def test_forecast_constante_es_indefinido(self) -> None:
        alpha, beta = mx.mincer_zarnowitz(np.arange(10.0), np.full(10, 3.0))
        assert np.isnan(alpha) and np.isnan(beta)


class TestIntervalos:
    def test_cobertura_exacta(self) -> None:
        y = np.linspace(0, 1, 100)
        lo = np.full(100, -10.0)
        hi = np.full(100, 10.0)
        m = mx.interval_metrics(y, lo, hi, 0.9)
        assert m.empirical_coverage == pytest.approx(1.0)
        assert m.coverage_gap_pp == pytest.approx(10.0)

    def test_intervalo_degenerado_no_cubre(self) -> None:
        y = np.linspace(1, 2, 50)
        m = mx.interval_metrics(y, np.zeros(50), np.zeros(50), 0.9)
        assert m.empirical_coverage == pytest.approx(0.0)

    def test_ancho_medio(self) -> None:
        y = np.zeros(10)
        m = mx.interval_metrics(y, np.full(10, -1.0), np.full(10, 2.0), 0.8)
        assert m.mean_width == pytest.approx(3.0)

    def test_winkler_penaliza_fallar(self) -> None:
        y = np.zeros(10)
        acierta = mx.interval_metrics(y, np.full(10, -1.0), np.full(10, 1.0), 0.9)
        falla = mx.interval_metrics(y, np.full(10, 5.0), np.full(10, 7.0), 0.9)
        assert falla.winkler_score > acierta.winkler_score

    def test_winkler_prefiere_el_intervalo_mas_angosto(self) -> None:
        y = np.zeros(100)
        angosto = mx.interval_metrics(y, np.full(100, -1.0), np.full(100, 1.0), 0.9)
        ancho = mx.interval_metrics(y, np.full(100, -50.0), np.full(100, 50.0), 0.9)
        assert angosto.winkler_score < ancho.winkler_score

    def test_summary_es_legible(self) -> None:
        y = np.zeros(10)
        m = mx.interval_metrics(y, np.full(10, -1.0), np.full(10, 1.0), 0.9)
        assert "cobertura" in m.summary() or "nominal" in m.summary()


class TestDieboldMariano:
    def test_modelos_identicos_no_son_distinguibles(self, rng) -> None:
        loss = rng.uniform(size=500)
        dm = mx.diebold_mariano(loss, loss.copy())
        assert dm.mean_loss_diff == pytest.approx(0.0)
        assert not dm.favors_model

    def test_detecta_un_modelo_claramente_mejor(self, rng) -> None:
        n = 2000
        base = rng.uniform(0.5, 1.5, n)
        modelo = base - 0.3  # pérdida sistemáticamente menor
        dm = mx.diebold_mariano(modelo, base, horizon=1)
        assert dm.mean_loss_diff < 0
        assert dm.p_value < 0.01
        assert dm.favors_model
        assert "significativa" in dm.verdict()

    def test_ruido_puro_no_da_significancia(self, rng) -> None:
        n = 1000
        a = rng.normal(1.0, 1.0, n)
        b = rng.normal(1.0, 1.0, n)
        dm = mx.diebold_mariano(a, b)
        assert dm.p_value > 0.05
        assert "NO significativa" in dm.verdict()

    def test_horizonte_largo_ensancha_el_error(self, rng) -> None:
        """Ignorar la autocorrelación a h pasos infla la significancia."""
        n = 2000
        base = rng.uniform(0.5, 1.5, n)
        modelo = base - 0.05
        h1 = mx.diebold_mariano(modelo, base, horizon=1)
        h20 = mx.diebold_mariano(modelo, base, horizon=20)
        assert abs(h20.statistic) < abs(h1.statistic)

    def test_muestra_insuficiente(self) -> None:
        dm = mx.diebold_mariano(np.array([1.0, 2.0]), np.array([1.0, 3.0]))
        assert np.isnan(dm.p_value)
        assert "sin datos" in dm.verdict()

    def test_sin_correccion_de_harvey(self, rng) -> None:
        n = 500
        base = rng.uniform(0.5, 1.5, n)
        dm = mx.diebold_mariano(base - 0.2, base, harvey_correction=False)
        assert np.isfinite(dm.p_value)


class TestSeriesDePerdida:
    def test_squared_error(self) -> None:
        out = mx.squared_error(np.array([1.0, 2.0]), np.array([1.0, 4.0]))
        np.testing.assert_allclose(out, [0.0, 4.0])

    def test_brier_loss_series(self) -> None:
        out = mx.brier_loss_series(np.array([1.0, 0.0]), np.array([0.75, 0.25]))
        np.testing.assert_allclose(out, [0.0625, 0.0625])
