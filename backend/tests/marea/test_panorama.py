"""Panorama de ciclo y cripto (M5.6): bloques, brief y ruta de la API."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bob.main import app
from marea import api, brief, panorama
from marea.data import store
from marea.data.binance_funding import FundingPoint
from marea.options import OptionsSnapshot
from marea.pit import OPEN_END, Period, apply_snapshot
from marea.recession import bond_equivalent, probability

TODAY = date(2026, 10, 7)
D0 = date(2019, 1, 1)


def _fred(conn: sqlite3.Connection, sid: str, freq: str, values: list[tuple[date, float]]) -> None:
    vintage = date(2010, 1, 1)
    store.replace_periods(conn, sid, [Period(d, vintage, OPEN_END, v) for d, v in values])
    store.upsert_series_meta(
        conn,
        {
            "id": sid,
            "title": sid,
            "frequency_short": freq,
            "units_short": "x",
            "observation_start": values[0][0].isoformat(),
            "observation_end": values[-1][0].isoformat(),
            "last_updated": "2026-10-07",
        },
        vintage,
        vintage,
        1,
    )


def _days(n: int, start: date = D0) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


@pytest.fixture
def seeded(conn: sqlite3.Connection, tmp_path: Path) -> tuple[sqlite3.Connection, Path, Path]:
    days = _days((TODAY - D0).days)
    _fred(conn, "DGS10", "D", [(d, 4.0) for d in days])
    _fred(conn, "DTB3", "D", [(d, 3.5) for d in days])
    _fred(conn, "SAHMREALTIME", "M", [(date(2026, m, 1), 0.1 * m) for m in range(1, 10)])
    _fred(conn, "CBBTCUSD", "D", [(d, 10_000.0 * math.exp(0.001 * i)) for i, d in enumerate(days)])
    pts = []
    for i, d in enumerate(days[-400:]):
        t0 = int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)
        for h in (0, 8, 16):
            pts.append(FundingPoint("BTCUSDT", t0 + h * 3_600_000, 0.0001))
            pts.append(FundingPoint("ETHUSDT", t0 + h * 3_600_000, 0.0001 + 1e-6 * (i % 5)))
    store.upsert_funding(conn, pts)
    snap = OptionsSnapshot(
        "BTC", datetime(2026, 10, 7, 16, tzinfo=UTC), 10, 80_000.0, 100, 50, 10, 20, 1e9, 40, 20
    )
    store.save_options(conn, snap, b"{}")
    store.save_options(conn, OptionsSnapshot("ETH", *list(vars(snap).values())[1:]), b"{}")
    supply = [(d, 1e11 + 1e8 * i) for i, d in enumerate(days)]
    periods, diff = apply_snapshot([], supply, TODAY)
    store.save_stable_run(conn, "STABLE_USD", periods, TODAY, diff.new, 0, 0, 0.0)

    bob_db = tmp_path / "bob.db"
    b = sqlite3.connect(bob_db)
    b.execute(
        "CREATE TABLE candlerecord (symbol TEXT, timeframe TEXT, open_time INTEGER, "
        "close_time INTEGER, close REAL, quote_volume REAL)"
    )
    syms = ["BTCUSDT", *(f"A{i:02d}USDT" for i in range(55)), "USDCUSDT"]
    rows = []
    for j, d in enumerate(_days(300, date(2025, 12, 1))):
        ot = int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)
        for k, s in enumerate(syms):
            px = (
                100.0
                if s in ("BTCUSDT", "USDCUSDT")
                else 10.0 * (1 + 0.001 * j * (1 if k % 2 else -1))
            )
            rows.append((s, "1d", ot, ot + 86_399_999, px, 1e9))
    b.executemany("INSERT INTO candlerecord VALUES (?,?,?,?,?,?)", rows)
    b.commit()
    b.close()
    universe = tmp_path / "universe-1d.json"
    universe.write_text(
        json.dumps({"symbols": [{"symbol": s, "contract_type": "PERPETUAL"} for s in syms]}),
        encoding="utf-8",
    )
    return conn, bob_db, universe


def test_los_seis_bloques_responden_con_su_fecha(
    seeded: tuple[sqlite3.Connection, Path, Path],
) -> None:
    conn, bob_db, universe = seeded
    p = panorama.build(conn, bob_db, universe, TODAY)
    errors = {k: v["error"] for k, v in p.items() if isinstance(v, dict) and "error" in v}
    assert errors == {}
    assert p["recession"]["probability"] == pytest.approx(probability(4.0 - bond_equivalent(3.5)))
    assert p["recession"]["sahm"]["value"] == pytest.approx(0.9)
    assert p["btc_cycle"]["risk_full"] is not None and p["btc_cycle"]["zone"] is not None
    assert p["breadth"]["n_symbols"] == 56  # USDC excluida
    assert p["breadth"]["altseason"] == pytest.approx(0.5, abs=0.06)
    assert p["funding"]["btc_annual_pct"] == pytest.approx(0.0003 * 365 * 100)
    assert p["options"]["btc"]["pc_oi"] == pytest.approx(0.5)
    st = p["stablecoins"]
    assert st["change_30d_usd"] == pytest.approx(30 * 1e8)
    assert st["ssr"] is not None and st["net_mints_7d_usd"] == pytest.approx(1e8)


def test_base_vacia_no_tira_el_panorama(conn: sqlite3.Connection, tmp_path: Path) -> None:
    p = panorama.build(conn, tmp_path / "no.db", tmp_path / "no.json", TODAY)
    assert all("error" in v for k, v in p.items() if k != "as_of")


def test_vocabulario_y_percentil() -> None:
    assert panorama._zone(None) is None
    assert panorama._zone(0.1) == "baja del ciclo"
    assert panorama._zone(0.5) == "media del ciclo"
    assert panorama._zone(0.7) == "alta del ciclo"
    assert panorama._zone(0.9) == "extrema del ciclo"
    assert panorama._pct_rank([1.0, 2.0, float("nan"), 3.0], 2.5) == pytest.approx(2 / 3)
    assert panorama._pct_rank([], 1.0) is None


def test_brief_renderiza_el_bloque_y_los_errores(
    seeded: tuple[sqlite3.Connection, Path, Path],
) -> None:
    conn, bob_db, universe = seeded
    p = panorama.build(conn, bob_db, universe, TODAY)
    text = "\n".join(brief._render_cycle_crypto(p))
    assert "4b. CICLO Y CRIPTO" in text and "Altcoin Season" in text and "SSR" in text
    p["funding"] = {"error": "LookupError: falta funding"}
    assert "sin dato: LookupError" in "\n".join(brief._render_cycle_crypto(p))
    assert brief._render_cycle_crypto(None) == []


def test_ruta_cachea_y_dice_que_correr_sin_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api._panorama_cache.clear()
    monkeypatch.setattr(api, "_db_path", lambda: tmp_path / "no.db")
    res = TestClient(app).get("/api/macro/panorama")
    assert res.status_code == 404 and "ingest" in res.json()["detail"]

    calls: list[int] = []

    def fake(conn: Any, *_a: Any, **_k: Any) -> dict[str, Any]:
        calls.append(1)
        return {"as_of": "2026-10-07"}

    db = tmp_path / "m.db"
    store.connect(db).close()
    monkeypatch.setattr(api, "_db_path", lambda: db)
    monkeypatch.setattr(panorama, "build", fake)
    client = TestClient(app)
    assert client.get("/api/macro/panorama").json() == {"as_of": "2026-10-07"}
    assert client.get("/api/macro/panorama").status_code == 200
    assert len(calls) == 1
    api._panorama_cache.clear()
