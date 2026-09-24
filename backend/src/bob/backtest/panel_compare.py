"""Comparador de runs del panel — la primaria contra las exploratorias declaradas.

Uso:
    uv run python -m bob.backtest.panel_compare

Lee los `.json` que deja `panel_runner.py` en `backend/artifacts/` y los pone
lado a lado contra la configuración **primaria** del pre-registro.

**Lo que este módulo NO hace: decidir.** Las 18 variantes de
`docs/PREREGISTRO_PANEL_DIARIO.md` §4 son **exploratorias**, y una
exploratoria que salga mejor que la primaria **no la reemplaza**. Elegir la
mejor de 18 después de verlas es exactamente el p-hacking que el pre-registro
existe para impedir: con 18 pruebas, que alguna supere a la primaria por azar
es lo esperable, no un hallazgo.

Lo que sí sirven es para lo contrario — para **falsificar**: si el resultado
solo aparece con un corte concreto, o se evapora al cambiar el horizonte, la
primaria era frágil y eso sí es información. Por eso la columna que importa no
es "cuál gana" sino "cuántas se sostienen".
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bob.utils.console import enable_utf8_stdout

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent.parent
ARTIFACTS_DIR = _BACKEND_DIR / "artifacts"

#: La configuración primaria, para separarla de las exploratorias en el reporte.
PRIMARY_SUFFIX = "xs"


@dataclass(frozen=True)
class PanelRun:
    """Un artefacto de panel, reducido a lo que la comparación necesita."""

    name: str
    path: Path
    data: dict[str, Any]

    @property
    def variant(self) -> str:
        """El tramo del `run_id` que describe la variante, sin timestamp."""
        stem = self.path.stem.removeprefix("panel-")
        parts = stem.rsplit("-", 1)
        return parts[0] if len(parts) == 2 and parts[1].isdigit() else stem

    @property
    def is_primary(self) -> bool:
        return self.variant == f"1d-H5-{PRIMARY_SUFFIX}"

    @property
    def is_placebo(self) -> bool:
        return "PLACEBO" in self.variant

    @property
    def outcome_selected(self) -> bool:
        """¿La población evaluada la define el RESULTADO y no una regla causal?

        El corte por tercil descarta el tercio del medio **según dónde terminó
        el retorno**, así que evalúa sobre ~2/3 de la sección elegidos con
        información futura. Sus AUC/BSS/IC contestan una pregunta condicional
        legítima —"entre los que van a moverse a un extremo, ¿cuál es cuál?"—
        pero su spread NO es alcanzable: al decidir no se sabe cuáles van a
        quedar en el medio. Marcarlo es obligatorio; si no, la fila se lee
        como si compitiera con la primaria y es la que mejor se ve.
        """
        cfg = self.data.get("config") or {}
        return bool(isinstance(cfg, dict) and cfg.get("target_cut", "median") != "median")

    def get(self, key: str, default: float = float("nan")) -> float:
        value = self.data.get(key, default)
        return float(value) if isinstance(value, (int, float)) else default


def load_runs(artifacts: Path | None = None) -> list[PanelRun]:
    """Todos los artefactos de panel, el más reciente por variante.

    El `run_id` termina en el timestamp, así que dentro de una variante el
    orden lexicográfico es cronológico — el mismo criterio que `compare.py`
    y por el mismo motivo: un run viejo de la misma variante refleja código
    que ya no existe.
    """
    directory = artifacts or ARTIFACTS_DIR
    latest: dict[str, PanelRun] = {}
    for path in sorted(directory.glob("panel-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:  # pragma: no cover — artefacto truncado
            continue
        run = PanelRun(name=path.stem, path=path, data=data)
        latest[run.variant] = run  # el sorted() deja el último = más reciente
    return list(latest.values())


def render_comparison(runs: list[PanelRun]) -> str:
    """Tabla de la primaria contra cada exploratoria."""
    out: list[str] = []
    w = out.append

    primary = next((r for r in runs if r.is_primary), None)
    placebo = next((r for r in runs if r.is_placebo), None)
    others = sorted(
        (r for r in runs if not r.is_primary and not r.is_placebo),
        key=lambda r: r.variant,
    )

    w("=" * 104)
    w("PANEL TRANSVERSAL DIARIO — primaria vs. exploratorias declaradas")
    w("=" * 104)
    w("pre-registro: docs/PREREGISTRO_PANEL_DIARIO.md §4 — 1 confirmatoria, 18 exploratorias")
    w("")
    w(
        f"{'variante':<28} {'filas':>9} {'feat':>5} {'AUC':>7} {'BSS':>9} "
        f"{'IC':>9} {'neto %':>8} {'IC95 neto':>19} {'oráculo':>8} {'rot':>6}"
    )
    w("-" * 104)

    def row(run: PanelRun, label: str) -> str:
        iv = run.data.get("intervals", {})
        net_lo, net_hi = (iv.get("spread_net") or [float("nan")] * 2)[:2]
        return (
            f"{label:<28} {int(run.get('n_rows', 0)):>9,} "
            f"{int(run.get('n_features', 0)):>5} "
            f"{run.get('auc'):>7.4f} {run.get('bss'):>+9.5f} "
            f"{run.get('model_mean_ic'):>+9.5f} "
            f"{run.get('model_spread_net') * 100:>+8.3f} "
            f"[{float(net_lo) * 100:>+7.3f},{float(net_hi) * 100:>+7.3f}] "
            f"{run.get('oracle_capture') * 100:>7.2f}% "
            f"{run.get('turnover') * 100:>5.1f}%"
        )

    if primary is not None:
        w(row(primary, "PRIMARIA (H=5, mediana)"))
        w("-" * 104)
    for run in others:
        label = run.variant.removeprefix("1d-")
        if run.outcome_selected:
            label += " ⚠"
        w(row(run, label))
    if placebo is not None:
        w("-" * 104)
        w(row(placebo, "PLACEBO (control)"))

    w("")
    w("=" * 104)
    w("CUÁNTAS SE SOSTIENEN")
    w("=" * 104)
    evaluables = [r for r in runs if not r.is_placebo]
    held = [r for r in evaluables if bool(r.data.get("criteria", {}).get("viable"))]
    w(f"  variantes evaluadas          : {len(evaluables)}")
    w(f"  con los TRES criterios       : {len(held)}")
    broken = [r for r in evaluables if r not in held]
    if broken:
        w(f"  sin los tres                 : {[r.variant.removeprefix('1d-') for r in broken]}")
    if placebo is not None:
        ok = not bool(placebo.data.get("criteria", {}).get("viable"))
        w(f"  placebo NO viable (debe ser) : {ok}")
    flagged = [r.variant.removeprefix("1d-") for r in evaluables if r.outcome_selected]
    if flagged:
        w("")
        w(f"  ⚠ POBLACIÓN DEFINIDA POR EL RESULTADO: {flagged}")
        w("    Descartan filas según dónde terminó el retorno, así que evalúan")
        w("    sobre una sección elegida con información futura. Su AUC sube")
        w("    porque el problema es más fácil, no porque el modelo sea mejor,")
        w("    y su spread NO es alcanzable. NO comparar con la primaria.")
    w("")
    w("  Una exploratoria que supere a la primaria NO la reemplaza: con 18")
    w("  pruebas, que alguna gane por azar es lo esperable. Lo que informan es")
    w("  la fragilidad — si el resultado se evapora al mover un parámetro.")
    return "\n".join(out)


def main() -> None:
    enable_utf8_stdout()
    ap = argparse.ArgumentParser(description="Compara los runs del panel")
    ap.add_argument("--artifacts", default=None)
    args = ap.parse_args()
    directory = Path(args.artifacts) if args.artifacts else ARTIFACTS_DIR
    runs = load_runs(directory)
    if not runs:
        print(f"no hay artefactos de panel en {directory}")
        return
    print(render_comparison(runs))


if __name__ == "__main__":
    main()
