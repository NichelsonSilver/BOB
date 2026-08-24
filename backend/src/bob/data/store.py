"""Persistencia de klines en SQLite y carga a series numéricas.

Frontera de pureza (regla 3 de CLAUDE.md): este módulo es el último que
toca I/O. Devuelve un `OHLCVSeries` de arrays numpy — de ahí en adelante
`signals/` y `models/` trabajan con números puros, sin saber que existe
una base de datos ni una API.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np
from loguru import logger
from sqlalchemy import func
from sqlmodel import Session, col, select

from bob.data.binance_rest import INTERVAL_MS, Kline
from bob.db.models import CandleRecord
from bob.db.session import get_session, init_db


@dataclass(frozen=True)
class OHLCVSeries:
    """Serie de velas cerradas, contigua y ordenada, lista para el motor numérico.

    Todos los arrays tienen el mismo largo y el índice `i` refiere a la misma
    vela en todos. `open_time` en epoch ms UTC.

    Invariante que sostiene el resto del pipeline: `open_time` es
    estrictamente creciente. Los huecos (velas que Binance no entregó) NO se
    rellenan con datos sintéticos — se reportan en `gaps`, porque inventar
    velas es inventar retornos.
    """

    symbol: str
    timeframe: str
    open_time: np.ndarray  # int64, epoch ms
    open: np.ndarray  # float64
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    quote_volume: np.ndarray
    taker_buy_volume: np.ndarray
    n_trades: np.ndarray  # int64

    def __len__(self) -> int:
        return int(self.open_time.shape[0])

    @property
    def interval_ms(self) -> int:
        return INTERVAL_MS[self.timeframe]

    @property
    def gaps(self) -> list[tuple[int, int]]:
        """Huecos detectados como (open_time_previo, open_time_siguiente)."""
        if len(self) < 2:
            return []
        step = self.interval_ms
        deltas = np.diff(self.open_time)
        idx = np.flatnonzero(deltas > step)
        return [(int(self.open_time[i]), int(self.open_time[i + 1])) for i in idx]

    def slice(self, start: int, stop: int | None = None) -> OHLCVSeries:
        """Sub-serie por índice posicional (no por timestamp)."""
        sl = slice(start, stop)
        return OHLCVSeries(
            symbol=self.symbol,
            timeframe=self.timeframe,
            open_time=self.open_time[sl],
            open=self.open[sl],
            high=self.high[sl],
            low=self.low[sl],
            close=self.close[sl],
            volume=self.volume[sl],
            quote_volume=self.quote_volume[sl],
            taker_buy_volume=self.taker_buy_volume[sl],
            n_trades=self.n_trades[sl],
        )


def _validate_monotonic(open_time: np.ndarray, symbol: str, timeframe: str) -> None:
    if open_time.size and not np.all(np.diff(open_time) > 0):
        raise ValueError(
            f"{symbol} {timeframe}: open_time no es estrictamente creciente "
            "(duplicados o desorden en la DB)"
        )


def series_from_klines(symbol: str, timeframe: str, klines: Sequence[Kline]) -> OHLCVSeries:
    """Construye la serie numérica directamente desde klines de la API.

    Útil para tests y para pipelines en memoria que no quieren pasar por DB.
    """
    ordered = sorted(klines, key=lambda k: k.open_time)
    open_time = np.array([k.open_time for k in ordered], dtype=np.int64)
    _validate_monotonic(open_time, symbol, timeframe)
    return OHLCVSeries(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=np.array([float(k.open) for k in ordered], dtype=np.float64),
        high=np.array([float(k.high) for k in ordered], dtype=np.float64),
        low=np.array([float(k.low) for k in ordered], dtype=np.float64),
        close=np.array([float(k.close) for k in ordered], dtype=np.float64),
        volume=np.array([float(k.volume) for k in ordered], dtype=np.float64),
        quote_volume=np.array([float(k.quote_volume) for k in ordered], dtype=np.float64),
        taker_buy_volume=np.array([float(k.taker_buy_volume) for k in ordered], dtype=np.float64),
        n_trades=np.array([k.n_trades for k in ordered], dtype=np.int64),
    )


def upsert_klines(
    symbol: str,
    timeframe: str,
    klines: Iterable[Kline],
    session: Session | None = None,
) -> int:
    """Inserta o actualiza velas. Única por (symbol, timeframe, open_time).

    Devuelve cuántas filas se escribieron. Idempotente: reejecutar la misma
    descarga no duplica ni corrompe.
    """
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None

    try:
        rows = list(klines)
        if not rows:
            return 0

        existing_times = set(
            session.exec(
                select(CandleRecord.open_time).where(
                    CandleRecord.symbol == symbol,
                    CandleRecord.timeframe == timeframe,
                    CandleRecord.open_time >= min(k.open_time for k in rows),
                    CandleRecord.open_time <= max(k.open_time for k in rows),
                )
            ).all()
        )

        written = 0
        for k in rows:
            if k.open_time in existing_times:
                record = session.exec(
                    select(CandleRecord).where(
                        CandleRecord.symbol == symbol,
                        CandleRecord.timeframe == timeframe,
                        CandleRecord.open_time == k.open_time,
                    )
                ).first()
                if record is None:  # pragma: no cover — carrera improbable
                    continue
            else:
                record = CandleRecord(
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time=k.open_time,
                    close_time=k.close_time,
                    open=k.open,
                    high=k.high,
                    low=k.low,
                    close=k.close,
                    volume=k.volume,
                )
            record.close_time = k.close_time
            record.open = k.open
            record.high = k.high
            record.low = k.low
            record.close = k.close
            record.volume = k.volume
            record.quote_volume = k.quote_volume
            record.taker_buy_volume = k.taker_buy_volume
            record.n_trades = k.n_trades
            session.add(record)
            written += 1

        session.commit()
        return written
    finally:
        if owns:
            session.close()


def load_series(
    symbol: str,
    timeframe: str,
    start_time: int | None = None,
    end_time: int | None = None,
    session: Session | None = None,
) -> OHLCVSeries:
    """Carga velas persistidas como arrays numpy, ordenadas por open_time."""
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None

    try:
        stmt = select(CandleRecord).where(
            CandleRecord.symbol == symbol, CandleRecord.timeframe == timeframe
        )
        if start_time is not None:
            stmt = stmt.where(CandleRecord.open_time >= start_time)
        if end_time is not None:
            stmt = stmt.where(CandleRecord.open_time <= end_time)
        stmt = stmt.order_by(col(CandleRecord.open_time))
        records = list(session.exec(stmt).all())
    finally:
        if owns:
            session.close()

    open_time = np.array([r.open_time for r in records], dtype=np.int64)
    _validate_monotonic(open_time, symbol, timeframe)
    series = OHLCVSeries(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        open=np.array([float(r.open) for r in records], dtype=np.float64),
        high=np.array([float(r.high) for r in records], dtype=np.float64),
        low=np.array([float(r.low) for r in records], dtype=np.float64),
        close=np.array([float(r.close) for r in records], dtype=np.float64),
        volume=np.array([float(r.volume) for r in records], dtype=np.float64),
        quote_volume=np.array([float(r.quote_volume) for r in records], dtype=np.float64),
        taker_buy_volume=np.array([float(r.taker_buy_volume) for r in records], dtype=np.float64),
        n_trades=np.array([r.n_trades for r in records], dtype=np.int64),
    )
    gaps = series.gaps
    if gaps:
        logger.warning(
            "{} {}: {} hueco(s) en la serie — no se rellenan, se reportan",
            symbol,
            timeframe,
            len(gaps),
        )
    return series


def coverage(symbol: str, timeframe: str, session: Session | None = None) -> dict[str, int]:
    """Resumen de qué hay persistido: n velas, primer y último open_time."""
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None
    try:
        # Agregación en SQL (no traer 70k filas para contarlas), pero con
        # `select` tipado en vez de texto crudo: el ORM arma los parámetros y
        # mypy verifica las columnas.
        # `col()` es el puente de SQLModel entre el atributo tipado del modelo
        # y la columna SQL que esperan las funciones de agregación.
        open_time_col = col(CandleRecord.open_time)
        stmt = select(
            func.count(open_time_col),
            func.min(open_time_col),
            func.max(open_time_col),
        ).where(CandleRecord.symbol == symbol, CandleRecord.timeframe == timeframe)
        count, first, last = session.exec(stmt).one()
        return {
            "n_candles": int(count or 0),
            "first_open_time": int(first or 0),
            "last_open_time": int(last or 0),
        }
    finally:
        if owns:
            session.close()
