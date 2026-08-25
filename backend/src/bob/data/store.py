"""Persistencia de klines en SQLite y carga a series numéricas.

Frontera de pureza (regla 3 de CLAUDE.md): este módulo es el último que
toca I/O. Devuelve un `OHLCVSeries` de arrays numpy — de ahí en adelante
`signals/` y `models/` trabajan con números puros, sin saber que existe
una base de datos ni una API.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
from loguru import logger
from sqlalchemy import Integer, func
from sqlalchemy.orm import Mapped
from sqlmodel import Session, col, select

from bob.data.binance_rest import INTERVAL_MS, Kline
from bob.db.models import BookDepthBar, CandleRecord, DerivativeSnapshot
from bob.db.session import get_session, init_db

if TYPE_CHECKING:  # solo para tipos: snapshots.py y vision.py importan store
    from bob.data.snapshots import DerivativePoint
    from bob.data.vision import BookDepthAggregate


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


def upsert_derivatives(
    symbol: str,
    period: str,
    points: Iterable[DerivativePoint],
    session: Session | None = None,
) -> int:
    """Inserta o actualiza puntos de derivados. Único por (symbol, period, timestamp).

    Mismo contrato que `upsert_klines`: idempotente, devuelve filas escritas.
    El solape entre snapshots consecutivos es deliberado (ver snapshots.py), y
    es justamente lo que exige que esto sea un upsert y no un insert.
    """
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None

    try:
        rows = list(points)
        if not rows:
            return 0

        existing = {
            record.timestamp: record
            for record in session.exec(
                select(DerivativeSnapshot).where(
                    DerivativeSnapshot.symbol == symbol,
                    DerivativeSnapshot.period == period,
                    DerivativeSnapshot.timestamp >= min(p.timestamp for p in rows),
                    DerivativeSnapshot.timestamp <= max(p.timestamp for p in rows),
                )
            ).all()
        }

        written = 0
        for point in rows:
            record = existing.get(point.timestamp)
            if record is None:
                record = DerivativeSnapshot(
                    symbol=symbol, period=period, timestamp=point.timestamp
                )
            # Solo se pisa lo que el snapshot trae: un ciclo donde falló un
            # endpoint no debe borrar el dato que ya había de los otros.
            if point.open_interest is not None:
                record.open_interest = point.open_interest
            if point.open_interest_value is not None:
                record.open_interest_value = point.open_interest_value
            if point.long_short_ratio is not None:
                record.long_short_ratio = point.long_short_ratio
            if point.long_account_pct is not None:
                record.long_account_pct = point.long_account_pct
            if point.short_account_pct is not None:
                record.short_account_pct = point.short_account_pct
            if point.taker_buy_sell_ratio is not None:
                record.taker_buy_sell_ratio = point.taker_buy_sell_ratio
            if point.top_trader_account_ratio is not None:
                record.top_trader_account_ratio = point.top_trader_account_ratio
            if point.top_trader_position_ratio is not None:
                record.top_trader_position_ratio = point.top_trader_position_ratio
            if point.funding_rate is not None:
                record.funding_rate = point.funding_rate
            session.add(record)
            written += 1

        session.commit()
        return written
    finally:
        if owns:
            session.close()


def derivatives_coverage(
    symbol: str, period: str, session: Session | None = None
) -> dict[str, int]:
    """Resumen de derivados persistidos: n puntos, primer y último timestamp."""
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None
    try:
        ts_col = col(DerivativeSnapshot.timestamp)
        stmt = select(func.count(ts_col), func.min(ts_col), func.max(ts_col)).where(
            DerivativeSnapshot.symbol == symbol, DerivativeSnapshot.period == period
        )
        count, first, last = session.exec(stmt).one()
        return {
            "n_points": int(count or 0),
            "first_timestamp": int(first or 0),
            "last_timestamp": int(last or 0),
        }
    finally:
        if owns:
            session.close()


@dataclass(frozen=True)
class DerivativesSeries:
    """Serie de derivados en su grilla nativa (5m/15m/1h de Binance).

    A diferencia de `OHLCVSeries`, acá los huecos **sí** aparecen como NaN
    dentro de los arrays: los tres endpoints no siempre pueblan la misma
    grilla, y un punto con OI y sin ratio es información útil que no se
    descarta. Alinear esta grilla con la de las velas es trabajo puro de
    `signals/derivatives.py`, no de este módulo.
    """

    symbol: str
    period: str
    timestamp: np.ndarray  # int64, epoch ms
    open_interest: np.ndarray  # float64, contratos (moneda base)
    open_interest_value: np.ndarray  # float64, notional USDT
    long_short_ratio: np.ndarray
    taker_buy_sell_ratio: np.ndarray
    top_trader_account_ratio: np.ndarray
    top_trader_position_ratio: np.ndarray
    funding_rate: np.ndarray

    def __len__(self) -> int:
        return int(self.timestamp.shape[0])


@dataclass(frozen=True)
class BookDepthSeries:
    """Profundidad del libro ya agregada a la grilla del timeframe.

    Magnitudes crudas en USDT: los ratios adimensionales los arma
    `signals/microstructure.py`.
    """

    symbol: str
    timeframe: str
    open_time: np.ndarray  # int64
    #: Near-touch (±0,2%): **NaN en el archivo anterior a ~2026-01-15**, cuando
    #: Binance agregó ese nivel. Los features que dependen de él salen NaN ahí.
    bid_02: np.ndarray
    ask_02: np.ndarray
    bid_1: np.ndarray
    ask_1: np.ndarray
    bid_5: np.ndarray
    ask_5: np.ndarray
    n_snapshots: np.ndarray  # int64

    def __len__(self) -> int:
        return int(self.open_time.shape[0])


def _optional_floats(values: Sequence[str | None]) -> np.ndarray:
    """Columna `str | None` de la DB -> float64 con NaN donde no había dato."""
    return np.array([np.nan if v is None else float(v) for v in values], dtype=np.float64)


def load_derivatives(
    symbol: str,
    period: str,
    start_time: int | None = None,
    end_time: int | None = None,
    session: Session | None = None,
) -> DerivativesSeries:
    """Carga los puntos de derivados persistidos, ordenados por timestamp."""
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None

    try:
        stmt = select(DerivativeSnapshot).where(
            DerivativeSnapshot.symbol == symbol, DerivativeSnapshot.period == period
        )
        if start_time is not None:
            stmt = stmt.where(DerivativeSnapshot.timestamp >= start_time)
        if end_time is not None:
            stmt = stmt.where(DerivativeSnapshot.timestamp <= end_time)
        stmt = stmt.order_by(col(DerivativeSnapshot.timestamp))
        records = list(session.exec(stmt).all())
    finally:
        if owns:
            session.close()

    timestamp = np.array([r.timestamp for r in records], dtype=np.int64)
    if timestamp.size and not np.all(np.diff(timestamp) > 0):
        raise ValueError(f"{symbol} {period}: timestamps de derivados duplicados o desordenados")

    return DerivativesSeries(
        symbol=symbol,
        period=period,
        timestamp=timestamp,
        open_interest=_optional_floats([r.open_interest for r in records]),
        open_interest_value=_optional_floats([r.open_interest_value for r in records]),
        long_short_ratio=_optional_floats([r.long_short_ratio for r in records]),
        taker_buy_sell_ratio=_optional_floats([r.taker_buy_sell_ratio for r in records]),
        top_trader_account_ratio=_optional_floats([r.top_trader_account_ratio for r in records]),
        top_trader_position_ratio=_optional_floats([r.top_trader_position_ratio for r in records]),
        funding_rate=_optional_floats([r.funding_rate for r in records]),
    )


def upsert_book_depth(
    symbol: str,
    timeframe: str,
    bars: Iterable[BookDepthAggregate],
    session: Session | None = None,
) -> int:
    """Inserta o actualiza barras de profundidad. Única por (symbol, timeframe, open_time).

    Idempotente, igual que `upsert_klines`: reingerir un día del archivo pisa
    la misma barra en vez de duplicarla.
    """
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None

    try:
        rows = list(bars)
        if not rows:
            return 0

        existing = {
            record.open_time: record
            for record in session.exec(
                select(BookDepthBar).where(
                    BookDepthBar.symbol == symbol,
                    BookDepthBar.timeframe == timeframe,
                    BookDepthBar.open_time >= min(b.open_time for b in rows),
                    BookDepthBar.open_time <= max(b.open_time for b in rows),
                )
            ).all()
        }

        for bar in rows:
            record = existing.get(bar.open_time)
            if record is None:
                record = BookDepthBar(symbol=symbol, timeframe=timeframe, open_time=bar.open_time)
            record.bid_notional_02pct = None if bar.bid_02 is None else repr(bar.bid_02)
            record.ask_notional_02pct = None if bar.ask_02 is None else repr(bar.ask_02)
            record.bid_notional_1pct = repr(bar.bid_1)
            record.ask_notional_1pct = repr(bar.ask_1)
            record.bid_notional_5pct = repr(bar.bid_5)
            record.ask_notional_5pct = repr(bar.ask_5)
            record.n_snapshots = bar.n_snapshots
            record.n_snapshots_near = bar.n_snapshots_near
            session.add(record)

        session.commit()
        return len(rows)
    finally:
        if owns:
            session.close()


def load_book_depth(
    symbol: str,
    timeframe: str,
    start_time: int | None = None,
    end_time: int | None = None,
    session: Session | None = None,
) -> BookDepthSeries:
    """Carga la profundidad agregada persistida, ordenada por open_time."""
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None

    try:
        stmt = select(BookDepthBar).where(
            BookDepthBar.symbol == symbol, BookDepthBar.timeframe == timeframe
        )
        if start_time is not None:
            stmt = stmt.where(BookDepthBar.open_time >= start_time)
        if end_time is not None:
            stmt = stmt.where(BookDepthBar.open_time <= end_time)
        stmt = stmt.order_by(col(BookDepthBar.open_time))
        records = list(session.exec(stmt).all())
    finally:
        if owns:
            session.close()

    open_time = np.array([r.open_time for r in records], dtype=np.int64)
    _validate_monotonic(open_time, symbol, timeframe)
    return BookDepthSeries(
        symbol=symbol,
        timeframe=timeframe,
        open_time=open_time,
        bid_02=_optional_floats([r.bid_notional_02pct for r in records]),
        ask_02=_optional_floats([r.ask_notional_02pct for r in records]),
        bid_1=np.array([float(r.bid_notional_1pct) for r in records], dtype=np.float64),
        ask_1=np.array([float(r.ask_notional_1pct) for r in records], dtype=np.float64),
        bid_5=np.array([float(r.bid_notional_5pct) for r in records], dtype=np.float64),
        ask_5=np.array([float(r.ask_notional_5pct) for r in records], dtype=np.float64),
        n_snapshots=np.array([r.n_snapshots for r in records], dtype=np.int64),
    )


def book_depth_coverage(
    symbol: str, timeframe: str, session: Session | None = None
) -> dict[str, int]:
    """Resumen de profundidad persistida: n barras, primer y último open_time."""
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None
    try:
        ot = col(BookDepthBar.open_time)
        stmt = select(func.count(ot), func.min(ot), func.max(ot)).where(
            BookDepthBar.symbol == symbol, BookDepthBar.timeframe == timeframe
        )
        count, first, last = session.exec(stmt).one()
        return {
            "n_bars": int(count or 0),
            "first_open_time": int(first or 0),
            "last_open_time": int(last or 0),
        }
    finally:
        if owns:
            session.close()


#: Milisegundos en un día UTC. Los archivos de data.binance.vision rotan por
#: día UTC, así que la ingesta cuenta cobertura en esa misma unidad.
DAY_MS = 86_400_000


def _day_counts(
    entity: type[DerivativeSnapshot] | type[BookDepthBar],
    time_col: Mapped[int],
    filters: list[Any],
    session: Session | None,
) -> dict[int, int]:
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None
    try:
        day = (time_col / DAY_MS).cast(Integer).label("day")
        stmt = select(day, func.count(time_col)).where(*filters).group_by(day)
        return {int(d): int(c) for d, c in session.exec(stmt).all()}
    finally:
        if owns:
            session.close()


def derivative_day_counts(
    symbol: str, period: str, session: Session | None = None
) -> dict[int, int]:
    """Cuántos puntos de derivados hay por día UTC (epoch // 86.400.000).

    Es lo que hace idempotente a la ingesta del archivo histórico: un día ya
    completo no se vuelve a descargar. Contar por día y no solo mirar el rango
    total importa porque el archivo tiene huecos reales en el medio.
    """
    return _day_counts(
        DerivativeSnapshot,
        col(DerivativeSnapshot.timestamp),
        [DerivativeSnapshot.symbol == symbol, DerivativeSnapshot.period == period],
        session,
    )


def book_depth_day_counts(
    symbol: str, timeframe: str, session: Session | None = None
) -> dict[int, int]:
    """Cuántas barras de profundidad hay por día UTC."""
    return _day_counts(
        BookDepthBar,
        col(BookDepthBar.open_time),
        [BookDepthBar.symbol == symbol, BookDepthBar.timeframe == timeframe],
        session,
    )
