"""Shared test fixtures.

The production engine points at the real SQLite file. For tests we swap
`_engine` to an in-memory DB and create tables fresh per test so there
is no cross-test state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from bob.db import session as session_mod
from bob.db.models import Bot, FillRecord, Order


@pytest.fixture
def in_memory_engine(monkeypatch):
    # StaticPool keeps a single shared connection so the threadpool-run
    # endpoint sees the same in-memory database as the test setup.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(session_mod, "_engine", engine)
    return engine


@pytest.fixture
def seed_fills(in_memory_engine):
    """Seed a small set of fills across two bots for aggregation tests."""
    ts = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    with Session(in_memory_engine) as s:
        s.add(Bot(
            bot_id="bot-a", symbol="BTC_USDT_Perp", direction="neutral",
            price_low="90000", price_high="100000", n_grids=10,
            investment_usdt="1000", realized_pnl="1.5",
        ))
        s.add(Bot(
            bot_id="bot-b", symbol="ETH_USDT_Perp", direction="long",
            price_low="3000", price_high="3500", n_grids=5,
            investment_usdt="500", realized_pnl="-0.25",
        ))
        fills = [
            FillRecord(
                bot_id="bot-a", client_order_id="bot-a-0-buy-0",
                level_index=0, side="buy", price="95000", quantity="0.001",
                fee="0.019", pnl="0", mode="live", filled_at=ts,
            ),
            FillRecord(
                bot_id="bot-a", client_order_id="bot-a-0-sell-1",
                level_index=1, side="sell", price="96000", quantity="0.001",
                fee="-0.0096", pnl="1.0", mode="live",
                filled_at=ts.replace(hour=13),
            ),
            FillRecord(
                bot_id="bot-b", client_order_id="bot-b-2-buy-0",
                level_index=2, side="buy", price="3200", quantity="0.05",
                fee="0.032", pnl="0", mode="paper",
                filled_at=ts.replace(hour=14),
            ),
        ]
        for f in fills:
            s.add(f)
        s.add(Order(
            bot_id="bot-a", client_order_id="bot-a-0-buy-0",
            level_index=0, side="buy", price="95000", quantity="0.001",
            status="filled", mode="live", created_at=ts,
        ))
        s.commit()
    return in_memory_engine


class _StubManager:
    """Stand-in for BotManager.list_all() in settings-route tests."""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    def list_all(self) -> list[dict]:
        return list(self._rows)


@pytest.fixture
def stub_manager_factory():
    return _StubManager


@pytest.fixture
def decimal_close():
    def _close(a, b, tol="0.0001"):
        return abs(Decimal(a) - Decimal(b)) <= Decimal(tol)
    return _close
