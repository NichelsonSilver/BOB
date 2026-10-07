"""Historial de funding de perpetuos de Binance (`/fapi/v1/fundingRate`).

Público, sin key, con historia completa desde el listado (BTCUSDT 2019-09).
Dos trampas medidas el 2026-10-07:

1. **`startTime=0` se trata como ausente** y la API devuelve lo más reciente,
   no lo más viejo. La paginación arranca en una fecha real.
2. El intervalo de liquidación **no es fijo entre símbolos** (8 h en BTC y ETH,
   4 h o 1 h en otros). Por eso lo diario se calcula sumando las tasas del día
   y no multiplicando una tasa por 3 (`marea.funding`).

Se lee a nivel de módulo `marea` y no se toca `bob.data.binance_rest`, que es
del vivo de 15m. Peso por request: 1; se espacian igual (regla 7).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

BASE_URL = "https://fapi.binance.com"
PAGE_LIMIT = 1000
MIN_INTERVAL_S = 0.5
#: Antes del primer perpetuo USDT de Binance: arranque de la paginación.
EPOCH_MS = int(datetime(2019, 9, 1, tzinfo=UTC).timestamp() * 1000)


class FundingError(RuntimeError):
    pass


@dataclass(frozen=True)
class FundingPoint:
    symbol: str
    funding_time: int  # epoch ms UTC; se conoce en ese instante
    rate: float


class FundingClient:
    def __init__(
        self,
        http: httpx.Client | None = None,
        min_interval_s: float = MIN_INTERVAL_S,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=30.0)
        self._min_interval = min_interval_s
        self._max_retries = max_retries
        self._sleep = sleep
        self._last_call = 0.0
        self.requests_made = 0

    def close(self) -> None:
        self._http.close()

    def _page(self, symbol: str, start_ms: int) -> list[dict[str, str]]:
        params: dict[str, str | int] = {
            "symbol": symbol,
            "startTime": start_ms,
            "limit": PAGE_LIMIT,
        }
        for attempt in range(self._max_retries + 1):
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
            self._last_call = time.monotonic()
            self.requests_made += 1
            try:
                r = self._http.get("/fapi/v1/fundingRate", params=params)
            except httpx.TransportError:
                if attempt == self._max_retries:
                    raise
                self._sleep(2.0**attempt)
                continue
            if (
                r.status_code in (418, 429) or r.status_code >= 500
            ) and attempt < self._max_retries:
                self._sleep(float(r.headers.get("Retry-After", 2.0**attempt)))
                continue
            if r.status_code != 200:
                raise FundingError(f"{symbol}: HTTP {r.status_code} {r.text[:200]}")
            rows: list[dict[str, str]] = r.json()
            return rows
        raise AssertionError("inalcanzable")  # pragma: no cover

    def history(self, symbol: str, since_ms: int | None = None) -> list[FundingPoint]:
        """Todo el funding desde `since_ms` (exclusivo) o desde el inicio."""
        start = EPOCH_MS if since_ms is None else since_ms + 1
        out: list[FundingPoint] = []
        while True:
            page = self._page(symbol, start)
            pts = [
                FundingPoint(symbol, int(r["fundingTime"]), float(r["fundingRate"])) for r in page
            ]
            out.extend(pts)
            if len(page) < PAGE_LIMIT:
                return out
            start = pts[-1].funding_time + 1
