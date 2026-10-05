"""Corre la medición pre-registrada de las reglas técnicas. Capa de I/O.

El criterio está fijado en `docs/PREREGISTRO_TECNICO.md`, commiteado junto
con este archivo y ANTES de correrlo. Acá no se decide nada: se carga la
serie, se recorren las reglas con la configuración primaria y se evalúan las
cuatro pruebas confirmatorias, sus placebos y la grilla exploratoria.

    uv run python -m bob.backtest.technical_runner --until 2026-09-14T00:45 --artifact
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from bob.backtest.runner import _end_time_ms
from bob.data.store import OHLCVSeries, load_series
from bob.models.labeling import BarrierConfig
from bob.models.technical_rules import (
    DEFAULT_COST_PCT,
    ORDER_BLOCK,
    SWEEP,
    RuleConfig,
    Trade,
    TradeStats,
    order_block_trades,
    placebo_trades,
    sweep_trades,
    trade_stats,
)
from bob.signals.technical import TechnicalParams, TechnicalScan, scan
from bob.utils.console import enable_utf8_stdout

ARTIFACT_DIR = Path(__file__).resolve().parents[3] / "artifacts"
PREREGISTRATION = "docs/PREREGISTRO_TECNICO.md"

#: §4 del pre-registro. NO se exponen como flags: barrerlos desde el CLI y
#: reportar el mejor es exactamente lo que el pre-registro impide.
CONFIRMATORY_TIMEFRAMES = ("15m", "1h")
RULES = (SWEEP, ORDER_BLOCK)
FAMILY_ALPHA = 0.05
N_CONFIRMATORY = len(CONFIRMATORY_TIMEFRAMES) * len(RULES)
CONFIRMATORY_LEVEL = 1.0 - FAMILY_ALPHA / N_CONFIRMATORY  # Bonferroni: 98,75%
MIN_TRADES = 150
N_BOOT = 5_000
SEED = 0

_BASE_MS = 900_000
_TF_FACTOR = {"15m": 1, "1h": 4, "4h": 16}


# --------------------------------------------------------------------------- #
# Datos
# --------------------------------------------------------------------------- #
def resample(series: OHLCVSeries, timeframe: str) -> dict[str, np.ndarray]:
    """Agrega 15m a `timeframe` con horas completas y alineadas a UTC.

    Un bloque con alguna vela faltante se descarta entero: una vela de 1h
    armada con tres cuartos de hora no existió.
    """
    f = _TF_FACTOR[timeframe]
    if f == 1:
        return {
            "open_time": series.open_time,
            "open": series.open,
            "high": series.high,
            "low": series.low,
            "close": series.close,
        }
    g = series.open_time // (f * _BASE_MS)
    _, start, counts = np.unique(g, return_index=True, return_counts=True)
    ok = counts == f
    # reduceat sobre los inicios; los bloques incompletos se calculan y se tiran.
    hi = np.maximum.reduceat(series.high, start)
    lo = np.minimum.reduceat(series.low, start)
    st = start[ok]
    # Un bloque completo tiene f velas contiguas: la última es st + f - 1.
    return {
        "open_time": series.open_time[st],
        "open": series.open[st],
        "high": hi[ok],
        "low": lo[ok],
        "close": series.close[st + f - 1],
    }


def rule_config(timeframe: str, **overrides: Any) -> RuleConfig:
    tf_ms = _TF_FACTOR[timeframe] * _BASE_MS
    funding = BarrierConfig().funding_per_bar(tf_ms)
    return replace(RuleConfig(funding_pct_per_bar=funding), **overrides)


def trades_for(
    rule: str, sc: TechnicalScan, bars: dict[str, np.ndarray], cfg: RuleConfig
) -> list[Trade]:
    o, h, lo, c = bars["open"], bars["high"], bars["low"], bars["close"]
    if rule == SWEEP:
        return sweep_trades(sc.sweeps, o, h, lo, c, cfg)
    return order_block_trades(sc.ob_touches, o, h, lo, c, cfg)


def by_year(trades: list[Trade], open_time: np.ndarray) -> dict[str, dict[str, float]]:
    out: dict[str, list[float]] = {}
    for t in trades:
        y = datetime.fromtimestamp(int(open_time[t.signal_idx]) / 1000, tz=UTC).year
        out.setdefault(str(y), []).append(t.net_r)
    return {y: {"n": len(v), "mean_r": float(np.mean(v))} for y, v in sorted(out.items())}


# --------------------------------------------------------------------------- #
# Corrida
# --------------------------------------------------------------------------- #
def _verdict(st: TradeStats) -> str:
    if st.n < MIN_TRADES:
        return "SIN POTENCIA"
    return "PASA" if st.passes else "NO PASA"


def run(until: str) -> dict[str, Any]:
    series = load_series("ETHUSDT", "15m", end_time=_end_time_ms(until))
    params = TechnicalParams()
    result: dict[str, Any] = {
        "symbol": "ETHUSDT",
        "until": until,
        "n_bars_15m": len(series),
        "first_open_time": int(series.open_time[0]),
        "last_open_time": int(series.open_time[-1]),
        "params": asdict(params),
        "rule_config": asdict(RuleConfig()),
        "cost_pct": DEFAULT_COST_PCT,
        "confirmatory_level": CONFIRMATORY_LEVEL,
        "min_trades": MIN_TRADES,
        "preregistration": PREREGISTRATION,
        "confirmatory": [],
        "exploratory": [],
    }

    cache: dict[str, tuple[dict[str, np.ndarray], TechnicalScan]] = {}
    for tf in ("15m", "1h", "4h"):
        bars = resample(series, tf)
        cache[tf] = (bars, scan(bars["open"], bars["high"], bars["low"], bars["close"], params))

    # --- confirmatorias + placebo ---------------------------------------- #
    for tf in CONFIRMATORY_TIMEFRAMES:
        bars, sc = cache[tf]
        n = bars["close"].size
        cfg = rule_config(tf)
        for rule in RULES:
            trades = trades_for(rule, sc, bars, cfg)
            st = trade_stats(trades, n, cfg, level=CONFIRMATORY_LEVEL, n_boot=N_BOOT, seed=SEED)
            pl = placebo_trades(
                trades,
                bars["open"],
                bars["high"],
                bars["low"],
                bars["close"],
                sc.atr,
                cfg,
                seed=SEED,
            )
            pst = trade_stats(pl, n, cfg, level=CONFIRMATORY_LEVEL, n_boot=N_BOOT, seed=SEED)
            result["confirmatory"].append(
                {
                    "rule": rule,
                    "timeframe": tf,
                    "n_bars": n,
                    "n_signals": len(sc.sweeps) if rule == SWEEP else len(sc.ob_touches),
                    "stats": asdict(st),
                    "verdict": _verdict(st),
                    "placebo": asdict(pst),
                    "placebo_passes": pst.passes,
                    "by_year": by_year(trades, bars["open_time"]),
                }
            )

    # --- exploratorias (§6): se reportan, no deciden --------------------- #
    variants: list[tuple[str, str, str, dict[str, Any]]] = []
    for tf in ("15m", "1h"):
        for rule in RULES:
            variants += [
                (rule, tf, "R=1", {"r_multiple": 1.0}),
                (rule, tf, "R=3", {"r_multiple": 3.0}),
                (rule, tf, "a favor de EMA200", {"trend_ema": 200}),
                (rule, tf, "RSI<=40 / >=60", {"rsi_gate": 40.0}),
                (rule, tf, "sin piso de riesgo", {"min_risk_pct": 0.0}),
            ]
        variants.append((SWEEP, tf, "máximos/mínimos iguales (>=2 niveles)", {"min_levels": 2}))
        maker = BarrierConfig().slippage_pct / 2 + 0.0002 + 0.0005
        variants.append((ORDER_BLOCK, tf, "entrada maker", {"cost_pct": maker}))
    for rule in RULES:
        variants.append((rule, "4h", "4h", {}))

    for rule, tf, label, ov in variants:
        bars, sc = cache[tf]
        cfg = rule_config(tf, **ov)
        trades = trades_for(rule, sc, bars, cfg)
        st = trade_stats(trades, bars["close"].size, cfg, level=0.95, n_boot=2_000, seed=SEED)
        result["exploratory"].append(
            {
                "rule": rule,
                "timeframe": tf,
                "variant": label,
                "overrides": ov,
                "stats": asdict(st),
            }
        )
    return result


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #
def _fmt_stats(st: dict[str, Any]) -> str:
    return (
        f"n={st['n']:>5}  E[R]={st['mean_r']:+.3f}  IC[{st['ci_low']:+.3f}, {st['ci_high']:+.3f}]  "
        f"win={st['win_rate']:.1%} (eq. sin costo {st['breakeven_win_rate']:.1%})  "
        f"PF={st['profit_factor']:.2f}  neto/trade={st['mean_net_pct']:+.3%}  "
        f"riesgo med={st['median_risk_pct']:.2%}"
    )


def render(result: dict[str, Any]) -> str:
    lines = [
        "REGLAS TÉCNICAS — medición pre-registrada",
        f"pre-registro: {result['preregistration']}",
        f"ETHUSDT 15m, {result['n_bars_15m']} velas hasta {result['until']} UTC",
        f"costo round-trip {result['cost_pct']:.2%} + funding; "
        f"R={result['rule_config']['r_multiple']}, H={result['rule_config']['horizon']} barras",
        "",
        f"CONFIRMATORIAS ({N_CONFIRMATORY}, IC {result['confirmatory_level']:.2%} Bonferroni, "
        f"bloques de H barras; pasa si el IC excluye 0 por arriba)",
    ]
    any_placebo = False
    for row in result["confirmatory"]:
        lines.append(
            f"  [{row['verdict']:>12}] {row['rule']:<12} {row['timeframe']:<4} "
            + _fmt_stats(row["stats"])
        )
        pst = row["placebo"]
        lines.append(
            f"  {'placebo':>14} {'':<17} n={pst['n']:>5}  E[R]={pst['mean_r']:+.3f}  "
            f"IC[{pst['ci_low']:+.3f}, {pst['ci_high']:+.3f}]"
            + ("  ⚠ EL PLACEBO PASA" if row["placebo_passes"] else "")
        )
        st = row["stats"]
        lines.append(
            f"  {'':>14} salidas: objetivo {st['exit_mix'].get('target', 0):.0%} / "
            f"stop {st['exit_mix'].get('stop', 0):.0%} / tiempo {st['exit_mix'].get('time', 0):.0%}"
            f"   peor racha {st['worst_streak']}   máx. drawdown {st['max_drawdown_r']:.1f} R"
        )
        yrs = "  ".join(f"{y}: {v['mean_r']:+.3f} (n={v['n']})" for y, v in row["by_year"].items())
        lines.append(f"  {'por año':>14} {yrs}")
        any_placebo |= row["placebo_passes"]
    if any_placebo:
        lines.append("  ⚠ Un placebo pasó: la medición queda INVÁLIDA (§5 del pre-registro).")
    lines += ["", "EXPLORATORIAS (IC 95%, no deciden nada — §6)"]
    for row in result["exploratory"]:
        lines.append(
            f"  {row['rule']:<12} {row['timeframe']:<4} {row['variant']:<38} "
            + _fmt_stats(row["stats"])
        )
    return "\n".join(lines)


def main() -> None:
    enable_utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--until", required=True, help="última barra, YYYY-MM-DD[THH:MM] UTC")
    ap.add_argument("--artifact", action="store_true", help="congela .txt y .json en artifacts/")
    args = ap.parse_args()
    result = run(args.until)
    text = render(result)
    print(text)
    if args.artifact:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        base = ARTIFACT_DIR / f"technical-ETHUSDT-{stamp}"
        base.with_suffix(".txt").write_text(text + "\n", encoding="utf-8")
        base.with_suffix(".json").write_text(
            json.dumps(result, indent=2, default=float), encoding="utf-8"
        )
        print(f"\nartefacto: {base}.txt|.json")


if __name__ == "__main__":
    main()
