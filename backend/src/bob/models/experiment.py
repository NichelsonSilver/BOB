"""Runner de experimento walk-forward — PURO, sin I/O de red.

Orquesta el pipeline completo y produce un resultado auditable:

    velas → features → labels → splits purgados → modelos vs baselines
          → métricas OOS → tests de significancia → reporte

Todas las métricas que reporta son **out-of-sample y pooled**: se concatenan
las predicciones de los folds de test, que ningún modelo vio al entrenar.
No se reporta una sola métrica in-sample, ni siquiera "de referencia":
publicarlas al lado de las OOS es la vía más rápida a que alguien cite la
que no corresponde.

El resultado serializa a dict → JSON, y de ahí a `BacktestRun` en DB y a la
página de Backtest del dashboard.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from loguru import logger

from bob.data.store import BookDepthSeries, DerivativesSeries, OHLCVSeries
from bob.models import metrics as mx
from bob.models.baselines import (
    BaseRateClassifier,
    EWMAVolForecaster,
    GarchVolForecaster,
    HARVolForecaster,
    RandomWalkForecaster,
)
from bob.models.forecast import (
    BarrierProbabilityModel,
    ConformalReturnInterval,
    ModelKind,
    VolatilityModel,
)
from bob.models.labeling import (
    BarrierConfig,
    forward_return,
    forward_volatility,
    target_volatility,
    triple_barrier_labels,
    uniqueness_weights,
)
from bob.signals import numeric as nm
from bob.signals.derivatives import build_derivative_features
from bob.signals.features import FeatureSet, build_features, feature_families
from bob.signals.microstructure import build_microstructure_features

MODEL_VERSION = "bob-forecast-0.1.0"


@dataclass(frozen=True)
class ExperimentConfig:
    """Configuración reproducible de un experimento."""

    barrier: BarrierConfig = field(default_factory=BarrierConfig)
    directions: tuple[str, ...] = ("long", "short")
    n_splits: int = 6
    min_train_frac: float = 0.35
    embargo_frac: float = 0.01
    expanding: bool = True
    model_kind: ModelKind = "gbm"
    vol_kind: str = "gbm"
    conformal_alphas: tuple[float, ...] = (0.20, 0.05)
    signal_threshold: float = 0.70
    seed: int = 42

    #: Familias de features que entran al modelo. "price" son las 55 de la
    #: Fase 2; las otras dos llegaron con la Fase 2b y solo tienen efecto si
    #: el runner pasa las series correspondientes.
    #:
    #: `use_book_near` va aparte a propósito: el nivel de 0,2% existe en el
    #: archivo solo desde 2026-01-15, así que esas columnas cubren ~30% de la
    #: muestra. Mezclarlas con el resto obliga a elegir entre tirar el 70% de
    #: las filas o dejar que el modelo aprenda de un feature que existe en un
    #: solo tramo del periodo — y esa elección tiene que ser explícita.
    use_derivatives: bool = True
    use_book: bool = True
    use_book_near: bool = False

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["barrier"] = asdict(self.barrier)
        out["model_version"] = MODEL_VERSION
        return out


#: Combinaciones de familias, como (derivados, libro, near-touch). Nombrarlas
#: acá y no dejar tres flags sueltos es lo que hace que un run sea citable:
#: "full contra price" dice algo, "--use-book --no-near" no.
#:
#: Vive en este módulo y no en el runner porque el vivo también las necesita:
#: el analista tiene que poder decir con qué variante corre, y comparar esa
#: variante contra la que pasó por el gate.
FEATURE_SETS: dict[str, tuple[bool, bool, bool]] = {
    "price": (False, False, False),
    "price+deriv": (True, False, False),
    "full": (True, True, False),
    "full+near": (True, True, True),
}


def feature_set_name(config: ExperimentConfig) -> str:
    """Nombre corto de la combinación de familias, para etiquetar un run."""
    for name, flags in FEATURE_SETS.items():
        if flags == (config.use_derivatives, config.use_book, config.use_book_near):
            return name
    return "custom"


@dataclass
class DirectionResult:
    """Resultado del target de barrera para una dirección."""

    direction: str
    n_samples: int
    effective_n: float
    resolution_mix: dict[str, float]
    breakeven_prob: float
    metrics_model: mx.ProbabilityMetrics
    metrics_uncalibrated: mx.ProbabilityMetrics
    metrics_baseline: mx.ProbabilityMetrics
    dm_vs_baseline: mx.DieboldMariano
    trading: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "n_samples": self.n_samples,
            "effective_n": self.effective_n,
            "resolution_mix": self.resolution_mix,
            "breakeven_prob": self.breakeven_prob,
            "model": _prob_metrics_dict(self.metrics_model),
            "uncalibrated": _prob_metrics_dict(self.metrics_uncalibrated),
            "baseline_base_rate": _prob_metrics_dict(self.metrics_baseline),
            "diebold_mariano": {
                "statistic": self.dm_vs_baseline.statistic,
                "p_value": self.dm_vs_baseline.p_value,
                "mean_loss_diff": self.dm_vs_baseline.mean_loss_diff,
                "verdict": self.dm_vs_baseline.verdict(),
            },
            "trading": self.trading,
        }


@dataclass
class VolatilityResult:
    """Resultado del target de volatilidad, con sus tres baselines."""

    n_samples: int
    model: mx.RegressionMetrics
    ewma: mx.RegressionMetrics
    garch: mx.RegressionMetrics
    har: mx.RegressionMetrics
    dm_vs_ewma: mx.DieboldMariano
    dm_vs_har: mx.DieboldMariano

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_samples": self.n_samples,
            "model": asdict(self.model),
            "baseline_ewma": asdict(self.ewma),
            "baseline_garch": asdict(self.garch),
            "baseline_har": asdict(self.har),
            "dm_vs_ewma": {
                "p_value": self.dm_vs_ewma.p_value,
                "mean_loss_diff": self.dm_vs_ewma.mean_loss_diff,
                "verdict": self.dm_vs_ewma.verdict(),
            },
            "dm_vs_har": {
                "p_value": self.dm_vs_har.p_value,
                "mean_loss_diff": self.dm_vs_har.mean_loss_diff,
                "verdict": self.dm_vs_har.verdict(),
            },
        }


@dataclass
class IntervalResult:
    """Cobertura del cono conformal vs la banda gaussiana, por nivel nominal."""

    conformal: dict[float, mx.IntervalMetrics]
    gaussian: dict[float, mx.IntervalMetrics]

    def to_dict(self) -> dict[str, Any]:
        return {
            "conformal": {f"{k:.2f}": asdict(v) for k, v in self.conformal.items()},
            "gaussian": {f"{k:.2f}": asdict(v) for k, v in self.gaussian.items()},
        }


#: Umbrales del criterio 2 del gate (CLAUDE.md, Fase 4). Viven acá para que
#: el reporte marque exactamente lo mismo que decide `discriminates()`.
GATE_MIN_AUC = 0.55
GATE_MIN_BSS = 0.0
GATE_MAX_CALIBRATION_ERROR_PP = 10.0


def direction_discriminates(
    m: mx.ProbabilityMetrics,
    min_auc: float = GATE_MIN_AUC,
    min_bss: float = GATE_MIN_BSS,
) -> bool:
    """Discriminación de UNA dirección: ordena casos (AUC) y aporta sobre la tasa base (BSS)."""
    return bool(
        np.isfinite(m.auc)
        and m.auc >= min_auc
        and np.isfinite(m.brier_skill_score)
        and m.brier_skill_score > min_bss
    )


@dataclass
class ExperimentResult:
    """Todo lo que produce un run, listo para serializar."""

    symbol: str
    timeframe: str
    config: ExperimentConfig
    n_bars: int
    n_features: int
    feature_names: list[str]
    date_from: str
    date_to: str
    folds: list[dict[str, Any]]
    directions: dict[str, DirectionResult]
    volatility: VolatilityResult
    intervals: IntervalResult
    importance: list[tuple[str, float]]
    family_importance: dict[str, float]
    runtime_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_version": MODEL_VERSION,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "config": self.config.to_dict(),
            "n_bars": self.n_bars,
            "n_features": self.n_features,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "folds": self.folds,
            "directions": {k: v.to_dict() for k, v in self.directions.items()},
            "volatility": self.volatility.to_dict(),
            "intervals": self.intervals.to_dict(),
            "importance_top": self.importance[:20],
            "family_importance": self.family_importance,
            "runtime_s": self.runtime_s,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=float)

    def gate_passed(
        self, max_calibration_error_pp: float = GATE_MAX_CALIBRATION_ERROR_PP
    ) -> bool:
        """Criterio de salida de la Fase 4 de CLAUDE.md.

        Exige que TODA dirección evaluada calibre por debajo del umbral. Con
        una sola dirección calibrada no se habilita nada: el dashboard
        ofrece ambas.
        """
        errors = [
            d.metrics_model.mean_calibration_error_pp for d in self.directions.values()
        ]
        return bool(errors) and all(
            np.isfinite(e) and e < max_calibration_error_pp for e in errors
        )

    def discriminates(
        self, min_auc: float = GATE_MIN_AUC, min_bss: float = GATE_MIN_BSS
    ) -> bool:
        """¿El modelo distingue casos, o solo reproduce la tasa base?

        Segundo criterio del gate de la Fase 4, tan obligatorio como la
        calibración: un modelo que predice siempre la tasa base está
        **perfectamente calibrado por construcción** y es completamente
        inútil — nunca supera el umbral de emisión y, si lo superara, no
        habría distinguido nada.

        Calibración = "cuando digo 70%, acierto 70%".
        Discriminación = "sé distinguir los casos de 70% de los de 40%".
        Hacen falta las dos, en TODAS las direcciones evaluadas.
        """
        return bool(self.directions) and all(
            direction_discriminates(d.metrics_model, min_auc, min_bss)
            for d in self.directions.values()
        )


def _prob_metrics_dict(m: mx.ProbabilityMetrics) -> dict[str, Any]:
    out = asdict(m)
    out["buckets"] = [asdict(b) | {"error_pp": b.error_pp} for b in m.buckets]
    return out


def _ms_to_date(ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def _trading_summary(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    net_return: np.ndarray,
    threshold: float,
    breakeven: float = float("nan"),
) -> dict[str, float]:
    """Traduce probabilidades a lo que el usuario ve: ¿cuánto y con qué riesgo?

    Aplica la regla de emisión de CLAUDE.md (solo se opera si la
    probabilidad supera el umbral) sobre los retornos NETOS ya calculados en
    el etiquetado. No es un backtest de ejecución — es la consecuencia
    aritmética de las señales que el modelo habría emitido.
    """
    taken = y_prob >= threshold
    n_taken = int(taken.sum())
    if n_taken == 0:
        return {
            "threshold": threshold,
            "breakeven_prob": breakeven,
            "n_signals": 0,
            "signal_rate": 0.0,
            "win_rate": float("nan"),
            "expectancy_pct": float("nan"),
            "profit_factor": float("nan"),
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
        }

    rets = net_return[taken]
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    equity = np.cumsum(rets)
    peak = np.maximum.accumulate(equity)
    drawdown = equity - peak

    gross_loss = float(-losses.sum())
    return {
        "threshold": threshold,
        "breakeven_prob": breakeven,
        "n_signals": n_taken,
        "signal_rate": float(n_taken / y_prob.size),
        "win_rate": float(np.mean(y_true[taken])),
        "expectancy_pct": float(rets.mean() * 100.0),
        "profit_factor": float(wins.sum() / gross_loss) if gross_loss > 1e-12 else float("inf"),
        "total_return_pct": float(equity[-1] * 100.0),
        "max_drawdown_pct": float(drawdown.min() * 100.0),
    }


def _permutation_importance(
    model: BarrierProbabilityModel,
    X: np.ndarray,
    y: np.ndarray,
    names: list[str],
    seed: int,
    n_repeats: int = 3,
) -> list[tuple[str, float]]:
    """Importancia por permutación medida en Brier: cuánto empeora al romper el feature.

    Se mide sobre datos de test, no de train: la importancia in-sample
    premia features que el modelo memorizó, no los que generalizan.
    """
    rng = np.random.default_rng(seed)
    base = mx.brier_score(y, model.predict_proba(X))
    scores: list[tuple[str, float]] = []
    for j, name in enumerate(names):
        deltas = []
        for _ in range(n_repeats):
            X_perm = X.copy()
            X_perm[:, j] = rng.permutation(X_perm[:, j])
            deltas.append(mx.brier_score(y, model.predict_proba(X_perm)) - base)
        scores.append((name, float(np.mean(deltas))))
    scores.sort(key=lambda kv: kv[1], reverse=True)
    return scores


def assemble_features(
    series: OHLCVSeries,
    cfg: ExperimentConfig,
    derivatives: DerivativesSeries | None = None,
    funding: DerivativesSeries | None = None,
    book: BookDepthSeries | None = None,
) -> tuple[np.ndarray, list[str], set[str], dict[str, list[str]]]:
    """Arma la matriz completa: precio + derivados + libro.

    Devuelve `(X, names, sparse, families)`. `sparse` son las columnas que
    **no** entran al criterio de finitud porque su cobertura es parcial por
    construcción, no por un bug: hoy, únicamente las del near-touch.

    PURO: recibe series de numpy, no toca la DB.
    """
    fs: FeatureSet = build_features(series)
    blocks: list[np.ndarray] = [fs.X]
    names: list[str] = list(fs.names)
    sparse: set[str] = set()
    families: dict[str, list[str]] = feature_families(fs.names)

    if cfg.use_derivatives and derivatives is not None and len(derivatives) > 0:
        df = build_derivative_features(series, derivatives, funding)
        blocks.append(df.X)
        names.extend(df.names)
        families["derivados"] = list(df.names)
        logger.info("+{} features de derivados", len(df.names))

    if cfg.use_book and book is not None and len(book) > 0:
        mf = build_microstructure_features(series, book)
        keep = list(mf.names) if cfg.use_book_near else mf.core_names()
        idx = [mf.names.index(nm_) for nm_ in keep]
        blocks.append(mf.X[:, idx])
        names.extend(keep)
        families["libro"] = list(keep)
        if cfg.use_book_near:
            sparse.update(mf.near_names)
        logger.info(
            "+{} features de libro ({} de cobertura parcial)", len(keep), len(sparse)
        )

    return np.column_stack(blocks), names, sparse, families



def assert_columns_trainable(
    X: np.ndarray, names: list[str], min_train_frac: float
) -> None:
    """Falla temprano si alguna columna es NaN entero en el primer train.

    El GBM de sklearn tolera NaN **salvo** cuando una columna no tiene ni un
    valor finito en el set de entrenamiento: ahí el binning no puede calcular
    umbrales y revienta con `window shape cannot be larger than input array
    shape`, un error que no dice absolutamente nada sobre la causa.

    Pasa de verdad, no en teoría: el near-touch del libro existe recién desde
    2026-01-15, así que en los primeros folds del walk-forward esas columnas
    son NaN puro. Un feature que no existe en el tramo donde el modelo aprende
    no es un feature con huecos — es un feature que no se puede evaluar con
    este periodo, y conviene que lo diga en esas palabras.
    """
    n_train = max(1, int(len(X) * min_train_frac))
    vacias = [
        name
        for i, name in enumerate(names)
        if not np.any(np.isfinite(X[:n_train, i]))
    ]
    if vacias:
        muestra = ", ".join(vacias[:5])
        extra = f" (y {len(vacias) - 5} más)" if len(vacias) > 5 else ""
        raise ValueError(
            f"{len(vacias)} columna(s) sin un solo valor en el primer train "
            f"({n_train:,} barras): {muestra}{extra}. El modelo no puede "
            "aprender de un feature que no existe donde entrena — si es el "
            "near-touch del libro, su historia arranca demasiado tarde para "
            "este periodo: correr con use_book_near=False."
        )


def run_experiment(
    series: OHLCVSeries,
    config: ExperimentConfig | None = None,
    derivatives: DerivativesSeries | None = None,
    funding: DerivativesSeries | None = None,
    book: BookDepthSeries | None = None,
) -> ExperimentResult:
    """Corre el experimento completo sobre una serie de velas.

    Las series de derivados y libro son opcionales: sin ellas el experimento
    corre con las 55 features de precio, que es exactamente el baseline contra
    el que hay que comparar.
    """
    from bob.models.validation import assert_no_leakage, purged_walk_forward

    cfg = config or ExperimentConfig()
    started = time.monotonic()
    n = len(series)
    if n < 2000:
        raise ValueError(f"serie demasiado corta para walk-forward: {n} velas")

    logger.info("features sobre {} velas de {} {}", n, series.symbol, series.timeframe)
    X, feature_names, sparse_names, families = assemble_features(
        series, cfg, derivatives, funding, book
    )

    if sparse_names and cfg.model_kind == "logistic":
        raise ValueError(
            "el modelo logístico no admite NaN: correr con use_book_near=False "
            "o con model_kind='gbm'"
        )

    assert_columns_trainable(X, feature_names, cfg.min_train_frac)

    # El criterio de finitud ignora las columnas de cobertura parcial: exigirlas
    # tiraría el 70% de la muestra para ganar 8 columnas que solo existen en el
    # último tramo. El GBM trata el NaN como rama propia y las aprovecha donde
    # están sin necesitar que estén siempre.
    dense_idx = [i for i, nm_ in enumerate(feature_names) if nm_ not in sparse_names]
    finite_rows = np.all(np.isfinite(X[:, dense_idx]), axis=1)
    logger.info(
        "matriz {}x{} — {:,} filas con las columnas densas completas ({:.1f}%)",
        X.shape[0],
        X.shape[1],
        int(finite_rows.sum()),
        100 * finite_rows.mean(),
    )

    horizon = cfg.barrier.horizon_bars
    y_vol = forward_volatility(series.close, horizon)
    y_ret = forward_return(series.close, horizon)

    returns = nm.log_returns(series.close)
    tf_ms = series.interval_ms
    sigma_h = target_volatility(series.close, cfg.barrier.vol_window_bars, horizon)

    # ------------------------------------------------------------------ #
    # Target de barrera, por dirección
    # ------------------------------------------------------------------ #
    direction_results: dict[str, DirectionResult] = {}
    fold_rows: list[dict[str, Any]] = []
    importance: list[tuple[str, float]] = []

    for direction in cfg.directions:
        labels = triple_barrier_labels(
            series.high, series.low, series.close, series.open, cfg.barrier, tf_ms, direction
        )
        span = labels.touch_idx
        usable = np.flatnonzero(finite_rows & labels.usable)
        if usable.size < 1000:
            raise ValueError(f"muestras utilizables insuficientes ({usable.size}) en {direction}")

        weights = uniqueness_weights(span, n)
        y = labels.label.astype(float)

        splits = purged_walk_forward(
            usable,
            span,
            n_splits=cfg.n_splits,
            min_train_frac=cfg.min_train_frac,
            embargo_frac=cfg.embargo_frac,
            expanding=cfg.expanding,
        )
        assert_no_leakage(splits, span)

        oos_prob: list[np.ndarray] = []
        oos_raw: list[np.ndarray] = []
        oos_base: list[np.ndarray] = []
        oos_y: list[np.ndarray] = []
        oos_ret: list[np.ndarray] = []
        oos_w: list[np.ndarray] = []
        last_model: BarrierProbabilityModel | None = None
        last_test: np.ndarray | None = None

        for split in splits:
            model = BarrierProbabilityModel(
                kind=cfg.model_kind, calibrate=True, seed=cfg.seed
            ).fit(X, y, split.train_idx, span, weights)
            base = BaseRateClassifier().fit(y[split.train_idx], weights[split.train_idx])

            p = model.predict_proba(X[split.test_idx])
            oos_prob.append(p)
            oos_raw.append(model.predict_proba_uncalibrated(X[split.test_idx]))
            oos_base.append(base.predict_proba(split.n_test))
            oos_y.append(y[split.test_idx])
            oos_ret.append(labels.net_return[split.test_idx])
            oos_w.append(weights[split.test_idx])
            last_model, last_test = model, split.test_idx

            fold_rows.append(
                {
                    "direction": direction,
                    "fold": split.name,
                    "n_train": split.n_train,
                    "n_test": split.n_test,
                    "test_from": _ms_to_date(int(series.open_time[split.test_idx[0]])),
                    "test_to": _ms_to_date(int(series.open_time[split.test_idx[-1]])),
                    "base_rate_train": float(
                        np.average(y[split.train_idx], weights=weights[split.train_idx])
                    ),
                    "base_rate_test": float(y[split.test_idx].mean()),
                    "brier": mx.brier_score(y[split.test_idx], p),
                    "auc": mx.roc_auc(y[split.test_idx], p),
                    "calibration_error_pp": mx.probability_metrics(
                        y[split.test_idx], p
                    ).mean_calibration_error_pp,
                }
            )
            logger.info(
                "{} {}: n_train={} n_test={} Brier={:.4f} AUC={:.3f}",
                direction,
                split.name,
                split.n_train,
                split.n_test,
                fold_rows[-1]["brier"],
                fold_rows[-1]["auc"],
            )

        prob = np.concatenate(oos_prob)
        raw = np.concatenate(oos_raw)
        base_p = np.concatenate(oos_base)
        y_all = np.concatenate(oos_y)
        ret_all = np.concatenate(oos_ret)

        dm = mx.diebold_mariano(
            mx.brier_loss_series(y_all, prob),
            mx.brier_loss_series(y_all, base_p),
            horizon=horizon,
        )

        # Probabilidad de equilibrio con la sigma mediana del periodo: es el
        # listón contra el que hay que leer el KPI. Un 45% de Seguridad no
        # dice nada hasta saber si el equilibrio está en 40% o en 64%.
        sigma_med = float(np.median(sigma_h[usable]))
        breakeven = cfg.barrier.breakeven_probability(sigma_med)

        direction_results[direction] = DirectionResult(
            direction=direction,
            n_samples=int(y_all.size),
            effective_n=float(weights[usable].sum()),
            resolution_mix=labels.resolution_mix(),
            breakeven_prob=breakeven,
            metrics_model=mx.probability_metrics(y_all, prob),
            metrics_uncalibrated=mx.probability_metrics(y_all, raw),
            metrics_baseline=mx.probability_metrics(y_all, base_p),
            dm_vs_baseline=dm,
            trading=_trading_summary(
                y_all, prob, ret_all, cfg.signal_threshold, breakeven
            ),
        )

        if direction == cfg.directions[0] and last_model is not None and last_test is not None:
            logger.info("importancia por permutación sobre el último fold…")
            importance = _permutation_importance(
                last_model, X[last_test], y[last_test], feature_names, cfg.seed
            )

    # ------------------------------------------------------------------ #
    # Target de volatilidad
    # ------------------------------------------------------------------ #
    vol_usable = np.flatnonzero(finite_rows & np.isfinite(y_vol) & (y_vol > 0))
    vol_span = np.minimum(np.arange(n) + horizon, n - 1)
    vol_splits = purged_walk_forward(
        vol_usable,
        vol_span,
        n_splits=cfg.n_splits,
        min_train_frac=cfg.min_train_frac,
        embargo_frac=cfg.embargo_frac,
        expanding=cfg.expanding,
    )

    w_d, w_w, w_m = (
        max(2, int(round(4 * 3_600_000 / tf_ms))),
        max(3, int(round(24 * 3_600_000 / tf_ms))),
        max(4, int(round(168 * 3_600_000 / tf_ms))),
    )
    har_design = HARVolForecaster.design_matrix(
        np.nan_to_num(nm.realized_vol(returns, w_d), nan=nm.EPS),
        np.nan_to_num(nm.realized_vol(returns, w_w), nan=nm.EPS),
        np.nan_to_num(nm.realized_vol(returns, w_m), nan=nm.EPS),
    )

    v_model: list[np.ndarray] = []
    v_ewma: list[np.ndarray] = []
    v_garch: list[np.ndarray] = []
    v_har: list[np.ndarray] = []
    v_true: list[np.ndarray] = []

    for split in vol_splits:
        vm = VolatilityModel(kind=cfg.vol_kind, seed=cfg.seed).fit(X, y_vol, split.train_idx)  # type: ignore[arg-type]
        ewma_pred = EWMAVolForecaster(horizon=horizon).predict_from_returns(returns)
        garch = GarchVolForecaster(horizon=horizon).fit(returns[split.train_idx])
        garch_pred = garch.predict_from_returns(returns)
        har = HARVolForecaster().fit(har_design[split.train_idx], y_vol[split.train_idx])

        v_model.append(vm.predict(X[split.test_idx]))
        v_ewma.append(ewma_pred[split.test_idx])
        v_garch.append(garch_pred[split.test_idx])
        v_har.append(har.predict(har_design[split.test_idx]))
        v_true.append(y_vol[split.test_idx])

    vt = np.concatenate(v_true)
    vm_p, ve_p, vg_p, vh_p = (np.concatenate(v) for v in (v_model, v_ewma, v_garch, v_har))

    volatility = VolatilityResult(
        n_samples=int(vt.size),
        model=mx.regression_metrics(vt, vm_p, ve_p),
        ewma=mx.regression_metrics(vt, ve_p),
        garch=mx.regression_metrics(vt, vg_p, ve_p),
        har=mx.regression_metrics(vt, vh_p, ve_p),
        dm_vs_ewma=mx.diebold_mariano(
            mx.squared_error(vt, vm_p), mx.squared_error(vt, ve_p), horizon=horizon
        ),
        dm_vs_har=mx.diebold_mariano(
            mx.squared_error(vt, vm_p), mx.squared_error(vt, vh_p), horizon=horizon
        ),
    )

    # ------------------------------------------------------------------ #
    # Cono de precio conformal
    # ------------------------------------------------------------------ #
    ret_usable = np.flatnonzero(finite_rows & np.isfinite(y_ret))
    ret_splits = purged_walk_forward(
        ret_usable,
        vol_span,
        n_splits=cfg.n_splits,
        min_train_frac=cfg.min_train_frac,
        embargo_frac=cfg.embargo_frac,
        expanding=cfg.expanding,
    )
    from scipy import stats as _stats

    conformal_metrics: dict[float, mx.IntervalMetrics] = {}
    gaussian_metrics: dict[float, mx.IntervalMetrics] = {}

    for alpha in cfg.conformal_alphas:
        c_lo: list[np.ndarray] = []
        c_hi: list[np.ndarray] = []
        g_lo: list[np.ndarray] = []
        g_hi: list[np.ndarray] = []
        c_true: list[np.ndarray] = []
        z = float(_stats.norm.ppf(1.0 - alpha / 2.0))

        for split in ret_splits:
            try:
                ci = ConformalReturnInterval(alpha=alpha, seed=cfg.seed).fit(
                    X, y_ret, split.train_idx
                )
            except ValueError:  # pragma: no cover — train chico en folds tempranos
                continue
            y_te = y_ret[split.test_idx]
            lo, hi = ci.predict_interval_adaptive(X[split.test_idx], y_te)
            c_lo.append(lo)
            c_hi.append(hi)

            # Baseline gaussiano: random walk centrado en 0 con sigma del
            # forecast EWMA. Es lo que hace por defecto una banda ±zσ.
            sigma = EWMAVolForecaster(horizon=horizon).predict_from_returns(returns)[
                split.test_idx
            ]
            rw = RandomWalkForecaster().fit(y_ret[split.train_idx]).predict(split.n_test)
            g_lo.append(rw - z * sigma)
            g_hi.append(rw + z * sigma)
            c_true.append(y_te)

        if not c_true:  # pragma: no cover
            continue
        yt = np.concatenate(c_true)
        conformal_metrics[alpha] = mx.interval_metrics(
            yt, np.concatenate(c_lo), np.concatenate(c_hi), 1.0 - alpha
        )
        gaussian_metrics[alpha] = mx.interval_metrics(
            yt, np.concatenate(g_lo), np.concatenate(g_hi), 1.0 - alpha
        )

    imp_map = dict(importance)
    family_importance = {
        fam: float(sum(imp_map.get(nm_, 0.0) for nm_ in members))
        for fam, members in families.items()
        if members
    }

    return ExperimentResult(
        symbol=series.symbol,
        timeframe=series.timeframe,
        config=cfg,
        n_bars=n,
        n_features=X.shape[1],
        feature_names=feature_names,
        date_from=_ms_to_date(int(series.open_time[0])),
        date_to=_ms_to_date(int(series.open_time[-1])),
        folds=fold_rows,
        directions=direction_results,
        volatility=volatility,
        intervals=IntervalResult(conformal=conformal_metrics, gaussian=gaussian_metrics),
        importance=importance,
        family_importance=family_importance,
        runtime_s=time.monotonic() - started,
    )
