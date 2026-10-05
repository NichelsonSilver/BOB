"""Calculadora de tamaño de posición — página Riesgo.

Pura aritmética sobre lo que manda el usuario: no lee la DB ni el analista,
así que responde aunque el analista todavía no haya emitido. Se calcula en el
servidor por la misma razón que el slider de leverage (ver `analysis.py`): la
fórmula de liquidación, con sus tiers de MMR, vive en un solo lugar.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from bob.models.sizing import DEFAULT_MIN_NOTIONAL, DEFAULT_QTY_STEP, size_position
from bob.venues import get_venue

router = APIRouter(tags=["risk"])


@router.get("/risk/sizing")
async def sizing(
    capital: float = Query(..., gt=0.0),
    risk_pct: float = Query(..., gt=0.0, lt=100.0, description="riesgo por trade, en %"),
    entry: float = Query(..., gt=0.0),
    stop: float = Query(..., gt=0.0),
    leverage: float = Query(1.0, gt=0.0, le=125.0),
    target: float | None = Query(None, gt=0.0),
    maker_entry: bool = Query(False),
    venue: str | None = Query(None),
    qty_step: float = Query(DEFAULT_QTY_STEP, gt=0.0),
    min_notional: float = Query(DEFAULT_MIN_NOTIONAL, ge=0.0),
) -> dict[str, Any]:
    try:
        profile = get_venue(venue)
        result = size_position(
            capital,
            risk_pct / 100.0,
            entry,
            stop,
            leverage,
            target=target,
            venue=profile,
            maker_entry=maker_entry,
            qty_step=qty_step,
            min_notional=min_notional,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"venue": profile.key, "venue_name": profile.name, **result.as_dict()}
