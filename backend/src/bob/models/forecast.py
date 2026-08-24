"""Estimadores del stack de forecasting — PUROS, sin I/O.

Tres modelos, uno por target:

- `BarrierProbabilityModel` — P(TP antes que SL). Clasificación binaria con
  calibración isotónica sobre predicciones out-of-fold *purgadas*. Es el
  KPI 1 de CLAUDE.md.
- `VolatilityModel` — volatilidad realizada futura. Regresión sobre
  log-volatilidad con corrección de Jensen al devolver a niveles.
- `ConformalReturnInterval` — cono de precio. Regresión cuantílica
  conformalizada (CQR): intervalos con cobertura garantizada en vez de
  bandas ±2σ que asumen normalidad que los retornos no tienen.

Por qué gradient boosting y no una red
---------------------------------------
Sobre datos tabulares de este tamaño (decenas de miles de filas, decenas de
features), el boosting sigue siendo el estado del arte y entrena en
segundos, lo que permite correr walk-forward completo muchas veces. Además
es auditable: se puede sacar importancia por permutación y explicar qué
familia de features mueve la probabilidad. Un modelo que no se puede
explicar no debería mover capital apalancado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
)
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler

from bob.models.validation import purged_walk_forward

EPS = 1e-12

ModelKind = Literal["logistic", "gbm"]


def _gbm_classifier(seed: int) -> HistGradientBoostingClassifier:
    """Boosting de capacidad deliberadamente modesta.

    `early_stopping=False` a propósito: el early stopping de sklearn parte
    un set de validación **aleatorio**, lo que en una serie temporal con
    labels solapados es fuga. Se prefiere capacidad fija y conservadora, que
    es reproducible y no miente.
    """
    return HistGradientBoostingClassifier(
        max_iter=250,
        learning_rate=0.05,
        max_leaf_nodes=15,
        min_samples_leaf=100,
        l2_regularization=1.0,
        max_bins=128,
        early_stopping=False,
        random_state=seed,
    )


def _gbm_regressor(
    seed: int, loss: str = "squared_error", quantile: float | None = None
) -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        loss=loss,
        quantile=quantile,
        max_iter=250,
        learning_rate=0.05,
        max_leaf_nodes=15,
        min_samples_leaf=100,
        l2_regularization=1.0,
        max_bins=128,
        early_stopping=False,
        random_state=seed,
    )


@dataclass
class BarrierProbabilityModel:
    """Probabilidad calibrada de que un setup toque TP antes que SL.

    La calibración isotónica se ajusta sobre predicciones out-of-fold
    generadas con walk-forward purgado *dentro* del train. Calibrar sobre
    predicciones in-sample produce un mapa que corrige un sobreajuste que no
    existirá en test — y deja el modelo peor calibrado que sin calibrar.
    """

    kind: ModelKind = "gbm"
    calibrate: bool = True
    n_inner_folds: int = 4
    seed: int = 42

    _model: object | None = field(default=None, repr=False)
    _scaler: StandardScaler | None = field(default=None, repr=False)
    _isotonic: IsotonicRegression | None = field(default=None, repr=False)
    _train_base_rate: float = field(default=0.5, repr=False)

    def _new_estimator(self) -> object:
        if self.kind == "logistic":
            return LogisticRegression(C=1.0, max_iter=2000, random_state=self.seed)
        return _gbm_classifier(self.seed)

    def _fit_estimator(
        self,
        X: np.ndarray,
        y: np.ndarray,
        w: np.ndarray | None,
    ) -> tuple[object, StandardScaler | None]:
        scaler: StandardScaler | None = None
        X_fit = X
        if self.kind == "logistic":
            # El boosting es invariante a escala; la logística no.
            scaler = StandardScaler().fit(X)
            X_fit = scaler.transform(X)
        est = self._new_estimator()
        est.fit(X_fit, y, sample_weight=w)  # type: ignore[attr-defined]
        return est, scaler

    @staticmethod
    def _raw_proba(est: object, scaler: StandardScaler | None, X: np.ndarray) -> np.ndarray:
        X_use = scaler.transform(X) if scaler is not None else X
        proba = est.predict_proba(X_use)  # type: ignore[attr-defined]
        return np.asarray(proba, dtype=np.float64)[:, 1]

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        train_idx: np.ndarray,
        span: np.ndarray,
        sample_weight: np.ndarray | None = None,
    ) -> BarrierProbabilityModel:
        """Ajusta sobre `train_idx` (índices globales de X/y)."""
        X_tr, y_tr = X[train_idx], y[train_idx]
        w_tr = sample_weight[train_idx] if sample_weight is not None else None
        self._train_base_rate = float(np.average(y_tr, weights=w_tr))

        if self.calibrate:
            oof_pred, oof_true, oof_w = self._out_of_fold(X, y, train_idx, span, sample_weight)
            if oof_pred.size >= 200:
                self._isotonic = IsotonicRegression(
                    y_min=0.0, y_max=1.0, out_of_bounds="clip"
                ).fit(oof_pred, oof_true, sample_weight=oof_w)
            else:
                self._isotonic = None

        self._model, self._scaler = self._fit_estimator(X_tr, y_tr, w_tr)
        return self

    def _out_of_fold(
        self,
        X: np.ndarray,
        y: np.ndarray,
        train_idx: np.ndarray,
        span: np.ndarray,
        sample_weight: np.ndarray | None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
        """Predicciones OOF dentro del train, con purga entre folds internos."""
        try:
            inner = purged_walk_forward(
                train_idx, span, n_splits=self.n_inner_folds, min_train_frac=0.4
            )
        except ValueError:
            return np.array([]), np.array([]), None

        preds: list[np.ndarray] = []
        trues: list[np.ndarray] = []
        weights: list[np.ndarray] = []
        for split in inner:
            w_in = sample_weight[split.train_idx] if sample_weight is not None else None
            if np.unique(y[split.train_idx]).size < 2:
                continue
            est, scaler = self._fit_estimator(X[split.train_idx], y[split.train_idx], w_in)
            preds.append(self._raw_proba(est, scaler, X[split.test_idx]))
            trues.append(y[split.test_idx])
            if sample_weight is not None:
                weights.append(sample_weight[split.test_idx])

        if not preds:
            return np.array([]), np.array([]), None
        return (
            np.concatenate(preds),
            np.concatenate(trues),
            np.concatenate(weights) if weights else None,
        )

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("el modelo no está ajustado")
        raw = self._raw_proba(self._model, self._scaler, X)
        if self._isotonic is None:
            return raw
        mapped = np.asarray(self._isotonic.predict(raw), dtype=np.float64)
        return np.clip(mapped, 0.0, 1.0)

    def predict_proba_uncalibrated(self, X: np.ndarray) -> np.ndarray:
        """Salida cruda del clasificador — para medir cuánto aporta calibrar."""
        if self._model is None:
            raise RuntimeError("el modelo no está ajustado")
        return self._raw_proba(self._model, self._scaler, X)


@dataclass
class VolatilityModel:
    """Forecast de volatilidad realizada a H barras.

    Se entrena sobre log(volatilidad) por dos razones: la distribución es
    aproximadamente log-normal (residuos simétricos → la pérdida cuadrática
    es la correcta) y garantiza que el forecast devuelto sea positivo, que
    una regresión en niveles no garantiza.
    """

    kind: Literal["ridge", "gbm"] = "gbm"
    seed: int = 42

    _model: object | None = field(default=None, repr=False)
    _scaler: StandardScaler | None = field(default=None, repr=False)
    _resid_var: float = field(default=0.0, repr=False)

    def fit(
        self, X: np.ndarray, y_vol: np.ndarray, train_idx: np.ndarray
    ) -> VolatilityModel:
        X_tr = X[train_idx]
        y_tr = np.log(np.maximum(y_vol[train_idx], EPS))

        if self.kind == "ridge":
            self._scaler = StandardScaler().fit(X_tr)
            model: object = Ridge(alpha=1.0, random_state=self.seed)
            model.fit(self._scaler.transform(X_tr), y_tr)  # type: ignore[attr-defined]
        else:
            self._scaler = None
            model = _gbm_regressor(self.seed)
            model.fit(X_tr, y_tr)  # type: ignore[attr-defined]

        self._model = model
        fitted = self._predict_log(X_tr)
        self._resid_var = float(np.var(y_tr - fitted))
        return self

    def _predict_log(self, X: np.ndarray) -> np.ndarray:
        if self._model is None:
            raise RuntimeError("el modelo no está ajustado")
        X_use = self._scaler.transform(X) if self._scaler is not None else X
        return np.asarray(self._model.predict(X_use), dtype=np.float64)  # type: ignore[attr-defined]

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Volatilidad en niveles, con corrección de Jensen."""
        return np.exp(self._predict_log(X) + 0.5 * self._resid_var)


@dataclass
class ConformalReturnInterval:
    """Cono de precio por regresión cuantílica conformalizada (CQR).

    Romano, Patterson & Candès (2019). Dos pasos:

    1. Se ajustan regresores cuantílicos a los niveles alpha/2 y 1-alpha/2
       sobre el tramo *propio* de entrenamiento.
    2. Sobre un tramo de calibración **disjunto y posterior** se miden los
       scores de conformidad E = max(q_lo - y, y - q_hi) y se corrige el
       intervalo por el cuantil (1-alpha) de esos scores.

    El resultado tiene cobertura marginal >= 1-alpha sin asumir ninguna
    distribución. La banda ±2σ que se usa por defecto en trading asume
    normalidad, y los retornos de cripto tienen colas gordas: esa banda
    subcubre justo en los días que importan.

    **Honestidad sobre el supuesto**: la garantía exige intercambiabilidad,
    que una serie financiera no cumple estrictamente. Por eso
    `adaptive=True` activa ACI (Gibbs & Candès, 2021), que ajusta alpha en
    línea según los fallos observados y recupera la cobertura empírica bajo
    cambio de régimen. La cobertura real se reporta siempre, medida, nunca
    asumida.
    """

    alpha: float = 0.20  # cobertura nominal 80%
    calib_frac: float = 0.30
    adaptive: bool = True
    gamma: float = 0.01  # tasa de aprendizaje de ACI
    seed: int = 42

    _lo_model: object | None = field(default=None, repr=False)
    _hi_model: object | None = field(default=None, repr=False)
    _correction: float = field(default=0.0, repr=False)
    _alpha_t: float = field(default=0.20, repr=False)

    def fit(
        self, X: np.ndarray, y: np.ndarray, train_idx: np.ndarray
    ) -> ConformalReturnInterval:
        """Ajusta cuantiles y calibra. `train_idx` debe venir en orden temporal."""
        idx = np.sort(np.asarray(train_idx, dtype=np.int64))
        n = idx.size
        n_calib = max(50, int(n * self.calib_frac))
        if n <= n_calib + 50:
            raise ValueError("train insuficiente para separar tramo de calibración")

        proper, calib = idx[: n - n_calib], idx[n - n_calib :]

        self._lo_model = _gbm_regressor(self.seed, loss="quantile", quantile=self.alpha / 2.0)
        self._hi_model = _gbm_regressor(
            self.seed, loss="quantile", quantile=1.0 - self.alpha / 2.0
        )
        self._lo_model.fit(X[proper], y[proper])  # type: ignore[attr-defined]
        self._hi_model.fit(X[proper], y[proper])  # type: ignore[attr-defined]

        q_lo = np.asarray(self._lo_model.predict(X[calib]), dtype=np.float64)  # type: ignore[attr-defined]
        q_hi = np.asarray(self._hi_model.predict(X[calib]), dtype=np.float64)  # type: ignore[attr-defined]
        scores = np.maximum(q_lo - y[calib], y[calib] - q_hi)

        # Cuantil conformal con la corrección de muestra finita (n+1).
        k = int(np.ceil((n_calib + 1) * (1.0 - self.alpha)))
        k = min(max(k, 1), n_calib)
        self._correction = float(np.sort(scores)[k - 1])
        self._alpha_t = self.alpha
        return self

    def predict_interval(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Intervalo [lo, hi] para el retorno log futuro."""
        if self._lo_model is None or self._hi_model is None:
            raise RuntimeError("el modelo no está ajustado")
        q_lo = np.asarray(self._lo_model.predict(X), dtype=np.float64)  # type: ignore[attr-defined]
        q_hi = np.asarray(self._hi_model.predict(X), dtype=np.float64)  # type: ignore[attr-defined]
        return q_lo - self._correction, q_hi + self._correction

    def predict_interval_adaptive(
        self, X: np.ndarray, y_true: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """ACI: recorre el test en orden y ajusta alpha tras cada observación.

        Solo usa el resultado de la barra t para el intervalo de t+1, nunca
        para el suyo: es online, no lookahead.
        """
        if not self.adaptive:
            return self.predict_interval(X)

        lo_base, hi_base = self.predict_interval(X)
        n = X.shape[0]
        lo_out = np.empty(n)
        hi_out = np.empty(n)
        alpha_t = self.alpha
        half_width = (hi_base - lo_base) / 2.0
        center = (hi_base + lo_base) / 2.0

        for t in range(n):
            # Escala el ancho según la desviación de alpha_t respecto del nominal.
            scale = max(0.1, 1.0 + (self.alpha - alpha_t) / max(self.alpha, EPS))
            lo_out[t] = center[t] - half_width[t] * scale
            hi_out[t] = center[t] + half_width[t] * scale
            err = 0.0 if lo_out[t] <= y_true[t] <= hi_out[t] else 1.0
            alpha_t = float(np.clip(alpha_t + self.gamma * (self.alpha - err), 1e-3, 0.999))

        self._alpha_t = alpha_t
        return lo_out, hi_out
