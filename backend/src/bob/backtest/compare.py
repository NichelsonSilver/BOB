"""Comparador de runs del gate — ¿aportan las familias nuevas, o solo cambió el número?

Un run aislado no puede responder "¿sirvió agregar derivados?". Solo lo dice la
diferencia contra el mismo experimento sin esa familia, con la misma semilla,
los mismos folds y las mismas barreras. Este módulo lee los `.json` que deja
`runner.py` y los pone lado a lado.

Uso:
    uv run python -m bob.backtest.compare
    uv run python -m bob.backtest.compare --runs price full

Lee de `backend/artifacts/`. Si hay varios runs de la misma variante toma el
más reciente, que es el que refleja el código actual.

**Lo que este módulo NO hace**: decidir. Muestra AUC, BSS, error de calibración
y el veredicto de los dos criterios por dirección. Que una variante gane 0.004
de AUC no la habilita — el gate es el gate, y está en `ExperimentResult`.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bob.models.experiment import (
    GATE_MAX_CALIBRATION_ERROR_PP,
    GATE_MIN_AUC,
    GATE_MIN_BSS,
)
from bob.utils.console import enable_utf8_stdout

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent.parent
ARTIFACTS_DIR = _BACKEND_DIR / "artifacts"

#: Orden en que se muestran las variantes: de menos a más features. Así la
#: tabla se lee como lo que es, una escalera de ablación.
VARIANT_ORDER: tuple[str, ...] = ("price", "price+deriv", "full", "full+near")

LINE = "  " + "─" * 88


@dataclass(frozen=True)
class RunSummary:
    """Lo que hace falta de un run para compararlo con otro."""

    variant: str
    run_id: str
    n_features: int
    n_bars: int
    date_from: str
    date_to: str
    directions: dict[str, dict[str, float]]
    family_importance: dict[str, float]
    top_features: list[tuple[str, float]]

    @property
    def calibra(self) -> bool:
        """Criterio 1. Los umbrales se importan del gate, no se copian.

        Un comparador con su propia definición podría decir "habilitado" donde
        `ExperimentResult.gate_passed` dice que no, y esa contradicción es
        justo la que no puede existir en este proyecto.
        """
        return all(
            d["calibration_error_pp"] < GATE_MAX_CALIBRATION_ERROR_PP
            for d in self.directions.values()
        )

    @property
    def discrimina(self) -> bool:
        """Criterio 2: distingue casos, no solo reproduce la tasa base."""
        return all(
            d["auc"] > GATE_MIN_AUC and d["bss"] > GATE_MIN_BSS
            for d in self.directions.values()
        )

    @property
    def habilitado(self) -> bool:
        """Los DOS criterios, en TODAS las direcciones (CLAUDE.md, Fase 4)."""
        return self.calibra and self.discrimina


def _variant_from_run_id(run_id: str) -> str:
    """`ETHUSDT-15m-full-20260825...` -> `full`. Los runs viejos no la llevan."""
    parts = run_id.split("-")
    if len(parts) >= 4 and parts[-2] in VARIANT_ORDER:
        return parts[-2]
    return "sin-etiqueta"


def load_run(path: Path) -> RunSummary:
    """Lee un `.json` de artefactos y extrae lo comparable."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    run_id = path.stem

    directions: dict[str, dict[str, float]] = {}
    for name, d in data["directions"].items():
        # `model` es el calibrado, que es el que evalúa el gate. El BSS viene
        # ya calculado por metrics.py: recomputarlo acá sería arriesgarse a
        # comparar contra una definición distinta de la que decide.
        model = d["model"]
        dm = d.get("diebold_mariano", {})
        directions[name] = {
            "auc": float(model["auc"]),
            "brier": float(model["brier"]),
            "bss": float(model["brier_skill_score"]),
            "calibration_error_pp": float(model["mean_calibration_error_pp"]),
            "n_samples": float(d["n_samples"]),
            "breakeven": float(d.get("breakeven_prob", float("nan"))),
            "dm_p": float(dm.get("p_value", float("nan"))),
            "dm_stat": float(dm.get("statistic", float("nan"))),
        }

    return RunSummary(
        variant=_variant_from_run_id(run_id),
        run_id=run_id,
        n_features=int(data["n_features"]),
        n_bars=int(data["n_bars"]),
        date_from=str(data["date_from"]),
        date_to=str(data["date_to"]),
        directions=directions,
        family_importance=dict(data.get("family_importance", {})),
        top_features=[(str(n), float(v)) for n, v in data.get("importance_top", [])],
    )


def latest_by_variant(directory: Path = ARTIFACTS_DIR) -> dict[str, RunSummary]:
    """El run más reciente de cada variante. El nombre lleva el timestamp."""
    runs: dict[str, RunSummary] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            run = load_run(path)
        except (KeyError, ValueError, json.JSONDecodeError):
            continue  # artefacto de otra versión del esquema
        runs[run.variant] = run  # el orden alfabético deja último al más nuevo
    return runs


def _fmt_delta(valor: float, base: float, decimales: int = 3) -> str:
    """Valor con su diferencia contra el baseline, que es lo que se lee."""
    d = valor - base
    if abs(d) < 10 ** (-decimales):
        return f"{valor:.{decimales}f}    ="
    return f"{valor:.{decimales}f} {d:+.{decimales}f}"


def render_comparison(runs: dict[str, RunSummary]) -> str:
    """Tabla de ablación: cada variante contra el baseline de solo precio."""
    presentes = [v for v in VARIANT_ORDER if v in runs]
    if not presentes:
        return "No hay runs etiquetados por variante en artifacts/."

    base = runs.get("price")
    out: list[str] = []
    add = out.append

    add("")
    add(LINE)
    add("  COMPARACIÓN DE VARIANTES — gate de la Fase 4")
    add(LINE)
    ref = runs[presentes[0]]
    add(f"  {ref.n_bars:,} velas · {ref.date_from} .. {ref.date_to}")
    add("  Las diferencias son contra `price` (las 55 features de la Fase 2).")
    add("  DM p = Diebold-Mariano contra la tasa base; < 0.05 = la mejora no es ruido.")
    add("")

    for direccion in ("long", "short"):
        add(f"  Dirección: {direccion}")
        add(
            f"    {'variante':<12} {'feat':>5}  {'AUC':>16} {'BSS':>18} "
            f"{'calib pp':>14} {'DM p':>8}"
        )
        for v in presentes:
            run = runs[v]
            d = run.directions.get(direccion)
            if d is None:
                continue
            b = base.directions[direccion] if base and direccion in base.directions else d
            add(
                f"    {v:<12} {run.n_features:>5}  "
                f"{_fmt_delta(d['auc'], b['auc']):>16} "
                f"{_fmt_delta(d['bss'], b['bss'], 4):>18} "
                f"{_fmt_delta(d['calibration_error_pp'], b['calibration_error_pp'], 1):>14} "
                f"{d['dm_p']:>8.4f}"
            )
        add("")

    add(LINE)
    add("  VEREDICTO POR VARIANTE (los dos criterios, en las dos direcciones)")
    add(LINE)
    for v in presentes:
        run = runs[v]
        cal = "PASA" if run.calibra else "NO PASA"
        dis = "PASA" if run.discrimina else "NO PASA"
        marca = "✓ HABILITADO" if run.habilitado else "✗ no habilitado"
        add(f"    {v:<12} calibración: {cal:<8} discriminación: {dis:<8} → {marca}")
    add("")

    add(LINE)
    add("  IMPORTANCIA POR FAMILIA (permutación, Δ Brier)")
    add(LINE)
    familias = sorted({f for r in runs.values() for f in r.family_importance})
    add(f"    {'familia':<18}" + "".join(f"{v:>14}" for v in presentes))
    for fam in familias:
        fila = "".join(
            f"{runs[v].family_importance.get(fam, float('nan')):>14.5f}" for v in presentes
        )
        add(f"    {fam:<18}{fila}")
    add("")

    mejor = presentes[-1]
    add(LINE)
    add(f"  TOP 10 FEATURES — variante `{mejor}`")
    add(LINE)
    for nombre, score in runs[mejor].top_features[:10]:
        add(f"    {nombre:<30} {score:>+9.5f}")
    add("")
    return "\n".join(out)


def main() -> None:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(description="Compara runs del gate por variante")
    parser.add_argument(
        "--runs",
        nargs="*",
        default=None,
        help=f"variantes a comparar (default: todas las presentes). Opciones: {VARIANT_ORDER}",
    )
    parser.add_argument("--dir", default=str(ARTIFACTS_DIR))
    args = parser.parse_args()

    runs = latest_by_variant(Path(args.dir))
    if args.runs:
        runs = {k: v for k, v in runs.items() if k in set(args.runs)}
    print(render_comparison(runs))


if __name__ == "__main__":
    main()
