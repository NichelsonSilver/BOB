"""Corre la medición pre-registrada del filtro de régimen CON STOP. Capa de I/O.

Criterio en `docs/PREREGISTRO_FILTRO_STOP.md`, commiteado junto con este
archivo y antes de correrlo. Reutiliza la sigma out-of-fold del runner de la
Fase 5-bis con la MISMA configuración y el mismo corte, para que los números
sean comparables con su artefacto. Lo único nuevo es el evento (stop a `d` en
vez de liquidación) y la calibración de la P de toque que muestra la
calculadora.

    uv run python -m bob.backtest.stopfilter_runner --artifact
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Literal

import numpy as np
from loguru import logger

from bob.backtest.riskfilter_runner import ARTIFACT_DIR, oof_volatility
from bob.backtest.runner import _end_time_ms
from bob.data.store import load_derivatives, load_series
from bob.models import riskfilter as rf
from bob.models import stopfilter as sf
from bob.models.experiment import FEATURE_SETS, ExperimentConfig
from bob.models.labeling import BarrierConfig
from bob.models.technical_rules import DEFAULT_COST_PCT
from bob.utils.console import enable_utf8_stdout

PREREGISTRATION = "docs/PREREGISTRO_FILTRO_STOP.md"

#: §4 del pre-registro. Fijos: no son flags, para que no se puedan barrer.
UNTIL = "2026-09-14T00:45"  # el mismo corte del artefacto de la Fase 5-bis
FEATURES = "price+deriv"
VOL_MODEL: Literal["gbm"] = "gbm"
HORIZON = 16
MIN_WARMUP = 2_000
PRIMARY_STOP = 0.01
PRIMARY_Q = rf.VALIDATED_Q
BASELINE = "ewma"
N_BOOT = 5_000
SEED = 0
#: Control de reproducción (§6): el lift de liquidación a 20x con esta misma
#: sigma tiene que dar lo que dio el artefacto de la Fase 5-bis.
REPRO_LEVERAGE = 20.0
REPRO_LIFT = 3.077
#: §5: la calculadora se corrige si la brecha supera esto en algún régimen.
CALIBRATION_TOLERANCE = 0.05

EXPLORATORY_STOPS = (0.005, 0.02)
EXPLORATORY_QS = (0.70, 0.90)
BRACKET_R = 2.0
FILTER_NAMES = ("model", "ewma", "garch", "har")


def filters_for(scores: dict[str, np.ndarray], eligible: np.ndarray, q: float) -> dict[str, Any]:
    """Umbral causal y decisión de cada filtro, más qué barras tienen umbral."""
    out: dict[str, Any] = {}
    for name in FILTER_NAMES:
        thr = rf.causal_quantile_threshold(scores[name], q, MIN_WARMUP)
        out[name] = {
            "elevated": rf.apply_filter(scores[name], thr, eligible),
            "has_threshold": eligible & np.isfinite(thr),
        }
    return out


def lift_block(
    filters: dict[str, Any], eligible: np.ndarray, events_all: np.ndarray, n_boot: int
) -> dict[str, Any]:
    """Lift de cada filtro contra los eventos de stop, restringido a elegibles.

    Misma restricción que `riskfilter_runner.evaluate_rejections`: contar
    barras no elegibles en el denominador infla el lift (Fase 5-bis).
    """
    sel = np.flatnonzero(eligible)
    events = events_all[sel]
    rej = {k: v["elevated"][sel] for k, v in filters.items()}
    lifts = {k: rf.filter_lift(r, events, label=k) for k, r in rej.items()}
    lo, hi = rf.lift_interval(rej["model"], events, block=HORIZON, n_boot=n_boot, seed=SEED)
    d_lo, d_hi = rf.lift_difference_interval(
        rej["model"], rej[BASELINE], events, block=HORIZON, n_boot=n_boot, seed=SEED
    )
    ratios = {
        k: sf.hit_rate_ratio(events_all, v["elevated"], v["has_threshold"])
        for k, v in filters.items()
    }
    return {
        "n_bars": int(sel.size),
        "n_events": lifts["model"].n_events,
        "event_rate": float(events.mean()),
        "lifts": {k: asdict(v) for k, v in lifts.items()},
        "lift_ci95": [lo, hi],
        "diff_vs_baseline": lifts["model"].lift - lifts[BASELINE].lift,
        "diff_ci95": [d_lo, d_hi],
        "hit_rate_ratio": ratios,
        "beats_chance": bool(lo > 1.0),
        "beats_baseline": bool(d_lo > 0.0),
    }


def calibration_block(
    sigma: np.ndarray, events: np.ndarray, filt: dict[str, Any], stop: float, n_boot: int
) -> dict[str, Any]:
    pred = sf.predicted_touch(sigma, stop)
    has = filt["has_threshold"]
    elev = filt["elevated"]
    out = {}
    for label, mask in (("todas", has), ("normal", has & ~elev), ("elevado", elev)):
        cal = sf.calibration(pred, events, mask, HORIZON, label=label, n_boot=n_boot, seed=SEED)
        out[label] = asdict(cal) | {
            "needs_correction": bool(cal.gap_excludes_zero and abs(cal.gap) > CALIBRATION_TOLERANCE)
        }
    return out


def run() -> dict[str, Any]:
    series = load_series("ETHUSDT", "15m", end_time=_end_time_ms(UNTIL))
    use_deriv, use_book, use_near = FEATURE_SETS[FEATURES]
    config = ExperimentConfig(
        barrier=BarrierConfig(horizon_bars=HORIZON),
        vol_kind=VOL_MODEL,
        use_derivatives=use_deriv,
        use_book=use_book,
        use_book_near=use_near,
    )
    derivatives = load_derivatives("ETHUSDT", "5m")
    funding = load_derivatives("ETHUSDT", "funding")
    scores = oof_volatility(series, config, derivatives, funding, None)
    exc = rf.adverse_excursion(series.open, series.high, series.low, HORIZON)
    eligible = exc.valid & np.isfinite(scores["model"])
    logger.info("barras evaluables: {:,}", int(eligible.sum()))

    by_q = {}
    for q in sorted({PRIMARY_Q, *EXPLORATORY_QS}):
        logger.info("umbral causal q={:.2f}…", q)
        by_q[q] = filters_for(scores, eligible, q)

    f0 = by_q[PRIMARY_Q]
    sel = np.flatnonzero(eligible)
    liq = np.column_stack(
        [
            rf.liquidates(exc.long, REPRO_LEVERAGE, "long")[sel],
            rf.liquidates(exc.short, REPRO_LEVERAGE, "short")[sel],
        ]
    )
    repro_lift = rf.filter_lift(f0["model"]["elevated"][sel], liq, label="model").lift
    repro_ok = round(repro_lift, 3) == REPRO_LIFT
    logger.info("control de reproducción: lift 20x = {:.3f} (esperado {})", repro_lift, REPRO_LIFT)

    ev_primary = sf.stop_events(exc, PRIMARY_STOP)
    primary = lift_block(f0, eligible, ev_primary, N_BOOT)
    calib = calibration_block(scores["model"], ev_primary, f0["model"], PRIMARY_STOP, N_BOOT)

    exploratory: list[dict[str, Any]] = []
    for stop in EXPLORATORY_STOPS:
        ev = sf.stop_events(exc, stop)
        exploratory.append(
            {
                "stop": stop,
                "q": PRIMARY_Q,
                **lift_block(f0, eligible, ev, 2_000),
                "calibration": calibration_block(scores["model"], ev, f0["model"], stop, 2_000),
            }
        )
    for q in EXPLORATORY_QS:
        exploratory.append(
            {
                "stop": PRIMARY_STOP,
                "q": q,
                **lift_block(by_q[q], eligible, ev_primary, 2_000),
                "calibration": calibration_block(
                    scores["model"], ev_primary, by_q[q]["model"], PRIMARY_STOP, 2_000
                ),
            }
        )

    logger.info("simulando trades con stop y objetivo…")
    outcomes = sf.bracket_outcomes(
        series.open,
        series.high,
        series.low,
        series.close,
        PRIMARY_STOP,
        BRACKET_R,
        HORIZON,
        DEFAULT_COST_PCT,
    )
    has = f0["model"]["has_threshold"]
    elev = f0["model"]["elevated"]
    planned_loss = -1.0 - DEFAULT_COST_PCT / PRIMARY_STOP
    bracket = {}
    for label, mask in (("todas", has), ("normal", has & ~elev), ("elevado", elev)):
        mean, lo, hi = sf.masked_mean_interval(outcomes, mask, HORIZON, seed=SEED)
        fin = np.isfinite(outcomes) & mask[:, None]
        vals = outcomes[fin]
        bracket[label] = {
            "mean_r": mean,
            "ci95": [lo, hi],
            "worst_r": float(vals.min()) if vals.size else float("nan"),
            # Pérdida peor que la planeada: gap a través del stop.
            "beyond_stop_rate": float((vals < planned_loss - 1e-9).mean()) if vals.size else 0.0,
            "n_setups": int(vals.size),
        }

    return {
        "config": {
            "symbol": "ETHUSDT",
            "timeframe": "15m",
            "until": UNTIL,
            "n_bars": len(series),
            "features": FEATURES,
            "vol_model": VOL_MODEL,
            "horizon": HORIZON,
            "min_warmup": MIN_WARMUP,
            "primary_stop": PRIMARY_STOP,
            "q": PRIMARY_Q,
            "baseline": BASELINE,
            "n_boot": N_BOOT,
            "seed": SEED,
            "cost_pct": DEFAULT_COST_PCT,
            "bracket_r": BRACKET_R,
            "calibration_tolerance": CALIBRATION_TOLERANCE,
            "preregistration": PREREGISTRATION,
        },
        "primary": primary,
        "calibration": calib,
        "reproduction": {"lift_20x": repro_lift, "expected": REPRO_LIFT, "ok": repro_ok},
        "verdict": {
            "c1_pass": primary["beats_chance"] and primary["beats_baseline"],
            "calculator_needs_correction": [
                k for k, v in calib.items() if k != "todas" and v["needs_correction"]
            ],
        },
        "exploratory": exploratory,
        "bracket": bracket,
    }


def _cal_lines(cal: dict[str, Any]) -> list[str]:
    out = []
    for label in ("todas", "normal", "elevado"):
        c = cal[label]
        lo, hi = c["gap_ci95"]
        flag = "  ⚠ corregir" if c["needs_correction"] else ""
        out.append(
            f"    {label:<8} n={c['n_bars']:>6,}  predicho {c['predicted']:6.1%}  "
            f"observado {c['observed']:6.1%}  brecha {c['gap']:+6.1%} "
            f"IC95% [{lo:+.1%}, {hi:+.1%}]  razón {c['ratio']:.2f}{flag}"
        )
    return out


def render(p: dict[str, Any]) -> str:
    cfg, pr, ver = p["config"], p["primary"], p["verdict"]
    lo, hi = pr["lift_ci95"]
    dlo, dhi = pr["diff_ci95"]
    lines = [
        "=" * 78,
        "FILTRO DE RÉGIMEN CON STOP — ETHUSDT 15m",
        f"{cfg['n_bars']:,} velas hasta {cfg['until']} UTC · {cfg['preregistration']}",
        f"sigma OOF {cfg['features']}/{cfg['vol_model']} · H={cfg['horizon']} · q={cfg['q']}",
        "=" * 78,
        f"control de reproducción: lift de liquidación a 20x = {p['reproduction']['lift_20x']:.3f}"
        f" (artefacto 5-bis: {p['reproduction']['expected']:.3f}) — "
        + ("REPRODUCE" if p["reproduction"]["ok"] else "⚠ NO REPRODUCE: MEDICIÓN INVÁLIDA"),
        "",
        f"CONFIRMATORIA 1 — stop a {cfg['primary_stop']:.1%}: "
        "¿el filtro marca las barras donde salta?",
        f"  barras {pr['n_bars']:,} · setups con stop saltado {pr['n_events']:,} "
        f"({pr['event_rate']:.1%} de los setups)",
        "  filtro     lift   rechaza  captura   tasa elevado / normal",
    ]
    for k in FILTER_NAMES:
        lf = pr["lifts"][k]
        lines.append(
            f"  {k:<8} {lf['lift']:6.3f}   {lf['reject_rate']:6.1%}   "
            f"{lf['capture_rate']:6.1%}   ×{pr['hit_rate_ratio'][k]:.2f}"
        )
    lines += [
        f"  criterio a (mejor que el azar): lift IC95% [{lo:.3f}, {hi:.3f}] — "
        + ("PASA" if pr["beats_chance"] else "NO PASA"),
        f"  criterio b (mejor que {cfg['baseline']}): diff {pr['diff_vs_baseline']:+.3f} "
        f"IC95% [{dlo:+.3f}, {dhi:+.3f}] — " + ("PASA" if pr["beats_baseline"] else "NO PASA"),
        "  VEREDICTO C1: " + ("PASA" if ver["c1_pass"] else "NO PASA"),
        "",
        "CONFIRMATORIA 2 — ¿la 'P(el ruido toca el stop)' de la calculadora está calibrada?",
        "  brecha = observado − predicho; > 0 = la calculadora SUBESTIMA",
        f"  se corrige si el IC excluye 0 y |brecha| > {cfg['calibration_tolerance']:.0%}",
        *_cal_lines(p["calibration"]),
        "  DECISIÓN C2: "
        + (
            "corregir la calculadora en " + ", ".join(ver["calculator_needs_correction"])
            if ver["calculator_needs_correction"]
            else "la calculadora queda como está"
        ),
        "",
        f"EXPLORATORIA — trade con stop {cfg['primary_stop']:.1%} y objetivo "
        f"{cfg['bracket_r']:g}R, neto de {cfg['cost_pct']:.2%}, dirección indistinta",
    ]
    for label in ("todas", "normal", "elevado"):
        b = p["bracket"][label]
        lines.append(
            f"    {label:<8} n={b['n_setups']:>6,}  E[R] {b['mean_r']:+.3f} "
            f"IC95% [{b['ci95'][0]:+.3f}, {b['ci95'][1]:+.3f}]  peor {b['worst_r']:+.2f} R  "
            f"pérdida peor que la planeada {b['beyond_stop_rate']:.2%}"
        )
    lines += ["", "EXPLORATORIAS — otros stops y cuantiles (no deciden)"]
    for e in p["exploratory"]:
        lf = e["lifts"]["model"]
        cal = e["calibration"]
        lines.append(
            f"  stop {e['stop']:.1%} q={e['q']:.2f}  lift {lf['lift']:.3f} "
            f"IC[{e['lift_ci95'][0]:.2f}, {e['lift_ci95'][1]:.2f}]  "
            f"vs ewma {e['diff_vs_baseline']:+.3f} "
            f"IC[{e['diff_ci95'][0]:+.2f}, {e['diff_ci95'][1]:+.2f}]  "
            f"×{e['hit_rate_ratio']['model']:.2f}  "
            f"brecha normal {cal['normal']['gap']:+.1%} / elevado {cal['elevado']['gap']:+.1%}"
        )
    lines.append("=" * 78)
    return "\n".join(lines)


def main() -> None:
    enable_utf8_stdout()
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--artifact", action="store_true")
    args = ap.parse_args()
    payload = run()
    text = render(payload)
    print(text)
    if args.artifact:
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        base = f"stopfilter-ETHUSDT-15m-{FEATURES}-{VOL_MODEL}-{stamp}"
        (ARTIFACT_DIR / f"{base}.txt").write_text(text + "\n", encoding="utf-8")
        (ARTIFACT_DIR / f"{base}.json").write_text(
            json.dumps(payload, indent=2, default=float), encoding="utf-8"
        )
        print(f"\nartefacto: artifacts/{base}.txt|.json")


if __name__ == "__main__":
    main()
