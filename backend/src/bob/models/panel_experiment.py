"""Experimento del panel transversal diario — PURO, sin I/O.

Mide exactamente lo que `docs/PREREGISTRO_PANEL_DIARIO.md` registró antes de
correr, y nada más. Los tres criterios de aprobación están acá como código,
no como prosa, para que el veredicto salga del `PanelResult` y no de alguien
leyendo una tabla.

**Orden de las filas.** El panel se aplana ordenado por `(fecha, símbolo)`. No
es cosmético: `BarrierProbabilityModel` calibra con un walk-forward purgado
*interno* que purga por índice de fila, y con las filas ordenadas por fecha un
rango de índices **es** un rango de fechas. Con cualquier otro orden esa purga
interna mezclaría fechas entre símbolos y volvería a filtrar justo lo que el
split externo se cuidó de separar.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

import numpy as np

from bob.models import metrics as mx
from bob.models.forecast import BarrierProbabilityModel
from bob.models.panel_validation import (
    assert_no_date_leakage,
    purged_walk_forward_by_date,
)
from bob.paper.forward_eval import coverage_interval
from bob.signals.cross_section import (
    PanelFeatures,
    cross_sectional_rank,
    forward_path_return,
    panel_uniqueness_weights,
    relative_target,
)

__all__ = ["PanelConfig", "PanelResult", "ScoreEvaluation", "run_panel_experiment"]

#: Costo round-trip de UNA pata (fees + slippage), igual que `BarrierConfig`.
#: Un par largo/corto paga dos.
DEFAULT_LEG_COST: float = 0.0014


@dataclass(frozen=True)
class PanelConfig:
    """Configuración primaria del pre-registro §4. No se toca tras ver resultados."""

    horizon: int = 5
    n_splits: int = 6
    min_train_frac: float = 0.35
    embargo_frac: float = 0.01
    portfolio_quantile: float = 0.10
    leg_cost: float = DEFAULT_LEG_COST
    model_kind: Literal["gbm", "logistic", "xgb"] = "gbm"
    use_cross_section: bool = True
    #: Corte del target: "median" (primaria) o "tercile" (exploratoria §4).
    target_cut: str = "median"
    #: Pondera cada fila por unicidad del label. Exploratoria declarada.
    uniqueness_weights: bool = False
    seed: int = 42
    n_boot: int = 20_000
    #: Placebo: permuta el resultado ENTRE símbolos dentro de cada fecha. Rompe
    #: el vínculo símbolo→resultado y deja intacto todo lo demás —fechas, ancho
    #: de la sección, factor de mercado, folds—. Un IC que sobreviva a esto no
    #: viene de predecir: viene de una fuga. Es el control que el §6.0 del
    #: pre-registro exige, porque con el piso económico en AUC 0,5047 un sesgo
    #: chico produce exactamente el efecto que se busca.
    placebo: bool = False

    @property
    def pair_cost(self) -> float:
        """Un par largo/corto paga las dos patas."""
        return 2.0 * self.leg_cost


@dataclass(frozen=True)
class ScoreEvaluation:
    """Cómo se comporta **un** score —el modelo o un baseline— fecha por fecha."""

    name: str
    dates: np.ndarray = field(repr=False)
    ic: np.ndarray = field(repr=False)
    spread_gross: np.ndarray = field(repr=False)
    n_per_date: np.ndarray = field(repr=False)

    @property
    def mean_ic(self) -> float:
        return float(np.nanmean(self.ic)) if self.ic.size else float("nan")

    @property
    def mean_spread_gross(self) -> float:
        return float(np.nanmean(self.spread_gross)) if self.spread_gross.size else float("nan")

    def mean_spread_net(self, pair_cost: float) -> float:
        return self.mean_spread_gross - pair_cost

    def ic_interval(self, block: int, *, n_boot: int, seed: int) -> tuple[float, float]:
        return coverage_interval(_finite(self.ic), block, n_boot=n_boot, seed=seed)

    def spread_interval(self, block: int, *, n_boot: int, seed: int) -> tuple[float, float]:
        return coverage_interval(_finite(self.spread_gross), block, n_boot=n_boot, seed=seed)


@dataclass(frozen=True)
class PanelResult:
    """Resultado completo. Es la fuente del veredicto — no hay cifras a mano."""

    config: PanelConfig
    n_rows: int
    n_symbols: int
    n_dates: int
    n_features: int
    date_from: int
    date_to: int
    feature_names: list[str] = field(repr=False)
    base_rate: float = 0.5
    auc: float = float("nan")
    brier: float = float("nan")
    bss: float = float("nan")
    calibration_error: float = float("nan")
    model: ScoreEvaluation | None = field(default=None, repr=False)
    oracle: ScoreEvaluation | None = field(default=None, repr=False)
    baselines: dict[str, ScoreEvaluation] = field(default_factory=dict, repr=False)
    paired_ic: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    intervals: dict[str, tuple[float, float]] = field(default_factory=dict)
    turnover: float = float("nan")
    zero_return_frac: float = float("nan")
    delisted_rows: int = 0
    fold_names: list[str] = field(default_factory=list)
    #: Timestamps reales de las fechas evaluadas. `ScoreEvaluation.dates` son
    #: ÍNDICES del eje del panel; volcarlos al artefacto como si fueran fechas
    #: haría que cualquier análisis posterior los lea mal y no se notaría.
    test_open_time: list[int] = field(default_factory=list, repr=False)

    # ---- los tres criterios del pre-registro, como código ---- #

    @property
    def passes_discrimination(self) -> bool:
        """Criterio 1: AUC > 0,55 con BSS > 0."""
        return bool(self.auc > 0.55 and self.bss > 0.0)

    @property
    def best_baseline(self) -> str | None:
        if not self.baselines:
            return None
        return max(self.baselines, key=lambda k: self.baselines[k].mean_ic)

    @property
    def passes_vs_baseline(self) -> bool:
        """Criterio 2: diferencia **pareada** de IC contra el mejor trivial, IC95% > 0."""
        best = self.best_baseline
        if best is None or best not in self.paired_ic:
            return False
        _, lo, _ = self.paired_ic[best]
        return bool(lo > 0.0)

    @property
    def passes_economics(self) -> bool:
        """Criterio 3: spread neto del par decil positivo, IC95% excluye 0."""
        if self.model is None or "spread_net" not in self.intervals:
            return False
        lo, _ = self.intervals["spread_net"]
        return bool(self.model.mean_spread_net(self.config.pair_cost) > 0.0 and lo > 0.0)

    @property
    def viable(self) -> bool:
        """Los TRES, no dos. Es la definición literal del §5."""
        return (
            self.passes_discrimination
            and self.passes_vs_baseline
            and self.passes_economics
        )

    @property
    def oracle_capture(self) -> float:
        """Qué fracción del techo se capturó. El número que dice si hay producto."""
        if self.model is None or self.oracle is None:
            return float("nan")
        ceiling = self.oracle.mean_spread_gross
        if not np.isfinite(ceiling) or abs(ceiling) < 1e-12:
            return float("nan")
        return self.model.mean_spread_gross / ceiling

    def to_dict(self) -> dict[str, object]:
        return {
            "config": asdict(self.config),
            "n_rows": self.n_rows,
            "n_symbols": self.n_symbols,
            "n_dates": self.n_dates,
            "n_features": self.n_features,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "base_rate": self.base_rate,
            "auc": self.auc,
            "brier": self.brier,
            "bss": self.bss,
            "calibration_error": self.calibration_error,
            "model_mean_ic": self.model.mean_ic if self.model else float("nan"),
            "model_spread_gross": self.model.mean_spread_gross if self.model else float("nan"),
            "model_spread_net": (
                self.model.mean_spread_net(self.config.pair_cost) if self.model else float("nan")
            ),
            "oracle_spread": self.oracle.mean_spread_gross if self.oracle else float("nan"),
            "oracle_capture": self.oracle_capture,
            "baselines": {
                k: {"mean_ic": v.mean_ic, "spread_gross": v.mean_spread_gross}
                for k, v in self.baselines.items()
            },
            "paired_ic": {k: list(v) for k, v in self.paired_ic.items()},
            "intervals": {k: list(v) for k, v in self.intervals.items()},
            "turnover": self.turnover,
            "zero_return_frac": self.zero_return_frac,
            "delisted_rows": self.delisted_rows,
            "criteria": {
                "discrimination": self.passes_discrimination,
                "vs_baseline": self.passes_vs_baseline,
                "economics": self.passes_economics,
                "viable": self.viable,
            },
            "fold_names": self.fold_names,
            # Series por fecha: dejan el artefacto autosuficiente para las
            # exploratorias declaradas (sub-períodos, cortes de cartera) sin
            # re-correr el walk-forward, que es lo que hace que una
            # exploratoria no se convierta en una excusa para tocar la
            # configuración primaria.
            "per_date": {
                "open_time": list(self.test_open_time),
                "ic": [float(x) for x in self.model.ic] if self.model else [],
                "spread_gross": (
                    [float(x) for x in self.model.spread_gross] if self.model else []
                ),
                "n_per_date": (
                    [int(x) for x in self.model.n_per_date] if self.model else []
                ),
                "oracle_spread": (
                    [float(x) for x in self.oracle.spread_gross] if self.oracle else []
                ),
                "baseline_ic": {
                    k: [float(x) for x in v.ic] for k, v in self.baselines.items()
                },
            },
        }


def _permute_within_dates(
    forward: np.ndarray, eligible: np.ndarray, seed: int
) -> np.ndarray:
    """Baraja los resultados entre los símbolos elegibles de cada fecha."""
    rng = np.random.default_rng(seed)
    out = forward.copy()
    for j in range(forward.shape[1]):
        rows = np.flatnonzero(eligible[:, j] & np.isfinite(forward[:, j]))
        if rows.size > 1:
            out[rows, j] = forward[rng.permutation(rows), j]
    return out


def _finite(x: np.ndarray) -> np.ndarray:
    a = np.asarray(x, dtype=np.float64)
    return a[np.isfinite(a)]


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Correlación de rangos. NaN si no hay variación — no 0, que sería mentir."""
    n = a.size
    if n < 3:
        return float("nan")
    ra = _rankdata(a)
    rb = _rankdata(b)
    sa, sb = ra.std(), rb.std()
    if sa < 1e-12 or sb < 1e-12:
        return float("nan")
    return float(np.mean((ra - ra.mean()) * (rb - rb.mean())) / (sa * sb))


def _rankdata(x: np.ndarray) -> np.ndarray:
    """Rangos con promedio en los empates."""
    n = x.size
    order = np.argsort(x, kind="stable")
    ranks = np.empty(n, dtype=np.float64)
    ranks[order] = np.arange(1, n + 1, dtype=np.float64)
    sorted_x = x[order]
    start = 0
    for end in range(1, n + 1):
        if end == n or sorted_x[end] != sorted_x[start]:
            if end - start > 1:
                ranks[order[start:end]] = ranks[order[start:end]].mean()
            start = end
    return ranks


def _evaluate_score(
    name: str,
    score: np.ndarray,
    forward: np.ndarray,
    date_idx: np.ndarray,
    dates: np.ndarray,
    quantile: float,
) -> ScoreEvaluation:
    """IC de rango y spread del par decil, fecha por fecha.

    El spread se calcula sobre el retorno forward crudo. Como el target ya es
    relativo a la mediana de la fecha, el factor común se cancela en la resta
    `decil alto − decil bajo`: es un par neutral al mercado por construcción,
    no por una cobertura estimada.
    """
    ics, spreads, counts, out_dates = [], [], [], []
    # Las filas llegan ordenadas por fecha (ver el encabezado del módulo), así
    # que cada fecha es un tramo contiguo. Con `date_idx == d` por fecha esto
    # sería O(T·N) y el panel tiene 2.456 fechas por ~300.000 filas.
    bounds = np.searchsorted(date_idx, dates, side="left")
    ends = np.searchsorted(date_idx, dates, side="right")
    for lo, hi in zip(bounds, ends, strict=True):
        d = int(date_idx[lo])
        if hi - lo < 3:
            continue
        s, f = score[lo:hi], forward[lo:hi]
        ok = np.isfinite(s) & np.isfinite(f)
        if ok.sum() < 3:
            continue
        s, f = s[ok], f[ok]
        out_dates.append(d)
        counts.append(s.size)
        ics.append(_spearman(s, f))

        n_side = max(1, int(round(s.size * quantile)))
        order = np.argsort(s, kind="stable")
        spreads.append(float(f[order[-n_side:]].mean() - f[order[:n_side]].mean()))

    return ScoreEvaluation(
        name=name,
        dates=np.asarray(out_dates, dtype=np.int64),
        ic=np.asarray(ics, dtype=np.float64),
        spread_gross=np.asarray(spreads, dtype=np.float64),
        n_per_date=np.asarray(counts, dtype=np.int64),
    )


def _paired_difference(
    a: ScoreEvaluation, b: ScoreEvaluation, block: int, *, n_boot: int, seed: int
) -> tuple[float, float, float]:
    """Media e IC95% de la diferencia **pareada** de IC, fecha por fecha.

    Pareada y no dos intervalos marginales. Dos ICs que se solapan no
    permiten concluir que no hay diferencia —es el mismo motivo por el que el
    proyecto usa Diebold-Mariano y no dos RMSE sueltos—, y acá las dos series
    comparten las mismas fechas y el mismo ruido de mercado, así que la
    diferencia tiene mucha menos varianza que cada término.
    """
    common, ia, ib = np.intersect1d(a.dates, b.dates, return_indices=True)
    if common.size == 0:
        return (float("nan"), float("nan"), float("nan"))
    diff = a.ic[ia] - b.ic[ib]
    diff = diff[np.isfinite(diff)]
    if diff.size == 0:
        return (float("nan"), float("nan"), float("nan"))
    lo, hi = coverage_interval(diff, block, n_boot=n_boot, seed=seed)
    return (float(diff.mean()), lo, hi)


def _trailing_return(close: np.ndarray, window: int) -> np.ndarray:
    """Retorno log de las últimas `window` barras, terminando en la barra actual."""
    out = np.full_like(close, np.nan)
    out[:, window:] = np.log(close[:, window:] / close[:, :-window])
    return np.where(np.isfinite(out), out, np.nan)


def run_panel_experiment(
    features: PanelFeatures,
    open_: np.ndarray,
    close: np.ndarray,
    eligible: np.ndarray,
    config: PanelConfig | None = None,
) -> PanelResult:
    """Corre el walk-forward purgado por fecha y evalúa los tres criterios."""
    cfg = config or PanelConfig()
    n_sym, n_dates, n_feat = features.shape

    forward, delisted = forward_path_return(open_, close, cfg.horizon)
    if cfg.placebo:
        forward = _permute_within_dates(forward, eligible, cfg.seed)
    y_grid, excess = relative_target(forward, eligible, cut=cfg.target_cut)

    usable = eligible & features.valid & (y_grid >= 0)
    sym_i, date_j = np.nonzero(usable)
    if sym_i.size == 0:
        raise ValueError("no quedó ninguna fila utilizable en el panel")

    # Orden por (fecha, símbolo): ver la nota del encabezado del módulo.
    order = np.lexsort((sym_i, date_j))
    sym_i, date_j = sym_i[order], date_j[order]

    X = features.X[sym_i, date_j, :]
    y = y_grid[sym_i, date_j].astype(np.int64)
    fwd = forward[sym_i, date_j]

    # `span`: última fila cuyo label todavía se solapa con el de esta. Con las
    # filas ordenadas por fecha, es la última fila de la fecha `j + horizon`.
    last_row_of_date = np.searchsorted(date_j, np.arange(n_dates + 1), side="right") - 1
    horizon_date = np.minimum(date_j + cfg.horizon, n_dates - 1)
    span = last_row_of_date[horizon_date].astype(np.int64)

    weights: np.ndarray | None = None
    if cfg.uniqueness_weights:
        weights = panel_uniqueness_weights(sym_i, date_j, cfg.horizon, n_dates)

    splits = purged_walk_forward_by_date(
        n_dates,
        cfg.horizon,
        n_splits=cfg.n_splits,
        min_train_frac=cfg.min_train_frac,
        embargo_frac=cfg.embargo_frac,
    )
    assert_no_date_leakage(splits, cfg.horizon)

    oof = np.full(y.size, np.nan, dtype=np.float64)
    tested = np.zeros(y.size, dtype=bool)
    for split in splits:
        tr = np.flatnonzero(np.isin(date_j, split.train_dates))
        te = np.flatnonzero(np.isin(date_j, split.test_dates))
        if tr.size < 50 or te.size == 0:
            continue
        model = BarrierProbabilityModel(kind=cfg.model_kind, seed=cfg.seed)
        model.fit(X, y, tr, span, sample_weight=weights)
        oof[te] = model.predict_proba(X[te])
        tested[te] = True

    if not tested.any():
        raise ValueError("ningún fold produjo predicciones")

    y_te, p_te, fwd_te = y[tested], oof[tested], fwd[tested]
    date_te = date_j[tested]
    base_rate = float(y_te.mean())
    prob = mx.probability_metrics(y_te, p_te)

    test_dates = np.unique(date_te)
    block = cfg.horizon

    model_eval = _evaluate_score(
        "modelo", p_te, fwd_te, date_te, test_dates, cfg.portfolio_quantile
    )
    oracle_eval = _evaluate_score(
        "oraculo", fwd_te, fwd_te, date_te, test_dates, cfg.portfolio_quantile
    )

    mom30 = cross_sectional_rank(_trailing_return(close, 30), eligible)[sym_i, date_j][tested]
    rev5 = -cross_sectional_rank(_trailing_return(close, 5), eligible)[sym_i, date_j][tested]
    baselines = {
        "xs_momentum_30d": _evaluate_score(
            "xs_momentum_30d", mom30, fwd_te, date_te, test_dates, cfg.portfolio_quantile
        ),
        "xs_reversal_5d": _evaluate_score(
            "xs_reversal_5d", rev5, fwd_te, date_te, test_dates, cfg.portfolio_quantile
        ),
    }

    paired = {
        name: _paired_difference(
            model_eval, ev, block, n_boot=cfg.n_boot, seed=cfg.seed
        )
        for name, ev in baselines.items()
    }

    spread_net_series = model_eval.spread_gross - cfg.pair_cost
    intervals = {
        "ic": model_eval.ic_interval(block, n_boot=cfg.n_boot, seed=cfg.seed),
        "spread_gross": model_eval.spread_interval(
            block, n_boot=cfg.n_boot, seed=cfg.seed
        ),
        "spread_net": coverage_interval(
            _finite(spread_net_series), block, n_boot=cfg.n_boot, seed=cfg.seed
        ),
    }

    returns_grid = np.where(eligible, close, np.nan)
    with np.errstate(invalid="ignore"):
        step_ret = np.diff(returns_grid, axis=1)
    zero_frac = (
        float(np.mean(np.abs(step_ret[np.isfinite(step_ret)]) < 1e-12))
        if np.isfinite(step_ret).any()
        else float("nan")
    )

    return PanelResult(
        config=cfg,
        n_rows=int(tested.sum()),
        n_symbols=n_sym,
        n_dates=n_dates,
        n_features=n_feat,
        date_from=int(features.open_time[date_te.min()]),
        date_to=int(features.open_time[date_te.max()]),
        feature_names=list(features.names),
        base_rate=base_rate,
        auc=prob.auc,
        brier=prob.brier,
        bss=prob.brier_skill_score,
        calibration_error=prob.mean_calibration_error_pp,
        model=model_eval,
        oracle=oracle_eval,
        baselines=baselines,
        paired_ic=paired,
        intervals=intervals,
        turnover=_turnover(p_te, date_te, test_dates, cfg.portfolio_quantile, sym_i[tested]),
        zero_return_frac=zero_frac,
        delisted_rows=int(delisted[sym_i, date_j][tested].sum()),
        fold_names=[s.name for s in splits],
        test_open_time=[int(features.open_time[d]) for d in model_eval.dates],
    )


def _turnover(
    score: np.ndarray,
    date_idx: np.ndarray,
    dates: np.ndarray,
    quantile: float,
    symbols: np.ndarray,
) -> float:
    """Fracción de la cartera que cambia entre fechas consecutivas.

    Se reporta apruebe o no (§5.5): un spread positivo que exige rotar toda la
    cartera cada rebalanceo no es operable a mano, que es como opera el
    usuario de este proyecto.
    """
    prev: set[int] | None = None
    changes: list[float] = []
    bounds = np.searchsorted(date_idx, dates, side="left")
    ends = np.searchsorted(date_idx, dates, side="right")
    for lo, hi in zip(bounds, ends, strict=True):
        s, syms = score[lo:hi], symbols[lo:hi]
        ok = np.isfinite(s)
        if ok.sum() < 3:
            continue
        s, syms = s[ok], syms[ok]
        n_side = max(1, int(round(s.size * quantile)))
        order = np.argsort(s, kind="stable")
        current = set(syms[order[-n_side:]].tolist()) | set(syms[order[:n_side]].tolist())
        if prev is not None and current:
            changes.append(1.0 - len(current & prev) / len(current))
        prev = current
    return float(np.mean(changes)) if changes else float("nan")
