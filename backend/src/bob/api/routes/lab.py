"""Laboratorio ▸ Resumen (M6.4): el registro de experimentos, leído de sus artefactos.

Una entrada por experimento que tiene artefacto versionado en
`backend/artifacts/`, con su veredicto y las cifras que lo sostienen. Existe
para que el dashboard pueda decir "esto pasó, esto no" **sin escribir un solo
número en el TSX**: si un experimento se remide, la página cambia sola.

**El veredicto no se recalcula acá.** Cada entrada lee el campo que escribió su
propio runner (`criteria.viable`, `verdict.c1_pass`, `RunSummary.habilitado`,
`calibrated`…), que aplica los criterios de su pre-registro. Las dos
excepciones están declaradas donde ocurren: el target de volatilidad del gate
usa `DieboldMariano.favors_model` —la misma propiedad que imprime el reporte—
y el cono forward usa "el nominal cae dentro del IC por bloques", que es el
criterio con que se cerró la Fase 5 (CLAUDE.md, punto 13).

Lo que **no** está acá, a propósito: el IC del forward del panel diario. La
Enmienda 1 prohíbe mirarlo antes del interino; su avance operativo sale de
`/api/macro/panel-status` y la página lo agrega como "en curso".

`PREREGISTRATIONS` es la única tabla escrita a mano y no tiene cifras: el
documento y el commit con que se fijó cada criterio son hechos históricos
inmutables. Donde el artefacto trae su propio commit, manda el artefacto.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from bob.api.routes import _common
from bob.api.routes._common import in_thread
from bob.backtest.compare import load_run
from bob.models.experiment import GATE_MIN_AUC
from bob.models.metrics import DieboldMariano

router = APIRouter(tags=["lab"])

#: Documento y commit del criterio de cada experimento. `None` = el criterio
#: no tiene pre-registro propio (se dice así en la página, no se disimula).
PREREGISTRATIONS: dict[str, tuple[str | None, str | None]] = {
    "gate_direction": ("CLAUDE.md § Backtesting engine", None),
    "gate_volatility": ("CLAUDE.md § Backtesting engine", None),
    "forward_15m": ("CLAUDE.md § Fase 5", None),
    "riskfilter": ("docs/PREREGISTRO_FILTRO_RIESGO.md", "5c94510"),
    "sigmatail": ("backtest/sigma_tail_runner.py (docstring)", None),
    "stopfilter": ("docs/PREREGISTRO_FILTRO_STOP.md", "015c9eb"),
    "technical": ("docs/PREREGISTRO_TECNICO.md", "07a71ff"),
    "panel_offline": ("docs/PREREGISTRO_PANEL_DIARIO.md", "605e1f7"),
    "panel_placebo": ("docs/PREREGISTRO_PANEL_DIARIO.md", "605e1f7"),
    "macro_power": ("marea/power.py", "d48db5c"),
    "macro_ranking": ("docs/PREREGISTRO_RANKING_MACRO.md", "836e603"),
}

#: Fecha del artefacto en su nombre: `...-20260914202117` o `...-20261005T221954Z`.
_STAMP_RE = re.compile(r"(\d{8})T?(\d{6})Z?$")


def _date(stem: str) -> str | None:
    m = _STAMP_RE.search(stem)
    if not m:
        return None
    d = m.group(1)
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def _latest(pattern: str, exclude: Callable[[str], bool] = lambda _: False) -> Path | None:
    """El artefacto más reciente de una familia, por la fecha de su nombre."""
    paths = [p for p in _common.ARTIFACTS_DIR.glob(pattern) if p.is_file() and not exclude(p.stem)]
    if not paths:
        return None
    return max(paths, key=lambda p: (_date(p.stem) or "", p.stem))


def _read(path: Path) -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return raw


def _num(x: Any) -> float | None:
    """JSON no tiene NaN: lo que no es un número finito viaja como null."""
    if isinstance(x, bool) or not isinstance(x, int | float):
        return None
    return float(x) if math.isfinite(x) else None


def _entry(exp_id: str, path: Path, status: str, figures: dict[str, Any]) -> dict[str, Any]:
    doc, commit = PREREGISTRATIONS[exp_id]
    return {
        "id": exp_id,
        "status": status,
        "artifact_id": path.stem,
        "date": _date(path.stem),
        "criterion_doc": doc,
        "criterion_commit": commit,
        "figures": figures,
    }


def _pass(ok: bool) -> str:
    return "pasa" if ok else "no_pasa"


# --------------------------------------------------------------------- 15m --


def _gate_paths() -> list[Path]:
    """Artefactos del gate de la Fase 4: los que `load_run` sabe leer."""
    out = []
    for p in sorted(_common.ARTIFACTS_DIR.glob("ETHUSDT-15m-*.json")):
        try:
            load_run(p)
        except (KeyError, ValueError, OSError, json.JSONDecodeError):
            continue
        out.append(p)
    return out


def _gate_direction() -> dict[str, Any] | None:
    paths = _gate_paths()
    if not paths:
        return None
    runs = [load_run(p) for p in paths]
    aucs = [d["auc"] for r in runs for d in r.directions.values()]
    latest = max(paths, key=lambda p: _date(p.stem) or "")
    return _entry(
        "gate_direction",
        latest,
        _pass(any(r.habilitado for r in runs)),
        {
            "n_runs": len(runs),
            "n_enabled": sum(r.habilitado for r in runs),
            "best_auc": _num(max(aucs)),
            "worst_auc": _num(min(aucs)),
            "n_bars": max(r.n_bars for r in runs),
            # El umbral se importa del gate, no se copia (mismo motivo que compare.py).
            "min_auc": GATE_MIN_AUC,
        },
    )


def _dm(raw: dict[str, Any]) -> DieboldMariano:
    return DieboldMariano(
        statistic=float(raw.get("statistic", math.nan)),
        p_value=float(raw.get("p_value", math.nan)),
        mean_loss_diff=float(raw.get("mean_loss_diff", raw.get("statistic", math.nan))),
        horizon=0,
        n=0,
    )


def _gate_volatility() -> dict[str, Any] | None:
    """El target que sí pasó. Se lee del run de la variante que corre en vivo."""
    path = _latest("ETHUSDT-15m-price+deriv-gbm-*.json")
    if path is None:
        return None
    vol = _read(path).get("volatility", {})
    dm_ewma, dm_har = _dm(vol.get("dm_vs_ewma", {})), _dm(vol.get("dm_vs_har", {}))
    return _entry(
        "gate_volatility",
        path,
        _pass(dm_ewma.favors_model and dm_har.favors_model),
        {
            "r2_vs_ewma": _num(vol.get("model", {}).get("r2_vs_baseline")),
            "dm_p_vs_ewma": _num(dm_ewma.p_value),
            "dm_p_vs_har": _num(dm_har.p_value),
            "n_samples": vol.get("n_samples"),
        },
    )


def _forward_15m() -> dict[str, Any] | None:
    path = _latest("forward-*.json")
    if path is None:
        return None
    raw = _read(path)
    cones, cone_ci = raw.get("cones", {}), raw.get("cone_ci", {})
    levels = {}
    covered = True
    for key, cone in cones.items():
        ci = cone_ci.get(key)
        nominal = cone.get("nominal_coverage")
        inside = bool(ci and nominal is not None and ci[0] <= nominal <= ci[1])
        covered = covered and inside
        levels[key] = {
            "nominal": _num(nominal),
            "empirical": _num(cone.get("empirical_coverage")),
            "ci95": ci,
            "nominal_inside": inside,
        }
    dm_ewma = _dm(raw.get("dm_vs_ewma", {}))
    return _entry(
        "forward_15m",
        path,
        _pass(covered and dm_ewma.favors_model),
        {
            "n_resolved": raw.get("n_resolved"),
            "date_from": raw.get("date_from"),
            "date_to": raw.get("date_to"),
            "cones": levels,
            "dm_p_vs_ewma": _num(dm_ewma.p_value),
            "r2_vs_ewma": _num(raw.get("volatility", {}).get("r2_vs_baseline")),
        },
    )


def _riskfilter() -> dict[str, Any] | None:
    path = _latest("riskfilter-*.json")
    if path is None:
        return None
    raw = _read(path)
    primary = raw.get("primary", {})
    lifts = primary.get("lifts", {})
    return _entry(
        "riskfilter",
        path,
        _pass(bool(raw.get("verdict", {}).get("viable"))),
        {
            "leverage": primary.get("leverage"),
            "lift": _num(lifts.get("model", {}).get("lift")),
            "lift_ci95": primary.get("lift_ci95"),
            "reject_rate": _num(lifts.get("model", {}).get("reject_rate")),
            "capture_rate": _num(lifts.get("model", {}).get("capture_rate")),
            "baseline_capture_rate": _num(lifts.get("ewma", {}).get("capture_rate")),
        },
    )


def _sigmatail() -> dict[str, Any] | None:
    path = _latest("sigmatail-*.json")
    if path is None:
        return None
    results = _read(path).get("results", [])
    per_q = [
        {
            "q": _num(r.get("q")),
            "nominal": _num(r.get("nominal_exceedance")),
            "exceedance": _num(r.get("exceedance_model")),
            "exceedance_ci95": r.get("exceedance_ci95"),
            "calibrated": bool(r.get("calibrated")),
            "adopted": bool(r.get("adopted")),
            # Candidato post-hoc: se informa, no decide (Fase 5-ter).
            "adaptive_calibrated": bool(r.get("adaptive_calibrated")),
        }
        for r in results
    ]
    return _entry(
        "sigmatail",
        path,
        _pass(bool(per_q) and all(r["adopted"] for r in per_q)),
        {"levels": per_q},
    )


def _stopfilter() -> dict[str, Any] | None:
    path = _latest("stopfilter-*.json")
    if path is None:
        return None
    raw = _read(path)
    cal = raw.get("calibration", {})
    return _entry(
        "stopfilter",
        path,
        _pass(bool(raw.get("verdict", {}).get("c1_pass"))),
        {
            "stop_pct": _num(raw.get("config", {}).get("primary_stop")),
            "hit_rate_ratio": _num(raw.get("primary", {}).get("hit_rate_ratio", {}).get("model")),
            "hit_rate_normal": _num(cal.get("normal", {}).get("observed")),
            "hit_rate_elevated": _num(cal.get("elevado", {}).get("observed")),
            "calibration_gap_pp": {
                k: (None if _num(v.get("gap")) is None else 100 * float(v["gap"]))
                for k, v in cal.items()
            },
            "mean_r": {k: _num(v.get("mean_r")) for k, v in raw.get("bracket", {}).items()},
        },
    )


def _technical() -> dict[str, Any] | None:
    path = _latest("technical-*.json")
    if path is None:
        return None
    raw = _read(path)
    conf = raw.get("confirmatory", [])
    expl = raw.get("exploratory", [])
    rules = [
        {
            "rule": c.get("rule"),
            "timeframe": c.get("timeframe"),
            "verdict": c.get("verdict"),
            "mean_r": _num(c.get("stats", {}).get("mean_r")),
            "placebo_mean_r": _num(c.get("placebo", {}).get("mean_r")),
        }
        for c in conf
    ]
    return _entry(
        "technical",
        path,
        _pass(any(c.get("verdict") == "PASA" for c in conf)),
        {
            "rules": rules,
            "n_exploratory": len(expl),
            "n_exploratory_positive": sum(
                1 for e in expl if (_num(e.get("stats", {}).get("mean_r")) or 0.0) > 0
            ),
            "cost_pct": _num(raw.get("cost_pct")),
        },
    )


# ------------------------------------------------------------- panel diario --


def _panel(exp_id: str, path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    raw = _read(path)
    viable = bool(raw.get("criteria", {}).get("viable"))
    placebo = bool(raw.get("config", {}).get("placebo"))
    # Un placebo "pasa" cuando falla: es el control de que la plomería no
    # fabrica edge.
    status = ("control_ok" if not viable else "control_falla") if placebo else _pass(viable)
    intervals = raw.get("intervals", {})
    return _entry(
        exp_id,
        path,
        status,
        {
            "horizon_days": raw.get("config", {}).get("horizon"),
            "n_symbols": raw.get("n_symbols"),
            "n_dates": raw.get("n_dates"),
            "auc": _num(raw.get("auc")),
            "ic": _num(raw.get("model_mean_ic")),
            "ic_ci95": intervals.get("ic"),
            "spread_net": _num(raw.get("model_spread_net")),
            "spread_net_ci95": intervals.get("spread_net"),
            "criteria": raw.get("criteria", {}),
        },
    )


# ------------------------------------------------------------------- macro --


def _macro_power() -> dict[str, Any] | None:
    path = _latest("marea-power-*.json")
    if path is None:
        return None
    raw = _read(path)
    primary = raw.get("primary", {})
    entry = _entry(
        "macro_power",
        path,
        # La medición de potencia no aprueba nada: informa qué efecto se puede ver.
        "informativo",
        {
            "verdict": primary.get("verdict"),
            "mde": _num(primary.get("mde")),
            "independent_blocks": _num(primary.get("independent_blocks")),
            "horizon_weeks": primary.get("horizon_weeks"),
        },
    )
    entry["criterion_commit"] = raw.get("criterion_commit") or entry["criterion_commit"]
    return entry


def _macro_ranking() -> dict[str, Any] | None:
    path = _latest("marea-ranking-*.json")
    if path is None:
        return None
    raw = _read(path)
    v = raw.get("primary", {}).get("verdict", {})
    entry = _entry(
        "macro_ranking",
        path,
        _pass(bool(v.get("viable"))),
        {
            "ic": _num(v.get("c1_ic_mean")),
            "ic_ci95": v.get("c1_ic_ci95"),
            "best_baseline": v.get("c2_best_baseline"),
            "paired_ci95": v.get("c2_paired", {}).get("ci95"),
            "reading": v.get("reading"),
            "liquidity_contribution": _num(raw.get("liquidity_contribution", {}).get("diff_mean")),
        },
    )
    entry["criterion_commit"] = raw.get("prereg_commit") or entry["criterion_commit"]
    return entry


def experiments() -> list[dict[str, Any]]:
    """Todas las entradas con artefacto, en el orden en que se cuentan."""
    readers: list[Callable[[], dict[str, Any] | None]] = [
        _gate_direction,
        _gate_volatility,
        _forward_15m,
        _riskfilter,
        _sigmatail,
        _stopfilter,
        _technical,
        lambda: _panel(
            "panel_offline",
            _latest("panel-1d-H5-xs-*.json", lambda s: not re.search(r"-xs-\d{14}$", s)),
        ),
        lambda: _panel("panel_placebo", _latest("panel-1d-H5-xs-PLACEBO-*.json")),
        _macro_power,
        _macro_ranking,
    ]
    out = []
    for read in readers:
        try:
            e = read()
        except (KeyError, ValueError, TypeError, OSError, json.JSONDecodeError):
            e = None  # un artefacto de otro esquema se omite, no rompe la página
        if e is not None:
            out.append(e)
    return out


@router.get("/lab/experiments")
async def lab_experiments() -> dict[str, Any]:
    """El registro de experimentos con su veredicto, leído de cada artefacto."""
    items = await in_thread(experiments)
    counts: dict[str, int] = {}
    for e in items:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    return {"experiments": items, "counts": counts}
