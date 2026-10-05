"""Backtest page — los artefactos del gate, servidos tal como quedaron.

**Solo lectura, y es una decisión, no una etapa pendiente.** Lanzar un
walk-forward desde la UI son ~10 ajustes de boosting sobre 69k barras
compitiendo por CPU con el analista en vivo, justo mientras corre la
validación forward. Los runs se corren por CLI (`bob.backtest.runner`), quedan
versionados en `backend/artifacts/` y esta capa los muestra.

El veredicto NO se recalcula acá. Sale de `RunSummary`, que importa
`GATE_MIN_AUC`, `GATE_MIN_BSS` y `GATE_MAX_CALIBRATION_ERROR_PP` de
`models/experiment.py`. Un endpoint con su propia definición del gate podría
contestar "habilitado" donde `gate_passed` dice que no, y esa contradicción es
justo la que no puede existir en este proyecto.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from bob.api.routes._common import ARTIFACTS_DIR, in_thread
from bob.backtest.compare import RunSummary, load_run
from bob.config import settings
from bob.models.experiment import (
    GATE_MAX_CALIBRATION_ERROR_PP,
    GATE_MIN_AUC,
    GATE_MIN_BSS,
    ExperimentConfig,
)

router = APIRouter(tags=["backtest"])

#: Prefijo de los artefactos del paper tracking. Viven en el mismo directorio
#: que los del gate y tienen otro esquema — `load_run` los descarta solo, pero
#: nombrarlos acá evita depender de que falle.
FORWARD_PREFIX = "forward-"

#: Igual que el anterior: los artefactos del filtro de régimen (Fase 5-bis)
#: comparten directorio y tienen su propio esquema.
RISKFILTER_PREFIX = "riskfilter-"
STOPFILTER_PREFIX = "stopfilter-"

#: El timestamp con que `runner.build_run_id` cierra el nombre: `...-YYYYmmddHHMMSS`.
_STAMP_RE = re.compile(r"(\d{14})$")


def _stamp(run_id: str) -> str:
    """Fecha del run, sacada del final de su id. Cadena vacía si no la trae.

    Ordenar por nombre completo parece equivalente y no lo es desde que el id
    lleva el estimador: `...-price+deriv-gbm-20260826141232` y
    `...-price+deriv-xgb-20260826141930` ordenan por `gbm` < `xgb`, o sea por
    librería y no por fecha. Con eso, "el run más reciente de esta variante"
    devolvía el de XGBoost aunque el vivo esté emitiendo con GBM — y el
    veredicto que el dashboard pone al lado del KPI 1 saldría de un modelo que
    no es el que produjo el número.
    """
    found = _STAMP_RE.search(run_id)
    return found.group(1) if found else ""


def _live_vol_kind() -> str:
    """Estimador de volatilidad con el que corre el vivo.

    Sale de `ExperimentConfig` y no de una constante local: es el mismo default
    que instancia `LiveAnalyst`, así que los dos se mueven juntos.
    """
    return str(ExperimentConfig().vol_kind)


def _gate_thresholds() -> dict[str, float]:
    return {
        "min_auc": GATE_MIN_AUC,
        "min_bss": GATE_MIN_BSS,
        "max_calibration_error_pp": GATE_MAX_CALIBRATION_ERROR_PP,
    }


def _verdict(run: RunSummary) -> dict[str, Any]:
    return {
        "calibra": run.calibra,
        "discrimina": run.discrimina,
        "habilitado": run.habilitado,
        "thresholds": _gate_thresholds(),
    }


def _gate_runs() -> list[Path]:
    if not ARTIFACTS_DIR.is_dir():
        return []
    return sorted(
        p
        for p in ARTIFACTS_DIR.glob("*.json")
        if not p.name.startswith((FORWARD_PREFIX, RISKFILTER_PREFIX))
    )


def _forward_artifacts() -> list[Path]:
    if not ARTIFACTS_DIR.is_dir():
        return []
    return sorted(ARTIFACTS_DIR.glob(f"{FORWARD_PREFIX}*.json"))


def _summary(path: Path) -> dict[str, Any] | None:
    """Resumen de un run: identidad, veredicto y el target que sí pasó."""
    try:
        run = load_run(path)
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (KeyError, ValueError, OSError, json.JSONDecodeError):
        return None  # artefacto de otro esquema — se omite, no se rompe

    vol = raw.get("volatility", {})
    config = raw.get("config", {})
    return {
        "run_id": run.run_id,
        "variant": run.variant,
        # Los artefactos anteriores a la Fase 4-bis no traen el campo porque no
        # había nada que elegir: el GBM de sklearn era el único estimador. Se
        # normaliza en vez de dejarlo en null para que el filtro por estimador
        # no los excluya de su propia historia.
        "vol_kind": config.get("vol_kind") or "gbm",
        "vol_kind_declared": config.get("vol_kind") is not None,
        "model_kind": config.get("model_kind"),
        "n_features": run.n_features,
        "n_bars": run.n_bars,
        "date_from": run.date_from,
        "date_to": run.date_to,
        "runtime_s": raw.get("runtime_s"),
        # Target 1 — dirección. El que NO pasó, y por eso viaja con veredicto.
        "directions": run.directions,
        "gate": _verdict(run),
        # Target 2 — volatilidad. El que sostiene el producto desde el 25-08.
        "volatility": {
            "model": vol.get("model"),
            "baseline_ewma": vol.get("baseline_ewma"),
            "baseline_garch": vol.get("baseline_garch"),
            "baseline_har": vol.get("baseline_har"),
            "dm_vs_ewma": vol.get("dm_vs_ewma"),
            "dm_vs_har": vol.get("dm_vs_har"),
        },
        "intervals": raw.get("intervals"),
        "family_importance": run.family_importance,
    }


@router.get("/backtest/runs")
async def runs() -> dict[str, Any]:
    """Todos los runs del gate versionados, del más viejo al más nuevo."""

    def read() -> list[dict[str, Any]]:
        out = [_summary(p) for p in _gate_runs()]
        return [s for s in out if s is not None]

    summaries = await in_thread(read)
    summaries.sort(key=lambda s: _stamp(str(s["run_id"])))
    # La ablación se lee como escalera: el run más reciente de cada
    # (variante, estimador). Los dos, no solo la variante — `price+deriv` con
    # GBM y con XGBoost son dos runs comparables entre sí y no uno que pisa al
    # otro (Fase 4-bis: empatan, y el empate es el hallazgo).
    latest: dict[str, str] = {}
    for s in summaries:
        latest[f"{s['variant']}/{s['vol_kind']}"] = str(s["run_id"])
    return {
        "n": len(summaries),
        "thresholds": _gate_thresholds(),
        "runs": summaries,
        "latest_by_variant": latest,
    }


@router.get("/backtest/runs/{run_id}")
async def run_detail(run_id: str) -> dict[str, Any]:
    """El artefacto completo — incluye los buckets de la curva de calibración.

    `run_id` se resuelve contra el listado del directorio y nunca se concatena
    a una ruta: un id con `..` no puede alcanzar nada fuera de `artifacts/`.
    """
    paths = {p.stem: p for p in _gate_runs()}
    path = paths.get(run_id)
    if path is None:
        raise HTTPException(status_code=404, detail=f"run desconocido: {run_id}")

    raw: dict[str, Any] = await in_thread(
        lambda: json.loads(path.read_text(encoding="utf-8"))
    )
    summary = await in_thread(lambda: _summary(path))
    return {"run_id": run_id, "gate": (summary or {}).get("gate"), "artifact": raw}


@router.get("/backtest/gate")
async def gate(
    variant: str | None = Query(
        None, description="variante a evaluar; default = la que corre en vivo"
    ),
    vol_kind: str | None = Query(
        None, description="estimador de volatilidad; default = el que corre en vivo"
    ),
) -> dict[str, Any]:
    """El veredicto que el dashboard tiene que mostrar al lado del KPI 1.

    Existe para que el frontend no hardcodee "no habilitado". El día que el
    motor direccional pase el gate, la página se enciende sola porque el
    número vino de acá; si estuviera escrito en el TSX, alguien tendría que
    acordarse — y la regla 2 no puede depender de eso.

    Filtra por variante **y** por estimador porque el veredicto que se muestra
    tiene que ser el del modelo que está emitiendo. Mostrar el run de XGBoost
    al lado de un pronóstico de GBM sería citar la medición de otro modelo:
    dan casi lo mismo (Fase 4-bis), pero "casi lo mismo" no es "el mismo", y
    esa es exactamente la distinción que el proyecto no puede aflojar.
    """
    wanted = variant or settings.bob_live_features
    wanted_vol = vol_kind or _live_vol_kind()

    def read() -> dict[str, Any] | None:
        matches = [
            s
            for s in (_summary(p) for p in _gate_runs())
            if s is not None
            and s["variant"] == wanted
            and str(s["vol_kind"]) == wanted_vol
        ]
        if not matches:
            return None
        return max(matches, key=lambda s: _stamp(str(s["run_id"])))

    summary = await in_thread(read)
    if summary is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no hay artefacto del gate para la variante {wanted} "
                f"con vol_kind={wanted_vol}"
            ),
        )
    return {
        "variant": wanted,
        "vol_kind": wanted_vol,
        "live_feature_set": settings.bob_live_features,
        "run_id": summary["run_id"],
        "date_from": summary["date_from"],
        "date_to": summary["date_to"],
        "n_bars": summary["n_bars"],
        "directions": summary["directions"],
        "gate": summary["gate"],
        "volatility": summary["volatility"],
        "intervals": summary["intervals"],
    }


@router.get("/backtest/riskfilter")
async def riskfilter() -> dict[str, Any]:
    """La medición del filtro de régimen, tal como quedó congelada.

    El dashboard la necesita para cumplir la regla 2 con el estado del filtro:
    igual que el gauge de Seguridad va con su AUC real al lado, el cartel de
    "no entres ahora" tiene que ir con el lift medido y su intervalo. Un
    "RÉGIMEN ELEVADO" sin decir que evita el 52% de las liquidaciones —y no el
    100%— es una alarma sin magnitud.

    Sale del artefacto y **no** de constantes en el TSX, por la misma razón que
    el veredicto del gate: el día que se remida, el número del dashboard cambia
    solo. Y el veredicto viene calculado por `verdict()`, que aplica los dos
    criterios del pre-registro — no se reinterpreta acá.
    """
    paths = sorted(
        p for p in ARTIFACTS_DIR.glob(f"{RISKFILTER_PREFIX}*.json") if p.is_file()
    )
    if not paths:
        raise HTTPException(status_code=404, detail="no hay medición del filtro")
    path = paths[-1]
    raw: dict[str, Any] = await in_thread(
        lambda: json.loads(path.read_text(encoding="utf-8"))
    )
    primary = raw.get("primary", {})
    model = primary.get("lifts", {}).get("model", {})
    baseline = primary.get("lifts", {}).get("ewma", {})
    return {
        "artifact_id": path.stem,
        "preregistration": raw.get("config", {}).get("preregistration"),
        "viable": raw.get("verdict", {}).get("viable", False),
        "reasons": raw.get("verdict", {}).get("reasons", []),
        "leverage": primary.get("leverage"),
        "quantile": primary.get("quantile"),
        "n_bars": primary.get("n_bars"),
        "n_events": primary.get("n_events"),
        "effective_events": primary.get("effective_events"),
        "lift": model.get("lift"),
        "lift_ci95": primary.get("lift_ci95"),
        "capture_rate": model.get("capture_rate"),
        "reject_rate": model.get("reject_rate"),
        "false_reject_rate": model.get("false_reject_rate"),
        "baseline_lift": baseline.get("lift"),
        "baseline_capture_rate": baseline.get("capture_rate"),
        "diff_ci95": primary.get("diff_ci95"),
        "report": raw,
    }


@router.get("/backtest/forward")
async def forward() -> dict[str, Any]:
    """El corte forward congelado más reciente de `backend/artifacts/`.

    Es distinto de `/api/history/coverage`: aquel mide la muestra **de ahora**,
    que sigue creciendo; este es el artefacto fechado que quedó versionado y no
    se mueve más. El dashboard muestra los dos y dice cuál es cuál — un
    veredicto que cambia cada vez que se refresca no es un veredicto.
    """
    paths = _forward_artifacts()
    if not paths:
        raise HTTPException(status_code=404, detail="no hay artefactos forward")
    path = paths[-1]
    raw: dict[str, Any] = await in_thread(
        lambda: json.loads(path.read_text(encoding="utf-8"))
    )
    return {"artifact_id": path.stem, "frozen": True, "report": raw}


@router.get("/backtest/stopfilter")
async def stopfilter() -> dict[str, Any]:
    """El filtro de régimen leído con stop (`PREREGISTRO_FILTRO_STOP.md`).

    Es la evidencia de las dos frases que la página Riesgo le dice a un
    operador con stop: cuánto más seguido salta el stop en régimen elevado, y
    qué tan calibrada está la P de toque de la calculadora. Sale del artefacto
    por la misma razón que el resto: si se remide, el dashboard cambia solo.
    """
    paths = sorted(p for p in ARTIFACTS_DIR.glob(f"{STOPFILTER_PREFIX}*.json") if p.is_file())
    if not paths:
        raise HTTPException(status_code=404, detail="no hay medición del filtro con stop")
    path = paths[-1]
    raw: dict[str, Any] = await in_thread(lambda: json.loads(path.read_text(encoding="utf-8")))
    primary = raw.get("primary", {})
    cal = raw.get("calibration", {})
    bracket = raw.get("bracket", {})

    def rate(label: str) -> float | None:
        c = cal.get(label)
        return None if c is None else c.get("observed")

    return {
        "artifact_id": path.stem,
        "preregistration": raw.get("config", {}).get("preregistration"),
        "reproduces": raw.get("reproduction", {}).get("ok", False),
        "c1_pass": raw.get("verdict", {}).get("c1_pass", False),
        "stop_pct": raw.get("config", {}).get("primary_stop"),
        "horizon_bars": raw.get("config", {}).get("horizon"),
        "hit_rate_ratio": primary.get("hit_rate_ratio", {}).get("model"),
        "hit_rate_normal": rate("normal"),
        "hit_rate_elevated": rate("elevado"),
        "lift": primary.get("lifts", {}).get("model", {}).get("lift"),
        "lift_ci95": primary.get("lift_ci95"),
        "diff_vs_baseline_ci95": primary.get("diff_ci95"),
        "calibration_gap": {k: v.get("gap") for k, v in cal.items()},
        "calibration_gap_ci95": {k: v.get("gap_ci95") for k, v in cal.items()},
        "calculator_needs_correction": raw.get("verdict", {}).get(
            "calculator_needs_correction", []
        ),
        "expectancy_r": {k: v.get("mean_r") for k, v in bracket.items()},
    }
