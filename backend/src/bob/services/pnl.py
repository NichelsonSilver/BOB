"""PnL aggregation over persisted fills.

Realized PnL comes from the bot's own bookkeeping (engine.on_fill updates
GridState.realized_pnl). This module rebuilds the same number from DB rows
for the UI (so the frontend can page over trades without loading every
bot into memory) and exposes per-day / per-symbol breakdowns.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlmodel import Session, select

from bob.db.models import Bot, FillRecord


@dataclass
class PnlBreakdown:
    realized_pnl: Decimal
    total_fees: Decimal
    gross_volume: Decimal
    fill_count: int

    def as_dict(self) -> dict:
        return {
            "realized_pnl": str(self.realized_pnl),
            "total_fees": str(self.total_fees),
            "gross_volume": str(self.gross_volume),
            "fill_count": self.fill_count,
        }


def _fills_query(
    session: Session,
    bot_id: str | None = None,
    symbol: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
):
    stmt = select(FillRecord)
    if bot_id:
        stmt = stmt.where(FillRecord.bot_id == bot_id)
    if symbol:
        stmt = stmt.join(Bot, Bot.bot_id == FillRecord.bot_id).where(Bot.symbol == symbol)
    if since:
        stmt = stmt.where(FillRecord.filled_at >= since)
    if until:
        stmt = stmt.where(FillRecord.filled_at < until)
    return stmt


def _pnl_per_grid_cycle(bot: Bot, level_step_lookup: dict[str, list[Decimal]]) -> None:
    """Placeholder — per-cycle profit reconstruction done at ingestion time.

    Kept here for potential future use; current implementation sums
    realized_pnl persisted on Bot rows directly.
    """
    return None


def aggregate_pnl(
    session: Session,
    bot_id: str | None = None,
    symbol: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> PnlBreakdown:
    """Sum realized PnL and fees from FillRecord rows in a window."""
    fills = session.exec(_fills_query(session, bot_id, symbol, since, until)).all()

    # Realized PnL is stored on Bot rows (single source of truth); for filters
    # that include all bots, aggregate there. For filtered windows we recompute
    # fees / volume from fills.
    total_fees = sum((Decimal(f.fee) for f in fills), Decimal("0"))
    gross_volume = sum(
        (Decimal(f.price) * Decimal(f.quantity) for f in fills), Decimal("0")
    )
    realized = sum(
        (Decimal(f.pnl) for f in fills), Decimal("0")
    ) if any(Decimal(f.pnl) != 0 for f in fills) else Decimal("0")

    if realized == 0 and bot_id and since is None and until is None:
        bot = session.exec(select(Bot).where(Bot.bot_id == bot_id)).first()
        if bot:
            realized = Decimal(bot.realized_pnl)

    return PnlBreakdown(
        realized_pnl=realized,
        total_fees=total_fees,
        gross_volume=gross_volume,
        fill_count=len(fills),
    )


def equity_curve(
    session: Session, bot_id: str | None = None
) -> list[tuple[datetime, Decimal]]:
    """Return list of (timestamp, cumulative_realized_pnl) points."""
    stmt = _fills_query(session, bot_id=bot_id)
    fills = session.exec(stmt).all()
    fills.sort(key=lambda f: f.filled_at)

    cumulative = Decimal("0")
    curve: list[tuple[datetime, Decimal]] = []
    for f in fills:
        cumulative += Decimal(f.pnl)
        curve.append((f.filled_at, cumulative))
    return curve


def daily_pnl(
    session: Session, bot_id: str | None = None
) -> dict[date, PnlBreakdown]:
    """Group PnL by UTC date."""
    fills = session.exec(_fills_query(session, bot_id=bot_id)).all()
    buckets: dict[date, list[FillRecord]] = {}
    for f in fills:
        d = f.filled_at.astimezone(timezone.utc).date()
        buckets.setdefault(d, []).append(f)

    result: dict[date, PnlBreakdown] = {}
    for day, items in buckets.items():
        fees = sum((Decimal(f.fee) for f in items), Decimal("0"))
        vol = sum(
            (Decimal(f.price) * Decimal(f.quantity) for f in items), Decimal("0")
        )
        pnl = sum((Decimal(f.pnl) for f in items), Decimal("0"))
        result[day] = PnlBreakdown(
            realized_pnl=pnl,
            total_fees=fees,
            gross_volume=vol,
            fill_count=len(items),
        )
    return result
