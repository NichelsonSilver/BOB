"""Tests for preset CRUD endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from bob.api.routes.presets import router as presets_router
from fastapi import FastAPI


def _app() -> TestClient:
    app = FastAPI()
    app.include_router(presets_router)
    return TestClient(app)


def _payload(name: str = "btc-long", **overrides):
    cfg = {
        "bot_id": name,
        "symbol": "BTC_USDT_Perp",
        "direction": "long",
        "price_low": "90000",
        "price_high": "100000",
        "n_grids": 20,
        "investment_usdt": "100",
        "leverage": 5,
        "spacing": "arithmetic",
        "mode": "paper",
    }
    cfg.update(overrides)
    return {"name": name, "config": cfg, "source": "manual"}


def test_save_and_list(in_memory_engine):
    client = _app()
    r = client.post("/api/presets", json=_payload("btc-1"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "btc-1"
    assert body["config"]["symbol"] == "BTC_USDT_Perp"

    r = client.get("/api/presets")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "btc-1"


def test_save_upserts_on_same_name(in_memory_engine):
    client = _app()
    client.post("/api/presets", json=_payload("eth-1"))
    # Update with different investment
    r = client.post(
        "/api/presets",
        json=_payload("eth-1", investment_usdt="250"),
    )
    assert r.status_code == 200
    assert r.json()["config"]["investment_usdt"] == "250"

    # Still only one row
    assert len(client.get("/api/presets").json()) == 1


def test_get_missing_returns_404(in_memory_engine):
    client = _app()
    r = client.get("/api/presets/does-not-exist")
    assert r.status_code == 404


def test_delete_preset(in_memory_engine):
    client = _app()
    client.post("/api/presets", json=_payload("to-delete"))
    r = client.delete("/api/presets/to-delete")
    assert r.status_code == 200
    assert r.json()["status"] == "deleted"
    assert client.get("/api/presets/to-delete").status_code == 404


def test_save_rejects_missing_symbol(in_memory_engine):
    client = _app()
    r = client.post(
        "/api/presets",
        json={"name": "bad", "config": {"direction": "long"}},
    )
    assert r.status_code == 400


def test_save_rejects_blank_name(in_memory_engine):
    client = _app()
    r = client.post(
        "/api/presets",
        json={"name": "   ", "config": {"symbol": "BTC", "direction": "long"}},
    )
    assert r.status_code == 400
