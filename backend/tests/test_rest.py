"""Unit tests for grvt/rest.py — cloid mapping and wrapper behavior.

No network: the GrvtCcxtPro client is stubbed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from bob.grvt.rest import (
    GrvtRestClient,
    PlacedOrder,
    cloid_str_to_int,
)


# ────────────────────── cloid mapping ──────────────────────


def test_cloid_in_machine_range():
    """Every generated id must land in [2^63, 2^64 - 1]."""
    for sample in [
        "bot-0-buy-0",
        "bot-499-sell-987654321",
        "very-long-bot-id-42-buy-12345",
        "x",
    ]:
        n = cloid_str_to_int(sample)
        assert (1 << 63) <= n < (1 << 64)


def test_cloid_deterministic():
    assert cloid_str_to_int("foo") == cloid_str_to_int("foo")


def test_cloid_distinct_for_distinct_input():
    samples = [f"bot-{i}-buy-0" for i in range(500)]
    ids = {cloid_str_to_int(s) for s in samples}
    assert len(ids) == len(samples)  # no collisions at this scale


# ────────────────────── stub client ──────────────────────


class _StubClient:
    """Captures calls made by GrvtRestClient for assertions."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.cancelled: list[dict[str, Any]] = []
        self.cancel_all_calls: int = 0
        self.open_orders_calls: list[str | None] = []
        self._next_order_id: int = 1000

    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: str,
        price: str,
        params: dict,
    ) -> dict:
        self.created.append(
            {
                "symbol": symbol,
                "order_type": order_type,
                "side": side,
                "amount": amount,
                "price": price,
                "params": dict(params),
            }
        )
        oid = str(self._next_order_id)
        self._next_order_id += 1
        return {
            "metadata": {
                "client_order_id": params.get("client_order_id"),
                "order_id": oid,
            },
            "order_id": oid,
        }

    async def cancel_order(self, id: Any = None, params: dict = {}) -> bool:
        self.cancelled.append({"id": id, "params": dict(params)})
        return True

    async def cancel_all_orders(self, params: dict = {}) -> bool:
        self.cancel_all_calls += 1
        return True

    async def fetch_open_orders(self, symbol: str | None = None) -> list[dict]:
        self.open_orders_calls.append(symbol)
        return []


# ────────────────────── wrapper behavior ──────────────────────


@pytest.mark.asyncio
async def test_place_order_registers_mapping_and_posts_post_only():
    stub = _StubClient()
    rc = GrvtRestClient(stub)  # type: ignore[arg-type]

    placed = await rc.place_order(
        symbol="BTC_USDT_Perp",
        side="buy",
        price=Decimal("95000.1"),
        quantity=Decimal("0.001"),
        internal_cloid="bot1-3-buy-42",
    )

    assert isinstance(placed, PlacedOrder)
    assert placed.internal_cloid == "bot1-3-buy-42"
    assert placed.exchange_cloid == str(cloid_str_to_int("bot1-3-buy-42"))
    assert placed.order_id == "1000"

    # POST_ONLY by default
    assert stub.created[0]["params"]["post_only"] is True
    assert stub.created[0]["params"]["time_in_force"] == "GOOD_TILL_TIME"
    assert stub.created[0]["params"]["client_order_id"] == placed.exchange_cloid
    assert stub.created[0]["amount"] == "0.001"
    assert stub.created[0]["price"] == "95000.1"
    assert stub.created[0]["side"] == "buy"

    # Mapping is bidirectional
    assert rc.resolve_cloid(placed.exchange_cloid) == "bot1-3-buy-42"


@pytest.mark.asyncio
async def test_cancel_by_internal_cloid_uses_mapping():
    stub = _StubClient()
    rc = GrvtRestClient(stub)  # type: ignore[arg-type]

    await rc.place_order(
        symbol="BTC_USDT_Perp",
        side="sell",
        price=Decimal("100000"),
        quantity=Decimal("0.01"),
        internal_cloid="bot1-7-sell-0",
    )
    ok = await rc.cancel_by_internal_cloid("bot1-7-sell-0")
    assert ok is True
    assert len(stub.cancelled) == 1
    assert stub.cancelled[0]["params"]["client_order_id"] == str(
        cloid_str_to_int("bot1-7-sell-0")
    )


@pytest.mark.asyncio
async def test_cancel_unknown_cloid_returns_false_without_calling_exchange():
    stub = _StubClient()
    rc = GrvtRestClient(stub)  # type: ignore[arg-type]
    ok = await rc.cancel_by_internal_cloid("never-registered")
    assert ok is False
    assert stub.cancelled == []


@pytest.mark.asyncio
async def test_cancel_all_delegates_with_perpetual_kind():
    stub = _StubClient()
    rc = GrvtRestClient(stub)  # type: ignore[arg-type]
    await rc.cancel_all()
    assert stub.cancel_all_calls == 1


@pytest.mark.asyncio
async def test_resolve_cloid_returns_none_for_unknown():
    stub = _StubClient()
    rc = GrvtRestClient(stub)  # type: ignore[arg-type]
    assert rc.resolve_cloid("99999999") is None
