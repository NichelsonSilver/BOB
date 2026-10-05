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

from marea.data.cftc import CotRow
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
CREATE TABLE IF NOT EXISTS cot (
    code           TEXT NOT NULL,
    report_date    TEXT NOT NULL,  -- "al martes"
    available_date TEXT NOT NULL,  -- cuando se conoce: ver marea.data.cftc
    name           TEXT NOT NULL,
    open_interest  REAL NOT NULL,
    spec_long      REAL NOT NULL,
    spec_short     REAL NOT NULL,
    inst_long      REAL,
    inst_short     REAL,
    PRIMARY KEY (code, report_date)
);
CREATE TABLE IF NOT EXISTS bcch_obs (
    series_id      TEXT NOT NULL,
    day            TEXT NOT NULL,
    available_date TEXT NOT NULL,  -- day + lag declarado en el catalogo
    value          REAL NOT NULL,
    PRIMARY KEY (series_id, day)
);
CREATE TABLE IF NOT EXISTS liquidity_report (
    as_of          TEXT PRIMARY KEY,  -- fecha "conocido al"
    computed_at    TEXT NOT NULL,
    payload        TEXT NOT NULL      -- JSON de marea.monitor.report
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


def last_cot_date(conn: sqlite3.Connection, code: str) -> date | None:
    row = conn.execute("SELECT MAX(report_date) FROM cot WHERE code = ?", (code,)).fetchone()
    return date.fromisoformat(row[0]) if row and row[0] else None


def load_cot(conn: sqlite3.Connection, code: str) -> list[CotRow]:
    rows = conn.execute(
        "SELECT code, report_date, available_date, name, open_interest, spec_long, "
        "spec_short, inst_long, inst_short FROM cot WHERE code = ? ORDER BY report_date",
        (code,),
    )
    return [
        CotRow(c, date.fromisoformat(r), date.fromisoformat(a), *rest) for c, r, a, *rest in rows
    ]


def upsert_cot(conn: sqlite3.Connection, rows: Iterable[CotRow]) -> int:
    data = [
        (
            r.code,
            r.report_date.isoformat(),
            r.available_date.isoformat(),
            r.name,
            r.open_interest,
            r.spec_long,
            r.spec_short,
            r.inst_long,
            r.inst_short,
        )
        for r in rows
    ]
    with conn:
        conn.executemany("INSERT OR REPLACE INTO cot VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", data)
    return len(data)


def load_bcch(conn: sqlite3.Connection, series_id: str) -> list[tuple[date, date, float]]:
    """(día, fecha en que se conoce, valor), ordenado por día."""
    rows = conn.execute(
        "SELECT day, available_date, value FROM bcch_obs WHERE series_id = ? ORDER BY day",
        (series_id,),
    )
    return [(date.fromisoformat(d), date.fromisoformat(a), v) for d, a, v in rows]


def upsert_bcch(
    conn: sqlite3.Connection, series_id: str, rows: Iterable[tuple[date, date, float]]
) -> int:
    data = [(series_id, d.isoformat(), a.isoformat(), v) for d, a, v in rows]
    with conn:
        conn.executemany("INSERT OR REPLACE INTO bcch_obs VALUES (?, ?, ?, ?)", data)
    return len(data)


def save_liquidity_report(conn: sqlite3.Connection, as_of: str, payload: str) -> None:
    """Un reporte por fecha "conocido al"; recalcular el mismo día lo reemplaza."""
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO liquidity_report VALUES (?, ?, ?)",
            (as_of, datetime.now(UTC).isoformat(timespec="seconds"), payload),
        )


def latest_liquidity_report(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT payload FROM liquidity_report ORDER BY as_of DESC LIMIT 1"
    ).fetchone()
    return str(row[0]) if row else None
