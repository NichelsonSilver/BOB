"""Archivo histórico de Binance — `data.binance.vision`.

**Por qué existe este módulo.** Los endpoints `/futures/data/*` conservan solo
~30 días de open interest y de ratios de posicionamiento, y durante la Fase 2
eso se leyó como "el histórico de derivados no existe gratis". Es falso: el
límite es del *endpoint*, no del *dato*. Binance publica los mismos campos en
un archivo estático diario, sin API key y sin rate limit de API:

- `futures/um/daily/metrics/`   — OI, ratios long/short y taker ratio, grilla
  de 5 minutos exacta, desde **2021-12-01**. ~12 KB por día.
- `futures/um/daily/bookDepth/` — profundidad acumulada del libro a ±0,2/1/2/
  3/4/5% del mid, un snapshot cada ~30s, desde **2023-01-01**. ~600 KB por día.

Con eso las features de derivados y microestructura se pueden entrenar sobre
los mismos 720 días que ya tiene el backtest, en vez de sobre la ventana
raquítica de snapshots en vivo.

Dos trampas del formato, ambas verificadas contra archivos reales:

1. No hay agregados mensuales para `metrics` ni `bookDepth` (sí para `klines`).
   Solo diarios: un archivo por día, y el listado S3 pagina de a 1000 claves.
2. El archivo del día D aparece con ~1 día de retraso. El tramo caliente lo
   sigue cubriendo `data/snapshots.py` vía REST — las dos fuentes escriben la
   misma tabla y el upsert las reconcilia.
3. **El esquema de `bookDepth` cambió con el tiempo**: el nivel ±0,2% aparece
   recién alrededor de 2026-01-15; antes solo hay ±1/2/3/4/5%. Verificado
   descargando días de 2024, 2025 y enero de 2026. Ver `DEPTH_LEVEL_NEAR`.

Regla 3 de CLAUDE.md: acá hay I/O y parseo, nada de features. Las magnitudes
salen crudas (USDT, contratos) hacia `store.py`; volverlas adimensionales es
trabajo de `signals/`.
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import TracebackType
from typing import Final, Self
from xml.etree import ElementTree

import httpx
from loguru import logger

from bob.data.binance_rest import INTERVAL_MS
from bob.data.snapshots import DerivativePoint

#: De acá se descargan los .zip.
VISION_BASE: Final = "https://data.binance.vision"
#: El listado va contra el bucket S3 crudo: `data.binance.vision` sirve objetos,
#: no índices. Es el mismo bucket, otro host.
LISTING_BASE: Final = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"

#: Primer día publicado de cada dataset (verificado 2026-08-24). Pedir días
#: anteriores devuelve 404, que este módulo trata como "no existe", no como error.
DATASET_START: Final[dict[str, date]] = {
    "metrics": date(2021, 12, 1),
    "bookDepth": date(2023, 1, 1),
}

#: Niveles del libro que se persisten, en porcentaje de distancia al mid.
DEPTH_LEVELS: Final[tuple[float, ...]] = (0.2, 1.0, 5.0)

#: Los que existen en TODA la historia del archivo. Un día sin estos no es
#: utilizable.
DEPTH_LEVELS_CORE: Final[tuple[float, ...]] = (1.0, 5.0)

#: El near-touch. **Binance lo agregó alrededor de 2026-01-15**: los archivos
#: anteriores traen solo ±1/2/3/4/5%. Exigirlo descartaba 500+ días en silencio
#: y —peor— los reportaba como huecos del archivo. Es opcional por diseño: las
#: features que dependen de él salen NaN en el tramo viejo, que es la respuesta
#: honesta, y el modelo ya tiene que tolerar features faltantes.
DEPTH_LEVEL_NEAR: Final = 0.2

#: Descargas simultáneas. Es un CDN de objetos estáticos, no la API con peso
#: por minuto, pero rafaguear 1.700 requests igual es de mal vecino (regla 7).
DEFAULT_CONCURRENCY: Final = 6

#: Reintentos ante fallo de transporte (DNS, timeout, corte de red). Una
#: descarga de 730 días dura lo suficiente como para atravesar un bache de
#: conectividad, y sin reintento ese bache se convierte en cientos de días
#: marcados como faltantes.
DEFAULT_RETRIES: Final = 3

_METRICS_COLUMNS: Final[tuple[str, ...]] = (
    "create_time",
    "symbol",
    "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
)

_BOOK_DEPTH_COLUMNS: Final[tuple[str, ...]] = ("timestamp", "percentage", "depth", "notional")


class VisionError(RuntimeError):
    """Error no recuperable del archivo histórico."""


# --------------------------------------------------------------------------- #
# Parseo
# --------------------------------------------------------------------------- #


def _ms(raw: str) -> int:
    """`2026-08-20 02:10:00` (UTC, sin tzinfo en el archivo) -> epoch ms."""
    return int(datetime.fromisoformat(raw.strip()).replace(tzinfo=UTC).timestamp() * 1000)


def _num(raw: str) -> str | None:
    """Devuelve el valor tal cual (convención `str` de db/models.py) o None.

    El archivo trae celdas vacías cuando Binance no computó la métrica en ese
    bucket. Guardar `None` y no `0` es la diferencia entre "no sé" y "no había
    posicionamiento", que para el modelo son cosas opuestas.
    """
    value = raw.strip()
    if not value:
        return None
    try:
        float(value)
    except ValueError:
        return None
    return value


def _long_short_pcts(ratio: str | None) -> tuple[str | None, str | None]:
    """Reparte el ratio de cuentas long/short en fracciones que suman 1.

    El REST entrega `longAccount`/`shortAccount` directo y el archivo solo el
    ratio r = long/short. Con long+short = 1 la reconstrucción es exacta
    (long = r/(1+r)), no una aproximación — así las filas de las dos fuentes
    quedan intercambiables en la misma tabla.
    """
    if ratio is None:
        return None, None
    r = float(ratio)
    if r <= 0.0:
        return None, None
    long_pct = r / (1.0 + r)
    return f"{long_pct:.8f}", f"{1.0 - long_pct:.8f}"


def parse_metrics_csv(raw: bytes, symbol: str) -> list[DerivativePoint]:
    """Convierte el CSV de `metrics/` en puntos de derivados, ordenados.

    Filtra por símbolo: el archivo trae una sola columna `symbol`, pero
    verificarlo cuesta nada y ataja un símbolo mal armado en la URL.
    """
    lines = raw.decode("utf-8").splitlines()
    if not lines:
        return []

    header = tuple(h.strip() for h in lines[0].split(","))
    if header != _METRICS_COLUMNS:
        raise VisionError(f"metrics: header inesperado {header} — cambió el formato del archivo")

    points: list[DerivativePoint] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        cells = line.split(",")
        if len(cells) != len(_METRICS_COLUMNS) or cells[1].strip() != symbol:
            continue
        account_ratio = _num(cells[6])
        long_pct, short_pct = _long_short_pcts(account_ratio)
        points.append(
            DerivativePoint(
                timestamp=_ms(cells[0]),
                open_interest=_num(cells[2]),
                open_interest_value=_num(cells[3]),
                long_short_ratio=account_ratio,
                long_account_pct=long_pct,
                short_account_pct=short_pct,
                taker_buy_sell_ratio=_num(cells[7]),
                top_trader_account_ratio=_num(cells[4]),
                top_trader_position_ratio=_num(cells[5]),
            )
        )
    points.sort(key=lambda p: p.timestamp)
    return points


@dataclass(frozen=True)
class DepthSnapshot:
    """Una foto del libro: notional acumulado hasta cada nivel, por lado.

    Las claves son la distancia al mid en porcentaje (0.2, 1.0, 5.0). Los
    valores son **acumulados**: el notional hasta 1% incluye el que está hasta
    0,2%. Verificado en el archivo — la serie crece monótona con la distancia.
    """

    timestamp: int  # epoch ms UTC
    bid: dict[float, float]
    ask: dict[float, float]

    @property
    def complete(self) -> bool:
        """Tiene los niveles del núcleo. El near-touch NO se exige: ver DEPTH_LEVEL_NEAR."""
        return all(lvl in self.bid and lvl in self.ask for lvl in DEPTH_LEVELS_CORE)

    @property
    def has_near(self) -> bool:
        """Si este snapshot trae el nivel de 0,2% (archivos desde ~2026-01)."""
        return DEPTH_LEVEL_NEAR in self.bid and DEPTH_LEVEL_NEAR in self.ask


def parse_book_depth_csv(raw: bytes) -> list[DepthSnapshot]:
    """Convierte el CSV de `bookDepth/` en snapshots, quedándose con DEPTH_LEVELS.

    El signo de `percentage` codifica el lado: negativo = bids (por debajo del
    mid), positivo = asks.
    """
    lines = raw.decode("utf-8").splitlines()
    if not lines:
        return []
    header = tuple(h.strip() for h in lines[0].split(","))
    if header != _BOOK_DEPTH_COLUMNS:
        raise VisionError(f"bookDepth: header inesperado {header}")

    wanted = set(DEPTH_LEVELS)
    by_ts: dict[int, tuple[dict[float, float], dict[float, float]]] = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        cells = line.split(",")
        if len(cells) != len(_BOOK_DEPTH_COLUMNS):
            continue
        pct = float(cells[1])
        level = abs(pct)
        if level not in wanted:
            continue
        bid, ask = by_ts.setdefault(_ms(cells[0]), ({}, {}))
        (bid if pct < 0 else ask)[level] = float(cells[3])

    return [
        DepthSnapshot(timestamp=ts, bid=bid, ask=ask) for ts, (bid, ask) in sorted(by_ts.items())
    ]


@dataclass(frozen=True)
class DayFetch:
    """Resultado de pedir un día del archivo. Tres estados, no dos.

    Distinguirlos no es prolijidad: `absent` significa que Binance nunca
    publicó ese día y hay que seguir de largo; `failed` significa que el
    problema es nuestro y el día hay que reintentarlo. Colapsar ambos en
    "no vino" hace que una corrida con la red caída reporte cientos de
    huecos del archivo que no existen.
    """

    day: date
    payload: bytes | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.payload is not None

    @property
    def absent(self) -> bool:
        """404 legítimo: el archivo no tiene ese día."""
        return self.payload is None and self.error is None

    @property
    def failed(self) -> bool:
        """Fallo de transporte: reintentable, y NO es un hueco del archivo."""
        return self.error is not None


@dataclass(frozen=True)
class BookDepthAggregate:
    """Profundidad promediada dentro de una barra del timeframe.

    `bid_02`/`ask_02` son `None` cuando ningún snapshot de la barra traía el
    near-touch — o sea, en todo el archivo anterior a ~2026-01-15.
    """

    open_time: int
    bid_1: float
    ask_1: float
    bid_5: float
    ask_5: float
    n_snapshots: int
    bid_02: float | None = None
    ask_02: float | None = None
    #: Cuántos de los `n_snapshots` traían el near-touch. Cero significa que el
    #: archivo de ese día es de la época sin ese nivel, no que el libro
    #: estuviera vacío cerca del mid.
    n_snapshots_near: int = 0


def aggregate_book_depth(
    snapshots: Iterable[DepthSnapshot], timeframe: str
) -> list[BookDepthAggregate]:
    """Promedia los snapshots dentro de cada barra del timeframe.

    Sin lookahead por construcción: un snapshot en `t` cae en la barra que
    **contiene** a `t`, y esa barra recién es observable a su cierre. Promediar
    dentro de la barra no filtra futuro; asignarlo a la barra anterior sí, y por
    eso el bucket se calcula con floor y nunca con round.
    """
    step = INTERVAL_MS[timeframe]
    buckets: dict[int, list[DepthSnapshot]] = {}
    for snap in snapshots:
        if not snap.complete:
            continue  # snapshot truncado: no se completa con el del vecino
        buckets.setdefault((snap.timestamp // step) * step, []).append(snap)

    out: list[BookDepthAggregate] = []
    for open_time, group in sorted(buckets.items()):
        n = len(group)
        con_near = [s for s in group if s.has_near]
        out.append(
            BookDepthAggregate(
                open_time=open_time,
                bid_1=sum(s.bid[1.0] for s in group) / n,
                ask_1=sum(s.ask[1.0] for s in group) / n,
                bid_5=sum(s.bid[5.0] for s in group) / n,
                ask_5=sum(s.ask[5.0] for s in group) / n,
                n_snapshots=n,
                # El near-touch se promedia solo sobre los snapshots que lo
                # traen: mezclarlo con ceros inventaría un libro vacío.
                bid_02=(
                    sum(s.bid[DEPTH_LEVEL_NEAR] for s in con_near) / len(con_near)
                    if con_near
                    else None
                ),
                ask_02=(
                    sum(s.ask[DEPTH_LEVEL_NEAR] for s in con_near) / len(con_near)
                    if con_near
                    else None
                ),
                n_snapshots_near=len(con_near),
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Cliente
# --------------------------------------------------------------------------- #


def daterange(start: date, end: date) -> Iterator[date]:
    """Días de `start` a `end`, ambos inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def object_key(dataset: str, symbol: str, day: date) -> str:
    """Clave del objeto diario dentro del bucket."""
    return f"data/futures/um/daily/{dataset}/{symbol}/{symbol}-{dataset}-{day.isoformat()}.zip"


def _day_from_key(key: str) -> date | None:
    """`.../ETHUSDT-metrics-2026-08-20.zip` -> date(2026, 8, 20). None si no calza."""
    stem = key.rsplit("/", 1)[-1].removesuffix(".zip")
    parts = stem.split("-")
    if len(parts) < 3:
        return None
    try:
        return date.fromisoformat("-".join(parts[-3:]))
    except ValueError:
        return None


def _unzip_single(payload: bytes) -> bytes:
    """Extrae el único CSV del zip diario."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise VisionError(f"el zip trae {len(names)} CSV, se esperaba 1: {names}")
        return archive.read(names[0])


class VisionClient:
    """Lector del archivo estático. Usar como context manager asíncrono."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        timeout: float = 60.0,
        retries: int = DEFAULT_RETRIES,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": "bob/0.1"}
        )
        self._owns_client = client is None
        self._sem = asyncio.Semaphore(concurrency)
        self._retries = max(1, retries)

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

    async def available_days(self, dataset: str, symbol: str) -> list[date]:
        """Días publicados de un dataset. Pagina el listado S3 (tope 1000 claves)."""
        prefix = f"data/futures/um/daily/{dataset}/{symbol}/"
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        days: set[date] = set()
        token: str | None = None

        while True:
            params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
            if token:
                params["continuation-token"] = token
            response = await self._client.get(LISTING_BASE, params=params)
            response.raise_for_status()
            root = ElementTree.fromstring(response.text)

            for node in root.findall("s3:Contents/s3:Key", ns):
                key = (node.text or "").strip()
                if not key.endswith(".zip"):
                    continue  # los .CHECKSUM comparten prefijo
                day = _day_from_key(key)
                if day is not None:
                    days.add(day)

            if root.findtext("s3:IsTruncated", default="false", namespaces=ns).lower() != "true":
                break
            token = root.findtext("s3:NextContinuationToken", namespaces=ns)
            if not token:  # pragma: no cover — S3 siempre lo manda si truncó
                break

        return sorted(days)

    async def fetch_day(self, dataset: str, symbol: str, day: date) -> bytes | None:
        """Baja y descomprime el CSV de un día. `None` si ese día no existe (404).

        Un día faltante no es un error: hay huecos reales en el archivo, y el
        pipeline los reporta en vez de rellenarlos — misma regla que los huecos
        de klines en `store.py`.
        """
        url = f"{VISION_BASE}/{object_key(dataset, symbol, day)}"
        async with self._sem:
            response = await self._client.get(url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return _unzip_single(response.content)

    async def _fetch_day_with_retries(self, dataset: str, symbol: str, day: date) -> DayFetch:
        """Un día, con reintentos ante fallo de transporte.

        Un 404 NO se reintenta: es una respuesta, no un fallo. `VisionError`
        tampoco: un zip con dos CSV no mejora insistiendo.
        """
        last: Exception | None = None
        for intento in range(self._retries):
            try:
                payload = await self.fetch_day(dataset, symbol, day)
            except VisionError:
                raise
            except Exception as exc:  # red caída, DNS, timeout, 5xx
                last = exc
                if intento + 1 < self._retries:
                    await asyncio.sleep(2.0**intento)
                continue
            return DayFetch(day=day, payload=payload)
        return DayFetch(day=day, error=str(last))

    async def fetch_days(
        self, dataset: str, symbol: str, days: Sequence[date]
    ) -> list[DayFetch]:
        """Baja varios días en paralelo, preservando el orden pedido.

        Un fallo de transporte se degrada a `DayFetch.failed` en vez de tumbar
        la ingesta entera: con 1.700 días en vuelo, un timeout aislado no puede
        obligar a empezar de nuevo. Pero se reporta **como fallo**, no como
        ausencia — la diferencia decide si el día se reintenta o se da por
        inexistente.
        """
        results = await asyncio.gather(
            *(self._fetch_day_with_retries(dataset, symbol, day) for day in days)
        )
        for result in results:
            if result.failed:
                logger.warning(
                    "{} {} {}: falló la descarga tras {} intentos — {}",
                    symbol,
                    dataset,
                    result.day,
                    self._retries,
                    result.error,
                )
        return list(results)
