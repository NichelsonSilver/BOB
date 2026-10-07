"""Rutas REST de MAREA, montadas bajo `/api` por el backend de `bob`.

Mismas dos reglas que `bob.api.routes._common`: **ninguna ruta escribe** y
**nada toca SQLite en el event loop**. El reporte lo calcula y guarda
`python -m marea.monitor`; la ruta solo lo lee. Calcularlo acá serían ~12 s de
CPU en el proceso que también corre el feed, para un número que cambia una vez
por semana.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from marea import strength
from marea.config import DB_PATH
from marea.data import store
from marea.power_run import ARTIFACTS

router = APIRouter(tags=["macro"])

#: Un reporte más viejo que esto se marca como vencido: los datos semanales de
#: la Fed salen los jueves, así que más de 8 días es perderse una publicación.
STALE_AFTER_DAYS = 8


def _read(path: Path) -> str | None:
    if not path.exists():
        return None
    conn = store.connect(path)
    try:
        return store.latest_liquidity_report(conn)
    finally:
        conn.close()


def _db_path() -> Path:
    return DB_PATH


@router.get("/macro/liquidity")
async def liquidity() -> dict[str, Any]:
    raw = await asyncio.to_thread(_read, _db_path())
    if raw is None:
        raise HTTPException(
            status_code=404,
            detail="no hay reporte de liquidez: correr `uv run python -m marea.monitor`",
        )
    rep: dict[str, Any] = json.loads(raw)
    age = (datetime.now(UTC).date() - date.fromisoformat(rep["as_of"])).days
    rep["age_days"] = age
    rep["stale"] = age > STALE_AFTER_DAYS
    return rep


def _strength(path: Path, weeks: int) -> dict[str, Any] | None:
    if not path.exists():
        return None
    conn = store.connect(path)
    try:
        return strength.from_db(conn, weeks)
    finally:
        conn.close()


@router.get("/macro/strength")
async def class_strength(weeks: int = Query(26)) -> dict[str, Any]:
    """Fuerza relativa por clase. Se calcula al pedirla (~0,5 s, en un hilo)."""
    if weeks not in strength.CHART_WEEKS:
        raise HTTPException(status_code=422, detail=f"weeks debe ser uno de {strength.CHART_WEEKS}")
    rep = await asyncio.to_thread(_strength, _db_path(), weeks)
    if rep is None:
        raise HTTPException(
            status_code=404,
            detail="no hay precios de ETFs: correr `uv run python -m marea.data.ingest etf`",
        )
    age = (datetime.now(UTC).date() - date.fromisoformat(rep["as_of"])).days
    rep["age_days"] = age
    rep["stale"] = age > 4  # un fin de semana largo son 3-4 días sin sesión
    return rep


def _artifacts_dir() -> Path:
    return ARTIFACTS


def ranking_verdict_from(art: dict[str, Any]) -> dict[str, Any]:
    """Lo que el panel muestra donde iría un pronóstico: el veredicto de la M3."""
    p = art["primary"]
    v = p["verdict"]
    base = v["c2_best_baseline"]
    placebo = next((r for r in art.get("runs", []) if r.get("config", {}).get("placebo")), None)
    return {
        "artifact": art["run_id"],
        "prereg_commit": art["prereg_commit"],
        "viable": v["viable"],
        "reading": v["reading"],
        "test_from": p["test_from"],
        "test_to": p["test_to"],
        "n_test_dates": p["n_test_dates"],
        "horizon_weeks": p["config"]["horizon"],
        "n_etfs": len(p["tickers"]),
        "ic_mean": v["c1_ic_mean"],
        "ic_ci95": v["c1_ic_ci95"],
        "baseline": base,
        "baseline_ic_mean": p["baselines"][base]["ic_mean"],
        "paired_diff": v["c2_paired"]["diff_mean"],
        "paired_ci95": v["c2_paired"]["ci95"],
        "auc": v["prob_auc"],
        "placebo_ic_mean": placebo["model"]["ic_mean"] if placebo else None,
        "liquidity_contribution": art.get("liquidity_contribution"),
        "ic_by_spy_sign": p.get("ic_by_spy_sign"),
    }


@router.get("/macro/ranking-verdict")
async def ranking_verdict() -> dict[str, Any]:
    def read() -> dict[str, Any] | None:
        files = sorted(_artifacts_dir().glob("marea-ranking-*.json"))
        return json.loads(files[-1].read_text(encoding="utf-8")) if files else None

    art = await asyncio.to_thread(read)
    if art is None:
        raise HTTPException(status_code=404, detail="no hay artefacto marea-ranking")
    return ranking_verdict_from(art)
