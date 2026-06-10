"""Integration tests for /api/points."""

from __future__ import annotations

from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bob.api.routes.points import router as points_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(points_router)
    return TestClient(app)


def test_points_total(seed_fills):
    client = _client()
    r = client.get("/api/points")
    assert r.status_code == 200
    body = r.json()
    # volume = 95*0.001k + 96*0.001k + 3200*0.05 = 95 + 96 + 160 = 351
    assert Decimal(body["gross_volume"]) == Decimal("351")
    # maker rebate = 0.0096 (the negative fee)
    assert Decimal(body["maker_rebates"]) == Decimal("0.0096")
    assert body["fill_count"] == 3
    # points = 351/10 = 35.10
    assert Decimal(body["estimated_points"]) == Decimal("35.10")


def test_points_by_bot(seed_fills):
    client = _client()
    r = client.get("/api/points/by-bot")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"bot-a", "bot-b"}
    assert Decimal(body["bot-a"]["gross_volume"]) == Decimal("191")
    assert Decimal(body["bot-b"]["gross_volume"]) == Decimal("160")
