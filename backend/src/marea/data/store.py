"""Persistencia de MAREA en SQLite (`backend/marea.db`).

sqlite3 plano y no SQLModel: `SQLModel.metadata` es global, y cualquier proceso
que importara estas tablas junto con las de `bob` las crearía también en
`bob.db` al primer `create_all`. Una base propia con DDL explícito deja el
módulo cripto intacto (regla 12).
"""

from __future__ import annotations

import sqlite3
import zlib
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from marea.data.binance_funding import FundingPoint
from marea.data.cftc import CotRow
from marea.data.tiingo import DailyBar
from marea.defi_categories import CategoryTvl
from marea.options import OptionsSnapshot
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
-- Series de DefiLlama con vintages construidas: stablecoins (M5.2) y, desde
-- la M5.7, DEFI_TVL y DEX_VOLUME. La fuente no tiene vintages: cada corrida
-- es una (marea.pit.apply_snapshot). Mismo esquema que fred_period.
CREATE TABLE IF NOT EXISTS stable_period (
    series_id      TEXT NOT NULL,
    obs_date       TEXT NOT NULL,
    realtime_start TEXT NOT NULL,
    realtime_end   TEXT NOT NULL,
    value          REAL,
    PRIMARY KEY (series_id, obs_date, realtime_start)
);
CREATE TABLE IF NOT EXISTS stable_run (
    series_id   TEXT NOT NULL,
    vintage     TEXT NOT NULL,
    run_at_utc  TEXT NOT NULL,
    new         INTEGER NOT NULL,
    revised     INTEGER NOT NULL,
    removed     INTEGER NOT NULL,
    max_revision REAL NOT NULL,
    PRIMARY KEY (series_id, run_at_utc)
);
CREATE TABLE IF NOT EXISTS funding (
    symbol       TEXT NOT NULL,
    funding_time INTEGER NOT NULL,  -- epoch ms UTC: se conoce en ese instante
    rate         REAL NOT NULL,
    PRIMARY KEY (symbol, funding_time)
);
-- Opciones de Deribit: la fuente no tiene historia; cada foto es irrecuperable.
CREATE TABLE IF NOT EXISTS options_snapshot (
    currency      TEXT NOT NULL,
    captured_at   TEXT NOT NULL,  -- UTC; la foto se conoce en ese instante
    instruments   INTEGER NOT NULL,
    underlying    REAL,
    oi_calls      REAL NOT NULL,
    oi_puts       REAL NOT NULL,
    vol_calls     REAL NOT NULL,
    vol_puts      REAL NOT NULL,
    oi_usd        REAL NOT NULL,
    near_oi_calls REAL NOT NULL,
    near_oi_puts  REAL NOT NULL,
    PRIMARY KEY (currency, captured_at)
);
-- Respuesta cruda comprimida (zlib): permite calcular a futuro metricas que
-- hoy nadie penso, sobre toda la historia acumulada.
CREATE TABLE IF NOT EXISTS options_raw (
    currency    TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    body_zlib   BLOB NOT NULL,
    PRIMARY KEY (currency, captured_at)
);
-- Paginas vistas de Wikipedia: no se revisan; el dia D se conoce en D+1.
CREATE TABLE IF NOT EXISTS wiki_views (
    article TEXT NOT NULL,  -- "en.wikipedia:Bitcoin"
    day     TEXT NOT NULL,
    views   INTEGER NOT NULL,
    PRIMARY KEY (article, day)
);
-- TVL de DeFi por categoria (M7): una foto por dia UTC. DefiLlama no da
-- historia gratis por categoria, asi que la historia es la suma de las fotos.
-- Correr dos veces el mismo dia reemplaza la foto de ese dia.
CREATE TABLE IF NOT EXISTS defi_category (
    captured_on         TEXT NOT NULL,  -- dia UTC de la corrida
    captured_at         TEXT NOT NULL,  -- instante UTC exacto
    category            TEXT NOT NULL,
    tvl                 REAL NOT NULL,
    protocols           INTEGER NOT NULL,
    now_day             REAL NOT NULL,
    prev_day            REAL NOT NULL,
    now_week            REAL NOT NULL,
    prev_week           REAL NOT NULL,
    now_month           REAL NOT NULL,
    prev_month          REAL NOT NULL,
    tvl_unmatched_month REAL NOT NULL,
    PRIMARY KEY (captured_on, category)
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


def load_stable_periods(conn: sqlite3.Connection, series_id: str) -> list[Period]:
    rows = conn.execute(
        "SELECT obs_date, realtime_start, realtime_end, value FROM stable_period "
        "WHERE series_id = ? ORDER BY obs_date, realtime_start",
        (series_id,),
    )
    return [
        Period(date.fromisoformat(o), date.fromisoformat(s), date.fromisoformat(e), v)
        for o, s, e, v in rows
    ]


def save_stable_run(
    conn: sqlite3.Connection,
    series_id: str,
    periods: Iterable[Period],
    vintage: date,
    new: int,
    revised: int,
    removed: int,
    max_revision: float,
) -> None:
    """Reescribe los períodos y registra la corrida, en una sola transacción.

    El registro de corridas es el log de revisiones: cuántas filas cambió la
    fuente entre una corrida y la siguiente, y cuánto.
    """
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
        conn.execute("DELETE FROM stable_period WHERE series_id = ?", (series_id,))
        conn.executemany("INSERT INTO stable_period VALUES (?, ?, ?, ?, ?)", rows)
        conn.execute(
            "INSERT INTO stable_run VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                series_id,
                vintage.isoformat(),
                datetime.now(UTC).isoformat(timespec="microseconds"),
                new,
                revised,
                removed,
                max_revision,
            ),
        )


def stable_runs(conn: sqlite3.Connection, series_id: str) -> list[dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM stable_run WHERE series_id = ? ORDER BY run_at_utc", (series_id,)
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur]


def last_funding_time(conn: sqlite3.Connection, symbol: str) -> int | None:
    row = conn.execute(
        "SELECT MAX(funding_time) FROM funding WHERE symbol = ?", (symbol,)
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def upsert_funding(conn: sqlite3.Connection, points: Iterable[FundingPoint]) -> int:
    data = [(p.symbol, p.funding_time, p.rate) for p in points]
    with conn:
        conn.executemany("INSERT OR REPLACE INTO funding VALUES (?, ?, ?)", data)
    return len(data)


def load_funding(conn: sqlite3.Connection, symbol: str) -> list[FundingPoint]:
    rows = conn.execute(
        "SELECT symbol, funding_time, rate FROM funding WHERE symbol = ? ORDER BY funding_time",
        (symbol,),
    )
    return [FundingPoint(s, int(t), float(r)) for s, t, r in rows]


def save_options(conn: sqlite3.Connection, snap: OptionsSnapshot, raw: bytes) -> None:
    ts = snap.captured_at.isoformat(timespec="seconds")
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO options_snapshot VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                snap.currency,
                ts,
                snap.instruments,
                snap.underlying,
                snap.oi_calls,
                snap.oi_puts,
                snap.vol_calls,
                snap.vol_puts,
                snap.oi_usd,
                snap.near_oi_calls,
                snap.near_oi_puts,
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO options_raw VALUES (?, ?, ?)",
            (snap.currency, ts, zlib.compress(raw, 9)),
        )


def load_options(conn: sqlite3.Connection, currency: str) -> list[OptionsSnapshot]:
    rows = conn.execute(
        "SELECT * FROM options_snapshot WHERE currency = ? ORDER BY captured_at", (currency,)
    )
    return [
        OptionsSnapshot(
            c,
            datetime.fromisoformat(t),
            int(n),
            float(u) if u is not None else float("nan"),
            *(float(x) for x in rest),
        )
        for c, t, n, u, *rest in rows
    ]


def load_options_raw(conn: sqlite3.Connection, currency: str, captured_at: str) -> bytes | None:
    row = conn.execute(
        "SELECT body_zlib FROM options_raw WHERE currency = ? AND captured_at = ?",
        (currency, captured_at),
    ).fetchone()
    return zlib.decompress(row[0]) if row else None


def last_wiki_day(conn: sqlite3.Connection, article: str) -> date | None:
    row = conn.execute("SELECT MAX(day) FROM wiki_views WHERE article = ?", (article,)).fetchone()
    return date.fromisoformat(row[0]) if row and row[0] else None


def count_wiki(conn: sqlite3.Connection, article: str) -> int:
    row = conn.execute("SELECT COUNT(*) FROM wiki_views WHERE article = ?", (article,)).fetchone()
    return int(row[0])


def upsert_wiki(conn: sqlite3.Connection, article: str, rows: Iterable[tuple[date, int]]) -> int:
    data = [(article, d.isoformat(), v) for d, v in rows]
    with conn:
        conn.executemany("INSERT OR REPLACE INTO wiki_views VALUES (?, ?, ?)", data)
    return len(data)


def load_wiki(conn: sqlite3.Connection, article: str) -> list[tuple[date, int]]:
    rows = conn.execute(
        "SELECT day, views FROM wiki_views WHERE article = ? ORDER BY day", (article,)
    )
    return [(date.fromisoformat(d), int(v)) for d, v in rows]


_DEFI_FIELDS = (
    "category, tvl, protocols, now_day, prev_day, now_week, prev_week, "
    "now_month, prev_month, tvl_unmatched_month"
)


def save_defi_categories(
    conn: sqlite3.Connection, captured_at: datetime, rows: Iterable[CategoryTvl]
) -> int:
    """Guarda la foto del día UTC de `captured_at`, reemplazando la anterior del mismo día."""
    day = captured_at.astimezone(UTC).date().isoformat()
    at = captured_at.astimezone(UTC).isoformat(timespec="seconds")
    data = [
        (
            day,
            at,
            r.category,
            r.tvl,
            r.protocols,
            r.now_day,
            r.prev_day,
            r.now_week,
            r.prev_week,
            r.now_month,
            r.prev_month,
            r.tvl_unmatched_month,
        )
        for r in rows
    ]
    with conn:
        conn.execute("DELETE FROM defi_category WHERE captured_on = ?", (day,))
        conn.executemany(
            "INSERT INTO defi_category (captured_on, captured_at, "
            f"{_DEFI_FIELDS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            data,
        )
    return len(data)


def load_defi_categories(conn: sqlite3.Connection) -> dict[date, tuple[str, list[CategoryTvl]]]:
    """Todas las fotos: día UTC → (instante de captura, categorías por TVL)."""
    out: dict[date, tuple[str, list[CategoryTvl]]] = {}
    rows = conn.execute(
        f"SELECT captured_on, captured_at, {_DEFI_FIELDS} FROM defi_category "
        "ORDER BY captured_on, tvl DESC"
    )
    for day, at, cat, tvl, n, *rest in rows:
        d = date.fromisoformat(day)
        row = CategoryTvl(cat, float(tvl), int(n), *(float(v) for v in rest))
        out.setdefault(d, (at, []))[1].append(row)
    return out
