"""Snapshot del libro de opciones de Deribit (API pública, sin key).

**Esta fuente no tiene historia.** `get_book_summary_by_currency` devuelve el
estado de ahora y nada más: el open interest de ayer no se puede pedir
después. Por eso la ingesta corre por tarea programada todos los días y guarda,
además de las métricas, **la respuesta cruda comprimida**: así una métrica que
hoy nadie pensó se puede calcular mañana sobre toda la historia acumulada. Es
la misma lección de la Fase 1 con el OI de Binance (ventana de 30 días que no
se recupera hacia atrás).

Medido el 2026-10-07: BTC devuelve 950 instrumentos en 424 KB.

Solo I/O.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

BASE_URL = "https://www.deribit.com/api/v2"
MIN_INTERVAL_S = 1.0
CURRENCIES: tuple[str, ...] = ("BTC", "ETH")


class DeribitError(RuntimeError):
    pass


class DeribitClient:
    def __init__(
        self,
        http: httpx.Client | None = None,
        min_interval_s: float = MIN_INTERVAL_S,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=60.0)
        self._min_interval = min_interval_s
        self._max_retries = max_retries
        self._sleep = sleep
        self._last_call = 0.0
        self.requests_made = 0

    def close(self) -> None:
        self._http.close()

    def options_summary(self, currency: str) -> tuple[list[dict[str, Any]], bytes]:
        """(filas parseadas, cuerpo crudo de la respuesta)."""
        params = {"currency": currency, "kind": "option"}
        for attempt in range(self._max_retries + 1):
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
            self._last_call = time.monotonic()
            self.requests_made += 1
            try:
                r = self._http.get("/public/get_book_summary_by_currency", params=params)
            except httpx.TransportError:
                if attempt == self._max_retries:
                    raise
                self._sleep(2.0**attempt)
                continue
            if (r.status_code == 429 or r.status_code >= 500) and attempt < self._max_retries:
                self._sleep(2.0**attempt)
                continue
            body = r.json() if r.content else {}
            if r.status_code != 200 or "result" not in body:
                raise DeribitError(f"{currency}: HTTP {r.status_code} {body.get('error', '')}")
            rows: list[dict[str, Any]] = body["result"]
            if not rows:
                raise DeribitError(f"{currency}: respuesta sin instrumentos")
            return rows, r.content
        raise AssertionError("inalcanzable")  # pragma: no cover
