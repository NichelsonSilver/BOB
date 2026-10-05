"""Ruta `/api/macro/liquidity`: lee el reporte guardado, no lo calcula ni escribe."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bob.main import app
from marea import api
from marea.data import store


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "m.db"
    monkeypatch.setattr(api, "_db_path", lambda: path)
    return path


def _save(path: Path, as_of: str) -> None:
    conn = store.connect(path)
    store.save_liquidity_report(conn, as_of, json.dumps({"as_of": as_of, "regime": {}}))
    conn.close()


def test_sin_reporte_dice_que_correr(db: Path) -> None:
    res = TestClient(app).get("/api/macro/liquidity")
    assert res.status_code == 404 and "marea.monitor" in res.json()["detail"]
    assert not db.exists()  # leer no crea la base


def test_devuelve_el_mas_reciente_y_marca_vencido(db: Path) -> None:
    today = datetime.now(UTC).date()
    _save(db, (today - timedelta(days=30)).isoformat())
    _save(db, (today - timedelta(days=2)).isoformat())
    body = TestClient(app).get("/api/macro/liquidity").json()
    assert body["age_days"] == 2 and body["stale"] is False

    conn = store.connect(db)
    conn.execute(
        "DELETE FROM liquidity_report WHERE as_of > ?", ((today - timedelta(days=9)).isoformat(),)
    )
    conn.commit()
    conn.close()
    body = TestClient(app).get("/api/macro/liquidity").json()
    assert body["age_days"] == 30 and body["stale"] is True
