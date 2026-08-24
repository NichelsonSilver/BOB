"""Baselines de referencia — PUROS, sin I/O.

Un modelo de mercado sin baseline no significa nada. El R² de 0.99 que sale
de predecir el *nivel* del precio no mide skill: mide que el precio de
mañana se parece al de hoy. La única cifra interpretable es cuánto le ganas
a la alternativa trivial.

Cada baseline de acá es el estándar competitivo de su target, no un
hombre de paja:

- `RandomWalkForecaster` — para retornos. Bajo la hipótesis de mercado
  eficiente en forma débil, el mejor predictor del retorno futuro es cero.
  Es sorprendentemente difícil de batir y por eso es EL baseline.
- `EWMAVolForecaster` — RiskMetrics (λ=0.94), un IGARCH(1,1) con parámetros
  fijos. Es el estándar de la industria desde JP Morgan (1996).
- `GarchVolForecaster` — GARCH(1,1) con QMLE gaussiana. El baseline
  académico de volatilidad desde Bollerslev (1986).
- `HARVolForecaster` — HAR-RV de Corsi (2009): regresión de la volatilidad
  futura sobre sus componentes diario / semanal / mensual. Simple, lineal, y
  sigue siendo difícil de batir con modelos complejos.
- `BaseRateClassifier` — para el target binario: predecir siempre la tasa
  base del train. Está perfectamente calibrado por construcción y no tiene
  ningún poder discriminante: separa "mi modelo calibra" de "mi modelo sabe algo".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import optimize

EPS = 1e-12


@dataclass
class BaseRateClassifier:
    """Predice siempre la tasa base observada en train."""

    rate: float = 0.5

    def fit(
        self, y: np.ndarray, weights: np.ndarray | None = None
    ) -> BaseRateClassifier:
        self.rate = float(np.average(np.asarray(y, dtype=np.float64), weights=weights))
        return self

    def predict_proba(self, n: int) -> np.ndarray:
        return np.full(n, self.rate, dtype=np.float64)


@dataclass
class RandomWalkForecaster:
    """Predice retorno futuro cero (equivalente a "el precio no cambia").

    Con `drift=True` usa el drift medio del train, que en cripto suele
    empeorar el forecast out-of-sample: el drift histórico es ruido.
    """

    use_drift: bool = False
    drift: float = 0.0

    def fit(self, y: np.ndarray) -> RandomWalkForecaster:
        self.drift = float(np.mean(np.asarray(y, dtype=np.float64))) if self.use_drift else 0.0
        return self

    def predict(self, n: int) -> np.ndarray:
        return np.full(n, self.drift, dtype=np.float64)


@dataclass
class EWMAVolForecaster:
    """RiskMetrics: sigma²_t = λ·sigma²_{t-1} + (1-λ)·r²_{t-1}.

    Para el horizonte h la varianza agregada es h·sigma²_t, porque en un
    IGARCH la varianza condicional no revierte a una media.
    """

    lam: float = 0.94
    horizon: int = 16

    def predict_from_returns(self, returns: np.ndarray) -> np.ndarray:
        """Volatilidad agregada a `horizon` barras, causal, para cada índice."""
        r = np.nan_to_num(np.asarray(returns, dtype=np.float64), nan=0.0)
        n = r.size
        var = np.empty(n)
        if n == 0:
            return var
        seed = float(np.mean(r[: min(n, 100)] ** 2))
        var[0] = max(seed, EPS)
        for i in range(1, n):
            var[i] = self.lam * var[i - 1] + (1.0 - self.lam) * r[i - 1] ** 2
        return np.sqrt(np.maximum(var * self.horizon, EPS))


@dataclass
class GarchVolForecaster:
    """GARCH(1,1) con quasi-máxima verosimilitud gaussiana.

    sigma²_t = omega + alpha·eps²_{t-1} + beta·sigma²_{t-1}

    El forecast agregado a h barras suma las varianzas condicionales
    esperadas, que revierten a la incondicional a tasa (alpha+beta)^k. Ese
    término de reversión es justo lo que el EWMA no tiene: tras un shock, el
    GARCH proyecta que la volatilidad baja; el EWMA la mantiene alta.

    Si la optimización no converge, cae a EWMA en vez de devolver basura.
    """

    horizon: int = 16
    omega: float = 0.0
    alpha: float = 0.05
    beta: float = 0.90
    converged: bool = False
    _scale: float = field(default=1.0, repr=False)

    def fit(self, returns: np.ndarray) -> GarchVolForecaster:
        r = np.asarray(returns, dtype=np.float64)
        r = r[np.isfinite(r)]
        if r.size < 100:
            self.converged = False
            return self

        # Reescalar a ~unidad: los retornos de 15m son ~1e-3 y la
        # optimización sobre varianzas de 1e-6 es numéricamente frágil.
        self._scale = float(np.std(r))
        if self._scale < EPS:
            self.converged = False
            return self
        x = r / self._scale

        def nll(params: np.ndarray) -> float:
            omega, alpha, beta = params
            if omega <= 0 or alpha < 0 or beta < 0 or alpha + beta >= 0.9999:
                return 1e10
            var = np.empty(x.size)
            var[0] = omega / max(1.0 - alpha - beta, EPS)
            for i in range(1, x.size):
                var[i] = omega + alpha * x[i - 1] ** 2 + beta * var[i - 1]
            var = np.maximum(var, EPS)
            return float(0.5 * np.sum(np.log(var) + x**2 / var))

        result = optimize.minimize(
            nll,
            x0=np.array([0.05, 0.08, 0.88]),
            method="L-BFGS-B",
            bounds=[(1e-8, 1.0), (0.0, 0.5), (0.0, 0.999)],
        )
        if result.success and np.isfinite(result.fun):
            self.omega, self.alpha, self.beta = (float(v) for v in result.x)
            self.converged = self.alpha + self.beta < 0.9999
        else:
            self.converged = False
        return self

    def predict_from_returns(self, returns: np.ndarray) -> np.ndarray:
        """Volatilidad agregada a `horizon`, causal, para cada índice."""
        if not self.converged:
            return EWMAVolForecaster(horizon=self.horizon).predict_from_returns(returns)

        r = np.nan_to_num(np.asarray(returns, dtype=np.float64), nan=0.0) / self._scale
        n = r.size
        persistence = self.alpha + self.beta
        uncond = self.omega / max(1.0 - persistence, EPS)

        var = np.empty(n)
        if n == 0:
            return var
        var[0] = uncond
        for i in range(1, n):
            var[i] = self.omega + self.alpha * r[i - 1] ** 2 + self.beta * var[i - 1]

        # Varianza agregada de las próximas h barras a partir de sigma²_{t+1}.
        next_var = self.omega + self.alpha * r**2 + self.beta * var
        h = self.horizon
        if abs(1.0 - persistence) < 1e-8:
            agg = h * next_var
        else:
            geom = (1.0 - persistence**h) / (1.0 - persistence)
            agg = h * uncond + (next_var - uncond) * geom
        return np.sqrt(np.maximum(agg, EPS)) * self._scale


@dataclass
class HARVolForecaster:
    """HAR-RV de Corsi (2009) sobre log-volatilidad.

    log(RV_futura) = c + b_d·log(RV_diaria) + b_w·log(RV_semanal) + b_m·log(RV_mensual)

    Captura la memoria larga de la volatilidad con tres regresores y OLS. Se
    ajusta en logs porque la volatilidad es aproximadamente log-normal, lo
    que hace que los residuos sean simétricos y el OLS sea el estimador
    correcto en vez de un apaño.
    """

    coefs: np.ndarray | None = None
    resid_var: float = 0.0

    @staticmethod
    def design_matrix(
        rv_short: np.ndarray, rv_mid: np.ndarray, rv_long: np.ndarray
    ) -> np.ndarray:
        """Matriz [1, log rv_corta, log rv_media, log rv_larga]."""
        cols = [
            np.ones(rv_short.shape[0]),
            np.log(np.maximum(rv_short, EPS)),
            np.log(np.maximum(rv_mid, EPS)),
            np.log(np.maximum(rv_long, EPS)),
        ]
        return np.column_stack(cols)

    def fit(self, design: np.ndarray, y_vol: np.ndarray) -> HARVolForecaster:
        y = np.log(np.maximum(np.asarray(y_vol, dtype=np.float64), EPS))
        mask = np.all(np.isfinite(design), axis=1) & np.isfinite(y)
        if mask.sum() < design.shape[1] + 1:
            self.coefs = None
            return self
        self.coefs, *_ = np.linalg.lstsq(design[mask], y[mask], rcond=None)
        resid = y[mask] - design[mask] @ self.coefs
        self.resid_var = float(np.var(resid))
        return self

    def predict(self, design: np.ndarray) -> np.ndarray:
        """Forecast en niveles de volatilidad, no en logs.

        Corrección de Jensen: E[exp(x)] = exp(E[x] + var/2) para x normal.
        Sin el término de varianza el forecast queda sistemáticamente
        sesgado hacia abajo — y subestimar la volatilidad es exactamente el
        error que liquida cuentas apalancadas.
        """
        if self.coefs is None:
            return np.full(design.shape[0], np.nan)
        log_pred = np.asarray(design @ self.coefs, dtype=np.float64)
        return np.exp(log_pred + 0.5 * self.resid_var)
