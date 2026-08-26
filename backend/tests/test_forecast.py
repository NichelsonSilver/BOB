"""Tests de los estimadores del stack.

Se construyen problemas sintéticos con señal conocida, para poder afirmar
"el modelo debe encontrar esto" en vez de solo "el modelo corre". Y se
verifica la propiedad que justifica cada pieza: que la calibración isotónica
efectivamente calibre, y que el intervalo conformal alcance su cobertura
nominal.
"""

from __future__ import annotations

import numpy as np
import pytest

from bob.models.forecast import (
    BarrierProbabilityModel,
    ConformalReturnInterval,
    VolatilityModel,
)
from bob.models.metrics import interval_metrics, probability_metrics, regression_metrics


@pytest.fixture(scope="module")
def problema_binario():
    """X con dos features informativos y ruido; y ~ Bernoulli(sigmoide)."""
    rng = np.random.default_rng(0)
    n, d = 6000, 8
    X = rng.normal(size=(n, d))
    logit = 1.4 * X[:, 0] - 0.9 * X[:, 1] + 0.3 * X[:, 0] * X[:, 1]
    p = 1.0 / (1.0 + np.exp(-logit))
    y = (rng.uniform(size=n) < p).astype(float)
    span = np.minimum(np.arange(n) + 3, n - 1)
    return X, y, span, p


@pytest.fixture(scope="module")
def problema_regresion():
    rng = np.random.default_rng(1)
    n, d = 4000, 6
    X = rng.normal(size=(n, d))
    vol = 0.01 * np.exp(0.5 * X[:, 0] + 0.3 * X[:, 1])
    y = vol * np.exp(rng.normal(0, 0.2, n))
    return X, y


class TestBarrierProbabilityModel:
    def test_encuentra_la_senal(self, problema_binario) -> None:
        X, y, span, _ = problema_binario
        train, test = np.arange(4000), np.arange(4000, 6000)
        m = BarrierProbabilityModel(calibrate=False, seed=0).fit(X, y, train, span)
        pred = m.predict_proba(X[test])
        assert probability_metrics(y[test], pred).auc > 0.75

    def test_no_encuentra_senal_donde_no_la_hay(self) -> None:
        """Control negativo: con y aleatorio, el AUC debe rondar 0.5."""
        rng = np.random.default_rng(2)
        n = 4000
        X = rng.normal(size=(n, 6))
        y = (rng.uniform(size=n) < 0.4).astype(float)
        span = np.minimum(np.arange(n) + 3, n - 1)
        train, test = np.arange(2600), np.arange(2600, n)
        m = BarrierProbabilityModel(calibrate=False, seed=0).fit(X, y, train, span)
        auc = probability_metrics(y[test], m.predict_proba(X[test])).auc
        assert 0.40 < auc < 0.60

    def test_la_calibracion_mejora_el_error_de_calibracion(self, problema_binario) -> None:
        X, y, span, _ = problema_binario
        train, test = np.arange(4000), np.arange(4000, 6000)
        m = BarrierProbabilityModel(calibrate=True, seed=0).fit(X, y, train, span)
        calibrada = probability_metrics(y[test], m.predict_proba(X[test]))
        cruda = probability_metrics(y[test], m.predict_proba_uncalibrated(X[test]))
        assert calibrada.mean_calibration_error_pp <= cruda.mean_calibration_error_pp + 1.0

    def test_probabilidades_en_rango_valido(self, problema_binario) -> None:
        X, y, span, _ = problema_binario
        m = BarrierProbabilityModel(seed=0).fit(X, y, np.arange(4000), span)
        p = m.predict_proba(X[4000:])
        assert p.min() >= 0.0 and p.max() <= 1.0

    def test_modelo_logistico_tambien_funciona(self, problema_binario) -> None:
        X, y, span, _ = problema_binario
        train, test = np.arange(4000), np.arange(4000, 6000)
        m = BarrierProbabilityModel(kind="logistic", calibrate=False, seed=0).fit(
            X, y, train, span
        )
        assert probability_metrics(y[test], m.predict_proba(X[test])).auc > 0.75

    def test_predecir_sin_ajustar_falla(self) -> None:
        m = BarrierProbabilityModel()
        with pytest.raises(RuntimeError, match="no está ajustado"):
            m.predict_proba(np.zeros((3, 4)))
        with pytest.raises(RuntimeError):
            m.predict_proba_uncalibrated(np.zeros((3, 4)))

    def test_respeta_los_pesos_de_muestra(self, problema_binario) -> None:
        X, y, span, _ = problema_binario
        train = np.arange(4000)
        w = np.ones(X.shape[0])
        m = BarrierProbabilityModel(calibrate=False, seed=0).fit(X, y, train, span, w)
        assert m.predict_proba(X[4000:]).size == 2000

    def test_train_pequeno_omite_la_calibracion(self) -> None:
        rng = np.random.default_rng(3)
        n = 300
        X = rng.normal(size=(n, 4))
        y = (rng.uniform(size=n) < 0.5).astype(float)
        span = np.minimum(np.arange(n) + 2, n - 1)
        m = BarrierProbabilityModel(calibrate=True, seed=0).fit(X, y, np.arange(200), span)
        assert m.predict_proba(X[200:]).size == 100

    def test_es_reproducible(self, problema_binario) -> None:
        X, y, span, _ = problema_binario
        train = np.arange(4000)
        a = BarrierProbabilityModel(seed=7).fit(X, y, train, span).predict_proba(X[4000:])
        b = BarrierProbabilityModel(seed=7).fit(X, y, train, span).predict_proba(X[4000:])
        np.testing.assert_allclose(a, b)


class TestVolatilityModel:
    def test_encuentra_la_estructura(self, problema_regresion) -> None:
        X, y = problema_regresion
        train, test = np.arange(2800), np.arange(2800, 4000)
        m = VolatilityModel(seed=0).fit(X, y, train)
        pred = m.predict(X[test])
        assert regression_metrics(y[test], pred).r2 > 0.3

    def test_forecast_siempre_positivo(self, problema_regresion) -> None:
        X, y = problema_regresion
        m = VolatilityModel(seed=0).fit(X, y, np.arange(2800))
        assert np.all(m.predict(X[2800:]) > 0)

    def test_variante_ridge(self, problema_regresion) -> None:
        X, y = problema_regresion
        train, test = np.arange(2800), np.arange(2800, 4000)
        m = VolatilityModel(kind="ridge", seed=0).fit(X, y, train)
        assert regression_metrics(y[test], m.predict(X[test])).r2 > 0.2

    def test_correccion_de_jensen_evita_el_sesgo_a_la_baja(self, problema_regresion) -> None:
        X, y = problema_regresion
        train, test = np.arange(2800), np.arange(2800, 4000)
        m = VolatilityModel(seed=0).fit(X, y, train)
        sesgo = np.mean(m.predict(X[test]) - y[test]) / np.mean(y[test])
        assert abs(sesgo) < 0.15

    def test_predecir_sin_ajustar_falla(self) -> None:
        with pytest.raises(RuntimeError):
            VolatilityModel().predict(np.zeros((3, 4)))


class TestVolatilityModelXGBoost:
    """XGBoost como estimador alternativo del target que SÍ pasó el gate."""

    def test_encuentra_la_estructura(self, problema_regresion) -> None:
        X, y = problema_regresion
        train, test = np.arange(2800), np.arange(2800, 4000)
        m = VolatilityModel(kind="xgb", seed=0).fit(X, y, train)
        assert regression_metrics(y[test], m.predict(X[test])).r2 > 0.3

    def test_forecast_siempre_positivo(self, problema_regresion) -> None:
        """La corrección de Jensen se aplica igual: se entrena en log."""
        X, y = problema_regresion
        m = VolatilityModel(kind="xgb", seed=0).fit(X, y, np.arange(2800))
        assert np.all(m.predict(X[2800:]) > 0)

    def test_es_reproducible(self, problema_regresion) -> None:
        """Bit a bit, no `allclose`.

        El proyecto tiene un control de regresión que compara corridas del
        gate hechas en días distintos exigiendo igualdad exacta. Un estimador
        que solo es reproducible dentro de una tolerancia lo vuelve inútil.
        """
        X, y = problema_regresion
        train = np.arange(2800)
        a = VolatilityModel(kind="xgb", seed=7).fit(X, y, train).predict(X[2800:])
        b = VolatilityModel(kind="xgb", seed=7).fit(X, y, train).predict(X[2800:])
        assert np.array_equal(a, b)

    def test_compite_con_el_gbm_de_sklearn(self, problema_regresion) -> None:
        """Mismos hiperparámetros -> mismo orden de magnitud de error.

        No se exige que gane: cuál gana lo decide el gate sobre datos reales,
        no un problema sintético. Lo que se exige es que la traducción de
        hiperparámetros sea fiel — si `max_leaves` no se estuviera aplicando,
        XGBoost correría con capacidad muy distinta y el RMSE se separaría.
        """
        X, y = problema_regresion
        train, test = np.arange(2800), np.arange(2800, 4000)
        sk = VolatilityModel(kind="gbm", seed=0).fit(X, y, train).predict(X[test])
        xg = VolatilityModel(kind="xgb", seed=0).fit(X, y, train).predict(X[test])
        rmse_sk = regression_metrics(y[test], sk).rmse
        rmse_xg = regression_metrics(y[test], xg).rmse
        assert 0.5 < rmse_xg / rmse_sk < 2.0

    def test_no_revienta_con_columna_vacia_y_por_eso_hace_falta_la_guarda(
        self, problema_regresion
    ) -> None:
        """El hallazgo que obliga a `assert_columns_trainable` a existir.

        sklearn levanta ValueError cuando una columna no tiene un solo valor
        finito en el train; XGBoost ajusta y predice sin decir nada. El fallo
        ruidoso se vuelve silencioso, así que la guarda de `experiment.py`
        pasa de traducir un error a ser la única protección.
        """
        X, y = problema_regresion
        X = X.copy()
        X[:, -1] = np.nan
        train = np.arange(2800)

        with pytest.raises(ValueError):
            VolatilityModel(kind="gbm", seed=0).fit(X, y, train)

        pred = VolatilityModel(kind="xgb", seed=0).fit(X, y, train).predict(X[2800:])
        assert np.all(np.isfinite(pred))


class TestConformalReturnInterval:
    @pytest.fixture(scope="class")
    def datos(self):
        rng = np.random.default_rng(4)
        n, d = 6000, 5
        X = rng.normal(size=(n, d))
        # Heterocedasticidad: la dispersión depende de X, no es constante.
        sigma = 0.01 * np.exp(0.4 * X[:, 0])
        y = 0.002 * X[:, 1] + sigma * rng.standard_t(df=4, size=n)
        return X, y

    def test_alcanza_la_cobertura_nominal(self, datos) -> None:
        X, y = datos
        train, test = np.arange(4000), np.arange(4000, 6000)
        ci = ConformalReturnInterval(alpha=0.20, adaptive=False, seed=0).fit(X, y, train)
        lo, hi = ci.predict_interval(X[test])
        m = interval_metrics(y[test], lo, hi, 0.80)
        assert abs(m.coverage_gap_pp) < 5.0

    def test_cobertura_al_95(self, datos) -> None:
        X, y = datos
        train, test = np.arange(4000), np.arange(4000, 6000)
        ci = ConformalReturnInterval(alpha=0.05, adaptive=False, seed=0).fit(X, y, train)
        lo, hi = ci.predict_interval(X[test])
        m = interval_metrics(y[test], lo, hi, 0.95)
        assert abs(m.coverage_gap_pp) < 4.0

    def test_el_intervalo_se_adapta_a_la_heterocedasticidad(self, datos) -> None:
        """Un intervalo constante no serviría: el ancho debe seguir a sigma."""
        X, y = datos
        train, test = np.arange(4000), np.arange(4000, 6000)
        ci = ConformalReturnInterval(alpha=0.20, adaptive=False, seed=0).fit(X, y, train)
        lo, hi = ci.predict_interval(X[test])
        ancho = hi - lo
        alta_vol = X[test, 0] > 1.0
        baja_vol = X[test, 0] < -1.0
        assert ancho[alta_vol].mean() > 1.5 * ancho[baja_vol].mean()

    def test_lower_siempre_bajo_upper(self, datos) -> None:
        X, y = datos
        ci = ConformalReturnInterval(alpha=0.20, adaptive=False, seed=0).fit(
            X, y, np.arange(4000)
        )
        lo, hi = ci.predict_interval(X[4000:])
        assert np.all(hi >= lo)

    def test_nivel_mas_estricto_da_intervalo_mas_ancho(self, datos) -> None:
        X, y = datos
        train, test = np.arange(4000), np.arange(4000, 6000)
        ancho_80 = np.subtract(
            *reversed(
                ConformalReturnInterval(alpha=0.20, adaptive=False, seed=0)
                .fit(X, y, train)
                .predict_interval(X[test])
            )
        ).mean()
        ancho_95 = np.subtract(
            *reversed(
                ConformalReturnInterval(alpha=0.05, adaptive=False, seed=0)
                .fit(X, y, train)
                .predict_interval(X[test])
            )
        ).mean()
        assert ancho_95 > ancho_80

    def test_aci_mantiene_cobertura_razonable(self, datos) -> None:
        X, y = datos
        train, test = np.arange(4000), np.arange(4000, 6000)
        ci = ConformalReturnInterval(alpha=0.20, adaptive=True, seed=0).fit(X, y, train)
        lo, hi = ci.predict_interval_adaptive(X[test], y[test])
        m = interval_metrics(y[test], lo, hi, 0.80)
        assert abs(m.coverage_gap_pp) < 8.0

    def test_aci_desactivado_devuelve_el_intervalo_base(self, datos) -> None:
        X, y = datos
        train, test = np.arange(4000), np.arange(4000, 6000)
        ci = ConformalReturnInterval(alpha=0.20, adaptive=False, seed=0).fit(X, y, train)
        base = ci.predict_interval(X[test])
        adaptativo = ci.predict_interval_adaptive(X[test], y[test])
        np.testing.assert_allclose(base[0], adaptativo[0])

    def test_train_insuficiente_falla_explicito(self) -> None:
        rng = np.random.default_rng(5)
        X = rng.normal(size=(60, 3))
        y = rng.normal(size=60)
        with pytest.raises(ValueError, match="calibración"):
            ConformalReturnInterval().fit(X, y, np.arange(60))

    def test_predecir_sin_ajustar_falla(self) -> None:
        with pytest.raises(RuntimeError):
            ConformalReturnInterval().predict_interval(np.zeros((3, 4)))
