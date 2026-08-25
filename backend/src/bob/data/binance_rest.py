"""Cliente REST de Binance USDⓈ-M Futures — histórico para backtest y ML.

Junto a `binance_ws.py`, el único lugar con I/O de mercado: regla 3 de
CLAUDE.md. Market data público, sin API key.

Autorregulación de rate limit: el peso consumido viene en el header
`X-MBX-USED-WEIGHT-1M` de cada response. En vez de contar pesos a mano (que
exige mantener una tabla que Binance cambia sin aviso), el limiter lee el
header real y frena cuando se acerca al techo. Ver docs/DATA_SOURCES.md.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Final, Self

import httpx
from loguru import logger

FAPI_BASE: Final = "https://fapi.binance.com"

#: Techo de peso por minuto que Binance aplica por IP en /fapi/*.
WEIGHT_LIMIT_1M: Final = 2400
#: Fracción del techo a partir de la cual el limiter empieza a frenar.
WEIGHT_SOFT_RATIO: Final = 0.75

#: Máximo de velas por request de /fapi/v1/klines.
KLINES_MAX_LIMIT: Final = 1500

#: Milisegundos por unidad de cada timeframe soportado.
INTERVAL_MS: Final[dict[str, int]] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


class BinanceRestError(RuntimeError):
    """Error no recuperable del cliente REST."""


@dataclass(frozen=True)
class Kline:
    """Una vela tal como la devuelve /fapi/v1/klines.

    Los precios se conservan como `str` (convención de db/models.py): la
    conversión a Decimal/float ocurre en el borde que los consume.
    """

    open_time: int  # epoch ms UTC
    open: str
    high: str
    low: str
    close: str
    volume: str
    close_time: int
    quote_volume: str
    n_trades: int
    taker_buy_volume: str
    taker_buy_quote_volume: str

    @classmethod
    def from_row(cls, row: list[Any]) -> Kline:
        """Mapea el array posicional de Binance (ver docs/DATA_SOURCES.md)."""
        return cls(
            open_time=int(row[0]),
            open=str(row[1]),
            high=str(row[2]),
            low=str(row[3]),
            close=str(row[4]),
            volume=str(row[5]),
            close_time=int(row[6]),
            quote_volume=str(row[7]),
            n_trades=int(row[8]),
            taker_buy_volume=str(row[9]),
            taker_buy_quote_volume=str(row[10]),
        )


class WeightLimiter:
    """Token bucket autorregulado por el header de peso usado de Binance.

    No modela los pesos por endpoint (Binance los cambia sin aviso): observa
    el peso realmente consumido en la ventana de 1 minuto y duerme lo que
    falta para que la ventana rote cuando se pasa del umbral blando.
    """

    def __init__(
        self,
        limit: int = WEIGHT_LIMIT_1M,
        soft_ratio: float = WEIGHT_SOFT_RATIO,
        min_interval: float = 0.05,
    ) -> None:
        self._limit = limit
        self._soft = int(limit * soft_ratio)
        self._min_interval = min_interval
        self._used = 0
        self._window_start = time.monotonic()
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    @property
    def used_weight(self) -> int:
        return self._used

    async def acquire(self) -> None:
        """Espera hasta que sea seguro emitir el siguiente request."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._window_start
            if elapsed >= 60.0:
                self._used = 0
                self._window_start = now
                elapsed = 0.0

            if self._used >= self._soft:
                wait = 60.0 - elapsed
                if wait > 0:
                    logger.warning(
                        "rate limit: peso {}/{} — durmiendo {:.1f}s",
                        self._used,
                        self._limit,
                        wait,
                    )
                    await asyncio.sleep(wait)
                self._used = 0
                self._window_start = time.monotonic()

            gap = self._min_interval - (time.monotonic() - self._last_call)
            if gap > 0:
                await asyncio.sleep(gap)
            self._last_call = time.monotonic()

    def observe(self, headers: httpx.Headers) -> None:
        """Sincroniza el contador con el peso real reportado por Binance."""
        raw = headers.get("X-MBX-USED-WEIGHT-1M")
        if raw is None:
            self._used += 1
            return
        try:
            self._used = int(raw)
        except ValueError:
            self._used += 1


class BinanceRestClient:
    """Cliente async de solo lectura. Usar como context manager."""

    def __init__(
        self,
        base_url: str = FAPI_BASE,
        timeout: float = 15.0,
        max_retries: int = 4,
        client: httpx.AsyncClient | None = None,
        limiter: WeightLimiter | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers={"User-Agent": "bob-assistant/0.1 (read-only market data)"},
        )
        self._limiter = limiter or WeightLimiter()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET con limiter, retry exponencial y respeto del Retry-After en 429."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            await self._limiter.acquire()
            try:
                resp = await self._client.get(path, params=params)
            except httpx.HTTPError as exc:  # red caída, timeout, DNS
                last_exc = exc
                backoff = min(2.0**attempt, 30.0)
                logger.warning("GET {} falló ({}), retry en {:.1f}s", path, exc, backoff)
                await asyncio.sleep(backoff)
                continue

            self._limiter.observe(resp.headers)

            if resp.status_code in (429, 418):
                retry_after = float(resp.headers.get("Retry-After", "5"))
                logger.warning("{} de Binance — durmiendo {:.1f}s", resp.status_code, retry_after)
                await asyncio.sleep(retry_after)
                continue
            if resp.status_code >= 500:
                backoff = min(2.0**attempt, 30.0)
                logger.warning("HTTP {} de Binance, retry en {:.1f}s", resp.status_code, backoff)
                await asyncio.sleep(backoff)
                continue
            if resp.status_code >= 400:
                raise BinanceRestError(f"HTTP {resp.status_code} en {path}: {resp.text[:200]}")

            return resp.json()

        raise BinanceRestError(f"GET {path} agotó {self._max_retries} intentos ({last_exc})")

    # ------------------------------------------------------------------ #
    # Endpoints
    # ------------------------------------------------------------------ #

    async def server_time_ms(self) -> int:
        data = await self._get("/fapi/v1/time")
        return int(data["serverTime"])

    async def exchange_filters(self, symbol: str) -> dict[str, str]:
        """tickSize / stepSize de un símbolo. El cacheo va arriba, no acá."""
        data = await self._get("/fapi/v1/exchangeInfo")
        for entry in data.get("symbols", []):
            if entry.get("symbol") != symbol.upper():
                continue
            out: dict[str, str] = {"symbol": entry["symbol"]}
            for filt in entry.get("filters", []):
                if filt.get("filterType") == "PRICE_FILTER":
                    out["tick_size"] = str(filt["tickSize"])
                elif filt.get("filterType") == "LOT_SIZE":
                    out["step_size"] = str(filt["stepSize"])
            return out
        raise BinanceRestError(f"símbolo desconocido: {symbol}")

    async def klines_page(
        self,
        symbol: str,
        interval: str,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = KLINES_MAX_LIMIT,
    ) -> list[Kline]:
        """Una página cruda de klines (hasta 1500)."""
        params: dict[str, Any] = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": min(limit, KLINES_MAX_LIMIT),
        }
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        rows = await self._get("/fapi/v1/klines", params)
        return [Kline.from_row(row) for row in rows]

    async def fetch_klines(
        self,
        symbol: str,
        interval: str,
        start_time: int,
        end_time: int | None = None,
        *,
        only_closed: bool = True,
        now_ms: int | None = None,
    ) -> list[Kline]:
        """Histórico completo paginado por startTime, en orden cronológico.

        `only_closed=True` descarta la vela en curso: incluirla es lookahead
        en producción (regla 5 de CLAUDE.md), porque su high/low/close aún
        pueden cambiar después de leerla.
        """
        if interval not in INTERVAL_MS:
            raise ValueError(f"timeframe no soportado: {interval}")
        step = INTERVAL_MS[interval]
        wall_now = now_ms if now_ms is not None else int(time.time() * 1000)
        hard_end = end_time if end_time is not None else wall_now

        out: list[Kline] = []
        cursor = int(start_time)
        seen: set[int] = set()

        while cursor <= hard_end:
            page = await self.klines_page(symbol, interval, start_time=cursor, end_time=hard_end)
            if not page:
                break
            fresh = [k for k in page if k.open_time not in seen]
            if not fresh:
                break
            for k in fresh:
                seen.add(k.open_time)
            out.extend(fresh)
            last_open = fresh[-1].open_time
            if last_open + step > hard_end:
                break
            cursor = last_open + step

        out.sort(key=lambda k: k.open_time)
        if only_closed:
            out = [k for k in out if k.close_time < wall_now]
        return out

    async def mark_price(self, symbol: str) -> dict[str, Any]:
        """Mark price + funding corriente + próximo cobro (`/premiumIndex`).

        Es el equivalente REST del stream `<sym>@markPrice@1s`: lo usa la
        fuente de polling cuando el WS no está disponible.
        """
        data = await self._get("/fapi/v1/premiumIndex", {"symbol": symbol.upper()})
        return dict(data)

    async def funding_history(
        self, symbol: str, start_time: int | None = None, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """Funding cobrado cada 8h. Máx 1000 rows por request."""
        params: dict[str, Any] = {"symbol": symbol.upper(), "limit": min(limit, 1000)}
        if start_time is not None:
            params["startTime"] = int(start_time)
        data = await self._get("/fapi/v1/fundingRate", params)
        return list(data)

    async def open_interest_hist(
        self, symbol: str, period: str = "15m", limit: int = 500
    ) -> list[dict[str, Any]]:
        """Binance solo conserva ~30 días en ESTE endpoint.

        Para el histórico largo no se usa acá: el mismo dato está en el archivo
        diario data.binance.vision (ver data/vision.py), con grilla de 5m desde
        2021-12-01. Este endpoint cubre el tramo caliente, que el archivo aún
        no publicó.
        """
        data = await self._get(
            "/futures/data/openInterestHist",
            {"symbol": symbol.upper(), "period": period, "limit": min(limit, 500)},
        )
        return list(data)

    async def long_short_ratio(
        self, symbol: str, period: str = "15m", limit: int = 500
    ) -> list[dict[str, Any]]:
        """Ratio long/short global de cuentas. Misma ventana ~30 días."""
        data = await self._get(
            "/futures/data/globalLongShortAccountRatio",
            {"symbol": symbol.upper(), "period": period, "limit": min(limit, 500)},
        )
        return list(data)

    async def taker_ratio(
        self, symbol: str, period: str = "15m", limit: int = 500
    ) -> list[dict[str, Any]]:
        """Ratio de volumen taker buy/sell. Misma ventana ~30 días."""
        data = await self._get(
            "/futures/data/takerlongshortRatio",
            {"symbol": symbol.upper(), "period": period, "limit": min(limit, 500)},
        )
        return list(data)

    async def top_account_ratio(
        self, symbol: str, period: str = "15m", limit: int = 500
    ) -> list[dict[str, Any]]:
        """Long/short de las cuentas TOP, contando cabezas. Ventana ~30 días.

        Los dos "top" existen porque el archivo diario `metrics/` los publica y
        el modelo los usa: `top_vs_crowd` y `top_concentration` comparan a los
        grandes contra la multitud, y divergen justo cuando el posicionamiento
        se concentra. Sin estos dos endpoints esas columnas solo existen hasta
        donde llegó el archivo —un día atrás— y el vivo no puede emitir.
        """
        data = await self._get(
            "/futures/data/topLongShortAccountRatio",
            {"symbol": symbol.upper(), "period": period, "limit": min(limit, 500)},
        )
        return list(data)

    async def top_position_ratio(
        self, symbol: str, period: str = "15m", limit: int = 500
    ) -> list[dict[str, Any]]:
        """Long/short de las cuentas TOP, pesando notional. Ventana ~30 días.

        "account" cuenta cabezas (una ballena pesa igual que un minorista) y
        "position" pesa dinero: la diferencia entre ambos ES la señal.
        """
        data = await self._get(
            "/futures/data/topLongShortPositionRatio",
            {"symbol": symbol.upper(), "period": period, "limit": min(limit, 500)},
        )
        return list(data)
