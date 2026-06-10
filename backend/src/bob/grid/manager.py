"""BotManager — registry and orchestration of GridBot instances."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlmodel import select

from bob.db.models import Bot as BotRow
from bob.db.session import get_session
from bob.grid.bot import GridBot
from bob.grid.engine import BotConfig  # noqa: F401 — used by type hints in module
from bob.grid.state_machine import BotState
from bob.grvt.rest import GrvtRestClient
from bob.grvt.ws_market import MarketDataHub
from bob.grvt.ws_trading import TradingHub

logger = logging.getLogger(__name__)


class BotManager:
    """Lifecycle owner of all GridBot instances.

    Shares a single MarketDataHub for market data subscriptions. In live
    mode, also depends on TradingHub (for fill streams) and a GrvtRestClient
    (for order placement / cancellation).
    """

    def __init__(
        self,
        market_data_hub: MarketDataHub,
        trading_hub: TradingHub | None = None,
        rest_client: GrvtRestClient | None = None,
    ) -> None:
        self._market_hub = market_data_hub
        self._trading_hub = trading_hub
        self._rest_client = rest_client
        self._bots: dict[str, GridBot] = {}
        self._tasks: dict[str, asyncio.Task] = {}  # type: ignore[type-arg]
        # (bot_id) -> queue used to cleanup trading subscription
        self._trading_queues: dict[str, asyncio.Queue] = {}  # type: ignore[type-arg]
        self._market_queues: dict[str, asyncio.Queue] = {}  # type: ignore[type-arg]
        self._reconcile_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._reconcile_interval = 30.0

    @property
    def bots(self) -> dict[str, GridBot]:
        return dict(self._bots)

    def get_bot(self, bot_id: str) -> GridBot | None:
        return self._bots.get(bot_id)

    async def create_and_start(
        self,
        bot_id: str,
        config: BotConfig,
        mode: str = "paper",
    ) -> GridBot:
        """Create a new bot and start it."""
        if bot_id in self._bots:
            raise ValueError(f"Bot {bot_id!r} already exists")

        if mode == "live":
            if self._trading_hub is None or self._rest_client is None:
                raise ValueError(
                    "live mode requires both TradingHub and GrvtRestClient on BotManager"
                )

        bot = GridBot(
            bot_id=bot_id,
            config=config,
            mode=mode,  # type: ignore[arg-type]
            rest_client=self._rest_client if mode == "live" else None,
        )
        self._bots[bot_id] = bot

        market_queue = await self._market_hub.subscribe_mini_ticker(config.symbol)
        self._market_queues[bot_id] = market_queue

        fill_queue: asyncio.Queue | None = None  # type: ignore[type-arg]
        if mode == "live" and self._trading_hub is not None:
            fill_queue = await self._trading_hub.subscribe_fills(symbol=config.symbol)
            self._trading_queues[bot_id] = fill_queue

        task = asyncio.create_task(
            self._run_bot(bot, market_queue, fill_queue),
            name=f"bot-{bot_id}",
        )
        self._tasks[bot_id] = task

        logger.info("BotManager: created and starting %r (%s)", bot_id, mode)
        return bot

    async def _run_bot(
        self,
        bot: GridBot,
        market_queue: asyncio.Queue,  # type: ignore[type-arg]
        fill_queue: asyncio.Queue | None,  # type: ignore[type-arg]
    ) -> None:
        """Run the bot until it stops, then clean up subscriptions."""
        try:
            await bot.start(market_queue, fill_queue)
        except asyncio.CancelledError:
            logger.info("BotManager: %r cancelled", bot.bot_id)
        except Exception as e:
            logger.error("BotManager: %r crashed: %s", bot.bot_id, e)
        finally:
            self._market_hub.unsubscribe("mini.s", bot.config.symbol, market_queue)
            if fill_queue is not None and self._trading_hub is not None:
                self._trading_hub.unsubscribe("fill", bot.config.symbol, fill_queue)

    async def stop_bot(self, bot_id: str, reason: str = "user") -> None:
        bot = self._bots.get(bot_id)
        if bot is None:
            raise ValueError(f"Bot {bot_id!r} not found")

        await bot.stop(reason=reason)

        task = self._tasks.get(bot_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        logger.info("BotManager: stopped %r", bot_id)

    async def pause_bot(self, bot_id: str) -> None:
        bot = self._bots.get(bot_id)
        if bot is None:
            raise ValueError(f"Bot {bot_id!r} not found")
        await bot.pause()

    async def resume_bot(self, bot_id: str) -> None:
        bot = self._bots.get(bot_id)
        if bot is None:
            raise ValueError(f"Bot {bot_id!r} not found")
        await bot.resume()

    async def reconcile_all_live(self) -> dict[str, dict[str, int]]:
        """Run reconciliation across every live+active bot. Safe to call often."""
        results: dict[str, dict[str, int]] = {}
        for bot_id, bot in list(self._bots.items()):
            if bot.mode != "live":
                continue
            if bot.state_machine.state not in (BotState.RUNNING, BotState.PAUSED):
                continue
            try:
                results[bot_id] = await bot.reconcile_live_orders()
            except Exception as e:  # pragma: no cover
                logger.warning("reconcile error for %s: %s", bot_id, e)
                results[bot_id] = {"error": 1}
        return results

    async def start_reconcile_loop(self) -> None:
        if self._reconcile_task is not None and not self._reconcile_task.done():
            return

        async def _loop() -> None:
            while True:
                try:
                    await asyncio.sleep(self._reconcile_interval)
                    await self.reconcile_all_live()
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # pragma: no cover
                    logger.warning("reconcile loop error: %s", e)

        self._reconcile_task = asyncio.create_task(_loop(), name="bot-reconcile")

    async def stop_reconcile_loop(self) -> None:
        t = self._reconcile_task
        if t and not t.done():
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._reconcile_task = None

    async def rehydrate_from_db(self) -> list[str]:
        """Rebuild bots that were RUNNING or PAUSED from the DB and restart loops.

        Returns the bot_ids that were successfully rehydrated. Live bots get
        reconcile_live_orders() called right after to drop phantoms.
        """
        rehydrated: list[str] = []
        with get_session() as session:
            stmt = select(BotRow).where(
                BotRow.state.in_(
                    [BotState.RUNNING.value, BotState.PAUSED.value]
                )
            )
            rows: list[BotRow] = list(session.exec(stmt).all())

        for row in rows:
            if row.bot_id in self._bots:
                # Already in memory (e.g., manual reload) — skip.
                continue
            try:
                bot = await self._rehydrate_one(row)
            except Exception as e:
                logger.exception(
                    "BotManager: rehydrate failed for %r: %s", row.bot_id, e
                )
                continue
            rehydrated.append(bot.bot_id)

        if rehydrated:
            logger.info("BotManager: rehydrated %d bots: %s", len(rehydrated), rehydrated)
        return rehydrated

    async def _rehydrate_one(self, row: BotRow) -> GridBot:
        if row.mode == "live" and (self._trading_hub is None or self._rest_client is None):
            raise ValueError(
                f"cannot rehydrate live bot {row.bot_id!r}: trading hub / rest client missing"
            )

        bot = GridBot.from_db(
            row,
            rest_client=self._rest_client if row.mode == "live" else None,
        )

        # Rebuild cloid→int mapping in rest client so reconciliation / cancellation
        # can find the exchange cloid for each internal cloid after restart.
        if row.mode == "live" and self._rest_client is not None and bot.grid_state is not None:
            for internal_cloid in bot.grid_state.live_orders.values():
                self._rest_client.register_cloid(internal_cloid)

        self._bots[bot.bot_id] = bot
        market_queue = await self._market_hub.subscribe_mini_ticker(row.symbol)
        self._market_queues[bot.bot_id] = market_queue

        fill_queue: asyncio.Queue | None = None  # type: ignore[type-arg]
        if row.mode == "live" and self._trading_hub is not None:
            fill_queue = await self._trading_hub.subscribe_fills(symbol=row.symbol)
            self._trading_queues[bot.bot_id] = fill_queue

        task = asyncio.create_task(
            self._resume_bot(bot, market_queue, fill_queue),
            name=f"bot-{bot.bot_id}-resumed",
        )
        self._tasks[bot.bot_id] = task

        # For live bots, reconcile immediately so ghost live_orders get dropped.
        if row.mode == "live":
            try:
                await bot.reconcile_live_orders()
            except Exception as e:  # pragma: no cover — best-effort
                logger.warning("rehydrate reconcile for %s failed: %s", bot.bot_id, e)

        logger.info(
            "BotManager: rehydrated %r (%s, state=%s, live_orders=%d)",
            bot.bot_id,
            bot.mode,
            bot.state_machine.state.value,
            len(bot.grid_state.live_orders) if bot.grid_state else 0,
        )
        return bot

    async def _resume_bot(
        self,
        bot: GridBot,
        market_queue: asyncio.Queue,  # type: ignore[type-arg]
        fill_queue: asyncio.Queue | None,  # type: ignore[type-arg]
    ) -> None:
        try:
            await bot.resume_loop(market_queue, fill_queue)
        except asyncio.CancelledError:
            logger.info("BotManager: %r resume cancelled", bot.bot_id)
        except Exception as e:
            logger.error("BotManager: %r resume crashed: %s", bot.bot_id, e)
        finally:
            self._market_hub.unsubscribe("mini.s", bot.config.symbol, market_queue)
            if fill_queue is not None and self._trading_hub is not None:
                self._trading_hub.unsubscribe("fill", bot.config.symbol, fill_queue)

    async def stop_all(self, reason: str = "kill_switch") -> int:
        count = 0
        for bot_id in list(self._bots):
            bot = self._bots[bot_id]
            if bot.state_machine.state in (
                BotState.RUNNING,
                BotState.PAUSED,
                BotState.STARTING,
            ):
                await self.stop_bot(bot_id, reason=reason)
                count += 1
        return count

    def get_status(self, bot_id: str) -> dict[str, Any]:
        bot = self._bots.get(bot_id)
        if bot is None:
            raise ValueError(f"Bot {bot_id!r} not found")

        result: dict[str, Any] = {
            "bot_id": bot.bot_id,
            "symbol": bot.config.symbol,
            "direction": bot.config.direction,
            "mode": bot.mode,
            "state": bot.state_machine.state.value,
            "n_grids": bot.config.n_grids,
            "price_low": str(bot.config.price_low),
            "price_high": str(bot.config.price_high),
            "investment_usdt": str(bot.config.investment_usdt),
            "leverage": bot.config.leverage,
            "last_price": str(bot._last_price) if bot._last_price else None,
        }
        if bot.grid_state:
            result.update(
                {
                    "realized_pnl": str(bot.grid_state.realized_pnl),
                    "grid_trades_count": bot.grid_state.grid_trades_count,
                    "total_volume": str(bot.grid_state.total_volume),
                    "live_orders_count": len(bot.grid_state.live_orders),
                }
            )
        return result

    def list_all(self) -> list[dict[str, Any]]:
        return [self.get_status(bid) for bid in self._bots]
