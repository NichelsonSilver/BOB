"""Bot configuration presets — save/load reusable bot templates."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from bob.db.models import BotPreset
from bob.db.session import get_session

router = APIRouter(prefix="/api/presets", tags=["presets"])


class PresetCreate(BaseModel):
    name: str
    config: dict[str, Any]
    source: str = "manual"
    notes: str | None = None


def _row_to_dict(row: BotPreset) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "symbol": row.symbol,
        "direction": row.direction,
        "mode": row.mode,
        "source": row.source,
        "notes": row.notes,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "config": json.loads(row.config_json),
    }


@router.get("")
async def list_presets() -> list[dict[str, Any]]:
    with get_session() as session:
        rows = session.exec(select(BotPreset).order_by(BotPreset.updated_at.desc())).all()
        return [_row_to_dict(r) for r in rows]


@router.get("/{name}")
async def get_preset(name: str) -> dict[str, Any]:
    with get_session() as session:
        row = session.exec(select(BotPreset).where(BotPreset.name == name)).first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Preset {name!r} not found")
        return _row_to_dict(row)


@router.post("")
async def save_preset(req: PresetCreate) -> dict[str, Any]:
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="name requerido")
    cfg = req.config or {}
    symbol = str(cfg.get("symbol") or "")
    direction = str(cfg.get("direction") or "")
    mode = str(cfg.get("mode") or "paper")
    if not symbol or not direction:
        raise HTTPException(
            status_code=400,
            detail="config debe incluir symbol y direction",
        )

    now = datetime.now(timezone.utc)
    with get_session() as session:
        existing = session.exec(
            select(BotPreset).where(BotPreset.name == req.name)
        ).first()
        if existing is None:
            row = BotPreset(
                name=req.name,
                symbol=symbol,
                direction=direction,
                mode=mode,
                config_json=json.dumps(cfg),
                source=req.source,
                notes=req.notes,
            )
            session.add(row)
        else:
            row = existing
            row.symbol = symbol
            row.direction = direction
            row.mode = mode
            row.config_json = json.dumps(cfg)
            row.source = req.source
            row.notes = req.notes
            row.updated_at = now
        session.commit()
        session.refresh(row)
        return _row_to_dict(row)


@router.delete("/{name}")
async def delete_preset(name: str) -> dict[str, Any]:
    with get_session() as session:
        row = session.exec(select(BotPreset).where(BotPreset.name == name)).first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Preset {name!r} not found")
        session.delete(row)
        session.commit()
        return {"status": "deleted", "name": name}
