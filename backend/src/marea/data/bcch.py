"""Cliente de la API BDE del Banco Central de Chile (SieteRestWS).

Lo medido el 2026-09-27 que ordena este módulo:

1. **Credenciales**: `user` = correo de la cuenta, `pass` = contraseña de la
   cuenta (`BCCH_PASS`), no el token largo que muestra el sitio. Y la cuenta
   exige un segundo paso de activación en el sitio de la API: antes de eso la
   API responde `Codigo -5 Invalid username or password` con credenciales
   correctas.
2. **Codificación**: responde en ISO-8859-1 aunque el cuerpo sea JSON. Leerlo
   como UTF-8 revienta en el primer acento ("dólar").
3. **Los errores vienen con HTTP 200**: el estado real es el campo `Codigo`
   (0 = éxito). Un cliente que mirara solo el status HTTP daría por buena una
   respuesta vacía.
4. **"ND" = sin dato** (fines de semana, feriados como el 18-19 de septiembre).
   No se guarda como valor; se cuenta.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

BASE_URL = "https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx"
MIN_INTERVAL_S = 1.0


class BcchError(RuntimeError):
    pass


@dataclass(frozen=True)
class BcchObs:
    day: date
    value: float


@dataclass(frozen=True)
class BcchSeriesData:
    series_id: str
    title: str
    obs: list[BcchObs]
    #: Días con `statusCode` distinto de OK (fines de semana, feriados, huecos).
    no_data_days: list[date]


def _parse_day(s: str) -> date:
    return datetime.strptime(s, "%d-%m-%Y").date()


class BcchClient:
    def __init__(
        self,
        user: str,
        password: str,
        http: httpx.Client | None = None,
        min_interval_s: float = MIN_INTERVAL_S,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not user or not password:
            raise BcchError("faltan BCCH_USER / BCCH_PASS en backend/.env")
        self._auth = {"user": user, "pass": password}
        self._http = http or httpx.Client(timeout=120.0)
        self._min_interval = min_interval_s
        self._max_retries = max_retries
        self._sleep = sleep
        self._last_call = 0.0
        self.requests_made = 0

    def close(self) -> None:
        self._http.close()

    def _get(self, params: dict[str, str]) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
            self._last_call = time.monotonic()
            self.requests_made += 1
            r = self._http.get(BASE_URL, params={**self._auth, **params})
            if r.status_code >= 500 and attempt < self._max_retries:
                self._sleep(2.0**attempt)
                continue
            if r.status_code != 200:
                raise BcchError(f"HTTP {r.status_code}")
            body: dict[str, Any] = json.loads(r.content.decode("latin-1"))
            if body.get("Codigo") != 0:
                raise BcchError(f"Codigo {body.get('Codigo')}: {body.get('Descripcion')}")
            return body
        raise AssertionError("inalcanzable")  # pragma: no cover

    def series(self, series_id: str, first: date, last: date) -> BcchSeriesData:
        body = self._get(
            {
                "function": "GetSeries",
                "timeseries": series_id,
                "firstdate": first.isoformat(),
                "lastdate": last.isoformat(),
            }
        )
        s = body["Series"]
        obs: list[BcchObs] = []
        nd: list[date] = []
        for o in s.get("Obs") or []:
            day = _parse_day(o["indexDateString"])
            if o["statusCode"] == "OK":
                obs.append(BcchObs(day, float(o["value"])))
            else:
                nd.append(day)
        return BcchSeriesData(series_id, s.get("descripEsp") or "", obs, nd)
