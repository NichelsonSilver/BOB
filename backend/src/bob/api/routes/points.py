"""Airdrop points tracking routes (local estimates until phase 9)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from bob.db.session import get_session
from bob.services import points as points_service

router = APIRouter(prefix="/api/points", tags=["points"])


@router.get("")
def get_points() -> dict[str, Any]:
    """Aggregated local estimate of airdrop points across all bots."""
    with get_session() as session:
        summary = points_service.total_points(session)
    return summary.as_dict()


@router.get("/by-bot")
def get_points_by_bot() -> dict[str, dict[str, Any]]:
    with get_session() as session:
        buckets = points_service.points_by_bot(session)
    return {bot_id: s.as_dict() for bot_id, s in buckets.items()}
