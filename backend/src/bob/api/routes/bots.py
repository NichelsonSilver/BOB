"""Bot management API routes."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlmodel import select

from bob.db.models import BotPreset
from bob.db.session import get_session

logger = logging.getLogger(__name__)

_DEFAULT_MAKER_FEE = Decimal("0.0002")
_MAINTENANCE_MARGIN_RATIO = Decimal("0.005")  # GRVT perps, ~0.5% rough

from bob.config import settings
from bob.grid.engine import BotConfig
from bob.grid.spacing import calc_qty_per_grid, generate_levels
from bob.grid.state_machine import BotState

router = APIRouter(prefix="/api/bots", tags=["bots"])


class CreateBotRequest(BaseModel):
    bot_id: str
    symbol: str = "BTC_USDT_Perp"
    direction: str = "long"
    price_low: str
    price_high: str
    n_grids: int = 10
    investment_usdt: str = "100"
    leverage: int = 3
    spacing: str = "arithmetic"
    stop_loss_pct: str | None = None
    take_profit_pct: str | None = None
    out_of_range_action: str = "pause"
    tick_size: str | None = None
    lot_size: str | None = None
    maker_fee: str = "0.0002"
    mode: str = "paper"

    @field_validator("price_low", "price_high", "investment_usdt", "maker_fee")
    @classmethod
    def validate_decimal(cls, v: str) -> str:
        try:
            Decimal(v)
        except InvalidOperation:
            raise ValueError(f"Invalid decimal: {v}")
        return v


def _instrument_meta(app: Any, symbol: str) -> dict[str, Any]:
    """Pull tick/lot/min_notional from loaded GRVT markets, with safe fallbacks."""
    client = getattr(app.state, "grvt_client", None)
    markets = getattr(client, "markets", None) or {}
    m = markets.get(symbol) or {}
    return {
        "tick_size": str(m.get("tick_size") or "0.1"),
        "lot_size": str(m.get("min_size") or m.get("lot_size") or "0.001"),
        "min_notional": str(m.get("min_notional") or "100"),
    }


def _apply_instrument_defaults(req: CreateBotRequest, app: Any) -> dict[str, str]:
    meta = _instrument_meta(app, req.symbol)
    if not req.tick_size:
        req.tick_size = meta["tick_size"]
    if not req.lot_size:
        req.lot_size = meta["lot_size"]
    return meta


_ACTIVE_STATES = {
    BotState.STARTING.value,
    BotState.RUNNING.value,
    BotState.PAUSED.value,
}


def _enforce_global_limits(req: CreateBotRequest, manager: Any) -> None:
    """Enforce settings.max_* before accepting a new bot."""
    if req.leverage > settings.max_leverage:
        raise HTTPException(
            status_code=400,
            detail=f"leverage {req.leverage} excede max_leverage={settings.max_leverage}",
        )
    bots = manager.list_all()
    active = [b for b in bots if b.get("state") in _ACTIVE_STATES]
    if len(active) >= settings.max_concurrent_bots:
        raise HTTPException(
            status_code=400,
            detail=(
                f"max_concurrent_bots={settings.max_concurrent_bots} alcanzado "
                f"({len(active)} activos)"
            ),
        )
    allocated = sum(
        Decimal(b.get("investment_usdt", "0")) for b in active
    )
    requested = Decimal(req.investment_usdt)
    if allocated + requested > Decimal(settings.max_total_capital):
        raise HTTPException(
            status_code=400,
            detail=(
                f"max_total_capital={settings.max_total_capital} excedido: "
                f"ya asignado={allocated}, pedido={requested}"
            ),
        )


def _build_config(req: CreateBotRequest) -> BotConfig:
    return BotConfig(
        symbol=req.symbol,
        direction=req.direction,
        price_low=Decimal(req.price_low),
        price_high=Decimal(req.price_high),
        n_grids=req.n_grids,
        investment_usdt=Decimal(req.investment_usdt),
        leverage=req.leverage,
        spacing=req.spacing,
        stop_loss_pct=Decimal(req.stop_loss_pct) if req.stop_loss_pct else None,
        take_profit_pct=Decimal(req.take_profit_pct) if req.take_profit_pct else None,
        out_of_range_action=req.out_of_range_action,
        tick_size=Decimal(req.tick_size),
        lot_size=Decimal(req.lot_size),
        maker_fee=Decimal(req.maker_fee),
    )


def _compute_preview(req: CreateBotRequest, meta: dict[str, str]) -> dict[str, Any]:
    investment = Decimal(req.investment_usdt)
    leverage = Decimal(req.leverage)
    min_notional = Decimal(meta["min_notional"])
    lot_size = Decimal(req.lot_size or meta["lot_size"])
    tick_size = Decimal(req.tick_size or meta["tick_size"])
    maker_fee = Decimal(req.maker_fee)

    levels = generate_levels(
        Decimal(req.price_low),
        Decimal(req.price_high),
        req.n_grids,
        req.spacing,  # type: ignore[arg-type]
        tick_size,
    )
    qty = calc_qty_per_grid(
        investment,
        req.leverage,
        req.n_grids,
        levels,
        lot_size,
    )
    avg_price = sum(levels) / len(levels) if levels else Decimal("0")
    notional = qty * avg_price
    total_notional = notional * req.n_grids

    # max_grids so notional/grid stays >= min_notional:
    # notional/grid = (investment * leverage) / n_grids  =>  n_grids <= (inv*lev)/min_notional
    if min_notional > 0:
        max_grids_allowed = int((investment * leverage) / min_notional)
    else:
        max_grids_allowed = req.n_grids
    max_grids_allowed = max(2, min(max_grids_allowed, 500))

    # profit per grid trade in USDT: step * qty - roundtrip fees
    step = (levels[1] - levels[0]) if len(levels) >= 2 else Decimal("0")
    gross = step * qty
    fees_roundtrip = Decimal(2) * maker_fee * qty * avg_price
    profit_per_grid_usdt = gross - fees_roundtrip

    # liquidation price (rough, isolated margin, ignoring funding):
    #   long:  entry * (1 - 1/lev + mm)
    #   short: entry * (1 + 1/lev - mm)
    inv_lev = Decimal(1) / leverage if leverage > 0 else Decimal(0)
    if req.direction == "long":
        liquidation_price = avg_price * (Decimal(1) - inv_lev + _MAINTENANCE_MARGIN_RATIO)
    else:
        liquidation_price = avg_price * (Decimal(1) + inv_lev - _MAINTENANCE_MARGIN_RATIO)

    margin_required = total_notional / leverage if leverage > 0 else total_notional
    inversion_per_grid = notional / leverage if leverage > 0 else notional

    warnings: list[str] = []
    if qty <= 0:
        warnings.append(
            f"qty/grid quantizes to 0 under lot_size={lot_size} — "
            "aumentá investment/leverage o bajá n_grids"
        )
    if notional > 0 and notional < min_notional:
        warnings.append(
            f"notional/grid ≈ {notional:.2f} USDT; GRVT rechaza órdenes < {min_notional} USDT. "
            f"Máximo permitido: {max_grids_allowed} grids."
        )
    if req.n_grids > max_grids_allowed:
        warnings.append(
            f"n_grids={req.n_grids} excede el máximo permitido ({max_grids_allowed}) "
            f"con investment={investment}, leverage={leverage}."
        )

    return {
        "qty_per_grid": str(qty),
        "avg_price": str(avg_price),
        "notional_per_grid": str(notional),
        "total_notional": str(total_notional),
        "levels_count": len(levels),
        "max_grids_allowed": max_grids_allowed,
        "profit_per_grid_usdt": str(profit_per_grid_usdt),
        "liquidation_price": str(liquidation_price),
        "margin_required": str(margin_required),
        "inversion_per_grid": str(inversion_per_grid),
        "tick_size": str(tick_size),
        "lot_size": str(lot_size),
        "min_notional": str(min_notional),
        "warnings": warnings,
    }


@router.post("/preview")
async def preview_bot(req: CreateBotRequest, request: Request) -> dict[str, Any]:
    """Dry-run the grid math so the UI can surface qty/notional warnings."""
    meta = _apply_instrument_defaults(req, request.app)
    return _compute_preview(req, meta)


@router.post("")
async def create_bot(req: CreateBotRequest, request: Request) -> dict[str, Any]:
    """Create and start a new bot (paper mode by default)."""
    manager = request.app.state.bot_manager

    meta = _apply_instrument_defaults(req, request.app)
    preview = _compute_preview(req, meta)
    if Decimal(preview["qty_per_grid"]) <= 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "qty/grid = 0 después de quantizar. "
                "Aumentá investment, subí leverage, bajá n_grids, "
                f"o reducí lot_size (actual={req.lot_size})."
            ),
        )

    _enforce_global_limits(req, manager)

    config = _build_config(req)
    try:
        await manager.create_and_start(req.bot_id, config, mode=req.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    _auto_save_preset(req, preview)

    return {
        "status": "started",
        "bot_id": req.bot_id,
        "mode": req.mode,
        "preview": preview,
    }


def _auto_save_preset(req: CreateBotRequest, preview: dict[str, Any]) -> None:
    """Persist the config as a preset named after the bot_id for later reuse."""
    try:
        payload = req.model_dump()
        payload["_preview_snapshot"] = preview
        now = datetime.now(timezone.utc)
        with get_session() as session:
            existing = session.exec(
                select(BotPreset).where(BotPreset.name == req.bot_id)
            ).first()
            if existing is None:
                session.add(
                    BotPreset(
                        name=req.bot_id,
                        symbol=req.symbol,
                        direction=req.direction,
                        mode=req.mode,
                        config_json=json.dumps(payload, default=str),
                        source="auto-create",
                    )
                )
            else:
                existing.symbol = req.symbol
                existing.direction = req.direction
                existing.mode = req.mode
                existing.config_json = json.dumps(payload, default=str)
                existing.source = "auto-create"
                existing.updated_at = now
            session.commit()
    except Exception as e:  # pragma: no cover — best effort
        logger.warning("auto-save preset failed for %s: %s", req.bot_id, e)


@router.get("")
async def list_bots(request: Request) -> list[dict[str, Any]]:
    """List all bots with current status."""
    manager = request.app.state.bot_manager
    return manager.list_all()


@router.get("/{bot_id}")
async def get_bot(bot_id: str, request: Request) -> dict[str, Any]:
    """Get status of a specific bot."""
    manager = request.app.state.bot_manager
    try:
        return manager.get_status(bot_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id!r} not found")


@router.post("/{bot_id}/stop")
async def stop_bot(bot_id: str, request: Request) -> dict[str, Any]:
    """Stop a running bot."""
    manager = request.app.state.bot_manager
    try:
        await manager.stop_bot(bot_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Bot {bot_id!r} not found")
    return {"status": "stopped", "bot_id": bot_id}


@router.post("/{bot_id}/pause")
async def pause_bot(bot_id: str, request: Request) -> dict[str, Any]:
    """Pause a running bot."""
    manager = request.app.state.bot_manager
    try:
        await manager.pause_bot(bot_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "paused", "bot_id": bot_id}


@router.post("/{bot_id}/resume")
async def resume_bot(bot_id: str, request: Request) -> dict[str, Any]:
    """Resume a paused bot."""
    manager = request.app.state.bot_manager
    try:
        await manager.resume_bot(bot_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "resumed", "bot_id": bot_id}


@router.post("/reconcile")
async def reconcile_bots(request: Request) -> dict[str, Any]:
    """Manually trigger a reconciliation pass across all live bots."""
    manager = request.app.state.bot_manager
    results = await manager.reconcile_all_live()
    return {"results": results}


@router.post("/kill-switch")
async def kill_switch(request: Request) -> dict[str, Any]:
    """Emergency stop all bots."""
    manager = request.app.state.bot_manager
    count = await manager.stop_all(reason="kill_switch")
    return {"status": "all_stopped", "bots_stopped": count}
