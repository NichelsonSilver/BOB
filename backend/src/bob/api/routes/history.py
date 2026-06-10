"""History, PnL, fills, equity curve."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from sqlmodel import select

from bob.db.models import Bot, FillRecord, Order
from bob.db.session import get_session
from bob.services import fees as fees_service
from bob.services import pnl as pnl_service

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("/fills")
def list_fills(
    bot_id: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """Paginated list of fills, optionally filtered."""
    with get_session() as session:
        stmt = select(FillRecord)
        if bot_id:
            stmt = stmt.where(FillRecord.bot_id == bot_id)
        if symbol:
            stmt = stmt.join(Bot, Bot.bot_id == FillRecord.bot_id).where(
                Bot.symbol == symbol
            )
        if since:
            stmt = stmt.where(FillRecord.filled_at >= since)
        if until:
            stmt = stmt.where(FillRecord.filled_at < until)
        stmt = stmt.order_by(FillRecord.filled_at.desc()).offset(offset).limit(limit)

        fills = session.exec(stmt).all()
        return [
            {
                "bot_id": f.bot_id,
                "client_order_id": f.client_order_id,
                "level_index": f.level_index,
                "side": f.side,
                "price": f.price,
                "quantity": f.quantity,
                "fee": f.fee,
                "pnl": f.pnl,
                "mode": f.mode,
                "filled_at": f.filled_at.isoformat(),
            }
            for f in fills
        ]


@router.get("/orders")
def list_orders(
    bot_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
) -> list[dict[str, Any]]:
    """Listing of orders (open/filled/cancelled) for audit trail."""
    with get_session() as session:
        stmt = select(Order)
        if bot_id:
            stmt = stmt.where(Order.bot_id == bot_id)
        if status:
            stmt = stmt.where(Order.status == status)
        stmt = stmt.order_by(Order.created_at.desc()).limit(limit)
        return [
            {
                "bot_id": o.bot_id,
                "client_order_id": o.client_order_id,
                "exchange_order_id": o.exchange_order_id,
                "level_index": o.level_index,
                "side": o.side,
                "price": o.price,
                "quantity": o.quantity,
                "status": o.status,
                "mode": o.mode,
                "created_at": o.created_at.isoformat(),
                "filled_at": o.filled_at.isoformat() if o.filled_at else None,
            }
            for o in session.exec(stmt).all()
        ]


@router.get("/pnl")
def get_pnl(
    bot_id: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
) -> dict[str, Any]:
    """Aggregated PnL / fees / volume for a window."""
    with get_session() as session:
        breakdown = pnl_service.aggregate_pnl(
            session, bot_id=bot_id, symbol=symbol, since=since, until=until
        )
        return breakdown.as_dict()


@router.get("/equity-curve")
def get_equity_curve(bot_id: str | None = Query(default=None)) -> list[dict[str, Any]]:
    """Time series of cumulative realized PnL."""
    with get_session() as session:
        return [
            {"timestamp": ts.isoformat(), "cumulative_pnl": str(pnl)}
            for ts, pnl in pnl_service.equity_curve(session, bot_id=bot_id)
        ]


@router.get("/pnl/daily")
def get_daily_pnl(bot_id: str | None = Query(default=None)) -> list[dict[str, Any]]:
    with get_session() as session:
        buckets = pnl_service.daily_pnl(session, bot_id=bot_id)
    return [
        {"date": d.isoformat(), **b.as_dict()} for d, b in sorted(buckets.items())
    ]


@router.get("/fees")
def get_fees(bot_id: str | None = Query(default=None)) -> dict[str, Any]:
    with get_session() as session:
        if bot_id:
            return {
                "bot_id": bot_id,
                "total_fees": str(fees_service.total_fees_paid(session, bot_id)),
            }
        by_bot = fees_service.fees_by_bot(session)
        return {
            "total_fees": str(sum(by_bot.values(), start=type(next(iter(by_bot.values()), 0))(0)))
            if by_bot
            else "0",
            "by_bot": {k: str(v) for k, v in by_bot.items()},
        }
