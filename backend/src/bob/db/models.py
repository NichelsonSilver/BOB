"""SQLModel models for BOB persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlmodel import Field, SQLModel


class Bot(SQLModel, table=True):
    """A grid bot configuration + runtime state."""

    id: int | None = Field(default=None, primary_key=True)
    bot_id: str = Field(index=True, unique=True)  # human-readable, e.g. "btc-long-01"
    symbol: str
    direction: str  # "long" | "short" | "neutral"
    mode: str = "paper"  # "paper" | "live"
    state: str = "idle"  # matches BotState enum values

    # Grid config
    price_low: str  # stored as string for Decimal precision
    price_high: str
    n_grids: int
    investment_usdt: str
    leverage: int = 3
    spacing: str = "arithmetic"
    stop_loss_pct: str | None = None
    take_profit_pct: str | None = None
    out_of_range_action: str = "pause"
    tick_size: str = "0.1"
    lot_size: str = "0.001"
    maker_fee: str = "0.0002"

    # Runtime stats
    entry_price: str | None = None
    realized_pnl: str = "0"
    grid_trades_count: int = 0
    total_volume: str = "0"

    # Grid runtime snapshot (JSON). Enables process-restart recovery without
    # losing filled levels or live-order mappings.
    filled_buys_json: str = "{}"
    filled_sells_json: str = "{}"
    live_orders_json: str = "{}"
    exchange_ids_json: str = "{}"

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    stopped_at: datetime | None = None


class Order(SQLModel, table=True):
    """An order placed (or simulated) by a bot."""

    id: int | None = Field(default=None, primary_key=True)
    bot_id: str = Field(index=True)
    client_order_id: str = Field(index=True, unique=True)
    level_index: int
    side: str  # "buy" | "sell"
    price: str
    quantity: str
    status: str = "open"  # "open" | "filled" | "cancelled"
    mode: str = "paper"  # "paper" | "live"
    exchange_order_id: str | None = None  # only for live orders

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    filled_at: datetime | None = None
    cancelled_at: datetime | None = None


class BotPreset(SQLModel, table=True):
    """Named bot configuration template.

    Stores the CreateBotRequest payload as JSON so the UI can offer
    "load preset" / "save preset" flows. The config_json is the source of
    truth — field-level columns are redundant metadata for filtering.
    """

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    symbol: str
    direction: str
    mode: str = "paper"
    config_json: str  # full CreateBotRequest payload
    source: str = "manual"  # "manual" | "auto-create" | "clone"
    notes: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FillRecord(SQLModel, table=True):
    """A fill event — either real or simulated."""

    id: int | None = Field(default=None, primary_key=True)
    bot_id: str = Field(index=True)
    client_order_id: str
    level_index: int
    side: str
    price: str
    quantity: str
    fee: str = "0"
    pnl: str = "0"  # PnL from this grid cycle (if completing one)
    mode: str = "paper"

    filled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
