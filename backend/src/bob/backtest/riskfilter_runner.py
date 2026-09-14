"""Corre la medición pre-registrada del filtro de régimen. Capa de I/O.

El criterio está fijado en `docs/PREREGISTRO_FILTRO_RIESGO.md`, commiteado
antes que este archivo. Acá no se decide nada: se carga la serie, se produce
la sigma **out-of-fold** con la misma maquinaria purgada del gate, y se
evalúa la única prueba confirmatoria más la grilla exploratoria declarada.

    uv run python -m bob.backtest.riskfilter_runner --artifact

La sigma OOF se reconstruye acá en vez de sacarla de `run_experiment` porque
aquel devuelve **métricas**, no predicciones: retenerlas obligaría a tocar el
camino que produce los artefactos del gate, y esos tienen que seguir
reproduciendo bit a bit.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

import bob.signals.numeric as nm
from bob.backtest.runner import _end_time_ms
from bob.data.store import load_book_depth, load_derivatives, load_series
from bob.models import riskfilter as rf
from bob.models.baselines import (
    EWMAVolForecaster,
    GarchVolForecaster,
    HARVolForecaster,
)
from bob.models.experiment import (
    FEATURE_SETS,
    ExperimentConfig,
    assemble_features,
    assert_columns_trainable,
)
from bob.models.labeling import BarrierConfig
from bob.models.forecast import VolatilityModel
from bob.models.labeling import forward_volatility
from bob.models.validation import purged_walk_forward
from bob.utils.console import enable_utf8_stdout

ARTIFACT_DIR = Path(__file__).resolve().parents[3] / "artifacts"

#: Configuración confirmatoria, §4 del pre-registro. NO se toca desde el CLI:
#: exponerla como flag sería invitar a barrerla y reportar la que mejor salga,
#: que es exactamente lo que el pre-registro existe para impedir.
PRIMARY_LEVERAGE = 20.0
PRIMARY_Q = 0.80
PRIMARY_BASELINE = "ewma"

#: Grilla exploratoria, §6. Declarada, y etiquetada como tal en el reporte.
EXPLORATORY_LEVERAGES = (5.0, 10.0, 20.0, 25.0, 50.0)
EXPLORATORY_QUANTILES = (0.70, 0.80, 0.90)

#: Bajo este número de eventos independientes la muestra no resuelve nada y
#: el reporte lo dice en vez de imprimir un lift con tres decimales.
MIN_EFFECTIVE_EVENTS = 30


def oof_volatility(
    series: Any, config: ExperimentConfig, derivatives: Any, funding: Any, book: Any
) -> dict[str, np.ndarray]:
    """Sigma out-of-fold del modelo y de los tres baselines, por posición.

    Mismo walk-forward purgado con embargo que usó el gate. Devuelve arrays
    del largo de la serie con NaN donde la barra no fue test de ningún fold:
    esas barras no tienen predicción honesta y no pueden entrar a la
    evaluación.
    """
    X, feature_names, sparse_names, _ = assemble_features(
        series, config, derivatives, funding, book
    )
    assert_columns_trainable(X, feature_names, config.min_train_frac)

    dense_idx = [i for i, n_ in enumerate(feature_names) if n_ not in sparse_names]
    finite_rows = np.all(np.isfinite(X[:, dense_idx]), axis=1)

    n = len(series)
    horizon = config.barrier.horizon_bars
    y_vol = forward_volatility(series.close, horizon)
    returns = nm.log_returns(series.close)
    tf_ms = series.interval_ms

    usable = np.flatnonzero(finite_rows & np.isfinite(y_vol) & (y_vol > 0))
    span = np.minimum(np.arange(n) + horizon, n - 1)
    splits = purged_walk_forward(
        usable,
        span,
        n_splits=config.n_splits,
        min_train_frac=config.min_train_frac,
        embargo_frac=config.embargo_frac,
        expanding=config.expanding,
    )

    w_d = max(2, int(round(4 * 3_600_000 / tf_ms)))
    w_w = max(3, int(round(24 * 3_600_000 / tf_ms)))
    w_m = max(4, int(round(168 * 3_600_000 / tf_ms)))
    har_design = HARVolForecaster.design_matrix(
        np.nan_to_num(nm.realized_vol(returns, w_d), nan=nm.EPS),
        np.nan_to_num(nm.realized_vol(returns, w_w), nan=nm.EPS),
        np.nan_to_num(nm.realized_vol(returns, w_m), nan=nm.EPS),
    )

    out = {k: np.full(n, np.nan) for k in ("model", "ewma", "garch", "har", "realized")}
    for split in splits:
        logger.info("fold {}: n_train={} n_test={}", split.name, split.n_train, split.n_test)
        vm = VolatilityModel(kind=config.vol_kind, seed=config.seed).fit(
            X, y_vol, split.train_idx
        )
        ewma = EWMAVolForecaster(horizon=horizon).predict_from_returns(returns)
        garch = GarchVolForecaster(horizon=horizon).fit(returns[split.train_idx])
        har = HARVolForecaster().fit(har_design[split.train_idx], y_vol[split.train_idx])

        t = split.test_idx
        out["model"][t] = vm.predict(X[t])
        out["ewma"][t] = ewma[t]
        out["garch"][t] = garch.predict_from_returns(returns)[t]
        out["har"][t] = har.predict(har_design[t])
        out["realized"][t] = y_vol[t]

    return out


def build_rejections(
    scores: dict[str, np.ndarray], eligible: np.ndarray, q: float, min_warmup: int
) -> dict[str, np.ndarray]:
    """Decisión del filtro por score, para un `q` dado.

    Se calcula una vez por `(score, q)` y se reusa en toda la columna de
    leverage: el umbral causal es O(n²) y no depende del leverage, así que
    recalcularlo por punto de la grilla multiplicaría por cinco el costo de
    la corrida sin cambiar un solo número.
    """
    out = {}
    for name, score in scores.items():
        if name == "realized":
            continue
        threshold = rf.causal_quantile_threshold(score, q, min_warmup)
        out[name] = rf.apply_filter(score, threshold, eligible)
    return out


def evaluate_rejections(
    rejections: dict[str, np.ndarray],
    eligible: np.ndarray,
    exc: rf.AdverseExcursion,
    leverage: float,
    q: float,
    horizon: int,
    *,
    n_boot: int,
    seed: int,
) -> dict[str, Any]:
    """Un punto de la grilla: lift de cada filtro contra los mismos eventos.

    **Todo se restringe a las barras elegibles antes de contar.** Las barras
    sin sigma OOF no pueden ser rechazadas ni pueden liquidar, así que dejarlas
    en el denominador baja la tasa de rechazo sin bajar la captura e infla el
    lift — medido sobre esta misma serie, 5,06 en vez de 3,08. Es un falso
    positivo silencioso: ninguna cuenta falla, el número simplemente miente.
    """
    sel = np.flatnonzero(eligible)
    events = np.column_stack(
        [
            rf.liquidates(exc.long, leverage, "long")[sel],
            rf.liquidates(exc.short, leverage, "short")[sel],
        ]
    )
    rejections = {name: r[sel] for name, r in rejections.items()}
    results = {
        name: rf.filter_lift(rejected, events, label=name)
        for name, rejected in rejections.items()
    }

    n_events = results["model"].n_events
    effective = n_events / horizon

    lo, hi = rf.lift_interval(
        rejections["model"], events, block=horizon, n_boot=n_boot, seed=seed
    )
    d_lo, d_hi = rf.lift_difference_interval(
        rejections["model"],
        rejections[PRIMARY_BASELINE],
        events,
        block=horizon,
        n_boot=n_boot,
        seed=seed,
    )

    return {
        "leverage": leverage,
        "quantile": q,
        "n_bars": int(sel.size),
        "n_events": n_events,
        "effective_events": effective,
        "underpowered": effective < MIN_EFFECTIVE_EVENTS,
        "lifts": {k: asdict(v) for k, v in results.items()},
        "lift_ci95": [lo, hi],
        "beats_chance": bool(lo > 1.0),
        "diff_vs_baseline": results["model"].lift - results[PRIMARY_BASELINE].lift,
        "diff_ci95": [d_lo, d_hi],
        "beats_baseline": bool(d_lo > 0.0),
    }


def evaluate(
    scores: dict[str, np.ndarray],
    exc: rf.AdverseExcursion,
    leverage: float,
    q: float,
    horizon: int,
    *,
    min_warmup: int,
    n_boot: int,
    seed: int,
) -> dict[str, Any]:
    """Conveniencia: arma los filtros y evalúa un punto de la grilla."""
    eligible = exc.valid & np.isfinite(scores["model"])
    rejections = build_rejections(scores, eligible, q, min_warmup)
    return evaluate_rejections(
        rejections, eligible, exc, leverage, q, horizon, n_boot=n_boot, seed=seed
    )


def verdict(primary: dict[str, Any]) -> tuple[bool, list[str]]:
    """Los dos criterios de §5 del pre-registro. Los dos, no uno."""
    reasons = []
    c1 = primary["beats_chance"]
    c2 = primary["beats_baseline"]
    lo, hi = primary["lift_ci95"]
    d_lo, d_hi = primary["diff_ci95"]
    reasons.append(
        f"criterio 1 (mejor que el azar): lift={primary['lifts']['model']['lift']:.3f} "
        f"IC95% [{lo:.3f}, {hi:.3f}] — {'PASA' if c1 else 'NO PASA'} "
        f"(el IC {'excluye' if c1 else 'incluye'} 1.0)"
    )
    reasons.append(
        f"criterio 2 (mejor que {PRIMARY_BASELINE}): diff={primary['diff_vs_baseline']:+.3f} "
        f"IC95% [{d_lo:+.3f}, {d_hi:+.3f}] — {'PASA' if c2 else 'NO PASA'} "
        f"(el IC {'excluye' if c2 else 'incluye'} 0)"
    )
    return (c1 and c2), reasons


def render(payload: dict[str, Any]) -> str:
    cfg = payload["config"]
    p = payload["primary"]
    ok, reasons = verdict(p)
    w = 74
    out = [
        "=" * w,
        f"FILTRO DE RÉGIMEN — {cfg['symbol']} {cfg['timeframe']}",
        f"{cfg['from']} .. {cfg['to']}",
        "criterio pre-registrado en docs/PREREGISTRO_FILTRO_RIESGO.md",
        "=" * w,
        "",
        f"features={cfg['features']}  vol_model={cfg['vol_model']}  "
        f"folds={cfg['folds']}  H={cfg['horizon']}",
        f"barras con sigma OOF: {cfg['n_oof']:,}",
        "",
        "-" * w,
        f"PRUEBA CONFIRMATORIA — leverage {PRIMARY_LEVERAGE:g}x, q={PRIMARY_Q:.2f}, "
        "direcciones agrupadas",
        "-" * w,
        f"  barras evaluables: {p['n_bars']:,}    eventos de liquidación: {p['n_events']:,}"
        f"  (~{p['effective_events']:.0f} independientes)",
        "",
        f"  {'filtro':10s} {'lift':>7s} {'rechaza':>9s} {'captura':>9s} {'costo':>8s}",
    ]
    for name in ("model", "ewma", "garch", "har"):
        lr = p["lifts"][name]
        label = "MODELO" if name == "model" else name
        out.append(
            f"  {label:10s} {lr['lift']:7.3f} {lr['reject_rate']:8.1%} "
            f"{lr['capture_rate']:8.1%} {lr['false_reject_rate']:7.1%}"
        )
    out += [
        "",
        "  lift = (liquidaciones evitadas) / (barras rechazadas).",
        "  Un filtro que rechaza al azar da exactamente 1.000.",
        "  costo = barras que NO liquidaban y el filtro rechazó igual.",
        "",
    ]
    out += [f"  {r}" for r in reasons]
    out += [
        "",
        f"  VEREDICTO: {'el filtro es VIABLE' if ok else 'el filtro NO es viable'}",
        "",
        "-" * w,
        "GRILLA EXPLORATORIA — declarada en §6, NO decide el veredicto",
        "-" * w,
        f"  {'lev':>5s} {'q':>5s} {'eventos':>8s} {'modelo':>8s} {'ewma':>8s} "
        f"{'garch':>8s} {'har':>8s}  nota",
    ]
    for row in payload["exploratory"]:
        note = "sin potencia" if row["underpowered"] else ""
        out.append(
            f"  {row['leverage']:4.0f}x {row['quantile']:5.2f} {row['n_events']:8,d} "
            f"{row['lifts']['model']['lift']:8.3f} {row['lifts']['ewma']['lift']:8.3f} "
            f"{row['lifts']['garch']['lift']:8.3f} {row['lifts']['har']['lift']:8.3f}"
            f"  {note}"
        )
    out += [
        "",
        "  Un lift que no crece con el leverage sería señal de bug, no hallazgo:",
        "  a más leverage la liquidación se acerca y depende más de la cola.",
        "",
        "=" * w,
        "Lo que esta medición NO decide (§7): no habilita el KPI 1, que sigue",
        "sin pasar el gate; no vuelve positivo el EV; y no arregla el techo de",
        "la sigma, que es la propiedad de magnitud y sigue rota.",
        "=" * w,
    ]
    return "\n".join(out)


def main() -> None:
    enable_utf8_stdout()
    exp_defaults = ExperimentConfig()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--features", default="price+deriv", choices=sorted(FEATURE_SETS))
    parser.add_argument("--vol-model", default=exp_defaults.vol_kind, choices=["gbm", "xgb", "ridge"])
    parser.add_argument("--folds", type=int, default=exp_defaults.n_splits)
    parser.add_argument("--horizon", type=int, default=BarrierConfig().horizon_bars)
    parser.add_argument("--until", default=None, metavar="YYYY-MM-DD[THH:MM]")
    parser.add_argument(
        "--min-warmup",
        type=int,
        default=2_000,
        help="barras mínimas de historia antes de que el umbral causal exista",
    )
    parser.add_argument("--n-boot", type=int, default=rf.DEFAULT_N_BOOT)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--artifact", action="store_true", help="congela el reporte en artifacts/")
    args = parser.parse_args()

    series = load_series(args.symbol, args.timeframe, end_time=_end_time_ms(args.until))
    if len(series) == 0:
        raise SystemExit(f"no hay velas de {args.symbol} {args.timeframe} en DB")
    logger.info("cargadas {:,} velas", len(series))

    use_deriv, use_book, use_near = FEATURE_SETS[args.features]
    config = ExperimentConfig(
        barrier=BarrierConfig(horizon_bars=args.horizon),
        n_splits=args.folds,
        vol_kind=args.vol_model,
        use_derivatives=use_deriv,
        use_book=use_book,
        use_book_near=use_near,
    )
    derivatives = load_derivatives(args.symbol, "5m") if use_deriv else None
    funding = load_derivatives(args.symbol, "funding") if use_deriv else None
    book = load_book_depth(args.symbol, args.timeframe) if use_book else None

    scores = oof_volatility(series, config, derivatives, funding, book)
    exc = rf.adverse_excursion(series.open, series.high, series.low, args.horizon)
    n_oof = int(np.isfinite(scores["model"]).sum())
    logger.info("sigma OOF disponible en {:,} barras", n_oof)

    eligible = exc.valid & np.isfinite(scores["model"])
    logger.info("barras evaluables: {:,}", int(eligible.sum()))

    # Un juego de filtros por q, reusado en toda la columna de leverage.
    by_q = {}
    for q in sorted({PRIMARY_Q, *EXPLORATORY_QUANTILES}):
        logger.info("umbral causal q={:.2f}…", q)
        by_q[q] = build_rejections(scores, eligible, q, args.min_warmup)

    kw = dict(n_boot=args.n_boot, seed=args.seed, horizon=args.horizon)
    primary = evaluate_rejections(
        by_q[PRIMARY_Q], eligible, exc, PRIMARY_LEVERAGE, PRIMARY_Q, **kw
    )

    exploratory = []
    for lev in EXPLORATORY_LEVERAGES:
        for q in EXPLORATORY_QUANTILES:
            logger.info("exploratoria: {:g}x q={:.2f}", lev, q)
            exploratory.append(
                evaluate_rejections(by_q[q], eligible, exc, lev, q, **kw)
            )

    payload = {
        "config": {
            "symbol": args.symbol,
            "timeframe": args.timeframe,
            "features": args.features,
            "vol_model": args.vol_model,
            "folds": args.folds,
            "horizon": args.horizon,
            "min_warmup": args.min_warmup,
            "n_boot": args.n_boot,
            "seed": args.seed,
            "n_oof": n_oof,
            "from": datetime.fromtimestamp(
                int(series.open_time[0]) / 1000, UTC
            ).strftime("%Y-%m-%d %H:%M"),
            "to": datetime.fromtimestamp(
                int(series.open_time[-1]) / 1000, UTC
            ).strftime("%Y-%m-%d %H:%M"),
            "preregistration": "docs/PREREGISTRO_FILTRO_RIESGO.md",
        },
        "primary": primary,
        "exploratory": exploratory,
    }
    passed, reasons = verdict(primary)
    payload["verdict"] = {"viable": passed, "reasons": reasons}

    text = render(payload)
    print(text)

    if args.artifact:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        base = f"riskfilter-{args.symbol}-{args.timeframe}-{args.features}-{args.vol_model}-{stamp}"
        (ARTIFACT_DIR / f"{base}.txt").write_text(text, encoding="utf-8")
        (ARTIFACT_DIR / f"{base}.json").write_text(
            json.dumps(payload, indent=2, default=float), encoding="utf-8"
        )
        print(f"\nartefacto: artifacts/{base}.txt|.json")


if __name__ == "__main__":
    main()
