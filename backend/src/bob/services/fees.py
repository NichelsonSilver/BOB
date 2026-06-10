"""Fee aggregation helpers."""

from __future__ import annotations

from decimal import Decimal

from sqlmodel import Session, select

from bob.db.models import FillRecord


def total_fees_paid(session: Session, bot_id: str | None = None) -> Decimal:
    """Sum of fees across all fills (maker rebates show as negative)."""
    stmt = select(FillRecord)
    if bot_id:
        stmt = stmt.where(FillRecord.bot_id == bot_id)
    return sum(
        (Decimal(f.fee) for f in session.exec(stmt).all()), Decimal("0")
    )


def fees_by_bot(session: Session) -> dict[str, Decimal]:
    """Map bot_id -> total fees paid."""
    fills = session.exec(select(FillRecord)).all()
    out: dict[str, Decimal] = {}
    for f in fills:
        out[f.bot_id] = out.get(f.bot_id, Decimal("0")) + Decimal(f.fee)
    return out
