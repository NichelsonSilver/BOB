"""Velas para el chart de la Signal page.

No usa `load_series`: esa función trae la serie **completa** —69k barras hoy—
porque el motor numérico la necesita entera para que las ventanas de contexto
signifiquen lo mismo que en el backtest. Un chart necesita las últimas 300, y
pedir 69k para tirar 68.800 es medio segundo de CPU por refresco del navegador
que se le resta al analista.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlmodel import Session, col, select

from bob.api.routes._common import (
    SymbolQuery,
    TimeframeQuery,
    in_thread,
    resolve,
    with_session,
)
from bob.data.binance_rest import INTERVAL_MS
from bob.db.models import CandleRecord

router = APIRouter(tags=["market"])


def _read_candles(
    session: Session, symbol: str, timeframe: str, limit: int
) -> list[CandleRecord]:
    stmt = (
        select(CandleRecord)
        .where(CandleRecord.symbol == symbol, CandleRecord.timeframe == timeframe)
        .order_by(col(CandleRecord.open_time).desc())
        .limit(limit)
    )
    return list(reversed(list(session.exec(stmt).all())))


@router.get("/market/candles")
async def candles(
    symbol: str | None = SymbolQuery(),
    timeframe: str | None = TimeframeQuery(),
    limit: int = Query(300, ge=2, le=5000),
) -> dict[str, Any]:
    """Últimas `limit` velas CERRADAS, en orden cronológico.

    Devuelve `gaps` porque el chart no puede mentir por omisión: una barra que
    Binance no entregó no se rellena (`data/store.py`), así que el precio salta
    y sin la marca el usuario lee ese salto como movimiento de mercado. Es
    además la señal visible de que el analista va a callarse en ese tramo —
    `_assert_tail_contiguous` se niega a emitir sobre un hueco.
    """
    sym, tf = resolve(symbol, timeframe)
    rows = await in_thread(lambda: with_session(lambda s: _read_candles(s, sym, tf, limit)))

    tf_ms = INTERVAL_MS[tf]
    out: list[dict[str, float | int]] = []
    gaps: list[dict[str, int]] = []
    prev: int | None = None
    for row in rows:
        if prev is not None and row.open_time - prev > tf_ms:
            gaps.append(
                {
                    "from_open_time": prev,
                    "to_open_time": row.open_time,
                    "missing_bars": int((row.open_time - prev) // tf_ms) - 1,
                }
            )
        prev = row.open_time
        out.append(
            {
                "open_time": row.open_time,
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": float(row.volume),
            }
        )

    return {
        "symbol": sym,
        "timeframe": tf,
        "interval_ms": tf_ms,
        "n": len(out),
        "candles": out,
        "gaps": gaps,
    }
