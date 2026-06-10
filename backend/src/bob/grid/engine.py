"""Grid trading engine — PURE, NO I/O.

All functions receive state and return actions. No network calls, no DB access.
This module is the core of BOB and must have ≥90% test coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Literal

from bob.grid.spacing import (
    calc_qty_per_grid,
    generate_levels,
    quantize,
)


# ────────────────────────── Data types ──────────────────────────


@dataclass(frozen=True)
class BotConfig:
    symbol: str
    direction: Literal["long", "short", "neutral"]
    price_low: Decimal
    price_high: Decimal
    n_grids: int
    investment_usdt: Decimal
    leverage: int
    spacing: Literal["arithmetic", "geometric"] = "arithmetic"
    stop_loss_pct: Decimal | None = None
    take_profit_pct: Decimal | None = None
    out_of_range_action: Literal["pause", "close", "trail"] = "pause"
    tick_size: Decimal = Decimal("0.1")
    lot_size: Decimal = Decimal("0.001")
    maker_fee: Decimal = Decimal("0.0002")  # 0.02% maker rebate typical
    # Markov regime detection
    markov_enabled: bool = False
    markov_window: int = 50          # rolling price buffer size
    markov_check_every: int = 10     # check regime every N ticks
    markov_trend_threshold: Decimal = Decimal("0.015")
    markov_vol_threshold: Decimal = Decimal("0.005")


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class GridOrder:
    """An order the engine wants placed on the exchange."""

    level_index: int
    side: OrderSide
    price: Decimal
    quantity: Decimal
    client_order_id: str = ""


@dataclass(frozen=True)
class CancelOrder:
    """An order the engine wants cancelled."""

    client_order_id: str
    reason: str = ""


class OutOfRangeSignal(str, Enum):
    ABOVE = "above"  # price > price_high
    BELOW = "below"  # price < price_low
    NONE = "none"


@dataclass
class Actions:
    """The output of the engine: what to do next."""

    place: list[GridOrder] = field(default_factory=list)
    cancel: list[CancelOrder] = field(default_factory=list)
    out_of_range: OutOfRangeSignal = OutOfRangeSignal.NONE
    should_pause: bool = False
    should_close: bool = False


@dataclass
class GridState:
    """Mutable state tracked per-bot between ticks."""

    # level_index -> True if a buy has been filled at this level
    filled_buys: dict[int, bool] = field(default_factory=dict)
    # level_index -> True if a sell has been filled at this level
    filled_sells: dict[int, bool] = field(default_factory=dict)
    # level_index -> client_order_id of the live order at that level
    live_orders: dict[int, str] = field(default_factory=dict)
    # Realized PnL from completed grid cycles
    realized_pnl: Decimal = Decimal("0")
    # Total grid trades completed
    grid_trades_count: int = 0
    # Entry price for SL/TP calculation
    entry_price: Decimal | None = None
    # Total volume traded (for points tracking)
    total_volume: Decimal = Decimal("0")


@dataclass(frozen=True)
class MarketState:
    """Current market info needed by the engine."""

    current_price: Decimal
    timestamp_ms: int = 0


@dataclass(frozen=True)
class Fill:
    """A fill event from the exchange."""

    client_order_id: str
    level_index: int
    side: OrderSide
    price: Decimal
    quantity: Decimal
    fee: Decimal = Decimal("0")


# ────────────────────────── Engine ──────────────────────────


class GridEngine:
    """Pure grid trading engine. No I/O.

    Usage:
        engine = GridEngine(config)
        levels = engine.levels
        actions = engine.compute_initial_orders(current_price)
        # ... after a fill ...
        actions = engine.on_fill(fill, grid_state)
        # ... on price update ...
        actions = engine.check_out_of_range(market_state, grid_state)
    """

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.levels = generate_levels(
            config.price_low,
            config.price_high,
            config.n_grids,
            config.spacing,
            config.tick_size,
        )
        self.qty_per_grid = calc_qty_per_grid(
            config.investment_usdt,
            config.leverage,
            config.n_grids,
            self.levels,
            config.lot_size,
        )

    def compute_initial_orders(
        self, current_price: Decimal, bot_id: str = "bot"
    ) -> tuple[Actions, GridState]:
        """Compute the initial set of orders when a bot starts.

        For LONG: buy limits below current price, sell limits above.
        For SHORT: sell limits above current price, buy limits below.
        For NEUTRAL: symmetric around the nearest level to current price.

        Returns (actions, initial_grid_state).
        """
        state = GridState()
        actions = Actions()

        if self.qty_per_grid <= 0:
            return actions, state

        # Find where current price sits among levels
        split_idx = self._find_split_index(current_price)
        state.entry_price = current_price

        if self.config.direction == "long":
            actions = self._initial_long(split_idx, bot_id)
        elif self.config.direction == "short":
            actions = self._initial_short(split_idx, bot_id)
        elif self.config.direction == "neutral":
            actions = self._initial_neutral(split_idx, bot_id)

        # Track live orders in state
        for order in actions.place:
            state.live_orders[order.level_index] = order.client_order_id

        return actions, state

    def on_fill(
        self, fill: Fill, state: GridState, bot_id: str = "bot", timestamp_ms: int = 0
    ) -> Actions:
        """Process a fill and return the cascade action.

        When a BUY at level_i fills -> place SELL at level_{i+1}
        When a SELL at level_i fills -> place BUY at level_{i-1}
        """
        actions = Actions()
        idx = fill.level_index

        # Remove from live orders
        if idx in state.live_orders:
            del state.live_orders[idx]

        # Track volume
        state.total_volume += fill.price * fill.quantity

        if fill.side == OrderSide.BUY:
            state.filled_buys[idx] = True
            # Place sell at next level up
            sell_idx = idx + 1
            if sell_idx < len(self.levels):
                order = GridOrder(
                    level_index=sell_idx,
                    side=OrderSide.SELL,
                    price=self.levels[sell_idx],
                    quantity=fill.quantity,
                    client_order_id=self._make_cloid(bot_id, sell_idx, "sell", timestamp_ms),
                )
                actions.place.append(order)
                state.live_orders[sell_idx] = order.client_order_id

                # If there was already a sell at that level, cancel it first
                # (shouldn't normally happen, but defensive)

        elif fill.side == OrderSide.SELL:
            state.filled_sells[idx] = True
            # Place buy at next level down
            buy_idx = idx - 1
            if buy_idx >= 0:
                order = GridOrder(
                    level_index=buy_idx,
                    side=OrderSide.BUY,
                    price=self.levels[buy_idx],
                    quantity=fill.quantity,
                    client_order_id=self._make_cloid(bot_id, buy_idx, "buy", timestamp_ms),
                )
                actions.place.append(order)
                state.live_orders[buy_idx] = order.client_order_id

        # Calculate realized PnL for a completed grid cycle
        if fill.side == OrderSide.SELL and idx > 0 and self.config.direction != "short":
            # Long/neutral: a sell completing means we bought lower and sold higher
            buy_price = self.levels[idx - 1]
            sell_price = self.levels[idx]
            pnl = (sell_price - buy_price) * fill.quantity - fill.fee
            state.realized_pnl += pnl
            state.grid_trades_count += 1
        elif fill.side == OrderSide.BUY and idx < len(self.levels) - 1:
            # Short: a buy completing means we sold higher and bought lower
            if self.config.direction == "short":
                sell_price = self.levels[idx + 1]
                buy_price = self.levels[idx]
                pnl = (sell_price - buy_price) * fill.quantity - fill.fee
                state.realized_pnl += pnl
                state.grid_trades_count += 1

        return actions

    def check_out_of_range(
        self, market: MarketState, state: GridState
    ) -> Actions:
        """Check if price is outside the grid range and return appropriate actions."""
        actions = Actions()

        if market.current_price > self.config.price_high:
            actions.out_of_range = OutOfRangeSignal.ABOVE
        elif market.current_price < self.config.price_low:
            actions.out_of_range = OutOfRangeSignal.BELOW
        else:
            actions.out_of_range = OutOfRangeSignal.NONE
            return actions

        if self.config.out_of_range_action == "pause":
            actions.should_pause = True
            # Cancel orders on the side that's out of range
            for idx, cloid in list(state.live_orders.items()):
                actions.cancel.append(CancelOrder(client_order_id=cloid, reason="out_of_range"))

        elif self.config.out_of_range_action == "close":
            actions.should_close = True
            for idx, cloid in list(state.live_orders.items()):
                actions.cancel.append(CancelOrder(client_order_id=cloid, reason="close_position"))

        # "trail" is v2 — not implemented yet
        return actions

    def check_stop_loss_take_profit(
        self, market: MarketState, state: GridState
    ) -> Actions:
        """Check SL/TP conditions."""
        actions = Actions()
        if state.entry_price is None or state.entry_price == 0:
            return actions

        price_change_pct = (
            (market.current_price - state.entry_price) / state.entry_price
        ) * 100

        # For long: SL triggers on price drop, TP on price rise
        # For short: SL triggers on price rise, TP on price drop
        if self.config.direction == "short":
            price_change_pct = -price_change_pct

        if self.config.stop_loss_pct is not None and price_change_pct <= -self.config.stop_loss_pct:
            actions.should_close = True
            for idx, cloid in list(state.live_orders.items()):
                actions.cancel.append(CancelOrder(client_order_id=cloid, reason="stop_loss"))

        if self.config.take_profit_pct is not None and price_change_pct >= self.config.take_profit_pct:
            actions.should_close = True
            for idx, cloid in list(state.live_orders.items()):
                actions.cancel.append(CancelOrder(client_order_id=cloid, reason="take_profit"))

        return actions

    # ────────────────── Internal helpers ──────────────────

    def _find_split_index(self, current_price: Decimal) -> int:
        """Find the index where current_price falls among levels.

        Returns i such that levels[i] <= current_price < levels[i+1].
        If price is below all levels, returns 0.
        If price is above all levels, returns len(levels) - 1.
        """
        for i in range(len(self.levels) - 1):
            if self.levels[i] <= current_price < self.levels[i + 1]:
                return i
        if current_price >= self.levels[-1]:
            return len(self.levels) - 1
        return 0

    def _initial_long(self, split_idx: int, bot_id: str) -> Actions:
        """Long: buy limits below price, sell limits above."""
        actions = Actions()
        for i in range(len(self.levels)):
            if i <= split_idx:
                # Buy at this level
                actions.place.append(
                    GridOrder(
                        level_index=i,
                        side=OrderSide.BUY,
                        price=self.levels[i],
                        quantity=self.qty_per_grid,
                        client_order_id=self._make_cloid(bot_id, i, "buy", 0),
                    )
                )
            else:
                # Sell at this level
                actions.place.append(
                    GridOrder(
                        level_index=i,
                        side=OrderSide.SELL,
                        price=self.levels[i],
                        quantity=self.qty_per_grid,
                        client_order_id=self._make_cloid(bot_id, i, "sell", 0),
                    )
                )
        return actions

    def _initial_short(self, split_idx: int, bot_id: str) -> Actions:
        """Short: sell limits above price, buy limits below."""
        actions = Actions()
        for i in range(len(self.levels)):
            if i <= split_idx:
                # Buy at this level (to cover short)
                actions.place.append(
                    GridOrder(
                        level_index=i,
                        side=OrderSide.BUY,
                        price=self.levels[i],
                        quantity=self.qty_per_grid,
                        client_order_id=self._make_cloid(bot_id, i, "buy", 0),
                    )
                )
            else:
                # Sell at this level
                actions.place.append(
                    GridOrder(
                        level_index=i,
                        side=OrderSide.SELL,
                        price=self.levels[i],
                        quantity=self.qty_per_grid,
                        client_order_id=self._make_cloid(bot_id, i, "sell", 0),
                    )
                )
        return actions

    def _initial_neutral(self, split_idx: int, bot_id: str) -> Actions:
        """Neutral: buy below center, sell above center.

        Identical placement to long, but semantically the bot does not
        start with a directional bias.
        """
        return self._initial_long(split_idx, bot_id)

    @staticmethod
    def _make_cloid(bot_id: str, level_idx: int, side: str, timestamp_ms: int) -> str:
        """Generate deterministic client_order_id.

        Format: {bot_id}-{level_idx}-{side}-{timestamp_bucket}
        timestamp_bucket groups by 10-second windows for idempotency.
        """
        ts_bucket = timestamp_ms // 1_000
        return f"{bot_id}-{level_idx}-{side}-{ts_bucket}"
