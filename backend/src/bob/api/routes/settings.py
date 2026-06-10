"""Settings / limits / kill switch config."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from bob.config import settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(request: Request) -> dict[str, Any]:
    """Read-only snapshot of runtime config and global limits.

    Credentials are never returned — only the env label and account id.
    """
    manager = request.app.state.bot_manager
    active = sum(1 for b in manager.list_all() if b.get("state") == "running")
    return {
        "env": settings.grvt_env,
        "trading_account_id": settings.grvt_trading_account_id,
        "limits": {
            "max_total_capital": settings.max_total_capital,
            "max_concurrent_bots": settings.max_concurrent_bots,
            "max_leverage": settings.max_leverage,
        },
        "runtime": {
            "active_bots": active,
            "total_bots": len(manager.list_all()),
        },
    }


@router.get("/limits")
def get_limits() -> dict[str, int]:
    return {
        "max_total_capital": settings.max_total_capital,
        "max_concurrent_bots": settings.max_concurrent_bots,
        "max_leverage": settings.max_leverage,
    }
