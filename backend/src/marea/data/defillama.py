"""Cliente de DefiLlama para la oferta de stablecoins (`stablecoins.llama.fi`).

Cuatro hechos medidos el 2026-10-07 que ordenan este módulo y su ingesta:

1. **La grilla es diaria a las 00:00 UTC**, sin saltos, desde 2017-11-29.
2. **La fila del día en curso es parcial.** A las 17:17 UTC ya existía la fila
   del 07-10 y su valor cambiaba durante el día. Por eso la fila del día D se
   descarta hasta que D termina: solo se devuelven días anteriores a `today`.
3. **No hay vintages.** DefiLlama reescribe su historia sin aviso: cuando suma
   una stablecoin, rellena su pasado. Las vintages se construyen del lado
   nuestro, con una por corrida (`marea.data.ingest_stable`).
4. **Los muertos siguen en el total.** UST (hoy listada como `USTC`, id 3) no
   aparece en el listado vigente con su símbolo viejo, pero el total histórico
   la incluye: cae de 186,8 a 158,0 mil millones entre el 30-04 y el 15-05 de
   2022. No hay sesgo de supervivencia en el agregado. Lo que sí hay que
   declarar es que esa caída fue valor destruido, no dinero que salió.

Solo I/O. Sin key; se espacian las llamadas igual (regla 7).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx

BASE_URL = "https://stablecoins.llama.fi"
API_URL = "https://api.llama.fi"
MIN_INTERVAL_S = 1.0


class DefiLlamaError(RuntimeError):
    pass


def _day(ts: str | int) -> date:
    return datetime.fromtimestamp(int(ts), UTC).date()


class DefiLlamaClient:
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

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(self._max_retries + 1):
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                self._sleep(wait)
            self._last_call = time.monotonic()
            self.requests_made += 1
            try:
                r = self._http.get(path, params=params)
            except httpx.TransportError:
                if attempt == self._max_retries:
                    raise
                self._sleep(2.0**attempt)
                continue
            if (r.status_code == 429 or r.status_code >= 500) and attempt < self._max_retries:
                self._sleep(2.0**attempt)
                continue
            if r.status_code != 200:
                raise DefiLlamaError(f"{path} {params or ''}: HTTP {r.status_code}")
            return r.json()
        raise AssertionError("inalcanzable")  # pragma: no cover

    def total(self, today: date) -> dict[str, list[tuple[date, float]]]:
        """Oferta total en USD, cerrada al día anterior a `today`.

        Devuelve dos series: `usd` (solo stablecoins atadas al dólar) y `all`
        (todas, convertidas a USD por DefiLlama).
        """
        rows = self._get("/stablecoincharts/all")
        usd: list[tuple[date, float]] = []
        every: list[tuple[date, float]] = []
        for r in rows:
            d = _day(r["date"])
            if d >= today:
                continue
            values = r.get("totalCirculatingUSD") or {}
            if "peggedUSD" in values:
                usd.append((d, float(values["peggedUSD"])))
            if values:
                every.append((d, float(sum(values.values()))))
        if not usd:
            raise DefiLlamaError("/stablecoincharts/all vino sin datos en USD")
        return {"usd": usd, "all": every}

    def token(self, stablecoin_id: int, today: date) -> list[tuple[date, float]]:
        """Oferta en USD de una stablecoin, cerrada al día anterior a `today`."""
        rows = self._get("/stablecoincharts/all", {"stablecoin": stablecoin_id})
        out: list[tuple[date, float]] = []
        for r in rows:
            d = _day(r["date"])
            values = r.get("totalCirculatingUSD") or {}
            if d < today and "peggedUSD" in values:
                out.append((d, float(values["peggedUSD"])))
        if not out:
            raise DefiLlamaError(f"stablecoin {stablecoin_id} vino sin datos en USD")
        return out

    # --- api.llama.fi: TVL y volumen de DEX (M5.7) ------------------------------
    # Otro host del mismo proveedor, con las mismas propiedades: grilla diaria a
    # las 00:00 UTC, fila del día en curso parcial, y sin vintages. Medido el
    # 2026-10-07: TVL desde 2017-09 (3.298 días); DEX desde 2014-02.

    def chain_tvl(self, today: date) -> list[tuple[date, float]]:
        """TVL total de DeFi en USD, cerrado al día anterior a `today`."""
        rows = self._get(f"{API_URL}/v2/historicalChainTvl")
        out = [(_day(r["date"]), float(r["tvl"])) for r in rows if _day(r["date"]) < today]
        if not out:
            raise DefiLlamaError("historicalChainTvl vino vacío")
        return out

    def dex_volume(self, today: date) -> list[tuple[date, float]]:
        """Volumen diario de todos los DEX en USD, cerrado al día anterior a `today`."""
        body = self._get(
            f"{API_URL}/overview/dexs",
            {
                "excludeTotalDataChart": "false",
                "excludeTotalDataChartBreakdown": "true",
                "dataType": "dailyVolume",
            },
        )
        chart = body.get("totalDataChart") or []
        out = [(_day(ts), float(v)) for ts, v in chart if _day(ts) < today]
        if not out:
            raise DefiLlamaError("overview/dexs vino sin totalDataChart")
        return out
