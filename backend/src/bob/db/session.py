"""Database session management — SQLite via SQLModel."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent.parent  # backend/
_DB_PATH = _BACKEND_DIR / "bob.db"

_engine = create_engine(
    f"sqlite:///{_DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False},
)


#: Columnas agregadas después del esquema inicial. Cada entrada es
#: (tabla, columna, spec SQL). `create_all` crea tablas nuevas pero NUNCA toca
#: una existente: sin esto, la DB con 69k velas seguiría con la tabla de
#: derivados vieja y los campos nuevos fallarían en silencio al leer.
#:
#: Aditivo y solo aditivo: nada de DROP ni de cambios de tipo. Es idempotente
#: y corre en cada init_db() — un PRAGMA por columna no cuesta nada.
_ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    # Fase 2b — el archivo histórico trae dos poblaciones de top traders que
    # los endpoints /futures/data/* no dan en el mismo request.
    ("derivativesnapshot", "top_trader_account_ratio", "TEXT"),
    ("derivativesnapshot", "top_trader_position_ratio", "TEXT"),
    # Funding: vive en la misma tabla bajo period="funding".
    ("derivativesnapshot", "funding_rate", "TEXT"),
]


def _apply_additive_columns(engine: Engine) -> None:
    """Agrega las columnas que aparecieron después del esquema inicial (SQLite)."""
    with engine.connect() as conn:
        for table, column, spec in _ADDITIVE_COLUMNS:
            rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            if not rows:
                continue  # la tabla aún no existe: create_all ya la dejó al día
            if column in {row[1] for row in rows}:
                continue
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
        conn.commit()


def init_db() -> None:
    """Crea las tablas que falten y aplica las migraciones aditivas."""
    import bob.db.models  # noqa: F401 — ensure models are registered

    SQLModel.metadata.create_all(_engine)
    _apply_additive_columns(_engine)


def get_session() -> Session:
    """Return a new SQLModel session."""
    return Session(_engine)
