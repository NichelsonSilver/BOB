"""Cliente de FRED/ALFRED con historial de vintages completo.

Por qué vintages y no la serie "actual": ver `marea.pit`. Tres restricciones de
la API, todas medidas el 2026-09-27, ordenan este módulo:

1. **Tope de 2.000 vintages por request.** `DGS10` tiene 5.117 y la API
   responde 400. Se piden las fechas de vintage primero y se parte la ventana
   de tiempo real en bloques de `VINTAGE_CHUNK`.
2. **Recorte en los bordes.** Un período que cruza el borde de la ventana
   vuelve con `realtime_start`/`realtime_end` recortados. `pit.stitch` los une.
3. **Rate limit de 120 requests/minuto.** Se espacian las llamadas con un
   intervalo mínimo (regla 7: nunca rafagear); un 429 o un 5xx se reintenta
   con backoff exponencial.

Solo I/O. La lógica de tiempo real vive en `marea.pit`, que es puro.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date
from typing import Any

import httpx
from loguru import logger

from marea.pit import OPEN_END, Period, stitch

BASE_URL = "https://api.stlouisfed.org/fred"
#: Menor que el tope de 2.000 para no depender del conteo exacto de la API.
VINTAGE_CHUNK = 1500
#: 0,55 s entre requests = ~109/min, bajo el tope de 120.
MIN_INTERVAL_S = 0.55
PAGE_LIMIT = 100_000
EARLIEST = date(1776, 7, 4)  # el "desde siempre" que documenta FRED


class FredError(RuntimeError):
    """Error que la API devolvió con mensaje (400, key inválida, serie inexistente)."""


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _value(s: str) -> float | None:
    return None if s == "." else float(s)


class FredClient:
    def __init__(
        self,
        api_key: str,
        http: httpx.Client | None = None,
        min_interval_s: float = MIN_INTERVAL_S,
        max_retries: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise FredError("falta API_FRED en backend/.env")
        self._key = api_key
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=60.0)
        self._min_interval = min_interval_s
        self._max_retries = max_retries
        self._sleep = sleep
        self._last_call = 0.0
        self.requests_made = 0

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = {**params, "api_key": self._key, "file_type": "json"}
        for attempt in range(self._max_retries + 1):
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
            self._last_call = time.monotonic()
            self.requests_made += 1
            try:
                r = self._http.get(path, params=query)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise
                logger.warning("FRED {} falló la red ({}), reintento", path, exc)
                self._sleep(2.0**attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == self._max_retries:
                    r.raise_for_status()
                logger.warning("FRED {} devolvió {}, reintento", path, r.status_code)
                self._sleep(2.0**attempt)
                continue
            body: dict[str, Any] = r.json()
            if r.status_code != 200 or "error_message" in body:
                raise FredError(f"{path} {params}: {body.get('error_message', r.status_code)}")
            return body
        raise AssertionError("inalcanzable")  # pragma: no cover

    def series_info(self, series_id: str) -> dict[str, Any]:
        body = self._get("/series", {"series_id": series_id})
        info: dict[str, Any] = body["seriess"][0]
        return info

    def vintage_dates(self, series_id: str, since: date = EARLIEST) -> list[date]:
        out: list[date] = []
        offset = 0
        while True:
            body = self._get(
                "/series/vintagedates",
                {
                    "series_id": series_id,
                    "realtime_start": since.isoformat(),
                    "realtime_end": OPEN_END.isoformat(),
                    "limit": 10_000,
                    "offset": offset,
                },
            )
            page = [_d(s) for s in body["vintage_dates"]]
            out.extend(page)
            offset += len(page)
            if not page or offset >= int(body["count"]):
                return out

    def observations(self, series_id: str, rt_start: date, rt_end: date) -> list[Period]:
        """Todos los períodos de tiempo real que tocan `[rt_start, rt_end]`, recortados."""
        out: list[Period] = []
        offset = 0
        while True:
            body = self._get(
                "/series/observations",
                {
                    "series_id": series_id,
                    "realtime_start": rt_start.isoformat(),
                    "realtime_end": rt_end.isoformat(),
                    "limit": PAGE_LIMIT,
                    "offset": offset,
                    "sort_order": "asc",
                },
            )
            page = body["observations"]
            out.extend(
                Period(
                    obs_date=_d(o["date"]),
                    realtime_start=_d(o["realtime_start"]),
                    realtime_end=_d(o["realtime_end"]),
                    value=_value(o["value"]),
                )
                for o in page
            )
            offset += len(page)
            if not page or offset >= int(body["count"]):
                return out

    def periods(self, series_id: str, since: date | None = None) -> list[Period]:
        """Historial de tiempo real desde la vintage `since` (o completo), cosido.

        Con `since`, los períodos vigentes en `since` vuelven con
        `realtime_start = since` recortado: quien llama los cose contra lo que
        ya tenía con `pit.stitch(..., boundary=since)`.
        """
        vintages = self.vintage_dates(series_id, since or EARLIEST)
        if not vintages:
            return []
        starts = vintages[::VINTAGE_CHUNK]
        if since is not None and starts[0] > since:
            starts[0] = since
        merged: list[Period] = []
        for i, start in enumerate(starts):
            end = (
                date.fromordinal(starts[i + 1].toordinal() - 1) if i + 1 < len(starts) else OPEN_END
            )
            chunk = self.observations(series_id, start, end)
            merged = chunk if i == 0 else stitch(merged, chunk, boundary=start)
        return merged
