"""GRVT airdrop points tracker.

For v1 we derive local estimates from persisted fills:
- volume points ≈ gross volume / 10 (placeholder until Phase 9 wires the
  official GRVT points API)
- maker rebates count toward the ecosystem score, so we surface them too

The real per-epoch numbers come from GRVT's points API and will replace
these estimates in phase 9 — callers should treat these as best-effort.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlmodel import Session, select

from bob.db.models import FillRecord


VOLUME_PER_POINT = Decimal("10")


@dataclass
class PointsSummary:
    gross_volume: Decimal
    maker_rebates: Decimal  # positive = rebates earned
    taker_fees: Decimal  # positive = fees paid
    fill_count: int
    estimated_points: Decimal

    def as_dict(self) -> dict:
        return {
            "gross_volume": str(self.gross_volume),
            "maker_rebates": str(self.maker_rebates),
            "taker_fees": str(self.taker_fees),
            "fill_count": self.fill_count,
            "estimated_points": str(self.estimated_points),
        }


def _summarize(fills: list[FillRecord]) -> PointsSummary:
    volume = Decimal("0")
    rebates = Decimal("0")
    taker = Decimal("0")
    for f in fills:
        volume += Decimal(f.price) * Decimal(f.quantity)
        fee = Decimal(f.fee)
        if fee < 0:
            rebates += -fee
        else:
            taker += fee
    points = (volume / VOLUME_PER_POINT).quantize(Decimal("0.01"))
    return PointsSummary(
        gross_volume=volume,
        maker_rebates=rebates,
        taker_fees=taker,
        fill_count=len(fills),
        estimated_points=points,
    )


def total_points(session: Session) -> PointsSummary:
    fills = session.exec(select(FillRecord)).all()
    return _summarize(fills)


def points_by_bot(session: Session) -> dict[str, PointsSummary]:
    fills = session.exec(select(FillRecord)).all()
    by_bot: dict[str, list[FillRecord]] = {}
    for f in fills:
        by_bot.setdefault(f.bot_id, []).append(f)
    return {bot_id: _summarize(items) for bot_id, items in by_bot.items()}
