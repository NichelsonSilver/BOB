"""Shared test fixtures.

The production engine points at the real SQLite file. For tests we swap
`_engine` to an in-memory DB and create tables fresh per test so there
is no cross-test state.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from bob.data.store import OHLCVSeries
from bob.db import session as session_mod

TF_MS = 900_000


def synthetic_series(n: int = 4000, seed: int = 0, symbol: str = "TESTUSDT") -> OHLCVSeries:
    """Serie sintética con la propiedad que hace aprendible la volatilidad.

    Volatilidad con clustering Y reversión a la media: un AR(1) en
    log-volatilidad. Es la propiedad real del mercado (Mandelbrot) y la que
    permite que el target de volatilidad tenga señal. Un paseo aleatorio en
    log-vol tendría clustering pero sería no estacionario: el nivel del test
    no se parecería al del train y ningún modelo honesto podría extrapolarlo.
    """
    rng = np.random.default_rng(seed)
    log_vol = np.empty(n)
    log_vol[0] = np.log(0.003)
    phi, mu = 0.98, np.log(0.003)
    for i in range(1, n):
        log_vol[i] = mu + phi * (log_vol[i - 1] - mu) + rng.normal(0, 0.06)
    vol = np.exp(log_vol)
    ret = rng.normal(0, 1, n) * vol
    close = 2000.0 * np.exp(np.cumsum(ret))
    spread = np.abs(rng.normal(0, 0.0015, n)) + 0.0005
    open_ = np.concatenate([[close[0]], close[:-1]])
    volume = np.abs(rng.lognormal(5, 1, n))
    return OHLCVSeries(
        symbol=symbol,
        timeframe="15m",
        open_time=np.arange(n, dtype=np.int64) * TF_MS + 1_700_000_000_000,
        open=open_,
        high=np.maximum(close * (1 + spread), np.maximum(open_, close)),
        low=np.minimum(close * (1 - spread), np.minimum(open_, close)),
        close=close,
        volume=volume,
        quote_volume=volume * close,
        taker_buy_volume=volume * rng.uniform(0.35, 0.65, n),
        n_trades=rng.integers(50, 5000, n).astype(np.int64),
    )


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
