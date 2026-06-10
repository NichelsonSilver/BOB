"""Database session management — SQLite via SQLModel."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from bob.config import settings

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent.parent  # backend/
_DB_PATH = _BACKEND_DIR / "bob.db"

_engine = create_engine(
    f"sqlite:///{_DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False},
)


# Additive-only schema patches for SQLite. Each entry is
# (table, column, column_spec). Run on every init_db() — cheap, idempotent.
_ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    ("bot", "filled_buys_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("bot", "filled_sells_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("bot", "live_orders_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("bot", "exchange_ids_json", "TEXT NOT NULL DEFAULT '{}'"),
]


def _apply_additive_columns(engine) -> None:
    """Add columns that were introduced after the first schema. SQLite only."""
    with engine.connect() as conn:
        for table, column, spec in _ADDITIVE_COLUMNS:
            rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            existing = {r[1] for r in rows}
            if column in existing:
                continue
            conn.exec_driver_sql(
                f"ALTER TABLE {table} ADD COLUMN {column} {spec}"
            )
        conn.commit()


def init_db() -> None:
    """Create all tables if they don't exist, plus apply additive migrations."""
    import bob.db.models  # noqa: F401 — ensure models are registered

    SQLModel.metadata.create_all(_engine)
    _apply_additive_columns(_engine)


def get_session() -> Session:
    """Return a new SQLModel session."""
    return Session(_engine)
