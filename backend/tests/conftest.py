"""Shared test fixtures.

The production engine points at the real SQLite file. For tests we swap
`_engine` to an in-memory DB and create tables fresh per test so there
is no cross-test state.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from bob.db import session as session_mod


@pytest.fixture
def in_memory_engine(monkeypatch):
    # StaticPool keeps a single shared connection so the threadpool-run
    # endpoint sees the same in-memory database as the test setup.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import bob.db.models  # noqa: F401 — ensure models are registered

    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(session_mod, "_engine", engine)
    return engine


@pytest.fixture
def decimal_close():
    def _close(a, b, tol="0.0001"):
        return abs(Decimal(a) - Decimal(b)) <= Decimal(tol)

    return _close
