"""Tests for grid/engine.py — the critical module.

Coverage target: ≥90%. Bugs here = money lost.
"""

from decimal import Decimal

import pytest

from bob.grid.engine import (
    Actions,
    BotConfig,
    CancelOrder,
    Fill,
    GridEngine,
    GridOrder,
    GridState,
    MarketState,
    OrderSide,
    OutOfRangeSignal,
)


def _make_config(**overrides) -> BotConfig:
    defaults = dict(
        symbol="BTC_USDT_Perp",
        direction="long",
        price_low=Decimal("90000"),
        price_high=Decimal("100000"),
        n_grids=10,
        investment_usdt=Decimal("1000"),
        leverage=3,
        spacing="arithmetic",
        tick_size=Decimal("0.1"),
        lot_size=Decimal("0.001"),
        maker_fee=Decimal("0.0002"),
    )
    defaults.update(overrides)
    return BotConfig(**defaults)


# ────────────────── Level generation via engine ──────────────────


class TestEngineLevels:
    def test_arithmetic_levels(self):
        engine = GridEngine(_make_config())
        assert len(engine.levels) == 11  # 10 grids + 1
        assert engine.levels[0] == Decimal("90000.0")
        assert engine.levels[-1] == Decimal("100000.0")

    def test_geometric_levels(self):
        engine = GridEngine(_make_config(spacing="geometric"))
        assert len(engine.levels) == 11
        assert engine.levels[0] == Decimal("90000.0")
        assert engine.levels[-1] == Decimal("100000.0")

    def test_2_grids(self):
        engine = GridEngine(_make_config(n_grids=2))
        assert len(engine.levels) == 3
        assert engine.levels == [
            Decimal("90000.0"),
            Decimal("95000.0"),
            Decimal("100000.0"),
        ]

    def test_qty_per_grid_positive(self):
        engine = GridEngine(_make_config())
        assert engine.qty_per_grid > 0


# ────────────────── Initial orders ──────────────────


class TestInitialOrders:
    def test_long_initial(self):
        engine = GridEngine(_make_config())
        current_price = Decimal("95000")
        actions, state = engine.compute_initial_orders(current_price, "bot1")

        buys = [o for o in actions.place if o.side == OrderSide.BUY]
        sells = [o for o in actions.place if o.side == OrderSide.SELL]

        # Price at 95000 -> split_idx=5 (levels: 90k,91k,...,95k,...,100k)
        # Buys at levels 0-5 (90k-95k), sells at 6-10 (96k-100k)
        assert len(buys) == 6  # levels 0-5
        assert len(sells) == 5  # levels 6-10
        assert all(b.price <= current_price for b in buys)
        assert all(s.price > current_price for s in sells)
        assert state.entry_price == current_price
        assert len(state.live_orders) == 11

    def test_short_initial(self):
        engine = GridEngine(_make_config(direction="short"))
        current_price = Decimal("95000")
        actions, state = engine.compute_initial_orders(current_price, "bot1")

        buys = [o for o in actions.place if o.side == OrderSide.BUY]
        sells = [o for o in actions.place if o.side == OrderSide.SELL]

        # Same placement as long for the grid
        assert len(buys) + len(sells) == 11
        assert len(state.live_orders) == 11

    def test_neutral_initial(self):
        engine = GridEngine(_make_config(direction="neutral"))
        current_price = Decimal("95000")
        actions, state = engine.compute_initial_orders(current_price, "bot1")

        buys = [o for o in actions.place if o.side == OrderSide.BUY]
        sells = [o for o in actions.place if o.side == OrderSide.SELL]

        assert len(buys) + len(sells) == 11
        assert len(state.live_orders) == 11

    def test_price_at_bottom(self):
        engine = GridEngine(_make_config())
        actions, state = engine.compute_initial_orders(Decimal("90000"), "bot1")
        buys = [o for o in actions.place if o.side == OrderSide.BUY]
        sells = [o for o in actions.place if o.side == OrderSide.SELL]
        # All at or below 90k are buys (just level 0), rest are sells
        assert len(buys) == 1
        assert len(sells) == 10

    def test_price_at_top(self):
        engine = GridEngine(_make_config())
        actions, state = engine.compute_initial_orders(Decimal("100000"), "bot1")
        buys = [o for o in actions.place if o.side == OrderSide.BUY]
        # All levels are buys since price >= highest level
        assert len(buys) == 11

    def test_client_order_ids_unique(self):
        engine = GridEngine(_make_config())
        actions, _ = engine.compute_initial_orders(Decimal("95000"), "bot1")
        cloids = [o.client_order_id for o in actions.place]
        assert len(cloids) == len(set(cloids))

    def test_client_order_id_format(self):
        engine = GridEngine(_make_config())
        actions, _ = engine.compute_initial_orders(Decimal("95000"), "mybot")
        for o in actions.place:
            parts = o.client_order_id.split("-")
            assert parts[0] == "mybot"
            assert parts[2] in ("buy", "sell")


# ────────────────── Fill cascade ──────────────────


class TestFillCascade:
    def test_buy_fill_creates_sell(self):
        engine = GridEngine(_make_config())
        actions, state = engine.compute_initial_orders(Decimal("95000"), "bot1")

        # Simulate buy fill at level 3 (93000)
        fill = Fill(
            client_order_id="bot1-3-buy-0",
            level_index=3,
            side=OrderSide.BUY,
            price=Decimal("93000"),
            quantity=engine.qty_per_grid,
        )
        cascade = engine.on_fill(fill, state)

        assert len(cascade.place) == 1
        sell = cascade.place[0]
        assert sell.side == OrderSide.SELL
        assert sell.level_index == 4
        assert sell.price == engine.levels[4]

    def test_sell_fill_creates_buy(self):
        engine = GridEngine(_make_config())
        actions, state = engine.compute_initial_orders(Decimal("95000"), "bot1")

        fill = Fill(
            client_order_id="bot1-7-sell-0",
            level_index=7,
            side=OrderSide.SELL,
            price=Decimal("97000"),
            quantity=engine.qty_per_grid,
        )
        cascade = engine.on_fill(fill, state)

        assert len(cascade.place) == 1
        buy = cascade.place[0]
        assert buy.side == OrderSide.BUY
        assert buy.level_index == 6
        assert buy.price == engine.levels[6]

    def test_buy_at_top_no_sell_above(self):
        """Buy fill at highest level -> no sell to place above."""
        engine = GridEngine(_make_config())
        actions, state = engine.compute_initial_orders(Decimal("100000"), "bot1")

        fill = Fill(
            client_order_id="bot1-10-buy-0",
            level_index=10,  # highest level
            side=OrderSide.BUY,
            price=engine.levels[10],
            quantity=engine.qty_per_grid,
        )
        cascade = engine.on_fill(fill, state)
        assert len(cascade.place) == 0

    def test_sell_at_bottom_no_buy_below(self):
        """Sell fill at lowest level -> no buy to place below."""
        engine = GridEngine(_make_config(direction="short"))
        _, state = engine.compute_initial_orders(Decimal("90000"), "bot1")

        fill = Fill(
            client_order_id="bot1-0-sell-0",
            level_index=0,
            side=OrderSide.SELL,
            price=engine.levels[0],
            quantity=engine.qty_per_grid,
        )
        cascade = engine.on_fill(fill, state)
        assert len(cascade.place) == 0

    def test_pnl_on_sell_fill(self):
        """Sell at level_i after buy at level_{i-1} -> positive realized PnL."""
        engine = GridEngine(_make_config())
        _, state = engine.compute_initial_orders(Decimal("95000"), "bot1")

        # First: buy fills at level 3
        buy_fill = Fill(
            client_order_id="bot1-3-buy-0",
            level_index=3,
            side=OrderSide.BUY,
            price=engine.levels[3],
            quantity=engine.qty_per_grid,
        )
        engine.on_fill(buy_fill, state)

        # Then: sell fills at level 4
        sell_fill = Fill(
            client_order_id="bot1-4-sell-100",
            level_index=4,
            side=OrderSide.SELL,
            price=engine.levels[4],
            quantity=engine.qty_per_grid,
            fee=Decimal("0.1"),
        )
        engine.on_fill(sell_fill, state)

        assert state.realized_pnl > 0
        assert state.grid_trades_count == 1

    def test_pnl_short_on_buy_fill(self):
        """Short direction: buy completing a grid cycle."""
        engine = GridEngine(_make_config(direction="short"))
        _, state = engine.compute_initial_orders(Decimal("95000"), "bot1")

        # Sell fills at level 7
        sell_fill = Fill(
            client_order_id="bot1-7-sell-0",
            level_index=7,
            side=OrderSide.SELL,
            price=engine.levels[7],
            quantity=engine.qty_per_grid,
        )
        engine.on_fill(sell_fill, state)

        # Buy fills at level 6
        buy_fill = Fill(
            client_order_id="bot1-6-buy-100",
            level_index=6,
            side=OrderSide.BUY,
            price=engine.levels[6],
            quantity=engine.qty_per_grid,
            fee=Decimal("0.1"),
        )
        engine.on_fill(buy_fill, state)

        assert state.realized_pnl > 0
        assert state.grid_trades_count == 1

    def test_volume_tracking(self):
        engine = GridEngine(_make_config())
        _, state = engine.compute_initial_orders(Decimal("95000"), "bot1")

        fill = Fill(
            client_order_id="bot1-3-buy-0",
            level_index=3,
            side=OrderSide.BUY,
            price=Decimal("93000"),
            quantity=Decimal("0.01"),
        )
        engine.on_fill(fill, state)
        assert state.total_volume == Decimal("93000") * Decimal("0.01")

    def test_multiple_cascades(self):
        """Simulate several grid cycles."""
        engine = GridEngine(_make_config())
        _, state = engine.compute_initial_orders(Decimal("95000"), "bot1")

        # Cycle 1: buy at 3, sell at 4
        engine.on_fill(
            Fill("c1", 3, OrderSide.BUY, engine.levels[3], engine.qty_per_grid),
            state,
        )
        engine.on_fill(
            Fill("c2", 4, OrderSide.SELL, engine.levels[4], engine.qty_per_grid),
            state,
        )

        # Cycle 2: buy at 3 again, sell at 4 again
        engine.on_fill(
            Fill("c3", 3, OrderSide.BUY, engine.levels[3], engine.qty_per_grid),
            state,
        )
        engine.on_fill(
            Fill("c4", 4, OrderSide.SELL, engine.levels[4], engine.qty_per_grid),
            state,
        )

        assert state.grid_trades_count == 2
        assert state.realized_pnl > 0

    def test_fill_removes_live_order(self):
        engine = GridEngine(_make_config())
        _, state = engine.compute_initial_orders(Decimal("95000"), "bot1")
        assert 3 in state.live_orders

        engine.on_fill(
            Fill("bot1-3-buy-0", 3, OrderSide.BUY, engine.levels[3], engine.qty_per_grid),
            state,
        )
        assert 3 not in state.live_orders
        assert 4 in state.live_orders  # new sell placed


# ────────────────── Out of range ──────────────────


class TestOutOfRange:
    def test_in_range(self):
        engine = GridEngine(_make_config())
        _, state = engine.compute_initial_orders(Decimal("95000"), "bot1")
        market = MarketState(current_price=Decimal("95000"))
        actions = engine.check_out_of_range(market, state)
        assert actions.out_of_range == OutOfRangeSignal.NONE
        assert not actions.should_pause
        assert not actions.should_close

    def test_above_range_pause(self):
        engine = GridEngine(_make_config(out_of_range_action="pause"))
        _, state = engine.compute_initial_orders(Decimal("95000"), "bot1")
        market = MarketState(current_price=Decimal("100001"))
        actions = engine.check_out_of_range(market, state)
        assert actions.out_of_range == OutOfRangeSignal.ABOVE
        assert actions.should_pause
        assert not actions.should_close
        assert len(actions.cancel) > 0

    def test_below_range_pause(self):
        engine = GridEngine(_make_config(out_of_range_action="pause"))
        _, state = engine.compute_initial_orders(Decimal("95000"), "bot1")
        market = MarketState(current_price=Decimal("89999"))
        actions = engine.check_out_of_range(market, state)
        assert actions.out_of_range == OutOfRangeSignal.BELOW
        assert actions.should_pause

    def test_above_range_close(self):
        engine = GridEngine(_make_config(out_of_range_action="close"))
        _, state = engine.compute_initial_orders(Decimal("95000"), "bot1")
        market = MarketState(current_price=Decimal("100001"))
        actions = engine.check_out_of_range(market, state)
        assert actions.should_close
        assert not actions.should_pause

    def test_below_range_close(self):
        engine = GridEngine(_make_config(out_of_range_action="close"))
        _, state = engine.compute_initial_orders(Decimal("95000"), "bot1")
        market = MarketState(current_price=Decimal("89999"))
        actions = engine.check_out_of_range(market, state)
        assert actions.should_close

    def test_at_boundary_still_in_range(self):
        engine = GridEngine(_make_config())
        _, state = engine.compute_initial_orders(Decimal("95000"), "bot1")
        # Exactly at price_high
        market = MarketState(current_price=Decimal("100000"))
        actions = engine.check_out_of_range(market, state)
        assert actions.out_of_range == OutOfRangeSignal.NONE

        # Exactly at price_low
        market = MarketState(current_price=Decimal("90000"))
        actions = engine.check_out_of_range(market, state)
        assert actions.out_of_range == OutOfRangeSignal.NONE


# ────────────────── Stop loss / Take profit ──────────────────


class TestStopLossTakeProfit:
    def test_no_sl_tp(self):
        engine = GridEngine(_make_config())
        _, state = engine.compute_initial_orders(Decimal("95000"), "bot1")
        market = MarketState(current_price=Decimal("80000"))
        actions = engine.check_stop_loss_take_profit(market, state)
        assert not actions.should_close

    def test_stop_loss_long(self):
        engine = GridEngine(_make_config(stop_loss_pct=Decimal("5")))
        _, state = engine.compute_initial_orders(Decimal("95000"), "bot1")
        # 5% drop from 95000 = 90250
        market = MarketState(current_price=Decimal("90000"))
        actions = engine.check_stop_loss_take_profit(market, state)
        assert actions.should_close
        assert any(c.reason == "stop_loss" for c in actions.cancel)

    def test_take_profit_long(self):
        engine = GridEngine(_make_config(take_profit_pct=Decimal("10")))
        _, state = engine.compute_initial_orders(Decimal("95000"), "bot1")
        # 10% rise from 95000 = 104500
        market = MarketState(current_price=Decimal("105000"))
        actions = engine.check_stop_loss_take_profit(market, state)
        assert actions.should_close
        assert any(c.reason == "take_profit" for c in actions.cancel)

    def test_stop_loss_short(self):
        """Short SL triggers on price RISE."""
        engine = GridEngine(
            _make_config(direction="short", stop_loss_pct=Decimal("5"))
        )
        _, state = engine.compute_initial_orders(Decimal("95000"), "bot1")
        market = MarketState(current_price=Decimal("100000"))
        actions = engine.check_stop_loss_take_profit(market, state)
        assert actions.should_close

    def test_take_profit_short(self):
        """Short TP triggers on price DROP."""
        engine = GridEngine(
            _make_config(direction="short", take_profit_pct=Decimal("10"))
        )
        _, state = engine.compute_initial_orders(Decimal("95000"), "bot1")
        market = MarketState(current_price=Decimal("85000"))
        actions = engine.check_stop_loss_take_profit(market, state)
        assert actions.should_close

    def test_no_entry_price(self):
        engine = GridEngine(_make_config(stop_loss_pct=Decimal("5")))
        state = GridState()
        market = MarketState(current_price=Decimal("80000"))
        actions = engine.check_stop_loss_take_profit(market, state)
        assert not actions.should_close


# ────────────────── Edge cases ──────────────────


class TestEdgeCases:
    def test_500_grids(self):
        engine = GridEngine(
            _make_config(
                n_grids=500,
                price_low=Decimal("80000"),
                price_high=Decimal("120000"),
                tick_size=Decimal("1"),
                investment_usdt=Decimal("50000"),
                leverage=10,
            )
        )
        assert len(engine.levels) <= 501
        assert engine.levels[0] == Decimal("80000")
        assert engine.levels[-1] == Decimal("120000")
        assert engine.qty_per_grid > 0

        actions, state = engine.compute_initial_orders(Decimal("100000"), "bot1")
        assert len(actions.place) == len(engine.levels)

    def test_2_grids_minimal(self):
        engine = GridEngine(_make_config(n_grids=2))
        assert len(engine.levels) == 3
        actions, state = engine.compute_initial_orders(Decimal("95000"), "bot1")
        assert len(actions.place) == 3

    def test_narrow_range(self):
        engine = GridEngine(
            _make_config(
                price_low=Decimal("95000"),
                price_high=Decimal("95100"),
                n_grids=10,
                tick_size=Decimal("0.1"),
            )
        )
        assert len(engine.levels) == 11
        assert engine.qty_per_grid > 0

    def test_very_wide_range(self):
        engine = GridEngine(
            _make_config(
                price_low=Decimal("10000"),
                price_high=Decimal("200000"),
                n_grids=10,
                tick_size=Decimal("1"),
            )
        )
        assert len(engine.levels) == 11
        assert engine.levels[0] == Decimal("10000")
        assert engine.levels[-1] == Decimal("200000")

    def test_geometric_wide_range(self):
        engine = GridEngine(
            _make_config(
                price_low=Decimal("1000"),
                price_high=Decimal("100000"),
                n_grids=10,
                spacing="geometric",
                tick_size=Decimal("0.01"),
            )
        )
        assert len(engine.levels) == 11
        assert engine.qty_per_grid > 0

    def test_all_directions_produce_orders(self):
        for direction in ("long", "short", "neutral"):
            engine = GridEngine(_make_config(direction=direction))
            actions, state = engine.compute_initial_orders(Decimal("95000"), "bot1")
            assert len(actions.place) > 0
            assert len(state.live_orders) > 0


# ────────────────── Client order ID ──────────────────


class TestClientOrderId:
    def test_deterministic(self):
        cloid1 = GridEngine._make_cloid("bot1", 3, "buy", 1000)
        cloid2 = GridEngine._make_cloid("bot1", 3, "buy", 1000)
        assert cloid1 == cloid2

    def test_different_timestamps_same_bucket(self):
        cloid1 = GridEngine._make_cloid("bot1", 3, "buy", 10000)
        cloid2 = GridEngine._make_cloid("bot1", 3, "buy", 19999)
        assert cloid1 == cloid2

    def test_different_buckets(self):
        cloid1 = GridEngine._make_cloid("bot1", 3, "buy", 10000)
        cloid2 = GridEngine._make_cloid("bot1", 3, "buy", 20000)
        assert cloid1 != cloid2
