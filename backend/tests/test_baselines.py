"""Tests de los baselines.

Un baseline mal implementado es peor que no tenerlo: hace que el modelo
parezca bueno por comparación. Se verifica que cada uno reproduzca la
propiedad teórica que lo define (persistencia del EWMA, reversión del GARCH,
memoria larga del HAR), no solo que devuelva números.
"""

from __future__ import annotations

import numpy as np
import pytest

from bob.models.baselines import (
    BaseRateClassifier,
    EWMAVolForecaster,
    GarchVolForecaster,
    HARVolForecaster,
    RandomWalkForecaster,
)
from bob.signals import numeric as nm


class TestBaseRateClassifier:
    def test_aprende_la_tasa_base(self) -> None:
        y = np.array([1.0, 1.0, 0.0, 0.0, 0.0])
        m = BaseRateClassifier().fit(y)
        assert m.rate == pytest.approx(0.4)
        assert np.all(m.predict_proba(10) == pytest.approx(0.4))

    def test_respeta_los_pesos(self) -> None:
        y = np.array([1.0, 0.0])
        m = BaseRateClassifier().fit(y, weights=np.array([3.0, 1.0]))
        assert m.rate == pytest.approx(0.75)

    def test_esta_perfectamente_calibrado_por_construccion(self) -> None:
        """No discrimina nada, pero su probabilidad es honesta: la referencia."""
        rng = np.random.default_rng(0)
        y = (rng.uniform(size=10_000) < 0.37).astype(float)
        m = BaseRateClassifier().fit(y)
        assert m.predict_proba(1)[0] == pytest.approx(y.mean())


class TestRandomWalk:
    def test_sin_drift_predice_cero(self) -> None:
        m = RandomWalkForecaster().fit(np.array([0.1, -0.2, 0.3]))
        assert np.all(m.predict(5) == 0.0)

    def test_con_drift_usa_la_media(self) -> None:
        y = np.array([0.1, 0.2, 0.3])
        m = RandomWalkForecaster(use_drift=True).fit(y)
        assert m.predict(3)[0] == pytest.approx(0.2)


class TestEWMAVol:
    def test_devuelve_volatilidad_positiva(self) -> None:
        rng = np.random.default_rng(1)
        out = EWMAVolForecaster(horizon=16).predict_from_returns(rng.normal(0, 0.01, 1000))
        assert np.all(out > 0)

    def test_escala_con_la_raiz_del_horizonte(self) -> None:
        rng = np.random.default_rng(2)
        r = rng.normal(0, 0.01, 1000)
        h1 = EWMAVolForecaster(horizon=1).predict_from_returns(r)
        h16 = EWMAVolForecaster(horizon=16).predict_from_returns(r)
        np.testing.assert_allclose(h16, h1 * 4.0, rtol=1e-9)

    def test_recupera_la_volatilidad_verdadera(self) -> None:
        rng = np.random.default_rng(3)
        sigma = 0.02
        r = rng.normal(0, sigma, 20_000)
        out = EWMAVolForecaster(horizon=1).predict_from_returns(r)
        assert np.mean(out[1000:]) == pytest.approx(sigma, rel=0.1)

    def test_reacciona_a_un_shock(self) -> None:
        r = np.concatenate([np.full(500, 0.001), np.full(50, 0.05)])
        out = EWMAVolForecaster(horizon=1).predict_from_returns(r)
        assert out[-1] > 5 * out[490]

    def test_no_revierte_tras_el_shock(self) -> None:
        """Propiedad definitoria del IGARCH: sin media a la que volver."""
        r = np.concatenate([np.full(300, 0.001), np.full(5, 0.10), np.full(50, 0.001)])
        out = EWMAVolForecaster(horizon=1).predict_from_returns(r)
        # 50 barras después del shock sigue muy por encima del nivel previo.
        assert out[-1] > 3 * out[250]

    def test_serie_vacia(self) -> None:
        assert EWMAVolForecaster().predict_from_returns(np.array([])).size == 0


class TestGarchVol:
    @pytest.fixture(scope="class")
    def ajustado(self) -> GarchVolForecaster:
        rng = np.random.default_rng(7)
        n = 4000
        omega, alpha, beta = 1e-6, 0.08, 0.90
        var = np.empty(n)
        r = np.empty(n)
        var[0] = omega / (1 - alpha - beta)
        for i in range(n):
            if i:
                var[i] = omega + alpha * r[i - 1] ** 2 + beta * var[i - 1]
            r[i] = rng.normal(0, np.sqrt(var[i]))
        return GarchVolForecaster(horizon=8).fit(r), r  # type: ignore[return-value]

    def test_converge_en_datos_garch(self, ajustado) -> None:
        model, _ = ajustado
        assert model.converged
        assert 0 < model.alpha < 0.5
        assert 0 < model.beta < 1.0

    def test_recupera_la_persistencia(self, ajustado) -> None:
        """alpha+beta simulado = 0.98; el ajuste debe quedar cerca."""
        model, _ = ajustado
        assert model.alpha + model.beta == pytest.approx(0.98, abs=0.06)

    def test_forecast_positivo(self, ajustado) -> None:
        model, r = ajustado
        out = model.predict_from_returns(r)
        assert np.all(out > 0)

    def test_muestra_corta_no_converge_y_cae_a_ewma(self) -> None:
        r = np.random.default_rng(4).normal(0, 0.01, 50)
        model = GarchVolForecaster(horizon=4).fit(r)
        assert not model.converged
        out = model.predict_from_returns(r)
        esperado = EWMAVolForecaster(horizon=4).predict_from_returns(r)
        np.testing.assert_allclose(out, esperado)

    def test_serie_constante_no_converge(self) -> None:
        model = GarchVolForecaster().fit(np.zeros(500))
        assert not model.converged

    def test_revierte_a_la_incondicional(self) -> None:
        """Lo que distingue al GARCH del EWMA: tras un shock, proyecta calma."""
        rng = np.random.default_rng(9)
        n = 3000
        omega, alpha, beta = 1e-6, 0.05, 0.90
        var = np.empty(n)
        r = np.empty(n)
        var[0] = omega / (1 - alpha - beta)
        for i in range(n):
            if i:
                var[i] = omega + alpha * r[i - 1] ** 2 + beta * var[i - 1]
            r[i] = rng.normal(0, np.sqrt(var[i]))

        model = GarchVolForecaster(horizon=200).fit(r)
        if not model.converged:  # pragma: no cover
            pytest.skip("el ajuste no convergió en esta semilla")
        # Con horizonte largo el forecast agregado tiende a h·varianza incondicional.
        uncond = model.omega / (1 - model.alpha - model.beta)
        agregado = model.predict_from_returns(r)[-1] / model._scale
        assert agregado == pytest.approx(np.sqrt(200 * uncond), rel=0.4)


class TestHARVol:
    def _datos(self, n: int = 3000, seed: int = 5):
        rng = np.random.default_rng(seed)
        # Volatilidad con memoria larga: mezcla de componentes lentos.
        lento = np.repeat(rng.lognormal(0, 0.4, n // 100 + 1), 100)[:n]
        vol = 0.01 * lento
        r = rng.normal(0, 1, n) * vol
        rv_d = np.nan_to_num(nm.realized_vol(r, 4), nan=1e-6)
        rv_w = np.nan_to_num(nm.realized_vol(r, 24), nan=1e-6)
        rv_m = np.nan_to_num(nm.realized_vol(r, 96), nan=1e-6)
        design = HARVolForecaster.design_matrix(rv_d, rv_w, rv_m)
        target = np.roll(rv_d, -4)
        return design[100:-10], target[100:-10]

    def test_design_matrix_tiene_intercepto_y_tres_regresores(self) -> None:
        x = np.full(10, 0.01)
        d = HARVolForecaster.design_matrix(x, x, x)
        assert d.shape == (10, 4)
        assert np.all(d[:, 0] == 1.0)

    def test_ajusta_y_predice_positivo(self) -> None:
        design, target = self._datos()
        m = HARVolForecaster().fit(design, target)
        pred = m.predict(design)
        assert m.coefs is not None
        assert np.all(pred > 0)

    def test_tiene_poder_predictivo_sobre_volatilidad_con_memoria(self) -> None:
        design, target = self._datos()
        n = design.shape[0]
        corte = int(n * 0.7)
        m = HARVolForecaster().fit(design[:corte], target[:corte])
        pred = m.predict(design[corte:])
        real = target[corte:]
        sse = np.sum((real - pred) ** 2)
        sst = np.sum((real - real.mean()) ** 2)
        assert 1 - sse / sst > 0.2

    def test_sin_ajustar_devuelve_nan(self) -> None:
        m = HARVolForecaster()
        assert np.all(np.isnan(m.predict(np.ones((5, 4)))))

    def test_datos_insuficientes_no_ajusta(self) -> None:
        m = HARVolForecaster().fit(np.ones((3, 4)), np.array([0.01, 0.02, 0.03]))
        assert m.coefs is None

    def test_correccion_de_jensen_eleva_el_forecast(self) -> None:
        """Sin el término de varianza el forecast queda sesgado hacia abajo."""
        design, target = self._datos()
        m = HARVolForecaster().fit(design, target)
        assert m.resid_var > 0
        con_correccion = m.predict(design)
        sin_correccion = np.exp(design @ m.coefs)  # type: ignore[operator]
        assert np.all(con_correccion > sin_correccion)

    def test_no_subestima_sistematicamente(self) -> None:
        design, target = self._datos()
        n = design.shape[0]
        corte = int(n * 0.7)
        m = HARVolForecaster().fit(design[:corte], target[:corte])
        pred = m.predict(design[corte:])
        sesgo = np.mean(pred - target[corte:]) / np.mean(target[corte:])
        assert abs(sesgo) < 0.25
