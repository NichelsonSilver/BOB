"""El piso económico del AUC: ¿cuántas barras superarían la probabilidad de equilibrio?

Pasar el gate no es lo mismo que poder operar. El gate exige AUC > 0.55; el
mercado exige que la probabilidad CALIBRADA supere la de equilibrio
(`P_equilibrio = (SL + costo) / (TP + SL)`, §2.6 de docs/PROBABILITY_MODEL.md).
Son dos listones distintos y el segundo es bastante más alto.

Este script cuantifica la brecha bajo un modelo binormal de varianza igual
—la idealización estándar para razonar sobre AUC— usando la tasa base y la
probabilidad de equilibrio REALES del artefacto del gate, no valores
inventados, y contrasta la predicción contra los buckets del run.

    uv run python scripts/economic_floor.py
    uv run python scripts/economic_floor.py --artifact artifacts/<run>.json

Es de SOLO LECTURA: no toca la DB ni escribe artefactos.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from scipy.stats import norm

from bob.utils.console import enable_utf8_stdout

# Umbral del gate y su entorno: es donde se decide si el KPI se enciende.
AUC_GRID = (0.52, 0.55, 0.58, 0.60, 0.63, 0.65, 0.70)
GATE_AUC = 0.55

BARS_PER_YEAR = {"1m": 525_600, "5m": 105_120, "15m": 35_040, "1h": 8_760}


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def auc_to_mu(auc: float) -> float:
    """Separación de medias del binormal de varianza igual.

    Con s|y=1 ~ N(mu, 1) y s|y=0 ~ N(0, 1) se tiene AUC = Phi(mu / sqrt(2)).
    """
    return float(math.sqrt(2.0) * norm.ppf(auc))


def fraction_over_breakeven(auc: float, base_rate: float, breakeven: float) -> tuple[float, float]:
    """Fracción de barras cuya probabilidad calibrada supera el equilibrio.

    Bajo el binormal la probabilidad calibrada es monótona en el score:

        logit p(s) = logit(pi) + mu*s - mu^2/2

    así que "p > p*" es un corte `s > c` y la fracción sale de la mixtura.
    Devuelve (fracción de barras, win rate esperado dentro de esa selección).
    """
    mu = auc_to_mu(auc)
    if mu <= 0.0:
        return 0.0, float("nan")

    cut = (_logit(breakeven) - _logit(base_rate)) / mu + mu / 2.0
    tail_pos = norm.sf(cut - mu)  # P(s > c | y = 1)
    tail_neg = norm.sf(cut)  # P(s > c | y = 0)

    frac = base_rate * tail_pos + (1.0 - base_rate) * tail_neg
    win_rate = (base_rate * tail_pos / frac) if frac > 0.0 else float("nan")
    return frac, win_rate


def _print_control(
    block: dict[str, Any],
    base_rate: float,
    breakeven: float,
    auc_real: float,
    n_real: int,
) -> None:
    """Contrasta la predicción del binormal contra los buckets reales del run.

    El binormal supone colas gaussianas; el modelo real las tiene más gordas y
    produce MÁS predicciones extremas de las que el binormal anticipa. Eso no
    es buena noticia por sí solo: lo que decide es si esas predicciones
    extremas se cumplen, y la curva de fiabilidad dice que no.
    """
    frac_real, _ = fraction_over_breakeven(auc_real, base_rate, breakeven)
    buckets = block["model"]["buckets"]

    over = [b for b in buckets if b["lower"] >= breakeven]
    straddle = [b for b in buckets if b["lower"] < breakeven < b["upper"]]
    n_over = sum(b["n"] for b in over)
    n_straddle = sum(b["n"] for b in straddle)

    print()
    print(f"  control contra el run real (AUC {auc_real:.3f}):")
    print(f"    el binormal predice ~{frac_real * n_real:,.1f} barras sobre el equilibrio")
    print(
        f"    el run tiene {n_over:,} en buckets enteramente por encima"
        f" (+{n_straddle:,} en el bucket que lo cruza)"
    )
    for bucket in straddle + over:
        if bucket["n"] == 0:
            continue
        verdict = "cumple" if bucket["observed_rate"] >= breakeven else "NO cumple"
        print(
            f"      {bucket['lower']:.0%}-{bucket['upper']:.0%}"
            f"  n={bucket['n']:>5,}"
            f"  predicho {bucket['mean_predicted']:.1%}"
            f"  observado {bucket['observed_rate']:.1%}  -> {verdict} el equilibrio"
        )
    if n_over + n_straddle == 0:
        print("    el modelo nunca predijo por encima del equilibrio, igual que")
        print("    lo anticipa el binormal.")
    else:
        print("    la cola real es más gorda que la gaussiana, pero está")
        print("    DESCALIBRADA: el binormal subestima cuántas señales aparecen")
        print("    y sobreestima cuántas de ellas sirven.")


def _latest_gate_artifact(artifacts_dir: Path) -> Path:
    """El run de gate más reciente de la variante y el estimador del vivo.

    Mismo criterio que `/api/backtest/gate`: `price+deriv` con `gbm` es lo que
    emite el analista. Ordenar por nombre completo ordenaría por librería antes
    que por fecha, así que se ordena por el timestamp del nombre.
    """
    for pattern in ("ETHUSDT-*-price+deriv-gbm-*.json", "ETHUSDT-*-price-gbm-*.json"):
        found = sorted(artifacts_dir.glob(pattern), key=lambda p: p.stem.rsplit("-", 1)[-1])
        if found:
            return found[-1]
    raise SystemExit(f"no hay artefactos de gate en {artifacts_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="JSON del gate del que se leen tasa base y equilibrio",
    )
    args = parser.parse_args()
    enable_utf8_stdout()

    root = Path(__file__).resolve().parent.parent
    path = args.artifact or _latest_gate_artifact(root / "artifacts")
    report = json.loads(path.read_text(encoding="utf-8"))

    timeframe = report["timeframe"]
    bars_year = BARS_PER_YEAR.get(timeframe)

    print("=" * 72)
    print("PISO ECONÓMICO DEL AUC — pasar el gate no es poder operar")
    print(f"{report['symbol']} {timeframe}   artefacto: {path.name}")
    print("=" * 72)

    for name, block in report["directions"].items():
        base_rate = block["model"]["base_rate"]
        breakeven = block["breakeven_prob"]
        auc_real = block["model"]["auc"]
        n_real = block["model"]["n"]

        print()
        print(f"-- {name.upper()} --")
        print(
            f"  tasa base {base_rate:.1%}   equilibrio {breakeven:.1%}"
            f"   -> hay que levantar {(breakeven - base_rate) * 100:+.1f}pp"
        )
        print(f"  AUC medido del modelo actual: {auc_real:.3f}  (n={n_real:,})")
        print()

        header = f"  {'AUC':>6} {'% barras > equilibrio':>22} {'win rate':>10}"
        if bars_year:
            header += f" {'barras/año':>12}"
        print(header)
        print("  " + "-" * (len(header) - 2))

        for auc in AUC_GRID:
            frac, win = fraction_over_breakeven(auc, base_rate, breakeven)
            flag = "  <- umbral del gate" if abs(auc - GATE_AUC) < 1e-9 else ""
            row = f"  {auc:>6.2f} {frac * 100:>21.3f}% {win:>9.1%}"
            if bars_year:
                row += f" {frac * bars_year:>12,.0f}"
            print(row + flag)

        _print_control(block, base_rate, breakeven, auc_real, n_real)

    print()
    print("El binormal de varianza igual es una idealización: supone scores")
    print("gaussianos de igual dispersión en las dos clases. Sirve para fijar el")
    print("orden de magnitud del listón, no para predecir un run concreto.")


if __name__ == "__main__":
    main()
