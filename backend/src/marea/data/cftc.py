"""Cliente del COT de la CFTC (Socrata, publicreporting.cftc.gov). Sin key.

Posicionamiento semanal de los especuladores en los futuros que mueven la
macro. Tres cosas medidas el 2026-09-27 ordenan este módulo:

1. **Se indexa por código de contrato, nunca por nombre.** La CFTC renombró
   los mercados en febrero de 2022 ("E-MINI S&P 500 STOCK INDEX" pasó a
   "E-MINI S&P 500"). Filtrando por nombre, el Tesoro, el petróleo y el cobre
   parecen empezar en 2022; por código aparecen las 1.059 semanas desde
   2006-06, sin huecos.
2. **Dos reportes según el mercado.** Financieros (índices, Tesoro, divisas,
   bitcoin) en *Traders in Financial Futures*: especulador = `lev_money`
   (fondos apalancados), institucional = `asset_mgr`. Commodities en
   *Disaggregated*: especulador = `m_money` (managed money), sin institucional.
3. **Fecha del dato ≠ fecha de publicación.** El reporte es "al martes" y se
   publica el viernes 15:30 ET; con feriado, el lunes. `available_date` =
   martes + `PUBLICATION_LAG_DAYS` (6: el lunes siguiente) es conservador a
   propósito — cuesta dos días de frescura en un horizonte de semanas y cubre
   los feriados. Usar el martes sería lookahead (regla 5).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

import httpx

BASE_URL = "https://publicreporting.cftc.gov/resource"
PUBLICATION_LAG_DAYS = 6
MIN_INTERVAL_S = 1.0
PAGE_LIMIT = 50_000

Report = Literal["tff", "disagg"]

DATASET: dict[Report, str] = {"tff": "gpe5-46if", "disagg": "72hh-3qpy"}

#: Columnas (largo, corto) por reporte: especulador y, si existe, institucional.
_SPEC: dict[Report, tuple[str, str]] = {
    "tff": ("lev_money_positions_long", "lev_money_positions_short"),
    "disagg": ("m_money_positions_long_all", "m_money_positions_short_all"),
}
_INST: dict[Report, tuple[str, str] | None] = {
    "tff": ("asset_mgr_positions_long", "asset_mgr_positions_short"),
    "disagg": None,
}


class CftcError(RuntimeError):
    pass


@dataclass(frozen=True)
class CotRow:
    code: str
    report_date: date
    available_date: date
    name: str
    open_interest: float
    spec_long: float
    spec_short: float
    inst_long: float | None
    inst_short: float | None


def available_date(report_date: date) -> date:
    return report_date + timedelta(days=PUBLICATION_LAG_DAYS)


class CftcClient:
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

    def _get(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        for attempt in range(self._max_retries + 1):
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
            self._last_call = time.monotonic()
            self.requests_made += 1
            r = self._http.get(path, params=params)
            if (r.status_code == 429 or r.status_code >= 500) and attempt < self._max_retries:
                self._sleep(2.0**attempt)
                continue
            if r.status_code != 200:
                raise CftcError(f"{path}: {r.status_code} {r.text[:200]}")
            rows: list[dict[str, Any]] = r.json()
            return rows
        raise AssertionError("inalcanzable")  # pragma: no cover

    def positions(self, report: Report, code: str, since: date) -> list[CotRow]:
        spec_l, spec_s = _SPEC[report]
        inst = _INST[report]
        out: list[CotRow] = []
        offset = 0
        while True:
            page = self._get(
                f"/{DATASET[report]}.json",
                {
                    "$where": (
                        f"cftc_contract_market_code='{code}' AND "
                        f"report_date_as_yyyy_mm_dd >= '{since.isoformat()}T00:00:00'"
                    ),
                    "$order": "report_date_as_yyyy_mm_dd ASC",
                    "$limit": str(PAGE_LIMIT),
                    "$offset": str(offset),
                },
            )
            for r in page:
                day = date.fromisoformat(r["report_date_as_yyyy_mm_dd"][:10])
                out.append(
                    CotRow(
                        code=code,
                        report_date=day,
                        available_date=available_date(day),
                        name=r["market_and_exchange_names"],
                        open_interest=float(r["open_interest_all"]),
                        spec_long=float(r[spec_l]),
                        spec_short=float(r[spec_s]),
                        inst_long=float(r[inst[0]]) if inst else None,
                        inst_short=float(r[inst[1]]) if inst else None,
                    )
                )
            offset += len(page)
            if len(page) < PAGE_LIMIT:
                return out
