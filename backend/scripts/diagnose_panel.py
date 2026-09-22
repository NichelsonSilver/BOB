"""Diagnóstico del panel: las exploratorias declaradas, sobre el artefacto.

    uv run python scripts/diagnose_panel.py
    uv run python scripts/diagnose_panel.py --artifact artifacts/panel-<run>.json

**Solo lectura**: no toca la DB, no reentrena, no re-corre el walk-forward.
Lee las series por fecha que el artefacto ya guardó. Esa separación es
deliberada — una exploratoria que reentrena es una invitación a tocar la
configuración primaria después de ver el resultado, que es exactamente lo que
el pre-registro existe para impedir.

Las 18 exploratorias están declaradas en `docs/PREREGISTRO_PANEL_DIARIO.md`
§4. Las que se pueden contestar sin reentrenar se contestan acá; las que
exigen otro ajuste (H distinto, sin transversal, XGBoost) son corridas
aparte del runner.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from bob.paper.forward_eval import coverage_interval
from bob.utils.console import enable_utf8_stdout

_BACKEND_DIR = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = _BACKEND_DIR / "artifacts"


def _latest_artifact() -> Path:
    candidates = sorted(
        p for p in ARTIFACTS_DIR.glob("panel-*.json") if "PLACEBO" not in p.name
    )
    if not candidates:
        raise FileNotFoundError(
            "no hay artefacto de panel. Correr: "
            "uv run python -m bob.backtest.panel_runner"
        )
    # El nombre lleva el timestamp al final, así que el orden lexicográfico
    # dentro de la misma variante es cronológico.
    return candidates[-1]


def _year(ms: int) -> int:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).year


def main() -> None:
    enable_utf8_stdout()
    ap = argparse.ArgumentParser(description="Exploratorias declaradas del panel")
    ap.add_argument("--artifact", default=None)
    ap.add_argument("--n-boot", type=int, default=20_000)
    args = ap.parse_args()

    path = Path(args.artifact) if args.artifact else _latest_artifact()
    data = json.loads(path.read_text(encoding="utf-8"))
    cfg = data["config"]
    per = data["per_date"]

    ts = np.asarray(per["open_time"], dtype=np.int64)
    ic = np.asarray(per["ic"], dtype=np.float64)
    spread = np.asarray(per["spread_gross"], dtype=np.float64)
    oracle = np.asarray(per["oracle_spread"], dtype=np.float64)
    width = np.asarray(per["n_per_date"], dtype=np.int64)
    horizon = int(cfg["horizon"])
    pair_cost = 2.0 * float(cfg["leg_cost"])

    print(f"artefacto : {path.name}")
    print(f"horizonte : {horizon} días   costo del par: {pair_cost * 100:.3f}%")
    print(f"fechas    : {ts.size}   ancho mediano de la sección: {int(np.median(width))}")
    print()

    # ------------------------------------------------------------------ #
    # Exploratoria: sub-períodos anuales (6 de las 18 declaradas)
    # ------------------------------------------------------------------ #
    print("=" * 74)
    print("SUB-PERÍODOS ANUALES — ¿el efecto vive en un solo año?")
    print("=" * 74)
    print(f"{'año':>6} {'fechas':>7} {'IC':>9} {'spread neto':>13} {'IC95% del neto':>24}")
    years = np.array([_year(int(t)) for t in ts])
    for y in sorted(set(years.tolist())):
        sel = years == y
        if sel.sum() < 30:
            continue
        net = spread[sel] - pair_cost
        net = net[np.isfinite(net)]
        lo, hi = coverage_interval(net, horizon, n_boot=args.n_boot, seed=0)
        mark = "" if lo > 0 else "   <- contiene 0"
        print(
            f"{y:>6} {int(sel.sum()):>7} {np.nanmean(ic[sel]):>+9.4f} "
            f"{np.mean(net) * 100:>+12.3f}% [{lo * 100:>+7.3f}%, {hi * 100:>+7.3f}%]{mark}"
        )

    # ------------------------------------------------------------------ #
    # Estabilidad: fracción de fechas con spread neto positivo
    # ------------------------------------------------------------------ #
    print()
    print("=" * 74)
    print("ESTABILIDAD")
    print("=" * 74)
    net_all = spread - pair_cost
    good = np.isfinite(net_all)
    print(f"  fechas con spread neto > 0 : {np.mean(net_all[good] > 0) * 100:.1f}%")
    print(f"  fechas con IC > 0          : {np.mean(ic[np.isfinite(ic)] > 0) * 100:.1f}%")
    print(f"  peor fecha                 : {np.min(net_all[good]) * 100:+.2f}%")
    print(f"  mejor fecha                : {np.max(net_all[good]) * 100:+.2f}%")
    sd = float(np.std(net_all[good]))
    mean = float(np.mean(net_all[good]))
    # Sharpe por período de tenencia, anualizado por el número de períodos
    # NO solapados que caben en un año — usar las 365 fechas inflaría por
    # sqrt(H), que es el error clásico con setups solapados.
    periods = 365.0 / horizon
    print(f"  media/desvío por período   : {mean / sd:.3f}")
    print(f"  'Sharpe' anualizado        : {mean / sd * math.sqrt(periods):.2f}")
    print("    (períodos NO solapados: 365/H. Anualizar con 365 inflaría por sqrt(H))")

    # ------------------------------------------------------------------ #
    # La cola — el número que decide si esto es usable por ESTE usuario
    # ------------------------------------------------------------------ #
    print()
    print("=" * 74)
    print("LA COLA DEL SPREAD NETO (por período de tenencia)")
    print("=" * 74)
    net = net_all[good]
    for q in (0.1, 1.0, 5.0, 25.0, 50.0, 75.0, 95.0, 99.0, 99.9):
        print(f"  p{q:<6}: {np.percentile(net, q) * 100:+8.2f}%")
    skew = float(((net - net.mean()) ** 3).mean() / net.std() ** 3)
    kurt = float(((net - net.mean()) ** 4).mean() / net.std() ** 4)
    print(f"  asimetría : {skew:+.2f}   curtosis: {kurt:.1f}")
    worst = np.sort(net)[:5]
    print(f"  5 peores  : {[f'{x * 100:.1f}%' for x in worst]}")
    print(f"  5 mejores : {[f'{x * 100:.1f}%' for x in np.sort(net)[-5:]]}")
    print(f"  media SIN los 5 peores: {np.sort(net)[5:].mean() * 100:+.3f}%")
    print("    Si la media casi no se mueve al quitarlos, el edge no vive en")
    print("    unos pocos períodos — pero la cola sigue siendo el riesgo real")
    print("    para una cuenta apalancada, que es la del usuario del proyecto.")

    # ------------------------------------------------------------------ #
    # Captura del techo
    # ------------------------------------------------------------------ #
    print()
    print("=" * 74)
    print("CAPTURA DEL ORÁCULO")
    print("=" * 74)
    ok = np.isfinite(spread) & np.isfinite(oracle) & (np.abs(oracle) > 1e-12)
    print(f"  spread del modelo  : {np.mean(spread[ok]) * 100:+.4f}%")
    print(f"  spread del oráculo : {np.mean(oracle[ok]) * 100:+.4f}%")
    print(f"  capturado          : {np.mean(spread[ok]) / np.mean(oracle[ok]) * 100:.2f}%")

    # ------------------------------------------------------------------ #
    # Contra los baselines, fecha por fecha
    # ------------------------------------------------------------------ #
    print()
    print("=" * 74)
    print("CONTRA EL RANKING TRIVIAL, POR AÑO (diferencia pareada de IC)")
    print("=" * 74)
    for name, series in per["baseline_ic"].items():
        b = np.asarray(series, dtype=np.float64)
        if b.size != ic.size:
            continue
        print(f"  {name}:")
        for y in sorted(set(years.tolist())):
            sel = years == y
            if sel.sum() < 30:
                continue
            diff = (ic[sel] - b[sel])
            diff = diff[np.isfinite(diff)]
            lo, hi = coverage_interval(diff, horizon, n_boot=args.n_boot, seed=0)
            mark = "" if lo > 0 else "   <- contiene 0"
            print(
                f"    {y}: {np.mean(diff):>+8.4f}  IC95% "
                f"[{lo:>+8.4f}, {hi:>+8.4f}]{mark}"
            )


if __name__ == "__main__":
    main()
