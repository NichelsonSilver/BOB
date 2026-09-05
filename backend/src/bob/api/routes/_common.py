"""Piezas compartidas por las rutas — resolución de símbolo, I/O en hilo, cache.

Dos reglas que valen para TODAS las rutas de este paquete y por eso viven acá:

1. **Ninguna ruta escribe.** La API de la Fase 6 es un visor. Mientras corre la
   validación forward, un endpoint que resuelva pronósticos o reajuste el
   modelo cambiaría la muestra que se está midiendo; y una muestra que se mide
   a sí misma no mide nada. Resolver es trabajo del tracker, ajustar es del
   analista.
2. **Ningún acceso a SQLite ocurre en el event loop.** El mismo proceso corre
   el feed de Binance: un request que se queda 4s ajustando GARCH bloquea la
   recepción de ticks y la regla 6 (latencia < 1s) se rompe sin que nadie vea
   por qué. Todo lo que toca disco va por `asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from fastapi import HTTPException, Query
from sqlmodel import Session, col, select

from bob.config import settings
from bob.data.binance_rest import INTERVAL_MS
from bob.db.models import ForecastRecord
from bob.db.session import get_session, init_db

T = TypeVar("T")

#: `backend/` — de acá cuelgan `artifacts/` y la DB.
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent
ARTIFACTS_DIR = BACKEND_DIR / "artifacts"


def default_symbol() -> str:
    return settings.watchlist[0] if settings.watchlist else "ETHUSDT"


def SymbolQuery() -> Any:  # noqa: N802 — es una factory de Query, no una clase
    return Query(default=None, description="símbolo; default = primero de la watchlist")


def TimeframeQuery() -> Any:  # noqa: N802
    return Query(default=None, description="timeframe; default = el del backend")


def resolve(symbol: str | None, timeframe: str | None) -> tuple[str, str]:
    """Completa símbolo y timeframe con los defaults del backend y valida.

    Valida el timeframe contra `INTERVAL_MS` y no contra una lista propia: si
    alguna vez se agrega un timeframe, hay un solo lugar donde declararlo.
    """
    sym = (symbol or default_symbol()).upper()
    tf = timeframe or settings.bob_default_timeframe
    if tf not in INTERVAL_MS:
        raise HTTPException(
            status_code=422,
            detail=f"timeframe desconocido: {tf} (conocidos: {sorted(INTERVAL_MS)})",
        )
    return sym, tf


async def in_thread(fn: Callable[[], T]) -> T:
    """Corre trabajo bloqueante fuera del event loop. Ver la regla 2 de arriba."""
    return await asyncio.to_thread(fn)


def with_session(fn: Callable[[Session], T]) -> T:
    """Abre una sesión, corre `fn` y la cierra pase lo que pase."""
    init_db()
    session = get_session()
    try:
        return fn(session)
    finally:
        session.close()


def latest_forecast(session: Session, symbol: str, timeframe: str) -> ForecastRecord | None:
    """El último pronóstico emitido, resuelto o no."""
    stmt = (
        select(ForecastRecord)
        .where(ForecastRecord.symbol == symbol, ForecastRecord.timeframe == timeframe)
        .order_by(col(ForecastRecord.open_time).desc())
        .limit(1)
    )
    return session.exec(stmt).first()


def bars_since(open_time_ms: int, timeframe: str) -> int:
    """Cuántas barras del timeframe pasaron desde `open_time_ms` hasta ahora.

    El dashboard lo necesita para no dibujar un pronóstico viejo como si fuera
    de recién. Es exactamente el modo de falla que costó los dos bugs de
    frescura de la Fase 5: el número se veía bien y describía otro momento.
    """
    tf_ms = INTERVAL_MS[timeframe]
    now_ms = int(time.time() * 1000)
    return max(0, int((now_ms - open_time_ms) // tf_ms))


class TTLCache:
    """Cache con vencimiento y un lock por clave.

    El lock no es paranoia: `coverage_report` ajusta GARCH y HAR sobre las 69k
    velas para poder comparar el forward contra los mismos baselines del gate.
    Son segundos de CPU. Sin lock, tres refrescos del navegador lanzan tres
    ajustes idénticos en paralelo y le compiten el CPU al analista en vivo.
    """

    def __init__(self, ttl_s: float) -> None:
        self._ttl = ttl_s
        self._entries: dict[Any, tuple[float, Any]] = {}
        self._locks: dict[Any, asyncio.Lock] = {}

    def peek(self, key: Any) -> tuple[Any, float] | None:
        """Valor cacheado y su antigüedad en segundos, o None si venció."""
        hit = self._entries.get(key)
        if hit is None:
            return None
        stamp, value = hit
        age = time.monotonic() - stamp
        if age > self._ttl:
            return None
        return value, age

    async def get(self, key: Any, produce: Callable[[], T]) -> tuple[T, float]:
        """Devuelve `(valor, antigüedad_s)`, produciéndolo en hilo si venció."""
        hit = self.peek(key)
        if hit is not None:
            return hit[0], hit[1]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # Otro request pudo haberlo producido mientras esperábamos el lock.
            hit = self.peek(key)
            if hit is not None:
                return hit[0], hit[1]
            value = await in_thread(produce)
            self._entries[key] = (time.monotonic(), value)
            return value, 0.0
