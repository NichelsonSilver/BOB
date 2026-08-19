"""Database session management — SQLite via SQLModel."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent.parent  # backend/
_DB_PATH = _BACKEND_DIR / "bob.db"

_engine = create_engine(
    f"sqlite:///{_DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    """Create all tables if they don't exist.

    El esquema partió de cero con el pivote a asistente (2026-08-19); si en
    el futuro se agregan columnas, usar el patrón de migraciones aditivas
    que está en legacy/grvt-grid:backend/src/bob/db/session.py.
    """
    import bob.db.models  # noqa: F401 — ensure models are registered

    SQLModel.metadata.create_all(_engine)


def get_session() -> Session:
    """Return a new SQLModel session."""
    return Session(_engine)
