"""Persistencia de MAREA en SQLite (`backend/marea.db`).

sqlite3 plano y no SQLModel: `SQLModel.metadata` es global, y cualquier proceso
que importara estas tablas junto con las de `bob` las crearía también en
`bob.db` al primer `create_all`. Una base propia con DDL explícito deja el
módulo cripto intacto (regla 12).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from marea.data.tiingo import DailyBar
from marea.pit import Period

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fred_series (
    series_id         TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    frequency         TEXT NOT NULL,
    units             TEXT NOT NULL,
    observation_start TEXT NOT NULL,
    observation_end   TEXT NOT NULL,
    last_updated      TEXT NOT NULL,
    first_vintage     TEXT,
    last_vintage      TEXT,
    fallback_lag_days INTEGER,
    fetched_at_utc    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fred_period (
    series_id      TEXT NOT NULL,
    obs_date       TEXT NOT NULL,
    realtime_start TEXT NOT NULL,
    realtime_end   TEXT NOT NULL,
    value          REAL,
    PRIMARY KEY (series_id, obs_date, realtime_start)
);
CREATE TABLE IF NOT EXISTS etf_bar (
    ticker       TEXT NOT NULL,
    day          TEXT NOT NULL,
    open         REAL NOT NULL,
    high         REAL NOT NULL,
    low          REAL NOT NULL,
    close        REAL NOT NULL,
    volume       REAL NOT NULL,
    div_cash     REAL NOT NULL,
    split_factor REAL NOT NULL,
    -- Solo control cruzado: Tiingo lo recalcula hacia atras en cada dividendo.
    adj_close    REAL NOT NULL,
    PRIMARY KEY (ticker, day)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def load_periods(conn: sqlite3.Connection, series_id: str) -> list[Period]:
    rows = conn.execute(
        "SELECT obs_date, realtime_start, realtime_end, value FROM fred_period "
        "WHERE series_id = ? ORDER BY obs_date, realtime_start",
        (series_id,),
    )
    return [
        Period(date.fromisoformat(o), date.fromisoformat(s), date.fromisoformat(e), v)
        for o, s, e, v in rows
    ]


def replace_periods(conn: sqlite3.Connection, series_id: str, periods: Iterable[Period]) -> int:
    """Reescribe la serie completa en una transacción. Idempotente."""
    rows = [
        (
            series_id,
            p.obs_date.isoformat(),
            p.realtime_start.isoformat(),
            p.realtime_end.isoformat(),
            p.value,
        )
        for p in periods
    ]
    with conn:
        conn.execute("DELETE FROM fred_period WHERE series_id = ?", (series_id,))
        conn.executemany("INSERT INTO fred_period VALUES (?, ?, ?, ?, ?)", rows)
    return len(rows)


def series_meta(conn: sqlite3.Connection, series_id: str) -> dict[str, Any] | None:
    cur = conn.execute("SELECT * FROM fred_series WHERE series_id = ?", (series_id,))
    row = cur.fetchone()
    if row is None:
        return None
    cols = [c[0] for c in cur.description]
    return dict(zip(cols, row, strict=True))


def upsert_series_meta(
    conn: sqlite3.Connection,
    info: dict[str, Any],
    first_vintage: date | None,
    last_vintage: date | None,
    fallback_lag_days: int | None,
) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO fred_series VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                info["id"],
                info["title"],
                info["frequency_short"],
                info["units_short"],
                info["observation_start"],
                info["observation_end"],
                info["last_updated"],
                first_vintage.isoformat() if first_vintage else None,
                last_vintage.isoformat() if last_vintage else None,
                fallback_lag_days,
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )


def last_bar_day(conn: sqlite3.Connection, ticker: str) -> date | None:
    row = conn.execute("SELECT MAX(day) FROM etf_bar WHERE ticker = ?", (ticker,)).fetchone()
    return date.fromisoformat(row[0]) if row and row[0] else None


def load_bars(conn: sqlite3.Connection, ticker: str) -> list[DailyBar]:
    rows = conn.execute(
        "SELECT day, open, high, low, close, volume, div_cash, split_factor, adj_close "
        "FROM etf_bar WHERE ticker = ? ORDER BY day",
        (ticker,),
    )
    return [DailyBar(date.fromisoformat(d), *vals) for d, *vals in rows]


def upsert_bars(conn: sqlite3.Connection, ticker: str, bars: Iterable[DailyBar]) -> int:
    rows = [
        (
            ticker,
            b.day.isoformat(),
            b.open,
            b.high,
            b.low,
            b.close,
            b.volume,
            b.div_cash,
            b.split_factor,
            b.adj_close,
        )
        for b in bars
    ]
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO etf_bar VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
        )
    return len(rows)
