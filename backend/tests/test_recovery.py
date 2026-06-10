"""Persistence + rehydrate tests for Phase 9."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest
from sqlmodel import Session, select

from bob.db.models import Bot as BotRow
from bob.grid.bot import GridBot
from bob.grid.engine import BotConfig, Fill, OrderSide
from bob.grid.manager import BotManager
from bob.grid.state_machine import BotState


def _make_config() -> BotConfig:
    return BotConfig(
        symbol="BTC_USDT_Perp",
        direction="long",
        price_low=Decimal("100"),
        price_high=Decimal("110"),
        n_grids=4,
        investment_usdt=Decimal("200"),
        leverage=5,
        spacing="arithmetic",
        tick_size=Decimal("0.1"),
        lot_size=Decimal("0.001"),
    )


class _StubMarketHub:
    """Minimal hub that produces an asyncio.Queue per subscribe call."""

    def __init__(self) -> None:
        self.subs: list[tuple[str, asyncio.Queue]] = []
        self.unsubs: list[tuple[str, str]] = []

    async def subscribe_mini_ticker(self, symbol: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subs.append((symbol, q))
        return q

    def unsubscribe(self, stream: str, symbol: str, queue: asyncio.Queue) -> None:
        self.unsubs.append((stream, symbol))


def test_grid_state_persist_roundtrip(in_memory_engine):
    """A bot with some grid activity should round-trip through the DB."""
    config = _make_config()
    bot = GridBot(bot_id="bot-roundtrip", config=config, mode="paper")
    bot.state_machine.transition(BotState.STARTING)

    # Simulate initial state the engine would have produced:
    _, state = bot.engine.compute_initial_orders(Decimal("105"), bot.bot_id)
    bot.grid_state = state
    # Apply a BUY fill at level 0 to create filled_buys + cascade sell
    fill = Fill(
        client_order_id=state.live_orders[0],
        level_index=0,
        side=OrderSide.BUY,
        price=bot.engine.levels[0],
        quantity=bot.engine.qty_per_grid,
        fee=Decimal("0.01"),
    )
    bot.engine.on_fill(fill, bot.grid_state, bot.bot_id, timestamp_ms=1)
    bot.state_machine.transition(BotState.RUNNING)
    bot._exchange_order_ids[1] = "exch-123"

    bot._persist_bot()

    with Session(in_memory_engine) as session:
        row = session.exec(select(BotRow).where(BotRow.bot_id == "bot-roundtrip")).one()

    assert json.loads(row.filled_buys_json) == {"0": True}
    # After the BUY cascade: level 0 consumed, level 1 sell remains live.
    live = json.loads(row.live_orders_json)
    assert "1" in live
    assert "0" not in live
    assert json.loads(row.exchange_ids_json) == {"1": "exch-123"}

    # Reconstruct and verify invariants survive
    restored = GridBot.from_db(row)
    assert restored.state_machine.state == BotState.RUNNING
    assert restored.grid_state is not None
    assert restored.grid_state.filled_buys == {0: True}
    assert 0 not in restored.grid_state.live_orders
    assert 1 in restored.grid_state.live_orders
    assert restored.grid_state.live_orders == bot.grid_state.live_orders
    assert restored._exchange_order_ids == {1: "exch-123"}
    assert restored.grid_state.realized_pnl == bot.grid_state.realized_pnl
    assert restored.grid_state.grid_trades_count == bot.grid_state.grid_trades_count


def test_from_db_leaves_stopped_bots_idle(in_memory_engine):
    config = _make_config()
    bot = GridBot(bot_id="bot-stopped", config=config, mode="paper")
    bot.grid_state = bot.engine.compute_initial_orders(Decimal("105"), bot.bot_id)[1]
    bot.state_machine.transition(BotState.STARTING)
    bot.state_machine.transition(BotState.RUNNING)
    bot.state_machine.transition(BotState.STOPPING)
    bot.state_machine.transition(BotState.STOPPED)
    bot._persist_bot(stopped=True)

    with Session(in_memory_engine) as session:
        row = session.exec(select(BotRow).where(BotRow.bot_id == "bot-stopped")).one()

    restored = GridBot.from_db(row)
    # Stopped rows should not spin back up to RUNNING automatically.
    assert restored.state_machine.state == BotState.IDLE


@pytest.mark.asyncio
async def test_rehydrate_from_db_restores_running_bots(in_memory_engine):
    # Seed a RUNNING bot row directly
    state_payload = {
        "filled_buys": {"0": True},
        "live_orders": {"1": "bot-rehydrate-1-sell-0"},
    }
    with Session(in_memory_engine) as session:
        session.add(
            BotRow(
                bot_id="bot-rehydrate",
                symbol="BTC_USDT_Perp",
                direction="long",
                mode="paper",
                state="running",
                price_low="100",
                price_high="110",
                n_grids=4,
                investment_usdt="200",
                leverage=5,
                spacing="arithmetic",
                tick_size="0.1",
                lot_size="0.001",
                maker_fee="0.0002",
                realized_pnl="1.5",
                grid_trades_count=1,
                entry_price="105",
                filled_buys_json=json.dumps(state_payload["filled_buys"]),
                filled_sells_json="{}",
                live_orders_json=json.dumps(state_payload["live_orders"]),
                exchange_ids_json="{}",
            )
        )
        session.commit()

    hub = _StubMarketHub()
    manager = BotManager(market_data_hub=hub)  # type: ignore[arg-type]
    rehydrated = await manager.rehydrate_from_db()

    assert rehydrated == ["bot-rehydrate"]
    bot = manager.get_bot("bot-rehydrate")
    assert bot is not None
    assert bot.state_machine.state == BotState.RUNNING
    assert bot.grid_state is not None
    assert bot.grid_state.filled_buys == {0: True}
    assert bot.grid_state.live_orders == {1: "bot-rehydrate-1-sell-0"}
    assert bot.grid_state.realized_pnl == Decimal("1.5")
    assert ("BTC_USDT_Perp", hub.subs[0][1]) == (hub.subs[0][0], hub.subs[0][1])

    # Clean up the task so pytest doesn't complain about unfinished tasks.
    task = manager._tasks["bot-rehydrate"]
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_rehydrate_skips_stopped_bots(in_memory_engine):
    with Session(in_memory_engine) as session:
        session.add(
            BotRow(
                bot_id="bot-dead",
                symbol="BTC_USDT_Perp",
                direction="long",
                mode="paper",
                state="stopped",
                price_low="100",
                price_high="110",
                n_grids=4,
                investment_usdt="200",
                leverage=5,
                spacing="arithmetic",
                tick_size="0.1",
                lot_size="0.001",
                maker_fee="0.0002",
            )
        )
        session.commit()

    hub = _StubMarketHub()
    manager = BotManager(market_data_hub=hub)  # type: ignore[arg-type]
    rehydrated = await manager.rehydrate_from_db()
    assert rehydrated == []
    assert manager.get_bot("bot-dead") is None
