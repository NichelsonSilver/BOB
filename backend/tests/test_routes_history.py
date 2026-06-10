"""Integration tests for /api/history/* endpoints."""

from __future__ import annotations

from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bob.api.routes.history import router as history_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(history_router)
    return TestClient(app)


def test_list_fills_returns_seeded(seed_fills):
    client = _client()
    r = client.get("/api/history/fills")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    assert {f["bot_id"] for f in body} == {"bot-a", "bot-b"}
    # ordered desc by filled_at
    assert body[0]["filled_at"] >= body[-1]["filled_at"]


def test_list_fills_filter_by_bot(seed_fills):
    client = _client()
    r = client.get("/api/history/fills", params={"bot_id": "bot-a"})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert all(f["bot_id"] == "bot-a" for f in body)


def test_list_fills_filter_by_symbol(seed_fills):
    client = _client()
    r = client.get("/api/history/fills", params={"symbol": "ETH_USDT_Perp"})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["bot_id"] == "bot-b"


def test_list_orders(seed_fills):
    client = _client()
    r = client.get("/api/history/orders")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["status"] == "filled"


def test_pnl_aggregate(seed_fills):
    client = _client()
    r = client.get("/api/history/pnl", params={"bot_id": "bot-a"})
    assert r.status_code == 200
    body = r.json()
    # bot-a fills: fees = 0.019 + (-0.0096), pnl sum = 1.0
    assert Decimal(body["total_fees"]) == Decimal("0.019") + Decimal("-0.0096")
    assert Decimal(body["realized_pnl"]) == Decimal("1.0")
    assert body["fill_count"] == 2


def test_equity_curve(seed_fills):
    client = _client()
    r = client.get("/api/history/equity-curve")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    cumulative = [Decimal(pt["cumulative_pnl"]) for pt in body]
    # strictly non-decreasing when pnls are non-negative, but bot-b has pnl=0
    assert cumulative[-1] == Decimal("1.0")


def test_daily_pnl(seed_fills):
    client = _client()
    r = client.get("/api/history/pnl/daily")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["date"] == "2026-04-01"


def test_fees_global(seed_fills):
    client = _client()
    r = client.get("/api/history/fees")
    assert r.status_code == 200
    body = r.json()
    assert "by_bot" in body
    assert set(body["by_bot"].keys()) == {"bot-a", "bot-b"}
