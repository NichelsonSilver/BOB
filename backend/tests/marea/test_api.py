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


# --- M6.1: encabezado, forward del panel y narrativa ---------------------------------


def test_frescura_dice_la_fuente_mas_vieja(db: Path) -> None:
    assert TestClient(app).get("/api/macro/freshness").status_code == 404
    _save(db, datetime.now(UTC).date().isoformat())
    body = TestClient(app).get("/api/macro/freshness").json()
    assert any(s["source"] == "reporte de liquidez" for s in body["sources"])
    assert body["max_age_days"] == 0
    # Cada fuente con su umbral: el COT semanal tolera más que el funding diario.
    lim = {x["source"]: x["stale_after_days"] for x in body["sources"]}
    assert (
        lim["CFTC COT (reporte)"]
        > lim["reporte de liquidez"]
        > lim["Binance funding (día cerrado)"]
    )
    liq = next(x for x in body["sources"] if x["source"] == "reporte de liquidez")
    assert liq["stale"] is False
    assert "Tiingo ETFs" in body["stale"]  # sin dato = atrasada


def test_estado_del_panel_sin_ic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite3

    bob_db = tmp_path / "bob.db"
    monkeypatch.setattr(api, "_bob_db", lambda: bob_db)
    assert TestClient(app).get("/api/macro/panel-status").status_code == 404
    c = sqlite3.connect(bob_db)
    c.execute("CREATE TABLE panelforecastrecord (open_time INTEGER, is_backfill INTEGER)")
    t0 = int(datetime(2026, 9, 23, tzinfo=UTC).timestamp() * 1000)
    c.executemany(
        "INSERT INTO panelforecastrecord VALUES (?, ?)",
        [(t0, 1), (t0 + 86_400_000, 0), (t0 + 2 * 86_400_000, 0)],
    )
    c.commit()
    c.close()
    body = TestClient(app).get("/api/macro/panel-status").json()
    assert body["valid"] == 2 and body["interim_valid_days"] == 90
    assert body["eta_interim_at_current_rate"] is not None
    assert "no se reporta" in body["ic"]
    assert not any("probab" in k or "ic_" in k for k in body)


def test_narrativa_reciente(monkeypatch: pytest.MonkeyPatch) -> None:
    from marea import narrative

    monkeypatch.setattr(narrative, "read", lambda: [])
    body = TestClient(app).get("/api/macro/narrative?days=7").json()
    assert body == {"days": 7, "entries": []}
    assert TestClient(app).get("/api/macro/narrative?days=0").status_code == 422
