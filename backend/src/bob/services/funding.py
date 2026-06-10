"""Funding rate tracker.

Funding rates are fetched from GRVT REST when the dashboard requests them.
Persisting every 8h snapshot is overkill for v1 — we expose a pass-through
that the API layer calls, and later we can cache if needed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


async def latest_funding(grvt_client: Any, symbol: str) -> dict:
    """Return latest funding rate + the next funding time for a symbol."""
    ticker = await grvt_client.fetch_ticker(symbol)
    if not ticker:
        return {}
    return {
        "symbol": symbol,
        "funding_rate": ticker.get("funding_rate"),
        "funding_rate_8h_curr": ticker.get("funding_rate_8h_curr"),
        "funding_rate_8h_avg": ticker.get("funding_rate_8h_avg"),
        "next_funding_time": ticker.get("next_funding_time"),
    }


def estimate_funding_cost(
    notional: Decimal, rate_8h: Decimal, hours: int = 8
) -> Decimal:
    """Estimate USDT funding payment for a given notional over an interval.

    Positive = user pays. Negative = user receives. `rate_8h` is expressed
    per 8h window (GRVT semantics) — for other windows we scale linearly.
    """
    if hours == 0:
        return Decimal("0")
    return notional * rate_8h * Decimal(hours) / Decimal(8)
