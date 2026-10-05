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

from fastapi import APIRouter, HTTPException

from marea.config import DB_PATH
from marea.data import store

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
