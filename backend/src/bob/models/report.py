"""Renderizado de resultados a texto — PURO, sin I/O.

El reporte está escrito para que un lector escéptico pueda desmontarlo: cada
número del modelo aparece al lado del baseline que tenía que batir y del
p-value que dice si la diferencia es distinguible de la suerte. Si el modelo
no aporta, el reporte lo dice con todas las letras en vez de esconderlo
entre métricas favorables.
"""

from __future__ import annotations

import numpy as np

from bob.models.experiment import (
    GATE_MAX_CALIBRATION_ERROR_PP,
    GATE_MIN_AUC,
    GATE_MIN_BSS,
    ExperimentResult,
    direction_discriminates,
)
from bob.models.metrics import ProbabilityMetrics

LINE = "=" * 78

#: Nombre legible del estimador de volatilidad. El reporte es lo que se cita
#: fuera del repo, así que la fila del target 2 tiene que decir qué modelo
#: produjo ese RMSE: una tabla que dice "GBM" mientras corrió XGBoost es una
#: cifra no verificable, que es justo lo que este proyecto no publica.
VOL_LABELS: dict[str, str] = {
    "gbm": "GBM sklearn",
    "xgb": "XGBoost",
    "ridge": "Ridge",
}


def _fmt(value: float, spec: str = ".4f", nan: str = "n/d") -> str:
    return nan if value is None or not np.isfinite(value) else format(value, spec)


def _reliability_table(m: ProbabilityMetrics, min_n: int = 20) -> list[str]:
    rows = [
        f"  {'bucket':>12} {'n':>7} {'predicho':>10} {'observado':>11} {'error':>9}",
        f"  {'-' * 12} {'-' * 7} {'-' * 10} {'-' * 11} {'-' * 9}",
    ]
    for b in m.buckets:
        flag = "" if b.n >= min_n else "  (n bajo)"
        rows.append(
            f"  {b.lower:>5.0%}-{b.upper:<5.0%} {b.n:>7,} {b.mean_predicted:>10.1%} "
            f"{b.observed_rate:>11.1%} {b.error_pp:>7.1f}pp{flag}"
        )
    return rows


def render_report(result: ExperimentResult) -> str:
    """Reporte completo en texto plano."""
    cfg = result.config
    out: list[str] = []
    add = out.append

    add(LINE)
    add("  BOB — EXPERIMENTO DE FORECASTING WALK-FORWARD")
    add(LINE)
    add(f"  símbolo         : {result.symbol} {result.timeframe}")
    add(f"  periodo         : {result.date_from} .. {result.date_to}  ({result.n_bars:,} velas)")
    add(f"  features        : {result.n_features}")
    add(
        f"  setup           : TP {cfg.barrier.tp_mult}σ / SL {cfg.barrier.sl_mult}σ / "
        f"H {cfg.barrier.horizon_bars} barras"
    )
    add(
        f"  costos          : {cfg.barrier.cost_pct:.4%} round-trip "
        f"(fees+slippage) + funding {cfg.barrier.funding_pct_per_8h:.4%}/8h"
    )
    add(
        f"  validación      : walk-forward purgado, {cfg.n_splits} folds, "
        f"embargo {cfg.embargo_frac:.1%}"
    )
    add(f"  modelo dir.     : {cfg.model_kind} + calibración isotónica OOF")
    add(f"  modelo vol.     : {VOL_LABELS.get(cfg.vol_kind, cfg.vol_kind)}")
    add(f"  runtime         : {result.runtime_s:.1f}s")
    add("")

    # ------------------------------------------------------------------ #
    add(LINE)
    add("  TARGET 1 — P(TP antes que SL)   [KPI 1: Seguridad]")
    add(LINE)
    for direction, dr in result.directions.items():
        m, u, b = dr.metrics_model, dr.metrics_uncalibrated, dr.metrics_baseline
        add("")
        add(f"  ── {direction.upper()} ── ({dr.n_samples:,} predicciones out-of-sample)")
        add("")
        mix = dr.resolution_mix
        add(
            f"  Resolución de los setups: TP {mix['tp']:.1%} | SL {mix['sl']:.1%} | "
            f"vertical {mix['vertical']:.1%}"
        )
        add(
            f"  Muestras efectivas tras ponderar por unicidad: {dr.effective_n:,.0f} "
            f"(de {int(mix['n']):,} labels — se solapan)"
        )
        add(
            f"  Probabilidad de equilibrio (EV=0): {dr.breakeven_prob:.1%}   "
            f"tasa base: {m.base_rate:.1%}   "
            f"→ el modelo debe levantar {(dr.breakeven_prob - m.base_rate) * 100:+.1f}pp"
        )
        add("")
        add(f"  {'métrica':<26} {'modelo':>12} {'sin calibrar':>14} {'baseline':>12}")
        add(f"  {'-' * 26} {'-' * 12} {'-' * 14} {'-' * 12}")
        add(
            f"  {'Brier (menor mejor)':<26} {_fmt(m.brier):>12} "
            f"{_fmt(u.brier):>14} {_fmt(b.brier):>12}"
        )
        add(
            f"  {'Brier skill score':<26} {_fmt(m.brier_skill_score, '+.4f'):>12} "
            f"{_fmt(u.brier_skill_score, '+.4f'):>14} {_fmt(b.brier_skill_score, '+.4f'):>12}"
        )
        add(
            f"  {'Log loss':<26} {_fmt(m.log_loss):>12} "
            f"{_fmt(u.log_loss):>14} {_fmt(b.log_loss):>12}"
        )
        add(f"  {'AUC':<26} {_fmt(m.auc, '.3f'):>12} {_fmt(u.auc, '.3f'):>14} {'0.500':>12}")
        add(
            f"  {'Error calibración':<26} {_fmt(m.mean_calibration_error_pp, '.1f') + 'pp':>12} "
            f"{_fmt(u.mean_calibration_error_pp, '.1f') + 'pp':>14} "
            f"{_fmt(b.mean_calibration_error_pp, '.1f') + 'pp':>12}"
        )
        add(f"  {'Tasa base observada':<26} {_fmt(m.base_rate, '.1%'):>12}")
        add("")
        add("  Curva de fiabilidad (lo prometido vs lo cumplido):")
        out.extend(_reliability_table(m))
        add("")
        add(f"  Diebold-Mariano vs baseline: {dr.dm_vs_baseline.verdict()}")
        add("")
        t = dr.trading
        add(
            f"  Consecuencia operativa con umbral {t['threshold']:.0%} "
            f"(equilibrio en {t['breakeven_prob']:.0%}, retornos NETOS):"
        )
        if t["n_signals"] == 0:
            add("    ninguna señal supera el umbral — el modelo no encuentra setups de esa calidad")
        else:
            add(
                f"    señales={int(t['n_signals']):,} ({t['signal_rate']:.1%} de las barras)   "
                f"win rate={t['win_rate']:.1%}"
            )
            add(
                f"    expectancy={t['expectancy_pct']:+.4f}%/trade   "
                f"profit factor={_fmt(t['profit_factor'], '.2f')}"
            )
            add(
                f"    retorno acumulado={t['total_return_pct']:+.2f}%   "
                f"max drawdown={t['max_drawdown_pct']:.2f}%"
            )
        add("")

    # ------------------------------------------------------------------ #
    add(LINE)
    add("  TARGET 2 — Volatilidad realizada futura")
    add(LINE)
    v = result.volatility
    add(f"  {v.n_samples:,} predicciones out-of-sample")
    add("")
    add(f"  {'modelo':<22} {'RMSE':>10} {'R² vs media':>13} {'R² vs EWMA':>12} {'QLIKE':>10}")
    add(f"  {'-' * 22} {'-' * 10} {'-' * 13} {'-' * 12} {'-' * 10}")
    for name, reg in (
        (f"{VOL_LABELS.get(cfg.vol_kind, cfg.vol_kind)} (features)", v.model),
        ("EWMA RiskMetrics", v.ewma),
        ("GARCH(1,1)", v.garch),
        ("HAR-RV", v.har),
    ):
        add(
            f"  {name:<22} {_fmt(reg.rmse, '.5f'):>10} {_fmt(reg.r2, '+.3f'):>13} "
            f"{_fmt(reg.r2_vs_baseline, '+.3f'):>12} {_fmt(reg.qlike, '.4f'):>10}"
        )
    add("")
    add(
        f"  Mincer-Zarnowitz del modelo: alpha={_fmt(v.model.mincer_zarnowitz_alpha, '+.5f')} "
        f"(0 = insesgado), beta={_fmt(v.model.mincer_zarnowitz_beta, '.3f')} (1 = eficiente)"
    )
    add(f"  DM vs EWMA  : {v.dm_vs_ewma.verdict()}")
    add(f"  DM vs HAR-RV: {v.dm_vs_har.verdict()}")
    add("")

    # ------------------------------------------------------------------ #
    add(LINE)
    add("  TARGET 3 — Cono de precio (intervalos conformales)")
    add(LINE)
    add(
        f"  {'nominal':>9} {'método':<14} {'cobertura':>11} {'desvío':>9} "
        f"{'ancho medio':>13} {'Winkler':>10}"
    )
    add(f"  {'-' * 9} {'-' * 14} {'-' * 11} {'-' * 9} {'-' * 13} {'-' * 10}")
    for alpha in sorted(result.intervals.conformal):
        c = result.intervals.conformal[alpha]
        g = result.intervals.gaussian[alpha]
        for label, iv in (("CQR + ACI", c), ("gaussiano ±zσ", g)):
            add(
                f"  {iv.nominal_coverage:>8.0%} {label:<14} {iv.empirical_coverage:>10.1%} "
                f"{iv.coverage_gap_pp:>+8.1f}pp {iv.mean_width:>13.5f} "
                f"{_fmt(iv.winkler_score, '.5f'):>10}"
            )
    add("")

    # ------------------------------------------------------------------ #
    add(LINE)
    add("  IMPORTANCIA DE FEATURES (permutación sobre test, Δ Brier)")
    add(LINE)
    add("  Por familia:")
    for fam, score in sorted(result.family_importance.items(), key=lambda kv: kv[1], reverse=True):
        bar = "█" * max(0, min(40, int(score * 4000)))
        add(f"    {fam:<18} {score:>+9.5f}  {bar}")
    add("")
    add("  Top 15 individuales:")
    for name, score in result.importance[:15]:
        add(f"    {name:<28} {score:>+9.5f}")
    add("")

    # ------------------------------------------------------------------ #
    add(LINE)
    add("  GATE DE LA FASE 4")
    add(LINE)
    passed = result.gate_passed()
    discrimina = result.discriminates()

    add(f"  Criterio 1 — CALIBRACIÓN (umbral {GATE_MAX_CALIBRATION_ERROR_PP:.0f}pp por bucket)")
    for direction, dr in result.directions.items():
        err = dr.metrics_model.mean_calibration_error_pp
        ok = bool(np.isfinite(err) and err < GATE_MAX_CALIBRATION_ERROR_PP)
        mark = "PASA" if ok else "NO PASA"
        add(f"    {direction:<8} error de calibración = {_fmt(err, '.1f')}pp → {mark}")
    add("")
    add(f"  Criterio 2 — DISCRIMINACIÓN (AUC ≥ {GATE_MIN_AUC:.2f} y BSS > {GATE_MIN_BSS:.0f})")
    for direction, dr in result.directions.items():
        m = dr.metrics_model
        mark = "PASA" if direction_discriminates(m) else "NO PASA"
        add(
            f"    {direction:<8} AUC = {_fmt(m.auc, '.3f')}  "
            f"BSS = {_fmt(m.brier_skill_score, '+.4f')} → {mark}"
        )
    add("")
    add("  Por qué hacen falta los dos:")
    add("    Un modelo que predice SIEMPRE la tasa base está perfectamente")
    add("    calibrado por construcción y es inútil — no distingue nada. La")
    add("    calibración dice 'cuando digo 70%, acierto 70%'; la discriminación")
    add("    dice 'sé cuáles son los casos de 70%'. Pasar solo la primera no")
    add("    habilita operar.")
    add("")

    if passed and discrimina:
        add("  ✓ HABILITADO para señales en vivo + paper tracking (Fase 5).")
        add("    El KPI se sigue mostrando con su precisión histórica al lado.")
    elif passed and not discrimina:
        add("  ✗ NO habilitado: calibra pero NO discrimina.")
        add("    El modelo reproduce la tasa base sin distinguir setups. Es el")
        add("    resultado esperable si el mercado es eficiente en este")
        add("    horizonte: la dirección del precio no es predecible con estas")
        add("    features. NO es un bug — es el hallazgo.")
        add("    El dashboard muestra el KPI en gris, etiquetado 'experimental'.")
    else:
        add("  ✗ NO habilitado para señales en vivo.")
        add("    El dashboard debe mostrar el KPI en gris, etiquetado 'experimental'.")
        add("    Iterar en features/modelo antes de avanzar (regla: no saltar el gate).")
    add("")
    add("  Nota: el target de volatilidad (TARGET 2) se evalúa aparte y puede")
    add("  ser útil aunque el de dirección no lo sea — de hecho es el orden")
    add("  esperado. Dimensionar TP/SL con una volatilidad bien pronosticada")
    add("  ya es valor operativo, sin necesidad de predecir la dirección.")
    add(LINE)

    return "\n".join(out)


def render_summary(result: ExperimentResult) -> str:
    """Una línea por dirección — para logs y notificaciones."""
    parts = []
    for direction, dr in result.directions.items():
        m = dr.metrics_model
        parts.append(
            f"{direction}: Brier={m.brier:.4f} AUC={m.auc:.3f} "
            f"calib={m.mean_calibration_error_pp:.1f}pp"
        )
    return f"{result.symbol} {result.timeframe} | " + " | ".join(parts)
