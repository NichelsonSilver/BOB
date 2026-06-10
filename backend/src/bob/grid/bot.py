"""GridBot — executable bot instance wrapping the pure engine.

Paper mode: simulates fills when market price crosses grid levels.
Live mode: places real orders via GrvtRestClient and consumes real fills
from the TradingHub WebSocket.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from bob.db.models import Bot, FillRecord, Order
from bob.db.session import get_session
from bob.grid.engine import (
    Actions,
    BotConfig,
    Fill,
    GridEngine,
    GridState,
    MarketState,
    OrderSide,
)
from bob.grid.markov import MarkovRegimeDetector, grid_action_for_regime
from bob.grid.state_machine import BotState, BotStateMachine, InvalidTransitionError
from bob.grvt.rest import GrvtRestClient

logger = logging.getLogger(__name__)


class GridBot:
    """A running grid bot instance.

    In paper mode, simulates fills when the market price crosses a grid
    level with a live order. In live mode, delegates to GRVT: places
    initial orders via REST and reacts to fills delivered through a queue
    owned by TradingHub.
    """

    def __init__(
        self,
        bot_id: str,
        config: BotConfig,
        mode: Literal["paper", "live"] = "paper",
        rest_client: GrvtRestClient | None = None,
    ) -> None:
        self.bot_id = bot_id
        self.config = config
        self.mode = mode
        self.engine = GridEngine(config)
        self.state_machine = BotStateMachine()
        self.grid_state: GridState | None = None
        self.rest_client = rest_client
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._stop_event = asyncio.Event()
        self._last_price: Decimal | None = None
        # level_index -> exchange order_id (live mode only)
        self._exchange_order_ids: dict[int, str] = {}

        # Markov regime detection
        if config.markov_enabled:
            self._price_buffer: collections.deque = collections.deque(
                maxlen=config.markov_window
            )
            self._markov: MarkovRegimeDetector | None = MarkovRegimeDetector(
                trend_threshold=config.markov_trend_threshold,
                vol_threshold=config.markov_vol_threshold,
            )
        else:
            self._price_buffer = collections.deque(maxlen=1)
            self._markov = None
        self._markov_paused: bool = False  # True when paused specifically by Markov
        self._tick_count: int = 0

        if mode == "live" and rest_client is None:
            raise ValueError("live mode requires a GrvtRestClient")

    @classmethod
    def from_db(
        cls,
        db_bot: Bot,
        rest_client: GrvtRestClient | None = None,
    ) -> "GridBot":
        """Reconstruct a GridBot + GridState from a persisted Bot row.

        The caller is responsible for attaching queues and starting the loop.
        Does not touch the network.
        """
        config = BotConfig(
            symbol=db_bot.symbol,
            direction=db_bot.direction,  # type: ignore[arg-type]
            price_low=Decimal(db_bot.price_low),
            price_high=Decimal(db_bot.price_high),
            n_grids=db_bot.n_grids,
            investment_usdt=Decimal(db_bot.investment_usdt),
            leverage=db_bot.leverage,
            spacing=db_bot.spacing,  # type: ignore[arg-type]
            stop_loss_pct=Decimal(db_bot.stop_loss_pct) if db_bot.stop_loss_pct else None,
            take_profit_pct=Decimal(db_bot.take_profit_pct) if db_bot.take_profit_pct else None,
            out_of_range_action=db_bot.out_of_range_action,  # type: ignore[arg-type]
            tick_size=Decimal(db_bot.tick_size),
            lot_size=Decimal(db_bot.lot_size),
            maker_fee=Decimal(db_bot.maker_fee),
        )

        bot = cls(
            bot_id=db_bot.bot_id,
            config=config,
            mode=db_bot.mode,  # type: ignore[arg-type]
            rest_client=rest_client if db_bot.mode == "live" else None,
        )

        # Rebuild GridState from the JSON snapshot
        state = GridState()
        state.realized_pnl = Decimal(db_bot.realized_pnl or "0")
        state.grid_trades_count = db_bot.grid_trades_count or 0
        state.total_volume = Decimal(db_bot.total_volume or "0")
        state.entry_price = (
            Decimal(db_bot.entry_price) if db_bot.entry_price else None
        )
        state.filled_buys = {
            int(k): bool(v) for k, v in json.loads(db_bot.filled_buys_json or "{}").items()
        }
        state.filled_sells = {
            int(k): bool(v) for k, v in json.loads(db_bot.filled_sells_json or "{}").items()
        }
        state.live_orders = {
            int(k): str(v) for k, v in json.loads(db_bot.live_orders_json or "{}").items()
        }
        bot.grid_state = state
        bot._exchange_order_ids = {
            int(k): str(v) for k, v in json.loads(db_bot.exchange_ids_json or "{}").items()
        }
        if state.entry_price is not None:
            bot._last_price = state.entry_price

        # Restore state machine: only rehydrate bots that were actually running.
        # The caller filters by state before calling from_db, but we still need
        # to advance the SM from IDLE so the bot will react to ticks.
        try:
            persisted_state = BotState(db_bot.state)
        except ValueError:
            persisted_state = BotState.IDLE

        if persisted_state in (BotState.RUNNING, BotState.PAUSED):
            bot.state_machine.transition(BotState.STARTING)
            bot.state_machine.transition(BotState.RUNNING)
            if persisted_state == BotState.PAUSED:
                bot.state_machine.transition(BotState.PAUSED)
        # else: leave at IDLE — the caller likely won't rehydrate these

        return bot

    @property
    def state(self) -> BotState:
        return self.state_machine.state

    @property
    def is_running(self) -> bool:
        return self.state_machine.is_active

    async def resume_loop(
        self,
        market_data_queue: asyncio.Queue,  # type: ignore[type-arg]
        trading_fill_queue: asyncio.Queue | None = None,  # type: ignore[type-arg]
    ) -> None:
        """Run the main loop on an already-initialized bot (post-rehydrate).

        Skips the initial compute_initial_orders — assumes grid_state is
        already populated and the exchange already has the live orders.
        """
        if self.mode == "live" and trading_fill_queue is None:
            raise ValueError("live mode requires a trading_fill_queue")
        if self.grid_state is None:
            raise ValueError("resume_loop requires grid_state to be populated")
        if self.state_machine.state not in (BotState.RUNNING, BotState.PAUSED):
            raise ValueError(
                f"resume_loop expects RUNNING or PAUSED, got {self.state_machine.state}"
            )
        logger.info(
            "[%s] resuming %s loop | %d live orders restored",
            self.bot_id,
            self.mode,
            len(self.grid_state.live_orders),
        )
        try:
            await self._run_loop(market_data_queue, trading_fill_queue)
        except asyncio.CancelledError:
            logger.info("[%s] resume cancelled", self.bot_id)
            raise

    async def start(
        self,
        market_data_queue: asyncio.Queue,  # type: ignore[type-arg]
        trading_fill_queue: asyncio.Queue | None = None,  # type: ignore[type-arg]
    ) -> None:
        """Start the bot loop.

        In live mode, trading_fill_queue is required and delivers user fills
        via TradingHub.
        """
        if self.mode == "live" and trading_fill_queue is None:
            raise ValueError("live mode requires a trading_fill_queue")

        self.state_machine.transition(BotState.STARTING)
        self._persist_bot()

        try:
            first_tick = await asyncio.wait_for(market_data_queue.get(), timeout=30)
            current_price = self._extract_price(first_tick)

            if current_price is None:
                raise ValueError("Could not extract price from first ticker message")

            self._last_price = current_price
            actions, self.grid_state = self.engine.compute_initial_orders(
                current_price, self.bot_id
            )
            self.grid_state.entry_price = current_price

            if self.mode == "live":
                await self._place_initial_live_orders(actions)
            self._persist_orders(actions)

            self.state_machine.transition(BotState.RUNNING)
            self._persist_bot()

            logger.info(
                "[%s] started %s mode @ %s | %d initial orders | qty/grid=%s",
                self.bot_id,
                self.mode,
                current_price,
                len(actions.place),
                self.engine.qty_per_grid,
            )

            await self._run_loop(market_data_queue, trading_fill_queue)

        except asyncio.CancelledError:
            logger.info("[%s] cancelled", self.bot_id)
            raise
        except Exception as e:
            logger.exception("[%s] error: %s", self.bot_id, e)
            try:
                self.state_machine.transition(BotState.ERROR)
            except InvalidTransitionError:
                pass
            self._persist_bot()
            raise

    # ────────────────────── main loop ──────────────────────

    async def _run_loop(
        self,
        market_queue: asyncio.Queue,  # type: ignore[type-arg]
        fill_queue: asyncio.Queue | None,  # type: ignore[type-arg]
    ) -> None:
        """Process market ticks (out-of-range / SL-TP / paper sim) and fills.

        Paper mode drives fills from price crossings of the market feed.
        Live mode consumes fills from the trading queue; market ticks only
        drive out-of-range and SL/TP checks.
        """
        pending: dict[asyncio.Task, str] = {}

        def _enqueue(name: str, queue: asyncio.Queue) -> None:
            t = asyncio.create_task(queue.get(), name=name)
            pending[t] = name

        _enqueue("market", market_queue)
        if fill_queue is not None:
            _enqueue("fill", fill_queue)

        try:
            while not self._stop_event.is_set():
                done, _ = await asyncio.wait(
                    pending.keys(), timeout=5.0, return_when=asyncio.FIRST_COMPLETED
                )
                if not done:
                    continue

                for task in done:
                    source = pending.pop(task)
                    try:
                        message = task.result()
                    except Exception as e:  # pragma: no cover — defensive
                        logger.warning("[%s] queue error: %s", self.bot_id, e)
                        if source == "market":
                            _enqueue("market", market_queue)
                        elif source == "fill" and fill_queue is not None:
                            _enqueue("fill", fill_queue)
                        continue

                    if source == "market":
                        await self._handle_market_tick(message)
                        _enqueue("market", market_queue)
                    elif source == "fill" and fill_queue is not None:
                        await self._handle_live_fill(message)
                        _enqueue("fill", fill_queue)
        finally:
            for task in pending:
                task.cancel()

    async def _handle_market_tick(self, tick: dict) -> None:  # type: ignore[type-arg]
        price = self._extract_price(tick)
        if price is None or self.grid_state is None:
            return

        # Update Markov buffer on every tick — even while paused so regime
        # recovery can be detected and the bot can auto-resume.
        if self._markov is not None:
            self._price_buffer.append(price)
            self._tick_count += 1
            if self._tick_count % self.config.markov_check_every == 0:
                await self._check_markov_regime()

        if self.state_machine.state == BotState.PAUSED:
            return
        if not self.is_running:
            return

        prev_price = self._last_price
        self._last_price = price
        timestamp_ms = int(time.time() * 1000)

        market = MarketState(current_price=price, timestamp_ms=timestamp_ms)

        oor_actions = self.engine.check_out_of_range(market, self.grid_state)
        if oor_actions.should_pause:
            await self._handle_pause_from_oor(oor_actions)
            return
        if oor_actions.should_close:
            logger.info("[%s] out of range, closing", self.bot_id)
            await self.stop(reason="out_of_range_close")
            return

        sltp = self.engine.check_stop_loss_take_profit(market, self.grid_state)
        if sltp.should_close:
            logger.info("[%s] SL/TP triggered, closing", self.bot_id)
            await self.stop(reason="sl_tp")
            return

        if self.mode == "paper" and prev_price is not None:
            self._simulate_fills(prev_price, price, timestamp_ms)

        if self.state_machine.state == BotState.PAUSED:
            if oor_actions.out_of_range.value == "none":
                try:
                    self.state_machine.transition(BotState.RUNNING)
                    logger.info("[%s] price back in range, resuming", self.bot_id)
                    self._persist_bot()
                except InvalidTransitionError:
                    pass

    async def _check_markov_regime(self) -> None:
        """Evaluate current market regime and pause/resume bot accordingly.

        Called periodically from _handle_market_tick. Only acts when the
        recommended action conflicts with the current running state.
        """
        if self._markov is None or len(self._price_buffer) < max(10, self._markov.subwindow + 1):
            return

        prices = list(self._price_buffer)
        regime_state = self._markov.current_regime(prices)
        action = grid_action_for_regime(regime_state.regime, self.config.direction)

        current_state = self.state_machine.state

        if action == "pause" and not self._markov_paused and current_state == BotState.RUNNING:
            self._markov_paused = True
            logger.info(
                "[%s] Markov: regime=%s (confidence=%.2f) → pausing",
                self.bot_id,
                regime_state.regime.value,
                float(regime_state.confidence),
            )
            try:
                self.state_machine.transition(BotState.PAUSED)
            except InvalidTransitionError:
                self._markov_paused = False
                return
            if self.mode == "live" and self.rest_client is not None and self.grid_state:
                for cloid in list(self.grid_state.live_orders.values()):
                    try:
                        await self.rest_client.cancel_by_internal_cloid(cloid)
                    except Exception as e:  # pragma: no cover — best-effort
                        logger.warning("[%s] markov pause cancel %s: %s", self.bot_id, cloid, e)
            self._persist_bot()

        elif action == "run" and self._markov_paused and current_state == BotState.PAUSED:
            self._markov_paused = False
            logger.info(
                "[%s] Markov: regime=%s → resuming",
                self.bot_id,
                regime_state.regime.value,
            )
            try:
                self.state_machine.transition(BotState.RUNNING)
            except InvalidTransitionError:
                pass
            self._persist_bot()

    async def _handle_pause_from_oor(self, oor: Actions) -> None:
        logger.info(
            "[%s] out of range (%s), pausing",
            self.bot_id,
            oor.out_of_range.value,
        )
        try:
            self.state_machine.transition(BotState.PAUSED)
        except InvalidTransitionError:
            pass
        if self.mode == "live" and self.rest_client is not None:
            for cancel in oor.cancel:
                try:
                    await self.rest_client.cancel_by_internal_cloid(cancel.client_order_id)
                except Exception as e:  # pragma: no cover — best-effort
                    logger.warning(
                        "[%s] cancel failed for %s: %s",
                        self.bot_id,
                        cancel.client_order_id,
                        e,
                    )
        self._persist_bot()

    # ────────────────────── live mode ──────────────────────

    async def _place_initial_live_orders(self, actions: Actions) -> None:
        assert self.rest_client is not None
        assert self.grid_state is not None
        rejected = 0
        for order in actions.place:
            try:
                placed = await self.rest_client.place_order(
                    symbol=self.config.symbol,
                    side=order.side.value,
                    price=order.price,
                    quantity=order.quantity,
                    internal_cloid=order.client_order_id,
                )
            except Exception as e:
                logger.error(
                    "[%s] initial place_order failed cloid=%s: %s",
                    self.bot_id,
                    order.client_order_id,
                    e,
                )
                self.grid_state.live_orders.pop(order.level_index, None)
                rejected += 1
                continue

            if placed.order_id:
                self._exchange_order_ids[order.level_index] = placed.order_id
            else:
                # Exchange did not ack — treat as rejected so we don't
                # falsely report it as live.
                self.grid_state.live_orders.pop(order.level_index, None)
                rejected += 1

        if rejected:
            logger.warning(
                "[%s] %d / %d initial orders rejected by exchange",
                self.bot_id,
                rejected,
                len(actions.place),
            )
        if rejected == len(actions.place):
            raise RuntimeError(
                f"all {rejected} initial orders rejected — check min_notional / balance"
            )

    async def _handle_live_fill(self, message: dict) -> None:  # type: ignore[type-arg]
        """Translate a GRVT fill message to Fill, run cascade, place follow-up."""
        if self.grid_state is None or self.rest_client is None:
            return

        exchange_cloid = str(message.get("client_order_id", ""))
        internal_cloid = self.rest_client.resolve_cloid(exchange_cloid)
        if not internal_cloid:
            # Not one of ours (different bot on same sub-account, or manual trade)
            return

        level_idx = self._parse_level_from_cloid(internal_cloid)
        if level_idx is None:
            return

        try:
            price = Decimal(str(message.get("price", "0")))
            quantity = Decimal(str(message.get("size", "0")))
            fee = Decimal(str(message.get("fee", "0")))
        except Exception as e:  # pragma: no cover — defensive
            logger.warning("[%s] bad fill message: %s (%s)", self.bot_id, message, e)
            return

        is_buyer = bool(message.get("is_buyer", False))
        side = OrderSide.BUY if is_buyer else OrderSide.SELL

        fill = Fill(
            client_order_id=internal_cloid,
            level_index=level_idx,
            side=side,
            price=price,
            quantity=quantity,
            fee=fee,
        )
        timestamp_ms = int(time.time() * 1000)

        cascade = self.engine.on_fill(fill, self.grid_state, self.bot_id, timestamp_ms)
        self._persist_fill(fill)

        # Place cascade orders via REST
        for new_order in cascade.place:
            try:
                placed = await self.rest_client.place_order(
                    symbol=self.config.symbol,
                    side=new_order.side.value,
                    price=new_order.price,
                    quantity=new_order.quantity,
                    internal_cloid=new_order.client_order_id,
                )
            except Exception as e:
                err_str = str(e)
                if "2012" in err_str or "overlaps" in err_str.lower():
                    # Order already active on exchange — keep it in live_orders
                    logger.warning(
                        "[%s] cascade cloid %s already active (2012), keeping",
                        self.bot_id,
                        new_order.client_order_id,
                    )
                    continue
                logger.error(
                    "[%s] cascade place_order failed cloid=%s: %s",
                    self.bot_id,
                    new_order.client_order_id,
                    e,
                )
                self.grid_state.live_orders.pop(new_order.level_index, None)
                continue

            if placed.order_id:
                self._exchange_order_ids[new_order.level_index] = placed.order_id
            else:
                self.grid_state.live_orders.pop(new_order.level_index, None)
        self._persist_orders(cascade)
        self._persist_bot()

        logger.info(
            "[%s] LIVE FILL: %s %s @ %s (level %d), realized_pnl=%s trades=%d",
            self.bot_id,
            side.value,
            quantity,
            price,
            level_idx,
            self.grid_state.realized_pnl,
            self.grid_state.grid_trades_count,
        )

    @staticmethod
    def _parse_level_from_cloid(cloid: str) -> int | None:
        """Extract level_index from cloid '{bot_id}-{level}-{side}-{bucket}'."""
        parts = cloid.rsplit("-", 3)
        if len(parts) != 4:
            return None
        try:
            return int(parts[1])
        except ValueError:
            return None

    # ────────────────────── paper mode ──────────────────────

    def _simulate_fills(
        self, prev_price: Decimal, current_price: Decimal, timestamp_ms: int
    ) -> None:
        """Paper mode: fire a fill when price crosses a level with a live order."""
        if self.grid_state is None:
            return

        price_went_up = current_price > prev_price
        price_went_down = current_price < prev_price
        if not price_went_up and not price_went_down:
            return

        filled_indices: list[int] = []
        for idx, cloid in list(self.grid_state.live_orders.items()):
            level_price = self.engine.levels[idx]
            is_buy = "buy" in cloid
            is_sell = "sell" in cloid

            crossed = False
            if is_buy and price_went_down:
                if prev_price > level_price >= current_price:
                    crossed = True
                elif prev_price >= level_price > current_price:
                    crossed = True
            elif is_sell and price_went_up:
                if prev_price < level_price <= current_price:
                    crossed = True
                elif prev_price <= level_price < current_price:
                    crossed = True

            if crossed:
                filled_indices.append(idx)

        filled_indices.sort()
        for idx in filled_indices:
            if idx not in self.grid_state.live_orders:
                continue
            cloid = self.grid_state.live_orders[idx]
            side = OrderSide.BUY if "buy" in cloid else OrderSide.SELL
            level_price = self.engine.levels[idx]
            fee = level_price * self.engine.qty_per_grid * self.config.maker_fee

            fill = Fill(
                client_order_id=cloid,
                level_index=idx,
                side=side,
                price=level_price,
                quantity=self.engine.qty_per_grid,
                fee=fee,
            )

            cascade_actions = self.engine.on_fill(
                fill, self.grid_state, self.bot_id, timestamp_ms
            )
            self._persist_fill(fill)
            self._persist_orders(cascade_actions)
            self._persist_bot()

            logger.info(
                "[%s] PAPER FILL: %s %s @ %s (level %d) pnl=%s trades=%d",
                self.bot_id,
                side.value,
                self.engine.qty_per_grid,
                level_price,
                idx,
                self.grid_state.realized_pnl,
                self.grid_state.grid_trades_count,
            )

    # ────────────────────── lifecycle ──────────────────────

    async def stop(self, reason: str = "user") -> None:
        """Stop the bot gracefully. In live mode cancels open orders first."""
        logger.info("[%s] stopping (reason: %s)", self.bot_id, reason)
        self._stop_event.set()

        if self.mode == "live" and self.rest_client is not None:
            try:
                await self.rest_client.cancel_all()
            except Exception as e:  # pragma: no cover — best effort
                logger.warning("[%s] cancel_all on stop failed: %s", self.bot_id, e)

        try:
            if self.state_machine.state in (
                BotState.RUNNING,
                BotState.PAUSED,
                BotState.STARTING,
            ):
                self.state_machine.transition(BotState.STOPPING)
                self.state_machine.transition(BotState.STOPPED)
        except InvalidTransitionError as e:
            logger.warning("[%s] state transition error on stop: %s", self.bot_id, e)

        self._persist_bot(stopped=True)

    async def reconcile_live_orders(self) -> dict[str, int]:
        """Drop `live_orders` entries whose exchange order is no longer open.

        Prevents phantom entries accumulating when GRVT rejects or silently
        drops an order (e.g. max-open-orders, post-only reject that returns
        no order_id). Returns a small summary for logging.
        """
        if self.mode != "live" or self.rest_client is None or self.grid_state is None:
            return {"checked": 0, "dropped": 0}
        try:
            open_orders = await self.rest_client.fetch_open_orders(
                symbol=self.config.symbol
            )
        except Exception as e:  # pragma: no cover — best effort
            logger.warning("[%s] reconcile fetch failed: %s", self.bot_id, e)
            return {"checked": 0, "dropped": 0, "error": 1}

        live_exchange_cloids: set[str] = set()
        for o in open_orders:
            for key in ("client_order_id", "clientOrderId"):
                v = o.get(key) if isinstance(o, dict) else None
                if v:
                    live_exchange_cloids.add(str(v))
                    break

        dropped = 0
        for idx, internal_cloid in list(self.grid_state.live_orders.items()):
            expected = self.rest_client._str_to_int.get(internal_cloid)  # noqa: SLF001
            if expected is None:
                # No mapping — we never successfully registered this one.
                self.grid_state.live_orders.pop(idx, None)
                self._exchange_order_ids.pop(idx, None)
                dropped += 1
                continue
            if expected not in live_exchange_cloids:
                self.grid_state.live_orders.pop(idx, None)
                self._exchange_order_ids.pop(idx, None)
                dropped += 1

        if dropped:
            logger.info(
                "[%s] reconcile: dropped %d phantom live_orders (exchange has %d open)",
                self.bot_id,
                dropped,
                len(live_exchange_cloids),
            )
        return {
            "checked": len(self.grid_state.live_orders) + dropped,
            "dropped": dropped,
            "exchange_open": len(live_exchange_cloids),
        }

    async def pause(self) -> None:
        self.state_machine.transition(BotState.PAUSED)
        if self.mode == "live" and self.rest_client is not None and self.grid_state:
            for cloid in list(self.grid_state.live_orders.values()):
                try:
                    await self.rest_client.cancel_by_internal_cloid(cloid)
                except Exception as e:  # pragma: no cover
                    logger.warning("[%s] pause cancel failed: %s", self.bot_id, e)
        self._persist_bot()
        logger.info("[%s] paused", self.bot_id)

    async def resume(self) -> None:
        self.state_machine.transition(BotState.RUNNING)
        self._persist_bot()
        logger.info("[%s] resumed", self.bot_id)

    # ────────────────── persistence helpers ──────────────────

    def _persist_bot(self, stopped: bool = False) -> None:
        with get_session() as session:
            from sqlmodel import select

            stmt = select(Bot).where(Bot.bot_id == self.bot_id)
            db_bot = session.exec(stmt).first()

            if db_bot is None:
                db_bot = Bot(
                    bot_id=self.bot_id,
                    symbol=self.config.symbol,
                    direction=self.config.direction,
                    mode=self.mode,
                    price_low=str(self.config.price_low),
                    price_high=str(self.config.price_high),
                    n_grids=self.config.n_grids,
                    investment_usdt=str(self.config.investment_usdt),
                    leverage=self.config.leverage,
                    spacing=self.config.spacing,
                    stop_loss_pct=str(self.config.stop_loss_pct)
                    if self.config.stop_loss_pct
                    else None,
                    take_profit_pct=str(self.config.take_profit_pct)
                    if self.config.take_profit_pct
                    else None,
                    out_of_range_action=self.config.out_of_range_action,
                    tick_size=str(self.config.tick_size),
                    lot_size=str(self.config.lot_size),
                    maker_fee=str(self.config.maker_fee),
                )
                session.add(db_bot)

            db_bot.state = self.state_machine.state.value
            if self.grid_state:
                db_bot.entry_price = (
                    str(self.grid_state.entry_price) if self.grid_state.entry_price else None
                )
                db_bot.realized_pnl = str(self.grid_state.realized_pnl)
                db_bot.grid_trades_count = self.grid_state.grid_trades_count
                db_bot.total_volume = str(self.grid_state.total_volume)
                db_bot.filled_buys_json = json.dumps(
                    {str(k): bool(v) for k, v in self.grid_state.filled_buys.items()}
                )
                db_bot.filled_sells_json = json.dumps(
                    {str(k): bool(v) for k, v in self.grid_state.filled_sells.items()}
                )
                db_bot.live_orders_json = json.dumps(
                    {str(k): v for k, v in self.grid_state.live_orders.items()}
                )
                db_bot.exchange_ids_json = json.dumps(
                    {str(k): v for k, v in self._exchange_order_ids.items()}
                )

            now = datetime.now(timezone.utc)
            if self.state_machine.state == BotState.RUNNING and db_bot.started_at is None:
                db_bot.started_at = now
            if stopped:
                db_bot.stopped_at = now

            session.commit()

    def _persist_orders(self, actions: Actions) -> None:
        from sqlmodel import select

        with get_session() as session:
            for order in actions.place:
                existing = session.exec(
                    select(Order).where(Order.client_order_id == order.client_order_id)
                ).first()
                if existing:
                    continue
                exchange_id = self._exchange_order_ids.get(order.level_index)
                db_order = Order(
                    bot_id=self.bot_id,
                    client_order_id=order.client_order_id,
                    level_index=order.level_index,
                    side=order.side.value,
                    price=str(order.price),
                    quantity=str(order.quantity),
                    status="open",
                    mode=self.mode,
                    exchange_order_id=exchange_id,
                )
                session.add(db_order)
            session.commit()

    def _persist_fill(self, fill: Fill) -> None:
        with get_session() as session:
            from sqlmodel import select

            db_fill = FillRecord(
                bot_id=self.bot_id,
                client_order_id=fill.client_order_id,
                level_index=fill.level_index,
                side=fill.side.value,
                price=str(fill.price),
                quantity=str(fill.quantity),
                fee=str(fill.fee),
                mode=self.mode,
            )
            session.add(db_fill)

            stmt = select(Order).where(Order.client_order_id == fill.client_order_id)
            db_order = session.exec(stmt).first()
            if db_order:
                db_order.status = "filled"
                db_order.filled_at = datetime.now(timezone.utc)

            session.commit()

    @staticmethod
    def _extract_price(tick: Any) -> Decimal | None:
        """Extract a usable price from a ticker / mini-ticker message."""
        if not isinstance(tick, dict):
            return None
        for key in ("last_price", "mark_price", "close", "last"):
            if key in tick:
                try:
                    return Decimal(str(tick[key]))
                except Exception:
                    continue
        for key in ("result", "data"):
            if key in tick and isinstance(tick[key], dict):
                return GridBot._extract_price(tick[key])
        return None
