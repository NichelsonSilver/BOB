# ruff: noqa: E501 — lineas de formato del reporte/fixtures
"""Corre el gate del ranking macro (M3) y lo congela en artefacto.

    uv run python -m marea.ranking_run

Pre-registro: `docs/PREREGISTRO_RANKING_MACRO.md` (`836e603`). Corre la
primaria, el placebo y las 8 exploratorias declaradas, en ese orden, y escribe
`artifacts/marea-ranking-*.txt|json`. Solo métricas: nada de series crudas de
Tiingo (licencia).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any

import numpy as np

from marea import monitor, power, ranking
from marea.config import DB_PATH
from marea.data import store
from marea.data.catalog import ETF_BY_TICKER, ETFS
from marea.liquidity import change, label
from marea.power_run import ARTIFACTS, load_daily, peak_memory_mb
from marea.ranking import RankingConfig

PRIMARY_EXCLUDE = ("REET",)


@dataclass(frozen=True)
class Variant:
    name: str
    exclude: tuple[str, ...] = PRIMARY_EXCLUDE
    cfg: RankingConfig = RankingConfig()
    confirmatory: bool = False


PRIMARY = Variant("PRIMARIA", confirmatory=True)
VARIANTS: tuple[Variant, ...] = (
    PRIMARY,
    Variant("placebo (control)", cfg=RankingConfig(placebo=True)),
    Variant("H = 1 semana", cfg=RankingConfig(horizon=1)),
    Variant("H = 13 semanas", cfg=RankingConfig(horizon=13)),
    Variant("23 ETFs desde 2014-07", exclude=()),
    Variant(
        "21 ETFs desde 2007-11 (sin Fed neta)",
        exclude=("REET", "CPER"),
        cfg=RankingConfig(drop_macro=("fed_net",)),
    ),
    Variant("sin bloque C (liquidez)", cfg=RankingConfig(use_macro=False)),
    Variant("sin bloque B (transversal)", cfg=RankingConfig(use_cross_section=False)),
    Variant("XGBoost", cfg=RankingConfig(model_kind="xgb")),
    Variant("entrada al cierre de t", cfg=RankingConfig(entry_lag=0)),
)


@dataclass
class Data:
    tickers: list[str]
    decision_days: list[date]
    closes: np.ndarray
    index: np.ndarray
    ends: list[int]
    macro: np.ndarray
    regime: list[str | None]


def load(exclude: tuple[str, ...]) -> Data:
    tickers = [e.ticker for e in ETFS if e.ticker not in exclude]
    days, daily, dropped = load_daily(tickers)
    if dropped:
        raise ValueError(f"{dropped} días fuera del calendario común: revisar la ingesta")
    ends = power.weekly_grid(days)
    idx = ranking.price_index(daily)
    dec = [days[i] for i in ends]
    conn = store.connect(DB_PATH)
    try:
        series = monitor.load_realtime(conn, dec)
    finally:
        conn.close()
    macro = np.column_stack(
        [
            change(series[k], monitor.BY_KEY[k].kind) * monitor.BY_KEY[k].sign
            for k in ranking.MACRO_FEATURES
        ]
    )
    mon = monitor.compute(series, dec)
    return Data(
        tickers=tickers,
        decision_days=dec,
        closes=ranking.decision_closes(idx, ends),
        index=idx,
        ends=ends,
        macro=macro,
        regime=[label(float(p)) for p in mon.composite_pct],
    )


def _by_group(ic: np.ndarray, groups: list[Any]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for g in sorted({str(x) for x in groups}):
        v = np.array([x for x, gg in zip(ic, groups, strict=True) if str(gg) == g])
        v = v[np.isfinite(v)]
        out[g] = {"n": int(v.size), "ic_mean": float(v.mean()) if v.size else float("nan")}
    return out


def evaluate(v: Variant, data: Data) -> tuple[dict[str, Any], ranking.RankingResult]:
    cfg = v.cfg
    spy = data.tickers.index("SPY")
    fwd = ranking.forward_returns(data.index, data.ends, cfg.horizon, cfg.entry_lag)
    blocks = [ETF_BY_TICKER[t].block for t in data.tickers]
    res = ranking.run(data.closes, fwd, data.macro, blocks, spy, cfg)

    cost = 2.0 * cfg.leg_cost
    block = cfg.horizon
    best = max(res.baselines, key=lambda k: float(np.nanmean(res.baselines[k].ic)))
    td = res.test_dates
    spy_fwd = fwd[td, spy]
    out: dict[str, Any] = {
        "name": v.name,
        "confirmatory": v.confirmatory,
        "config": asdict(cfg),
        "tickers": data.tickers,
        "first_decision": data.decision_days[0].isoformat(),
        "test_from": data.decision_days[int(td[0])].isoformat(),
        "test_to": data.decision_days[int(td[-1])].isoformat(),
        "n_rows": res.n_rows,
        "n_test_rows": res.n_test_rows,
        "n_test_dates": int(td.size),
        "fold_sizes": res.fold_sizes,
        "macro_nan_frac_by_fold": res.macro_nan_frac_by_fold,
        "verdict": ranking.verdict(res, best),
        "model": res.model.summary(block, cfg, cost),
        "baselines": {k: e.summary(block, cfg, cost) for k, e in res.baselines.items()},
        "oracle": res.oracle.summary(block, cfg, cost),
        "pair_cost": cost,
        "turnover_top": ranking.turnover(res.tops, res.asset_idx),
        "ic_by_year": _by_group(res.model.ic, [data.decision_days[int(d)].year for d in td]),
        "ic_by_spy_sign": _by_group(
            res.model.ic, ["SPY sube" if x > 0 else "SPY cae" for x in spy_fwd]
        ),
        "ic_by_liquidity_regime": _by_group(
            res.model.ic, [data.regime[int(d)] or "sin dato" for d in td]
        ),
    }
    og = out["oracle"]["spread_gross_mean"]
    out["oracle_fraction"] = out["model"]["spread_gross_mean"] / og if og else float("nan")
    return out, res


def _ci(x: list[float]) -> str:
    return f"[{x[0]:+.4f}, {x[1]:+.4f}]"


def render(rep: dict[str, Any]) -> str:
    p = rep["primary"]
    v = p["verdict"]
    lines = [
        "GATE DEL RANKING MACRO — MAREA M3",
        f"run {rep['run_id']} · pre-registro docs/PREREGISTRO_RANKING_MACRO.md ({rep['prereg_commit']})",
        f"primaria: {len(p['tickers'])} ETFs · H={p['config']['horizon']} semanas · test "
        f"{p['test_from']} .. {p['test_to']} ({p['n_test_dates']} fechas, {p['n_test_rows']} filas)",
        "",
        "CRITERIOS CONFIRMATORIOS (§5)",
        f"  1. ordena          IC medio {v['c1_ic_mean']:+.4f}  IC95% {_ci(v['c1_ic_ci95'])}"
        f"  -> {'PASA' if v['c1_ordena'] else 'NO PASA'}",
        f"  2. supera trivial  vs {v['c2_best_baseline']}: diferencia "
        f"{v['c2_paired']['diff_mean']:+.4f}  IC95% {_ci(v['c2_paired']['ci95'])}"
        f"  -> {'PASA' if v['c2_supera_trivial'] else 'NO PASA'}",
        f"  VEREDICTO: {'VIABLE' if v['viable'] else 'NO VIABLE'} — {v['reading']}",
        "",
        "REPORTADO, NO DECIDE",
        f"  3. probabilidad    AUC {v['prob_auc']:.4f} · BSS {v['prob_bss']:+.5f} · calibración "
        f"{v['prob_calib_pp']:.2f} pp -> regla 2 {'cumple' if v['prob_gate_regla2'] else 'NO cumple'}"
        " (ninguna probabilidad se muestra como operable)"
        if not v["prob_gate_regla2"]
        else f"  3. probabilidad    AUC {v['prob_auc']:.4f} · BSS {v['prob_bss']:+.5f} -> regla 2 cumple",
        f"  4. spread neto tercio sup − inf  {p['model']['spread_net_mean']:+.4%} por período"
        f"  IC95% {_ci(p['model']['spread_net_ci95'])}  (costo del par {p['pair_cost']:.2%};"
        " sin potencia declarada)",
        f"  5. aporte de la liquidez (modelo − sin bloque C)  {rep['liquidity_contribution']['diff_mean']:+.4f}"
        f"  IC95% {_ci(rep['liquidity_contribution']['ci95'])}  (sin potencia declarada)",
        f"  6. rotación del tercio superior {p['turnover_top']:.1%} por rebalanceo · fracción del"
        f" oráculo {p['oracle_fraction']:.1%}",
        "",
        "  baselines triviales (mismo período de test):",
    ]
    for k, b in p["baselines"].items():
        lines.append(f"    {k:16} IC {b['ic_mean']:+.4f}  IC95% {_ci(b['ic_ci95'])}")
    lines.append(f"    {'oráculo':16} IC {p['oracle']['ic_mean']:+.4f}")
    for title, key in (
        ("IC por año", "ic_by_year"),
        ("IC según SPY en el horizonte", "ic_by_spy_sign"),
        ("IC según régimen de liquidez M2", "ic_by_liquidity_regime"),
    ):
        lines.append(
            f"  {title}: "
            + " · ".join(f"{g} {d['ic_mean']:+.3f} (n={d['n']})" for g, d in p[key].items())
        )
    lines.append(
        f"  NaN del bloque C en el train, por fold: {', '.join(f'{x:.0%}' for x in p['macro_nan_frac_by_fold'])}"
    )
    lines += [
        "",
        "TODAS LAS CORRIDAS (solo la primaria decide)",
        f"  {'variante':28} {'filas':>6} {'IC':>8} {'IC95%':>20} {'vs trivial':>11} {'AUC':>7} {'neto':>8}",
    ]
    for r in rep["runs"]:
        vv = r["verdict"]
        lines.append(
            f"  {r['name'][:28]:28} {r['n_test_rows']:>6} {vv['c1_ic_mean']:>+8.4f} {_ci(vv['c1_ic_ci95']):>20} "
            f"{vv['c2_paired']['diff_mean']:>+11.4f} {vv['prob_auc']:>7.4f} {r['model']['spread_net_mean']:>+8.3%}"
        )
    for dev in rep["deviations"]:
        lines.append(f"  DESVIACIÓN DECLARADA: {dev}")
    lines += [
        "",
        f"recursos: {rep['runtime_s']:.1f} s · RAM pico "
        + (f"{rep['peak_memory_mb']:.0f} MB" if rep["peak_memory_mb"] else "no medida"),
    ]
    return "\n".join(lines)


def main() -> int:
    from bob.utils.console import enable_utf8_stdout

    enable_utf8_stdout()
    t0 = time.perf_counter()
    cache: dict[tuple[str, ...], Data] = {}
    runs: list[dict[str, Any]] = []
    results: dict[str, ranking.RankingResult] = {}
    for v in VARIANTS:
        if v.exclude not in cache:
            cache[v.exclude] = load(v.exclude)
        out, res = evaluate(v, cache[v.exclude])
        runs.append(out)
        results[v.name] = res
        print(
            f"  {v.name:30} IC {out['verdict']['c1_ic_mean']:+.4f}  ({time.perf_counter() - t0:.0f} s)",
            flush=True,
        )

    prim, noc = results[PRIMARY.name], results["sin bloque C (liquidez)"]
    if not np.array_equal(prim.test_dates, noc.test_dates):
        raise AssertionError("la primaria y la variante sin liquidez no comparten fechas de test")
    contribution = ranking.paired(prim.model.ic, noc.model.ic, PRIMARY.cfg.horizon, PRIMARY.cfg)

    run_id = "marea-ranking-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rep: dict[str, Any] = {
        "run_id": run_id,
        "prereg_commit": "836e603",
        "deviations": [
            "exploratoria '21 ETFs desde 2007-11' corre sin la Fed neta en el bloque C: "
            "no existe antes de 2014 y dejaba la columna entera en NaN en los folds "
            "internos de calibración (sklearn: 'window shape cannot be larger than input "
            "array shape'). Encontrado por el error, sin ver resultados de esa variante. "
            "La primaria no cambia."
        ],
        "primary": runs[0],
        "liquidity_contribution": contribution,
        "runs": runs,
        "runtime_s": time.perf_counter() - t0,
        "peak_memory_mb": peak_memory_mb(),
    }
    text = render(rep)
    print("\n" + text)
    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS / f"{run_id}.txt").write_text(text + "\n", encoding="utf-8")
    (ARTIFACTS / f"{run_id}.json").write_text(
        json.dumps(rep, indent=2, default=float), encoding="utf-8"
    )
    print(f"\nartefacto: artifacts/{run_id}.txt|json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
