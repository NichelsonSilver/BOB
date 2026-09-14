"""¿Se arregla el techo de la sigma con regresión cuantílica? Capa de I/O.

**El problema.** `VolatilityModel` entrena con pérdida cuadrática sobre
log-volatilidad, o sea estima la **media condicional**, y una media encoge
hacia el centro de la distribución de entrenamiento por construcción. Medido
en la corrida forward de la Fase 5: el 3,6% de las barras realizó más
movimiento que el máximo que el modelo llegó a emitir en 13 días, y el peor
fue **2,75×** ese techo. Para un usuario apalancado es la dirección que duele
— TP y SL dimensionados por esa sigma quedan cortos justo en el pico.

**La hipótesis.** Entrenar el **cuantil** condicional en vez de la media da
una sigma que cubre la cola, porque la pérdida pinball penaliza asimétricamente
y no tiene por qué colapsar al centro.

Criterios, fijados ANTES de correr
-----------------------------------
La sigma cuantílica se adopta si y solo si cumple **los tres**:

1. **Calibra como cuantil.** La tasa de excedencia empírica —fracción de
   barras con realizada por encima de la pronosticada— tiene que estar cerca
   del nominal `1−q`, con IC95% por bloques que lo contenga. Un cuantil al 90%
   que excede el 30% de las veces no es un cuantil, es una media con otro
   nombre.
2. **Le gana al escalado trivial.** El baseline es `k · sigma_media`, con `k`
   calibrado **en el train de cada fold** para dar exactamente la misma tasa
   de excedencia. Si multiplicar la media por una constante hace el mismo
   trabajo, no hace falta un modelo nuevo — y ese es el resultado honesto que
   hay que poder reportar.
3. **Pérdida pinball menor** que la de ese baseline, que es la pérdida propia
   del problema cuantílico.

Se reporta siempre, apruebe o no: **cuánto del déficit de cola recupera**, o
sea qué pasa en las barras donde la media se quedó corta. Ese es el número con
consecuencia operativa; los otros tres dicen si el objeto es lo que dice ser.

**Lo que esta medición NO decide.** No toca el filtro de régimen, que usa el
*orden* y ya pasó su criterio. No habilita el KPI 1. Y adoptar la sigma
cuantílica para dimensionar barreras cambiaría `model_version`: no se hace a
mitad de una corrida forward, por la razón de la Fase 4-bis.

    uv run python -m bob.backtest.sigma_tail_runner --artifact
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from bob.backtest.runner import _end_time_ms
from bob.data.store import load_book_depth, load_derivatives, load_series
from bob.models.experiment import (
    FEATURE_SETS,
    ExperimentConfig,
    assemble_features,
    assert_columns_trainable,
)
from bob.models.forecast import VolatilityModel
from bob.models.labeling import BarrierConfig, forward_volatility
from bob.models.validation import purged_walk_forward
from bob.utils.console import enable_utf8_stdout

ARTIFACT_DIR = Path(__file__).resolve().parents[3] / "artifacts"

#: Cuantiles a evaluar. Declarados: 0,80 y 0,90 son los dos niveles con los
#: que tendría sentido dimensionar una barrera conservadora.
QUANTILES = (0.80, 0.90)

#: Tolerancia del criterio 1, en puntos porcentuales sobre la excedencia.
CALIBRATION_TOLERANCE_PP = 3.0


def pinball_loss(y: np.ndarray, f: np.ndarray, q: float) -> float:
    """Pérdida pinball media — la propia del problema cuantílico."""
    d = y - f
    return float(np.mean(np.maximum(q * d, (q - 1.0) * d)))


def run_folds(
    series: Any, config: ExperimentConfig, derivatives: Any, funding: Any, book: Any
) -> dict[str, np.ndarray]:
    """Walk-forward purgado: media, cuantiles y el escalado trivial, OOF.

    El `k` del baseline se calibra **solo con el train** de cada fold. Sacarlo
    del test sería darle al baseline una ventaja que el modelo no tiene, y
    entonces ganarle no querría decir nada.
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

    out: dict[str, np.ndarray] = {"realized": np.full(n, np.nan), "mean": np.full(n, np.nan)}
    for q in QUANTILES:
        out[f"q{q:.2f}"] = np.full(n, np.nan)
        out[f"scaled{q:.2f}"] = np.full(n, np.nan)

    for split in splits:
        logger.info("fold {}: n_train={} n_test={}", split.name, split.n_train, split.n_test)
        tr, te = split.train_idx, split.test_idx

        mean_model = VolatilityModel(kind=config.vol_kind, seed=config.seed).fit(X, y_vol, tr)
        mean_te = mean_model.predict(X[te])
        out["mean"][te] = mean_te
        out["realized"][te] = y_vol[te]

        # Razón realizada/pronosticada EN EL TRAIN: su cuantil q es el k que
        # deja exactamente la excedencia nominal sobre datos ya vistos.
        mean_tr = mean_model.predict(X[tr])
        ratio_tr = y_vol[tr] / np.maximum(mean_tr, 1e-12)

        for q in QUANTILES:
            qm = VolatilityModel(kind="gbm", seed=config.seed, quantile=q).fit(X, y_vol, tr)
            out[f"q{q:.2f}"][te] = qm.predict(X[te])
            out[f"scaled{q:.2f}"][te] = float(np.quantile(ratio_tr, q)) * mean_te

    for q in QUANTILES:
        out[f"adaptive{q:.2f}"] = _adaptive_scale(
            out["realized"], out["mean"], q, horizon
        )
    return out


def _adaptive_scale(
    realized: np.ndarray, mean_pred: np.ndarray, q: float, horizon: int
) -> np.ndarray:
    """`k_t · sigma_media` con `k` corregido en línea, al estilo del ACI.

    Existe porque los dos estimadores estáticos —el GBM cuantílico y el
    `k·media` con `k` del train— **sub-cubren los dos** fuera de muestra. Que
    fallen igual y en la misma dirección dice que el problema no es el
    estimador sino la **deriva**: la cola del test es más pesada que la del
    train, y ninguna constante ajustada en el pasado puede seguir eso.

    El proyecto ya tiene la forma correcta de respuesta a la deriva —el ACI
    que lleva el cono conformal, que en la Fase 5 movió `alpha_t` de 0,05 a
    0,025 justo donde había sub-cobertura—, y lo que la sigma no tiene es un
    mecanismo equivalente. Esto lo prueba con la misma aritmética.

    **La retroalimentación llega con `horizon` barras de retraso**, igual que
    en vivo: el resultado de la barra `t` recién se conoce cuando su horizonte
    cierra. Actualizar con el dato de la propia barra sería el lookahead de la
    regla 5, y acá se vería como una calibración perfecta que en producción no
    existiría.
    """
    n = realized.size
    out = np.full(n, np.nan)
    gamma = 0.02
    log_k = 0.0
    target = 1.0 - q
    for t in range(n):
        if not np.isfinite(mean_pred[t]):
            continue
        out[t] = float(np.exp(log_k)) * mean_pred[t]
        s = t - horizon
        if s >= 0 and np.isfinite(out[s]) and np.isfinite(realized[s]):
            err = 1.0 if realized[s] > out[s] else 0.0
            log_k += gamma * (err - target)
    return out


def evaluate_quantile(
    realized: np.ndarray,
    model: np.ndarray,
    baseline: np.ndarray,
    mean_pred: np.ndarray,
    adaptive: np.ndarray,
    q: float,
    horizon: int,
    *,
    n_boot: int,
    seed: int,
) -> dict[str, Any]:
    """Los tres criterios más el déficit de cola, para un cuantil."""
    ok = (
        np.isfinite(realized)
        & np.isfinite(model)
        & np.isfinite(baseline)
        & np.isfinite(adaptive)
    )
    y, f, b, m = realized[ok], model[ok], baseline[ok], mean_pred[ok]
    a = adaptive[ok]
    nominal = 1.0 - q

    exc_model = y > f
    exc_base = y > b
    exc_adapt = y > a
    ci_lo, ci_hi = _proportion_ci(exc_model, horizon, n_boot=n_boot, seed=seed)
    cb_lo, cb_hi = _proportion_ci(exc_base, horizon, n_boot=n_boot, seed=seed)
    ca_lo, ca_hi = _proportion_ci(exc_adapt, horizon, n_boot=n_boot, seed=seed)

    # Déficit de cola: en las barras donde la MEDIA se quedó corta, cuánto
    # falta todavía. Es el número con consecuencia operativa.
    short = y > m
    deficit_mean = float(np.mean((y[short] - m[short]) / y[short])) if short.any() else 0.0
    still_short = y[short] > f[short]
    deficit_model = (
        float(np.mean((y[short][still_short] - f[short][still_short]) / y[short][still_short]))
        if still_short.any()
        else 0.0
    )

    calibrated = ci_lo <= nominal <= ci_hi
    close = abs(exc_model.mean() - nominal) * 100 <= CALIBRATION_TOLERANCE_PP
    pinball_model = pinball_loss(y, f, q)
    pinball_base = pinball_loss(y, b, q)

    return {
        "q": q,
        "nominal_exceedance": nominal,
        "n": int(y.size),
        "exceedance_model": float(exc_model.mean()),
        "exceedance_ci95": [ci_lo, ci_hi],
        "exceedance_baseline": float(exc_base.mean()),
        "exceedance_baseline_ci95": [cb_lo, cb_hi],
        "exceedance_adaptive": float(exc_adapt.mean()),
        "exceedance_adaptive_ci95": [ca_lo, ca_hi],
        "adaptive_calibrated": bool(
            ca_lo <= nominal <= ca_hi
            and abs(exc_adapt.mean() - nominal) * 100 <= CALIBRATION_TOLERANCE_PP
        ),
        "pinball_adaptive": pinball_loss(y, a, q),
        "ceiling_adaptive": float(np.max(a)),
        "calibrated": bool(calibrated and close),
        "pinball_model": pinball_model,
        "pinball_baseline": pinball_base,
        "beats_baseline_pinball": bool(pinball_model < pinball_base),
        "ceiling_model": float(np.max(f)),
        "ceiling_mean": float(np.max(m)),
        "ceiling_realized": float(np.max(y)),
        "bars_over_mean_ceiling": int((y > np.max(m)).sum()),
        "bars_over_model_ceiling": int((y > np.max(f)).sum()),
        "tail_deficit_mean": deficit_mean,
        "tail_deficit_model": deficit_model,
        "adopted": bool(calibrated and close and pinball_model < pinball_base),
    }


def _proportion_ci(
    hits: np.ndarray, block: int, *, n_boot: int, seed: int
) -> tuple[float, float]:
    """IC de una proporción por bloques móviles — misma convención del proyecto."""
    from bob.paper.forward_eval import coverage_interval

    return coverage_interval(hits, block, n_boot=n_boot, seed=seed)


def render(payload: dict[str, Any]) -> str:
    cfg = payload["config"]
    w = 76
    out = [
        "=" * w,
        f"TECHO DE LA SIGMA — {cfg['symbol']} {cfg['timeframe']}",
        f"{cfg['from']} .. {cfg['to']}",
        "¿la regresión cuantílica cubre la cola que la media no cubre?",
        "=" * w,
        "",
        f"features={cfg['features']}  vol_model={cfg['vol_model']}  "
        f"folds={cfg['folds']}  H={cfg['horizon']}",
        "",
    ]
    for r in payload["results"]:
        q = r["q"]
        out += [
            "-" * w,
            f"CUANTIL {q:.2f}  (excedencia nominal {100 * r['nominal_exceedance']:.0f}%)",
            "-" * w,
            f"  n={r['n']:,}",
            "",
            f"  {'':16s} {'excedencia':>12s} {'IC95%':>18s} {'pinball':>12s}",
            f"  {'cuantílico':16s} {100 * r['exceedance_model']:11.1f}% "
            f"  [{100 * r['exceedance_ci95'][0]:5.1f}, {100 * r['exceedance_ci95'][1]:5.1f}] "
            f"{r['pinball_model']:12.6f}",
            f"  {'k·media':16s} {100 * r['exceedance_baseline']:11.1f}% "
            f"  [{100 * r['exceedance_baseline_ci95'][0]:5.1f}, "
            f"{100 * r['exceedance_baseline_ci95'][1]:5.1f}] "
            f"{r['pinball_baseline']:12.6f}",
            f"  {'k(t)·media ACI':16s} {100 * r['exceedance_adaptive']:11.1f}% "
            f"  [{100 * r['exceedance_adaptive_ci95'][0]:5.1f}, "
            f"{100 * r['exceedance_adaptive_ci95'][1]:5.1f}] "
            f"{r['pinball_adaptive']:12.6f}"
            f"{'   <-- calibra' if r['adaptive_calibrated'] else ''}",
            "",
            f"  criterio 1 — calibra como cuantil: "
            f"{'PASA' if r['calibrated'] else 'NO PASA'}",
            f"  criterio 2/3 — pinball < baseline:  "
            f"{'PASA' if r['beats_baseline_pinball'] else 'NO PASA'}",
            "",
            "  El techo, que es el hallazgo que motivó esta medición:",
            f"    máximo que emite la media        {r['ceiling_mean']:.5f}",
            f"    máximo que emite el cuantílico   {r['ceiling_model']:.5f}"
            f"  ({r['ceiling_model'] / r['ceiling_mean']:.2f}× la media)",
            f"    máximo realizado                 {r['ceiling_realized']:.5f}",
            f"    barras por encima del techo de la media      "
            f"{r['bars_over_mean_ceiling']:,}",
            f"    barras por encima del techo del cuantílico   "
            f"{r['bars_over_model_ceiling']:,}",
            "",
            "  Déficit en las barras donde la media se quedó corta:",
            f"    con la media       {100 * r['tail_deficit_mean']:.1f}% del movimiento sin cubrir",
            f"    con el cuantílico  {100 * r['tail_deficit_model']:.1f}%",
            "",
            f"  VEREDICTO q={q:.2f}: "
            f"{'ADOPTABLE' if r['adopted'] else 'NO adoptable'}",
            "",
        ]
    out += [
        "=" * w,
        "Esta medición NO toca el filtro de régimen (usa el orden, ya validado)",
        "ni habilita el KPI 1. Adoptar la sigma cuantílica cambiaría",
        "model_version: no se hace a mitad de una corrida forward.",
        "=" * w,
    ]
    return "\n".join(out)


def main() -> None:
    enable_utf8_stdout()
    defaults = ExperimentConfig()
    parser = argparse.ArgumentParser(description="Techo de la sigma: media vs cuantil")
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--timeframe", default="15m")
    parser.add_argument("--features", default="price+deriv", choices=sorted(FEATURE_SETS))
    parser.add_argument("--vol-model", default=defaults.vol_kind, choices=["gbm", "xgb", "ridge"])
    parser.add_argument("--folds", type=int, default=defaults.n_splits)
    parser.add_argument("--horizon", type=int, default=BarrierConfig().horizon_bars)
    parser.add_argument("--until", default=None, metavar="YYYY-MM-DD[THH:MM]")
    parser.add_argument("--n-boot", type=int, default=2_000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--artifact", action="store_true")
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

    preds = run_folds(series, config, derivatives, funding, book)

    results = [
        evaluate_quantile(
            preds["realized"],
            preds[f"q{q:.2f}"],
            preds[f"scaled{q:.2f}"],
            preds["mean"],
            preds[f"adaptive{q:.2f}"],
            q,
            args.horizon,
            n_boot=args.n_boot,
            seed=args.seed,
        )
        for q in QUANTILES
    ]

    payload = {
        "config": {
            "symbol": args.symbol,
            "timeframe": args.timeframe,
            "features": args.features,
            "vol_model": args.vol_model,
            "folds": args.folds,
            "horizon": args.horizon,
            "seed": args.seed,
            "n_boot": args.n_boot,
            "from": datetime.fromtimestamp(
                int(series.open_time[0]) / 1000, UTC
            ).strftime("%Y-%m-%d %H:%M"),
            "to": datetime.fromtimestamp(
                int(series.open_time[-1]) / 1000, UTC
            ).strftime("%Y-%m-%d %H:%M"),
        },
        "results": results,
    }
    text = render(payload)
    print(text)

    if args.artifact:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        base = f"sigmatail-{args.symbol}-{args.timeframe}-{args.features}-{args.vol_model}-{stamp}"
        (ARTIFACT_DIR / f"{base}.txt").write_text(text, encoding="utf-8")
        (ARTIFACT_DIR / f"{base}.json").write_text(
            json.dumps(payload, indent=2, default=float), encoding="utf-8"
        )
        print(f"\nartefacto: artifacts/{base}.txt|.json")


if __name__ == "__main__":
    main()
