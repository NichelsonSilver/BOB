"""Runner del panel transversal diario — la capa de I/O de la Fase 10.

    uv run python -m bob.backtest.panel_runner
    uv run python -m bob.backtest.panel_runner --horizon 10 --no-cross-section
    uv run python -m bob.backtest.panel_runner --until 2026-09-21

Lee la DB, arma el panel, corre `models/panel_experiment` y congela el
resultado en `backend/artifacts/panel-*.txt|.json`.

`--until` no es un lujo: `load_panel` lee todo lo que hay, y la DB crece.
Reproducir un run exige **nombrar su última fecha**, que es la lección que la
Fase 4-bis dejó escrita al descubrir que el mismo comando, dos días seguidos,
entrenaba sobre muestras distintas sin que nada fallara.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from loguru import logger

from bob.data.panel import PanelData, load_panel, load_universe
from bob.models.panel_experiment import PanelConfig, PanelResult, run_panel_experiment
from bob.signals.panel_features import assemble_panel_features
from bob.signals.universe import UniverseRule, eligibility_mask
from bob.utils.console import enable_utf8_stdout

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent.parent
ARTIFACTS_DIR = _BACKEND_DIR / "artifacts"


def _end_time_ms(until: str | None) -> int | None:
    if not until:
        return None
    text = until.strip()
    fmt = "%Y-%m-%dT%H:%M" if "T" in text else "%Y-%m-%d"
    return int(datetime.strptime(text, fmt).replace(tzinfo=UTC).timestamp() * 1000)


def build_panel(
    timeframe: str = "1d",
    *,
    min_months: int = 6,
    until: str | None = None,
    exclude_delisted: bool = False,
) -> PanelData:
    """Carga el panel del universo **congelado**, nunca de `exchangeInfo` en vivo.

    `exclude_delisted=True` es la exploratoria que **mide** el sesgo de
    supervivencia en vez de asumirlo: reconstruye el universo como lo haría
    alguien que solo mira `exchangeInfo`. Si el resultado mejora al sacarlos,
    la mejora es el sesgo.
    """
    universe = load_universe(timeframe=timeframe)
    metas = universe.crypto_perps(min_months=min_months)
    if exclude_delisted:
        metas = [m for m in metas if not m.is_delisted]
        logger.warning(
            "EXCLUYENDO deslistados: universo sesgado a propósito ({} símbolos)",
            len(metas),
        )
    symbols = [m.symbol for m in metas]
    logger.info(
        "universo congelado el {} — {} perpetuos cripto",
        universe.discovered_at,
        len(symbols),
    )
    return load_panel(symbols, timeframe, end_time=_end_time_ms(until))


def render_panel_report(result: PanelResult, run_id: str) -> str:
    """Reporte legible. El veredicto sale del `PanelResult`, no de esta función."""
    cfg = result.config
    out: list[str] = []
    w = out.append

    def fmt_date(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")

    w("=" * 78)
    w(f"PANEL TRANSVERSAL DIARIO — {run_id}")
    w("=" * 78)
    w("")
    w("pre-registro    : docs/PREREGISTRO_PANEL_DIARIO.md")
    w(f"símbolos        : {result.n_symbols}")
    w(
        f"fechas          : {result.n_dates}  "
        f"({fmt_date(result.date_from)} .. {fmt_date(result.date_to)})"
    )
    w(f"filas evaluadas : {result.n_rows:,}")
    w(f"features        : {result.n_features}")
    w(f"horizonte       : {cfg.horizon} días")
    w(f"folds           : {', '.join(result.fold_names)}")
    w(f"costo del par   : {cfg.pair_cost * 100:.3f}%")
    w("")
    w("-" * 78)
    w("DISCRIMINACIÓN (criterio 1: AUC > 0.55 y BSS > 0)")
    w("-" * 78)
    w(f"  tasa base            : {result.base_rate * 100:.2f}%")
    w(f"  AUC                  : {result.auc:.4f}")
    w(f"  Brier                : {result.brier:.5f}")
    w(f"  Brier Skill Score    : {result.bss:+.5f}")
    w(f"  error de calibración : {result.calibration_error:.2f} pp")
    w(f"  -> {'PASA' if result.passes_discrimination else 'NO PASA'}")
    w("")
    w("-" * 78)
    w("CONTRA EL RANKING TRIVIAL (criterio 2: diferencia pareada de IC, IC95% > 0)")
    w("-" * 78)
    if result.model:
        lo, hi = result.intervals.get("ic", (float("nan"), float("nan")))
        w(f"  IC del modelo        : {result.model.mean_ic:+.5f}  IC95% [{lo:+.5f}, {hi:+.5f}]")
    for name, ev in result.baselines.items():
        w(f"  IC {name:<18}: {ev.mean_ic:+.5f}")
    w("")
    for name, (mean, lo, hi) in result.paired_ic.items():
        mark = "excluye 0" if lo > 0 else "CONTIENE 0"
        w(f"  modelo − {name:<18}: {mean:+.5f}  IC95% [{lo:+.5f}, {hi:+.5f}]  {mark}")
    w(f"  mejor baseline: {result.best_baseline}")
    w(f"  -> {'PASA' if result.passes_vs_baseline else 'NO PASA'}")
    w("")
    w("-" * 78)
    w("ECONOMÍA (criterio 3: spread neto del par decil > 0, IC95% excluye 0)")
    w("-" * 78)
    if result.model:
        g_lo, g_hi = result.intervals.get("spread_gross", (float("nan"), float("nan")))
        n_lo, n_hi = result.intervals.get("spread_net", (float("nan"), float("nan")))
        w(f"  spread bruto         : {result.model.mean_spread_gross * 100:+.4f}%  "
          f"IC95% [{g_lo * 100:+.4f}%, {g_hi * 100:+.4f}%]")
        w(f"  spread NETO          : {result.model.mean_spread_net(cfg.pair_cost) * 100:+.4f}%  "
          f"IC95% [{n_lo * 100:+.4f}%, {n_hi * 100:+.4f}%]")
    if result.oracle:
        w(f"  spread del oráculo   : {result.oracle.mean_spread_gross * 100:+.4f}%  (techo)")
        w(f"  fracción capturada   : {result.oracle_capture * 100:.2f}% del techo")
    w(f"  -> {'PASA' if result.passes_economics else 'NO PASA'}")
    w("")
    w("-" * 78)
    w("SE REPORTA SIEMPRE")
    w("-" * 78)
    w(f"  rotación por rebalanceo    : {result.turnover * 100:.1f}% de la cartera")
    w(f"  retornos exactamente cero  : {result.zero_return_frac * 100:.3f}% de las filas")
    w(f"  filas con símbolo deslistado dentro del horizonte: {result.delisted_rows:,}")
    w("")
    w("=" * 78)
    verdict = "VIABLE" if result.viable else "NO VIABLE"
    w(f"VEREDICTO: {verdict}  (los tres criterios, no dos)")
    w(f"  discriminación {result.passes_discrimination} | "
      f"vs. trivial {result.passes_vs_baseline} | economía {result.passes_economics}")
    w("=" * 78)
    return "\n".join(out)


def write_panel_artifacts(result: PanelResult, run_id: str) -> tuple[Path, Path]:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    txt = ARTIFACTS_DIR / f"{run_id}.txt"
    js = ARTIFACTS_DIR / f"{run_id}.json"
    txt.write_text(render_panel_report(result, run_id), encoding="utf-8")
    js.write_text(json.dumps(result.to_dict(), indent=2, default=str), encoding="utf-8")
    return txt, js


def main() -> None:
    enable_utf8_stdout()
    defaults = PanelConfig()
    rule_defaults = UniverseRule()

    p = argparse.ArgumentParser(description="Experimento del panel transversal diario")
    p.add_argument("--timeframe", default="1d")
    p.add_argument("--horizon", type=int, default=defaults.horizon)
    p.add_argument("--folds", type=int, default=defaults.n_splits)
    p.add_argument("--quantile", type=float, default=defaults.portfolio_quantile)
    p.add_argument(
        "--model", default=defaults.model_kind, choices=["gbm", "logistic", "xgb"]
    )
    p.add_argument(
        "--target-cut", default=defaults.target_cut, choices=["median", "tercile"]
    )
    p.add_argument(
        "--uniqueness-weights",
        action="store_true",
        help="pondera cada fila por unicidad del label (exploratoria declarada)",
    )
    p.add_argument(
        "--exclude-delisted",
        action="store_true",
        help="control del sesgo de supervivencia: saca los deslistados del universo",
    )
    p.add_argument("--min-history", type=int, default=rule_defaults.min_history_bars)
    p.add_argument("--min-volume", type=float, default=rule_defaults.min_dollar_volume)
    p.add_argument(
        "--no-cross-section",
        action="store_true",
        help="baseline secundario: el mismo modelo sin el bloque transversal",
    )
    p.add_argument(
        "--placebo",
        action="store_true",
        help="control: permuta el resultado entre símbolos de cada fecha (IC debe dar ~0)",
    )
    p.add_argument("--until", default=None, help="última fecha (YYYY-MM-DD) — reproducibilidad")
    p.add_argument("--min-months", type=int, default=6)
    args = p.parse_args()

    t0 = time.perf_counter()
    panel = build_panel(
        args.timeframe,
        min_months=args.min_months,
        until=args.until,
        exclude_delisted=args.exclude_delisted,
    )
    logger.info(
        "panel cargado: {} símbolos x {} fechas ({:,} symbol-days con dato)",
        panel.n_symbols,
        panel.n_dates,
        int(panel.observed.sum()),
    )

    rule = UniverseRule(
        min_history_bars=args.min_history,
        min_dollar_volume=args.min_volume,
        volume_lookback=rule_defaults.volume_lookback,
    )
    eligible = eligibility_mask(panel.close, panel.quote_volume, rule)
    logger.info(
        "elegibles: {:,} symbol-days, ancho mediano de la sección {:.0f}",
        int(eligible.sum()),
        float(np.median(eligible.sum(axis=0))),
    )

    features = assemble_panel_features(
        panel, eligible, add_cross_sectional=not args.no_cross_section
    )
    logger.info("features: {} columnas", len(features.names))

    cfg = PanelConfig(
        horizon=args.horizon,
        n_splits=args.folds,
        portfolio_quantile=args.quantile,
        model_kind=args.model,
        use_cross_section=not args.no_cross_section,
        target_cut=args.target_cut,
        uniqueness_weights=args.uniqueness_weights,
        placebo=args.placebo,
    )
    result = run_panel_experiment(features, panel.open, panel.close, eligible, cfg)

    suffix = "xs" if cfg.use_cross_section else "noxs"
    if cfg.target_cut != "median":
        suffix += f"-{cfg.target_cut}"
    if cfg.uniqueness_weights:
        suffix += "-uniq"
    if args.exclude_delisted:
        suffix += "-nodelist"
    if cfg.model_kind != "gbm":
        suffix += f"-{cfg.model_kind}"
    if args.min_volume != rule_defaults.min_dollar_volume:
        suffix += f"-vol{int(args.min_volume / 1e6)}M"
    if abs(cfg.portfolio_quantile - defaults.portfolio_quantile) > 1e-9:
        suffix += f"-q{int(cfg.portfolio_quantile * 100)}"
    if cfg.placebo:
        suffix += "-PLACEBO"
    run_id = (
        f"panel-{args.timeframe}-H{cfg.horizon}-{suffix}-"
        f"{datetime.now(tz=UTC).strftime('%Y%m%d%H%M%S')}"
    )
    txt, js = write_panel_artifacts(result, run_id)
    print(render_panel_report(result, run_id))
    print(f"\nartefactos: {txt}\n            {js}")
    print(f"runtime: {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    main()
