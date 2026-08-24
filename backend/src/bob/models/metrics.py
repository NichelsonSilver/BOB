"""Métricas de evaluación — PURO, sin I/O.

Separadas por tipo de target porque cada uno miente de forma distinta:

- **Probabilidad** (KPI 1): lo que importa NO es el accuracy sino la
  calibración. Un modelo que dice 70% y acierta 70% es útil aunque su
  accuracy sea 55%; uno que dice 90% y acierta 60% es peligroso aunque
  acierte más veces. De ahí Brier, ECE y la curva de fiabilidad.
- **Volatilidad**: el R² sobre volatilidad es engañoso por la asimetría de
  la distribución; se acompaña de QLIKE, que penaliza más subestimar la
  volatilidad que sobreestimarla — que es exactamente la asimetría de riesgo
  de un trader apalancado.
- **Intervalos**: cobertura empírica vs nominal y ancho medio. Un intervalo
  con 95% de cobertura que abarca todo el rango del día es inútil; la
  métrica honesta es cobertura Y ancho juntos.

Cierra el módulo el test de **Diebold-Mariano**, que responde la pregunta
que casi nunca se hace en proyectos de trading: la mejora sobre el baseline,
¿es estadísticamente distinguible de la suerte?
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import stats

EPS = 1e-12


# ---------------------------------------------------------------------- #
# Probabilidad / clasificación
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReliabilityBucket:
    """Un bucket de la curva de fiabilidad: lo prometido vs lo cumplido."""

    lower: float
    upper: float
    n: int
    mean_predicted: float
    observed_rate: float

    @property
    def error_pp(self) -> float:
        """Error de calibración del bucket, en puntos porcentuales."""
        return abs(self.mean_predicted - self.observed_rate) * 100.0


@dataclass(frozen=True)
class ProbabilityMetrics:
    """Métricas de un forecast probabilístico binario."""

    n: int
    base_rate: float
    brier: float
    brier_skill_score: float  # vs predecir siempre la tasa base
    log_loss: float
    auc: float
    accuracy: float
    ece: float  # Expected Calibration Error (ponderado por n)
    mce: float  # Maximum Calibration Error
    mean_calibration_error_pp: float  # el criterio de gate de la Fase 4
    buckets: list[ReliabilityBucket] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"n={self.n}  base={self.base_rate:.3f}  Brier={self.brier:.4f} "
            f"(BSS={self.brier_skill_score:+.3f})  AUC={self.auc:.3f}  "
            f"ECE={self.ece:.4f}  err_calib={self.mean_calibration_error_pp:.1f}pp"
        )


def brier_score(y_true: np.ndarray, y_prob: np.ndarray, weights: np.ndarray | None = None) -> float:
    """Error cuadrático medio de la probabilidad. Menor es mejor."""
    err = (np.asarray(y_prob, dtype=np.float64) - np.asarray(y_true, dtype=np.float64)) ** 2
    return float(np.average(err, weights=weights))


def log_loss(y_true: np.ndarray, y_prob: np.ndarray, weights: np.ndarray | None = None) -> float:
    """Log loss con clipping para evitar infinitos en predicciones extremas."""
    p = np.clip(np.asarray(y_prob, dtype=np.float64), 1e-7, 1 - 1e-7)
    y = np.asarray(y_true, dtype=np.float64)
    return float(np.average(-(y * np.log(p) + (1 - y) * np.log(1 - p)), weights=weights))


def roc_auc(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """AUC vía el estadístico de Mann-Whitney U (maneja empates por rangos)."""
    y = np.asarray(y_true, dtype=int)
    s = np.asarray(y_score, dtype=np.float64)
    n_pos = int((y == 1).sum())
    n_neg = int((y == 0).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = stats.rankdata(s)
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def reliability_curve(
    y_true: np.ndarray, y_prob: np.ndarray, n_buckets: int = 10, min_bucket_n: int = 20
) -> list[ReliabilityBucket]:
    """Curva de fiabilidad por buckets fijos de probabilidad.

    Buckets fijos (no cuantiles) a propósito: la promesa al usuario es "de
    los casos donde dije 70-80%, acerté X%", y esa frase necesita cortes
    interpretables. Los buckets con menos de `min_bucket_n` casos se
    reportan igual pero no se usan para el gate — con n=3 el observado no
    dice nada.
    """
    y = np.asarray(y_true, dtype=np.float64)
    p = np.asarray(y_prob, dtype=np.float64)
    edges = np.linspace(0.0, 1.0, n_buckets + 1)
    buckets: list[ReliabilityBucket] = []
    for k in range(n_buckets):
        lo, hi = edges[k], edges[k + 1]
        mask = (p >= lo) & (p < hi) if k < n_buckets - 1 else (p >= lo) & (p <= hi)
        count = int(mask.sum())
        if count == 0:
            continue
        buckets.append(
            ReliabilityBucket(
                lower=float(lo),
                upper=float(hi),
                n=count,
                mean_predicted=float(p[mask].mean()),
                observed_rate=float(y[mask].mean()),
            )
        )
    return buckets


def probability_metrics(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    weights: np.ndarray | None = None,
    n_buckets: int = 10,
    min_bucket_n: int = 20,
) -> ProbabilityMetrics:
    """Paquete completo de métricas para el KPI 1."""
    y = np.asarray(y_true, dtype=np.float64)
    p = np.asarray(y_prob, dtype=np.float64)
    n = int(y.size)
    base = float(np.average(y, weights=weights)) if n else float("nan")

    brier = brier_score(y, p, weights)
    brier_base = brier_score(y, np.full_like(p, base), weights)
    bss = 1.0 - brier / brier_base if brier_base > EPS else 0.0

    buckets = reliability_curve(y, p, n_buckets=n_buckets, min_bucket_n=min_bucket_n)
    solid = [b for b in buckets if b.n >= min_bucket_n]
    if solid:
        total = sum(b.n for b in solid)
        ece = sum(b.n * abs(b.mean_predicted - b.observed_rate) for b in solid) / total
        mce = max(abs(b.mean_predicted - b.observed_rate) for b in solid)
        mean_pp = float(np.mean([b.error_pp for b in solid]))
    else:
        ece = mce = float("nan")
        mean_pp = float("nan")

    return ProbabilityMetrics(
        n=n,
        base_rate=base,
        brier=brier,
        brier_skill_score=float(bss),
        log_loss=log_loss(y, p, weights),
        auc=roc_auc(y, p),
        accuracy=float(np.average((p >= 0.5) == (y >= 0.5), weights=weights)),
        ece=float(ece),
        mce=float(mce),
        mean_calibration_error_pp=mean_pp,
        buckets=buckets,
    )


# ---------------------------------------------------------------------- #
# Regresión / volatilidad
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class RegressionMetrics:
    """Métricas de un forecast puntual continuo."""

    n: int
    rmse: float
    mae: float
    r2: float  # vs la media de test
    r2_vs_baseline: float  # ganancia sobre el baseline provisto
    qlike: float  # asimétrico, penaliza subestimar la volatilidad
    mincer_zarnowitz_alpha: float  # sesgo: 0 si insesgado
    mincer_zarnowitz_beta: float  # eficiencia: 1 si el forecast es eficiente

    def summary(self) -> str:
        return (
            f"n={self.n}  RMSE={self.rmse:.5f}  R2={self.r2:+.3f}  "
            f"R2_vs_base={self.r2_vs_baseline:+.3f}  QLIKE={self.qlike:.4f}  "
            f"MZ(a={self.mincer_zarnowitz_alpha:+.4f}, b={self.mincer_zarnowitz_beta:.3f})"
        )


def qlike(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Pérdida QLIKE sobre varianzas: y/ŷ - log(y/ŷ) - 1.

    Estándar en la literatura de volatilidad (Patton 2011). Es robusta a que
    la volatilidad verdadera sea inobservable y castiga fuerte la
    subestimación — la asimetría correcta para quien opera apalancado.
    """
    y = np.maximum(np.asarray(y_true, dtype=np.float64) ** 2, EPS)
    f = np.maximum(np.asarray(y_pred, dtype=np.float64) ** 2, EPS)
    ratio = y / f
    return float(np.mean(ratio - np.log(ratio) - 1.0))


def mincer_zarnowitz(y_true: np.ndarray, y_pred: np.ndarray) -> tuple[float, float]:
    """Regresión y = a + b·ŷ. Un forecast eficiente da a=0, b=1."""
    y = np.asarray(y_true, dtype=np.float64)
    f = np.asarray(y_pred, dtype=np.float64)
    if f.size < 2 or np.std(f) < EPS:
        return float("nan"), float("nan")
    beta, alpha = np.polyfit(f, y, 1)
    return float(alpha), float(beta)


def regression_metrics(
    y_true: np.ndarray, y_pred: np.ndarray, y_baseline: np.ndarray | None = None
) -> RegressionMetrics:
    """Paquete completo para los targets continuos (volatilidad, retorno)."""
    y = np.asarray(y_true, dtype=np.float64)
    f = np.asarray(y_pred, dtype=np.float64)
    resid = y - f
    sse = float(np.sum(resid**2))
    sst = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - sse / sst if sst > EPS else float("nan")

    if y_baseline is not None:
        sse_base = float(np.sum((y - np.asarray(y_baseline, dtype=np.float64)) ** 2))
        r2_base = 1.0 - sse / sse_base if sse_base > EPS else float("nan")
    else:
        r2_base = float("nan")

    alpha, beta = mincer_zarnowitz(y, f)
    return RegressionMetrics(
        n=int(y.size),
        rmse=float(np.sqrt(np.mean(resid**2))),
        mae=float(np.mean(np.abs(resid))),
        r2=float(r2),
        r2_vs_baseline=float(r2_base),
        qlike=qlike(y, f),
        mincer_zarnowitz_alpha=alpha,
        mincer_zarnowitz_beta=beta,
    )


# ---------------------------------------------------------------------- #
# Intervalos de predicción
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class IntervalMetrics:
    """Cobertura y ancho de un intervalo de predicción."""

    n: int
    nominal_coverage: float
    empirical_coverage: float
    mean_width: float
    median_width: float
    winkler_score: float  # cobertura y ancho en un solo número; menor es mejor

    @property
    def coverage_gap_pp(self) -> float:
        return (self.empirical_coverage - self.nominal_coverage) * 100.0

    def summary(self) -> str:
        return (
            f"n={self.n}  nominal={self.nominal_coverage:.0%}  "
            f"empírica={self.empirical_coverage:.1%} "
            f"({self.coverage_gap_pp:+.1f}pp)  ancho medio={self.mean_width:.5f}  "
            f"Winkler={self.winkler_score:.5f}"
        )


def interval_metrics(
    y_true: np.ndarray, lower: np.ndarray, upper: np.ndarray, nominal: float
) -> IntervalMetrics:
    """Evalúa un intervalo. El Winkler score combina ancho y penalización por fallo."""
    y = np.asarray(y_true, dtype=np.float64)
    lo = np.asarray(lower, dtype=np.float64)
    hi = np.asarray(upper, dtype=np.float64)
    inside = (y >= lo) & (y <= hi)
    width = hi - lo

    alpha = 1.0 - nominal
    penalty = np.zeros_like(width)
    below = y < lo
    above = y > hi
    penalty[below] = (2.0 / alpha) * (lo[below] - y[below])
    penalty[above] = (2.0 / alpha) * (y[above] - hi[above])

    return IntervalMetrics(
        n=int(y.size),
        nominal_coverage=float(nominal),
        empirical_coverage=float(inside.mean()) if y.size else float("nan"),
        mean_width=float(width.mean()) if y.size else float("nan"),
        median_width=float(np.median(width)) if y.size else float("nan"),
        winkler_score=float(np.mean(width + penalty)) if y.size else float("nan"),
    )


# ---------------------------------------------------------------------- #
# Significancia estadística
# ---------------------------------------------------------------------- #


@dataclass(frozen=True)
class DieboldMariano:
    """Resultado del test DM: ¿el modelo le gana al baseline por skill o por suerte?"""

    statistic: float
    p_value: float
    mean_loss_diff: float  # negativo = el modelo pierde menos que el baseline
    horizon: int
    n: int

    @property
    def favors_model(self) -> bool:
        return self.mean_loss_diff < 0 and self.p_value < 0.05

    def verdict(self) -> str:
        if np.isnan(self.p_value):
            return "sin datos suficientes para concluir"
        if self.p_value >= 0.05:
            return f"diferencia NO significativa (p={self.p_value:.3f}) — indistinguible de suerte"
        better = "modelo" if self.mean_loss_diff < 0 else "baseline"
        return f"gana el {better} de forma significativa (p={self.p_value:.4f})"


def diebold_mariano(
    loss_model: np.ndarray,
    loss_baseline: np.ndarray,
    horizon: int = 1,
    harvey_correction: bool = True,
) -> DieboldMariano:
    """Test de Diebold-Mariano (1995) con corrección de Harvey et al. (1997).

    Compara dos secuencias de pérdidas sobre las MISMAS observaciones. La
    varianza se estima con Newey-West a `horizon - 1` rezagos, porque los
    forecasts a h pasos tienen errores autocorrelacionados hasta h-1 —
    ignorarlo produce p-values artificialmente diminutos, que es la forma
    más común de "demostrar" un edge inexistente.
    """
    d = np.asarray(loss_model, dtype=np.float64) - np.asarray(loss_baseline, dtype=np.float64)
    d = d[np.isfinite(d)]
    n = int(d.size)
    if n < 10:
        return DieboldMariano(float("nan"), float("nan"), float("nan"), horizon, n)

    d_mean = float(d.mean())
    demeaned = d - d_mean

    gamma0 = float(np.mean(demeaned**2))
    var = gamma0
    for lag in range(1, max(horizon, 1)):
        if lag >= n:
            break
        gamma = float(np.mean(demeaned[lag:] * demeaned[:-lag]))
        var += 2.0 * (1.0 - lag / horizon) * gamma  # kernel de Bartlett
    var = max(var, EPS)

    stat = d_mean / np.sqrt(var / n)

    if harvey_correction:
        h = max(horizon, 1)
        factor = (n + 1 - 2 * h + h * (h - 1) / n) / n
        stat *= np.sqrt(max(factor, EPS))
        p = float(2.0 * stats.t.sf(abs(stat), df=n - 1))
    else:
        p = float(2.0 * stats.norm.sf(abs(stat)))

    return DieboldMariano(
        statistic=float(stat),
        p_value=p,
        mean_loss_diff=d_mean,
        horizon=horizon,
        n=n,
    )


def squared_error(y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
    """Pérdida puntual para alimentar el DM en targets continuos."""
    return (np.asarray(y_true, dtype=np.float64) - np.asarray(y_pred, dtype=np.float64)) ** 2


def brier_loss_series(y_true: np.ndarray, y_prob: np.ndarray) -> np.ndarray:
    """Pérdida puntual para alimentar el DM en targets binarios."""
    return (np.asarray(y_prob, dtype=np.float64) - np.asarray(y_true, dtype=np.float64)) ** 2
