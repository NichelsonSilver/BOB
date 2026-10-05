"""Gate del ranking macro — Fase M3. PURO, sin I/O.

Implementa `docs/PREREGISTRO_RANKING_MACRO.md` (commit `836e603`, escrito antes
de correr ningún ranking). Cada decisión de diseño está ahí con su motivo; acá
se cita la sección en vez de repetirla.

Reutiliza sin modificar (regla 12): el walk-forward purgado por fecha
(`bob.models.panel_validation`), el modelo calibrado con los hiperparámetros
del gate (`bob.models.forecast.BarrierProbabilityModel`), las métricas de
probabilidad (`bob.models.metrics`) y el bootstrap de bloques
(`bob.paper.forward_eval.coverage_interval`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from scipy.stats import rankdata

from bob.models import metrics as mx
from bob.models.forecast import BarrierProbabilityModel
from bob.models.panel_validation import assert_no_date_leakage, purged_walk_forward_by_date
from bob.paper.forward_eval import coverage_interval

F64 = NDArray[np.float64]

ASSET_BLOCKS = ("acciones", "renta_fija", "reserva_valor", "commodities", "real_estate", "divisas")
PRICE_FEATURES = (
    "ret_4s",
    "ret_13s",
    "ret_26s",
    "mom_12_1",
    "vol_13s",
    "vol_52s",
    "dist_max_52s",
    "mom_aj_vol",
)
MACRO_FEATURES = ("fed_net", "m2", "dolar", "tasa_real", "nfci")
WARMUP_WEEKS = 52


@dataclass(frozen=True)
class RankingConfig:
    """Configuración primaria del pre-registro §4. No se toca tras ver resultados."""

    horizon: int = 4
    #: 1 = entrada al cierre del día hábil siguiente a la decisión (§3.2).
    entry_lag: int = 1
    use_macro: bool = True
    use_cross_section: bool = True
    model_kind: Literal["gbm", "xgb"] = "gbm"
    n_splits: int = 6
    min_train_frac: float = 0.35
    embargo_frac: float = 0.01
    #: Ida y vuelta por pata: 0,10% por lado (§3.8).
    leg_cost: float = 0.0020
    #: Tercio superior / inferior (§4).
    portfolio_frac: float = 1 / 3
    placebo: bool = False
    #: Columnas del bloque C que se excluyen. Vacío en la primaria. Solo la
    #: exploratoria de 21 ETFs desde 2007 lo usa: la Fed neta no existe antes
    #: de 2014 y queda entera en NaN en sus folds tempranos (desviación
    #: declarada en el artefacto, encontrada por un error y no por resultado).
    drop_macro: tuple[str, ...] = ()
    seed: int = 42
    n_boot: int = 20_000


# --------------------------------------------------------------------------- precios


def price_index(daily: F64) -> F64:
    """Índice de retorno total acumulado (días × activos), base 1 antes del día 0."""
    return np.asarray(np.cumprod(1.0 + daily, axis=0), dtype=np.float64)


def decision_closes(index: F64, ends: Sequence[int]) -> F64:
    return np.asarray(index[np.asarray(ends)], dtype=np.float64)


def forward_returns(index: F64, ends: Sequence[int], horizon: int, entry_lag: int) -> F64:
    """Retorno total entre la entrada y la salida de cada decisión (§3.2).

    Entrada: `entry_lag` días hábiles después del cierre de la decisión `k`.
    Salida: el mismo rezago después de la decisión `k + horizon`. NaN donde la
    salida todavía no ocurrió.
    """
    e = np.asarray(ends)
    n_days = index.shape[0]
    out = np.full((e.size, index.shape[1]), np.nan)
    for k in range(e.size - horizon):
        entry, exit_ = e[k] + entry_lag, e[k + horizon] + entry_lag
        if exit_ < n_days:
            out[k] = index[exit_] / index[entry] - 1.0
    return out


# --------------------------------------------------------------------------- features


def _rolling_std(x: F64, k: int, window: int) -> F64:
    return np.asarray(np.std(x[k - window + 1 : k + 1], axis=0, ddof=1), dtype=np.float64)


def price_features(closes: F64) -> F64:
    """Bloque A (§3.4): (fechas, activos, 8). Solo usa cierres hasta `k`."""
    n, m = closes.shape
    logc = np.log(closes)
    w = np.vstack([np.full(m, np.nan), np.diff(logc, axis=0)])
    out = np.full((n, m, len(PRICE_FEATURES)), np.nan)
    for k in range(WARMUP_WEEKS, n):
        vol52 = _rolling_std(w, k, 52)
        mom = logc[k - 4] - logc[k - 52]
        out[k, :, 0] = logc[k] - logc[k - 4]
        out[k, :, 1] = logc[k] - logc[k - 13]
        out[k, :, 2] = logc[k] - logc[k - 26]
        out[k, :, 3] = mom
        out[k, :, 4] = _rolling_std(w, k, 13)
        out[k, :, 5] = vol52
        out[k, :, 6] = logc[k] - np.max(logc[k - 51 : k + 1], axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            out[k, :, 7] = np.where(vol52 > 0, mom / vol52, np.nan)
    return out


def beta_to(closes: F64, ref: int, window: int = 52) -> F64:
    """Beta de cada activo contra el activo `ref` en las últimas `window` semanas."""
    logc = np.log(closes)
    w = np.diff(logc, axis=0)
    n, m = closes.shape
    out = np.full((n, m), np.nan)
    for k in range(window, n):
        seg = w[k - window : k]
        r = seg[:, ref]
        var = np.var(r, ddof=1)
        if var > 0:
            out[k] = ((seg - seg.mean(axis=0)) * (r - r.mean())[:, None]).sum(axis=0) / (
                (window - 1) * var
            )
    return out


def cross_rank(x: F64) -> F64:
    """Rango percentil en [0, 1] dentro de cada fecha; NaN si la fila no está completa."""
    out = np.full(x.shape, np.nan)
    for k in range(x.shape[0]):
        row = x[k]
        if np.all(np.isfinite(row)) and row.size > 1:
            out[k] = (rankdata(row) - 1.0) / (row.size - 1.0)
    return out


def assemble(
    closes: F64,
    macro: F64,
    blocks: Sequence[str],
    spy_col: int,
    cfg: RankingConfig,
) -> tuple[F64, list[str]]:
    """Matriz (fechas, activos, features) en el orden del pre-registro §3.4."""
    n, m = closes.shape
    a = price_features(closes)
    parts = [a]
    names = list(PRICE_FEATURES)
    if cfg.use_cross_section:
        parts.append(np.stack([cross_rank(a[:, :, j]) for j in range(a.shape[2])], axis=2))
        names += [f"xs_{f}" for f in PRICE_FEATURES]
    if cfg.use_macro:
        keep = [i for i, f in enumerate(MACRO_FEATURES) if f not in cfg.drop_macro]
        parts.append(np.repeat(macro[:, None, keep], m, axis=1))
        names += [f"liq_{MACRO_FEATURES[i]}" for i in keep]
    parts.append(beta_to(closes, spy_col)[:, :, None])
    names.append("beta_spy_52s")
    onehot = np.zeros((n, m, len(ASSET_BLOCKS)))
    for j, b in enumerate(blocks):
        onehot[:, j, ASSET_BLOCKS.index(b)] = 1.0
    parts.append(onehot)
    names += [f"bloque_{b}" for b in ASSET_BLOCKS]
    return np.concatenate(parts, axis=2), names


# --------------------------------------------------------------------------- evaluación


def ic_series(score: F64, fwd: F64, date_idx: NDArray[np.int64], dates: NDArray[np.int64]) -> F64:
    """IC de Spearman por fecha, sobre las filas evaluadas de esa fecha."""
    out = np.full(dates.size, np.nan)
    for i, d in enumerate(dates):
        sel = date_idx == d
        s, f = score[sel], fwd[sel]
        if s.size > 2 and np.ptp(s) > 0 and np.ptp(f) > 0:
            out[i] = float(np.corrcoef(rankdata(s), rankdata(f))[0, 1])
    return out


def spread_series(
    score: F64, fwd: F64, date_idx: NDArray[np.int64], dates: NDArray[np.int64], frac: float
) -> tuple[F64, list[frozenset[int]]]:
    """Retorno bruto del tercio superior menos el inferior, y los miembros del superior."""
    out = np.full(dates.size, np.nan)
    tops: list[frozenset[int]] = []
    for i, d in enumerate(dates):
        rows = np.flatnonzero(date_idx == d)
        k = max(1, int(round(rows.size * frac)))
        order = rows[np.argsort(score[rows], kind="stable")]
        out[i] = float(fwd[order[-k:]].mean() - fwd[order[:k]].mean())
        tops.append(frozenset(int(r) for r in order[-k:]))
    return out, tops


@dataclass
class ScoreEval:
    name: str
    ic: F64
    spread_gross: F64

    def summary(self, block: int, cfg: RankingConfig, cost: float) -> dict[str, Any]:
        lo, hi = coverage_interval(self.ic[np.isfinite(self.ic)], block, n_boot=cfg.n_boot, seed=0)
        net = self.spread_gross - cost
        nlo, nhi = coverage_interval(net[np.isfinite(net)], block, n_boot=cfg.n_boot, seed=0)
        return {
            "ic_mean": float(np.nanmean(self.ic)),
            "ic_ci95": [lo, hi],
            "ic_sd": float(np.nanstd(self.ic, ddof=1)),
            "spread_gross_mean": float(np.nanmean(self.spread_gross)),
            "spread_net_mean": float(np.nanmean(net)),
            "spread_net_ci95": [nlo, nhi],
        }


def paired(a: F64, b: F64, block: int, cfg: RankingConfig) -> dict[str, Any]:
    d = a - b
    d = d[np.isfinite(d)]
    lo, hi = coverage_interval(d, block, n_boot=cfg.n_boot, seed=0)
    return {"diff_mean": float(d.mean()), "ci95": [lo, hi], "excludes_zero": bool(lo > 0 or hi < 0)}


@dataclass
class RankingResult:
    config: RankingConfig
    feature_names: list[str]
    n_rows: int
    n_test_rows: int
    test_dates: NDArray[np.int64]
    y: NDArray[np.int64]
    p: F64
    fwd: F64
    date_idx: NDArray[np.int64]
    asset_idx: NDArray[np.int64]
    model: ScoreEval
    baselines: dict[str, ScoreEval]
    oracle: ScoreEval
    tops: list[frozenset[int]]
    fold_sizes: list[tuple[int, int]]
    macro_nan_frac_by_fold: list[float] = field(default_factory=list)


def _permute_within_dates(fwd: F64, seed: int) -> F64:
    rng = np.random.default_rng(seed)
    out = fwd.copy()
    for k in range(out.shape[0]):
        if np.all(np.isfinite(out[k])):
            out[k] = rng.permutation(out[k])
    return out


def run(
    closes: F64,
    fwd_grid: F64,
    macro: F64,
    blocks: Sequence[str],
    spy_col: int,
    cfg: RankingConfig,
) -> RankingResult:
    """Walk-forward purgado por fecha, OOF, y las series por fecha de cada score."""
    X3, names = assemble(closes, macro, blocks, spy_col, cfg)
    fwd_all = _permute_within_dates(fwd_grid, cfg.seed) if cfg.placebo else fwd_grid
    n_dates, n_assets = closes.shape

    a_ok = np.all(np.isfinite(X3[:, :, : len(PRICE_FEATURES)]), axis=2)
    row_ok = np.all(a_ok, axis=1) & np.all(np.isfinite(fwd_all), axis=1)
    date_ok = np.flatnonzero(row_ok)
    med = np.median(fwd_all[date_ok], axis=1, keepdims=True)
    ygrid = np.full(fwd_all.shape, -1, dtype=np.int64)
    ygrid[date_ok] = (fwd_all[date_ok] > med).astype(np.int64)

    # Filas ordenadas por (fecha, activo), como en la Fase 10.
    date_j = np.repeat(date_ok, n_assets)
    asset_i = np.tile(np.arange(n_assets), date_ok.size)
    X = X3[date_j, asset_i]
    y = ygrid[date_j, asset_i]
    fwd = fwd_all[date_j, asset_i]

    last_row_of_date = np.searchsorted(date_j, np.arange(n_dates + 1), side="right") - 1
    span = last_row_of_date[np.minimum(date_j + cfg.horizon, n_dates - 1)].astype(np.int64)

    splits = purged_walk_forward_by_date(
        n_dates,
        cfg.horizon,
        n_splits=cfg.n_splits,
        min_train_frac=cfg.min_train_frac,
        embargo_frac=cfg.embargo_frac,
    )
    assert_no_date_leakage(splits, cfg.horizon)

    oof = np.full(y.size, np.nan)
    tested = np.zeros(y.size, dtype=bool)
    sizes: list[tuple[int, int]] = []
    macro_nan: list[float] = []
    macro_cols = [i for i, n in enumerate(names) if n.startswith("liq_")]
    for split in splits:
        tr = np.flatnonzero(np.isin(date_j, split.train_dates))
        te = np.flatnonzero(np.isin(date_j, split.test_dates))
        if tr.size < 50 or te.size == 0:
            continue
        model = BarrierProbabilityModel(kind=cfg.model_kind, seed=cfg.seed)
        model.fit(X, y, tr, span)
        oof[te] = model.predict_proba(X[te])
        tested[te] = True
        sizes.append((int(tr.size), int(te.size)))
        if macro_cols:
            macro_nan.append(float(np.mean(~np.isfinite(X[tr][:, macro_cols]))))
    if not tested.any():
        raise ValueError("ningún fold produjo predicciones")

    y_te, p_te, f_te = y[tested], oof[tested], fwd[tested]
    d_te, a_te = date_j[tested], asset_i[tested]
    test_dates = np.unique(d_te)

    def evaluate(name: str, score: F64) -> tuple[ScoreEval, list[frozenset[int]]]:
        sp, tops = spread_series(score, f_te, d_te, test_dates, cfg.portfolio_frac)
        return ScoreEval(name, ic_series(score, f_te, d_te, test_dates), sp), tops

    model_eval, tops = evaluate("modelo", p_te)
    i_mom, i_ret4 = PRICE_FEATURES.index("mom_12_1"), PRICE_FEATURES.index("ret_4s")
    baselines = {
        "momentum_12_1": evaluate("momentum_12_1", X3[d_te, a_te, i_mom])[0],
        "reversion_4s": evaluate("reversion_4s", -X3[d_te, a_te, i_ret4])[0],
    }
    oracle = evaluate("oraculo", f_te)[0]
    return RankingResult(
        config=cfg,
        feature_names=names,
        n_rows=int(y.size),
        n_test_rows=int(tested.sum()),
        test_dates=test_dates,
        y=y_te,
        p=p_te,
        fwd=f_te,
        date_idx=d_te,
        asset_idx=a_te,
        model=model_eval,
        baselines=baselines,
        oracle=oracle,
        tops=tops,
        fold_sizes=sizes,
        macro_nan_frac_by_fold=macro_nan,
    )


def turnover(tops: list[frozenset[int]], asset_idx: NDArray[np.int64]) -> float:
    """Fracción del tercio superior que cambia de una fecha a la siguiente."""
    names = [frozenset(int(asset_idx[r]) for r in t) for t in tops]
    ch = [1.0 - len(a & b) / max(1, len(b)) for a, b in zip(names, names[1:], strict=False)]
    return float(np.mean(ch)) if ch else float("nan")


def verdict(res: RankingResult, best: str) -> dict[str, Any]:
    """Los dos criterios del §5 y la lectura de la tabla de desenlaces."""
    cfg = res.config
    block = cfg.horizon
    ic = res.model.ic[np.isfinite(res.model.ic)]
    lo, hi = coverage_interval(ic, block, n_boot=cfg.n_boot, seed=0)
    c1 = bool(ic.mean() > 0 and lo > 0)
    pair = paired(res.model.ic, res.baselines[best].ic, block, cfg)
    c2 = bool(pair["diff_mean"] > 0 and pair["ci95"][0] > 0)
    prob = mx.probability_metrics(res.y.astype(np.float64), res.p)
    gate_prob = bool(prob.auc > 0.55 and prob.brier_skill_score > 0)
    if not c1:
        reading = "no hay orden detectable a esta potencia"
    elif not c2:
        reading = "el ranking trivial captura lo que hay"
    else:
        reading = "viable: habilita validación forward, no operar"
    return {
        "c1_ordena": c1,
        "c1_ic_mean": float(ic.mean()),
        "c1_ic_ci95": [lo, hi],
        "c2_supera_trivial": c2,
        "c2_best_baseline": best,
        "c2_paired": pair,
        "viable": c1 and c2,
        "reading": reading,
        "prob_auc": prob.auc,
        "prob_bss": prob.brier_skill_score,
        "prob_calib_pp": prob.mean_calibration_error_pp,
        "prob_gate_regla2": gate_prob,
    }
