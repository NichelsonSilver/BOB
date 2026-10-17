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
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from marea import brief, i18n, industries, narrative, panorama, strength
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


def _bob_db() -> Path:
    return DB_PATH.parent / "bob.db"


def _universe_json() -> Path:
    return DB_PATH.parent / "artifacts" / "universe-1d.json"


@router.get("/macro/liquidity")
async def liquidity(lang: Literal["es", "en"] = Query("es")) -> dict[str, Any]:
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
    return i18n.liquidity(rep, lang)


def _strength(path: Path, weeks: int) -> dict[str, Any] | None:
    if not path.exists():
        return None
    conn = store.connect(path)
    try:
        return strength.from_db(conn, weeks)
    finally:
        conn.close()


@router.get("/macro/strength")
async def class_strength(
    weeks: int = Query(26), lang: Literal["es", "en"] = Query("es")
) -> dict[str, Any]:
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
    return i18n.strength(rep, lang)


def _industries(path: Path, weeks: int) -> dict[str, Any] | None:
    if not path.exists():
        return None
    conn = store.connect(path)
    try:
        return industries.from_db(conn, weeks)
    finally:
        conn.close()


@router.get("/macro/industries")
async def industry_growth(
    weeks: int = Query(26), lang: Literal["es", "en"] = Query("es")
) -> dict[str, Any]:
    """Crecimiento por industria: sectores de EE.UU. y categorías DeFi (M7).

    Cada mitad puede venir en `null` sin tirar la otra: los sectores dependen
    de Tiingo y DeFi de las fotos diarias de DefiLlama.
    """
    if weeks not in industries.CHART_WEEKS:
        raise HTTPException(
            status_code=422, detail=f"weeks debe ser uno de {industries.CHART_WEEKS}"
        )
    rep = await asyncio.to_thread(_industries, _db_path(), weeks)
    if rep is None:
        raise HTTPException(status_code=404, detail="no hay base de MAREA")
    today = datetime.now(UTC).date()
    if rep["equity"] is not None:
        age = (today - date.fromisoformat(rep["equity"]["as_of"])).days
        rep["equity"]["age_days"] = age
        rep["equity"]["stale"] = age > 4  # mismo criterio que /macro/strength
    if rep["defi"] is not None:
        age = (today - date.fromisoformat(rep["defi"]["as_of"])).days
        rep["defi"]["age_days"] = age
        rep["defi"]["stale"] = age > 1  # foto diaria: un día sin correr ya es hueco
    return i18n.industries(rep, lang)


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
async def ranking_verdict(lang: Literal["es", "en"] = Query("es")) -> dict[str, Any]:
    def read() -> dict[str, Any] | None:
        files = sorted(_artifacts_dir().glob("marea-ranking-*.json"))
        return json.loads(files[-1].read_text(encoding="utf-8")) if files else None

    art = await asyncio.to_thread(read)
    if art is None:
        raise HTTPException(status_code=404, detail="no hay artefacto marea-ranking")
    return i18n.ranking_verdict(ranking_verdict_from(art), lang)


#: El panorama cuesta ~5 s (la amplitud recorre el panel de 652 perpetuos) y sus
#: datos cambian una vez al día. Se cachea por proceso.
PANORAMA_TTL_S = 900.0
_panorama_cache: dict[str, Any] = {}
_panorama_lock = asyncio.Lock()


def _panorama(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    conn = store.connect(path)
    try:
        return panorama.build(conn, _bob_db(), _universe_json())
    finally:
        conn.close()


@router.get("/macro/panorama")
async def macro_panorama() -> dict[str, Any]:
    """Ciclo y cripto de la M5: recesión, Risk BTC, amplitud, funding, opciones,
    stablecoins. Cada bloque con su evidencia y su fecha; uno caído trae `error`."""
    async with _panorama_lock:
        hit = _panorama_cache.get("data")
        if hit is not None and time.monotonic() - _panorama_cache["at"] < PANORAMA_TTL_S:
            return dict(hit)
        data = await asyncio.to_thread(_panorama, _db_path())
        if data is None:
            raise HTTPException(
                status_code=404,
                detail="no hay base de MAREA: correr `uv run python -m marea.data.ingest todo`",
            )
        _panorama_cache.update(data=data, at=time.monotonic())
        return dict(data)


def _freshness(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    conn = store.connect(path)
    try:
        return brief.freshness(conn, datetime.now(UTC).date())
    finally:
        conn.close()


@router.get("/macro/freshness")
async def freshness() -> dict[str, Any]:
    """Antigüedad de cada fuente de MAREA, para el encabezado del panel."""
    rows = await asyncio.to_thread(_freshness, _db_path())
    if rows is None:
        raise HTTPException(status_code=404, detail="no hay base de MAREA")
    ages = [r["age_days"] for r in rows if r["age_days"] is not None]
    return {
        "sources": rows,
        "max_age_days": max(ages) if ages else None,
        "stale": [r["source"] for r in rows if r["stale"]],
    }


@router.get("/macro/panel-status")
async def panel_status() -> dict[str, Any]:
    """Estado OPERATIVO del forward del panel diario (Fase 10-bis).

    Fechas válidas, backfill, huecos y ritmo. **No lee ni devuelve el IC**: la
    Enmienda 1 prohíbe mirarlo antes del análisis interino.
    """
    st = await asyncio.to_thread(brief.panel_forward_status, _bob_db(), datetime.now(UTC).date())
    if st is None:
        raise HTTPException(status_code=404, detail="no hay registros forward del panel")
    return st


@router.get("/macro/narrative")
async def narrative_recent(days: int = Query(14, ge=1, le=365)) -> dict[str, Any]:
    """Últimas entradas del registro de narrativa (capa 4: peso bajo)."""
    entries = await asyncio.to_thread(lambda: narrative.recent(narrative.read(), days))
    return {"days": days, "entries": [e.model_dump(mode="json") for e in entries]}
