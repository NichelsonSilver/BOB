"""Cliente de Tiingo para precios diarios de ETFs.

Tres decisiones, cada una con su razón medida o declarada:

1. **Se guarda el cierre crudo + dividendo + split, no `adjClose`.** Tiingo
   recalcula TODO el historial ajustado cada vez que hay un dividendo. Una
   ingesta incremental que guardara `adjClose` mezclaría dos bases de ajuste y
   fabricaría un retorno falso en el borde. El retorno total se calcula en
   `marea.returns` a partir del crudo, y `adjClose` se guarda solo como
   control cruzado.
2. **Rate limit del plan gratuito: 50 requests/hora, 1.000/día.** Con 23 ETFs
   la carga inicial son 23 requests y se espacian igual (regla 7). **Un 429 no
   se reintenta**: significa que la cuota de la hora se agotó, y esperar un
   minuto no la devuelve. La primera versión esperaba 75 s por reintento y ETF
   por ETF; medido el 2026-09-27, una corrida con la cuota agotada tardó 10
   minutos para terminar fallando igual. Ahora se levanta `TiingoQuotaError` y
   la corrida marca el resto como omitido; la próxima, idempotente, lo completa.
3. **Licencia de uso interno.** Los precios no salen del equipo de Nichelson:
   ni al repo, ni a un artefacto publicado. Métricas sí.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import httpx

BASE_URL = "https://api.tiingo.com/tiingo"
#: 50/hora; la carga diaria (23) cabe con 2 s de separación.
MIN_INTERVAL_S = 2.0


class TiingoError(RuntimeError):
    pass


class TiingoQuotaError(TiingoError):
    """Cuota por hora agotada (429). No tiene sentido seguir pidiendo en esta corrida."""


@dataclass(frozen=True)
class DailyBar:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    div_cash: float
    split_factor: float
    adj_close: float


class TiingoClient:
    def __init__(
        self,
        api_key: str,
        http: httpx.Client | None = None,
        min_interval_s: float = MIN_INTERVAL_S,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise TiingoError("falta API_TIINGO en backend/.env")
        self._http = http or httpx.Client(
            base_url=BASE_URL,
            timeout=60.0,
            headers={"Authorization": f"Token {api_key}"},
        )
        self._min_interval = min_interval_s
        self._max_retries = max_retries
        self._sleep = sleep
        self._last_call = 0.0
        self.requests_made = 0

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict[str, str]) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
            self._last_call = time.monotonic()
            self.requests_made += 1
            r = self._http.get(path, params=params)
            if r.status_code == 429:
                raise TiingoQuotaError(f"{path}: cuota de Tiingo agotada (50 requests/hora)")
            if r.status_code >= 500 and attempt < self._max_retries:
                self._sleep(2.0**attempt)
                continue
            if r.status_code != 200:
                detail = r.json().get("detail", r.text) if r.content else r.status_code
                raise TiingoError(f"{path}: {r.status_code} {detail}")
            return r
        raise AssertionError("inalcanzable")  # pragma: no cover

    def daily(self, ticker: str, start: date, end: date | None = None) -> list[DailyBar]:
        params = {"startDate": start.isoformat(), "resampleFreq": "daily"}
        if end is not None:
            params["endDate"] = end.isoformat()
        rows = self._get(f"/daily/{ticker}/prices", params).json()
        return [
            DailyBar(
                day=date.fromisoformat(r["date"][:10]),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=float(r["volume"]),
                div_cash=float(r["divCash"]),
                split_factor=float(r["splitFactor"]),
                adj_close=float(r["adjClose"]),
            )
            for r in rows
        ]
