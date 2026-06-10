"""Integration tests for /api/settings."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bob.api.routes.settings import router as settings_router


def _client(manager) -> TestClient:
    app = FastAPI()
    app.include_router(settings_router)
    app.state.bot_manager = manager
    return TestClient(app)


def test_get_settings_shape(stub_manager_factory):
    manager = stub_manager_factory([
        {"bot_id": "a", "state": "running"},
        {"bot_id": "b", "state": "stopped"},
    ])
    r = _client(manager).get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert "env" in body
    assert "trading_account_id" in body
    assert "grvt_private_key" not in body  # credentials never leak
    assert body["limits"]["max_leverage"] >= 1
    assert body["runtime"]["active_bots"] == 1
    assert body["runtime"]["total_bots"] == 2


def test_get_limits(stub_manager_factory):
    r = _client(stub_manager_factory([])).get("/api/settings/limits")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"max_total_capital", "max_concurrent_bots", "max_leverage"}
