"""Universo y panel diario multi-símbolo — la capa de I/O de la Fase 10.

Frontera de pureza (regla 3 de CLAUDE.md): igual que `store.py`, este módulo
es el último que toca red y disco. Devuelve un `PanelData` de arrays numpy;
de ahí para arriba `signals/` y `models/` no saben que existe una DB.

**Por qué el universo se congela en un artefacto.** `exchangeInfo` es
solo-vivo: los símbolos deslistados desaparecen de él por completo. Medido el
2026-09-22 sobre el archivo de `data.binance.vision`: de los 864 símbolos
USDT que el archivo guarda, 31 ya no están en `exchangeInfo` —entre ellos
`LUNAUSDT`, el colapso de Terra de mayo de 2022, el mayor reventón de la
muestra—. Armar el universo desde la API en vivo omite en silencio justo lo
que cayó, que es el sesgo de supervivencia en su forma más cara.

Y hay una segunda razón, de reproducibilidad: esa llamada devuelve algo
distinto cada semana. Un run que la consulte no se puede reproducir. Así que
el descubrimiento es un paso explícito que escribe un JSON versionado, y el
runner lee de ese archivo y nunca de la red — el mismo trato que el proyecto
le da a los artefactos del gate.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
from loguru import logger
from sqlmodel import Session, col, select

from bob.data.binance_rest import INTERVAL_MS, BinanceRestClient
from bob.data.vision import VisionClient
from bob.db.models import CandleRecord
from bob.db.session import get_session, init_db

_BACKEND_DIR: Final = Path(__file__).resolve().parent.parent.parent.parent
ARTIFACTS_DIR: Final = _BACKEND_DIR / "artifacts"

#: Perpetuos de acciones e índices (`AAPLUSDT`, `NVDAUSDT`, …). Binance los
#: marca con este `contractType`, así que la exclusión es por bandera del
#: exchange y no por heurística sobre el nombre —que fallaría con `ALLUSDT`
#: o con cualquier token cuyo ticker coincida con el de una acción.
TRADFI_CONTRACT: Final = "TRADIFI_PERPETUAL"
CRYPTO_CONTRACT: Final = "PERPETUAL"

#: Un símbolo ausente del `exchangeInfo` no tiene `contractType` que consultar.
#: Se asume cripto: los 31 ausentes medidos el 2026-09-22 lo son todos, y los
#: 179 `TRADIFI_PERPETUAL` estaban los 179 presentes. La suposición queda
#: registrada en `SymbolMeta.source` para que sea auditable.
STATUS_DELISTED: Final = "DELISTED"


@dataclass(frozen=True)
class SymbolMeta:
    """Ficha de un símbolo del archivo, suficiente para decidir si entra.

    `first_month` / `last_month` salen del listado S3 (`YYYY-MM`), no de una
    descarga: dan la profundidad de historia sin bajar un solo byte, y para
    los deslistados son la única fuente porque ya no tienen `onboardDate`.
    """

    symbol: str
    contract_type: str
    status: str
    first_month: str
    last_month: str
    n_months: int
    source: str  # "exchangeInfo" | "archive-only"

    @property
    def is_crypto_perp(self) -> bool:
        return self.contract_type == CRYPTO_CONTRACT

    @property
    def is_delisted(self) -> bool:
        return self.status == STATUS_DELISTED


@dataclass(frozen=True)
class Universe:
    """Universo congelado: la lista de símbolos y cuándo se la descubrió."""

    timeframe: str
    discovered_at: str
    symbols: tuple[SymbolMeta, ...]

    def crypto_perps(self, min_months: int = 0) -> list[SymbolMeta]:
        """Los perpetuos cripto, excluidos los de acciones e índices."""
        return [
            m for m in self.symbols if m.is_crypto_perp and m.n_months >= min_months
        ]

    def summary(self) -> dict[str, int]:
        return {
            "total": len(self.symbols),
            "crypto_perp": sum(1 for m in self.symbols if m.is_crypto_perp),
            "tradfi": sum(1 for m in self.symbols if m.contract_type == TRADFI_CONTRACT),
            "delisted": sum(1 for m in self.symbols if m.is_delisted),
        }


async def discover_universe(timeframe: str = "1d", *, quote: str = "USDT") -> Universe:
    """Cruza el archivo de Vision con `exchangeInfo` y arma el universo.

    El archivo manda sobre quién existe; `exchangeInfo` solo aporta la
    clasificación de contrato de los que siguen vivos. Un símbolo que está en
    el archivo y no en la API **entra igual**, marcado `DELISTED`: es el caso
    que justifica todo este módulo.
    """
    async with VisionClient() as vision:
        archive = await vision.list_symbols("klines")
        archive = [s for s in archive if s.endswith(quote)]
        logger.info("archivo de Vision: {} símbolos {}", len(archive), quote)

        async with BinanceRestClient() as rest:
            info = await rest.exchange_info()
        live: dict[str, Any] = {e["symbol"]: e for e in info.get("symbols", [])}

        metas: list[SymbolMeta] = []
        for symbol in archive:
            months = await vision.available_months(symbol, timeframe)
            if not months:
                continue
            entry = live.get(symbol)
            metas.append(
                SymbolMeta(
                    symbol=symbol,
                    contract_type=(
                        entry.get("contractType", CRYPTO_CONTRACT)
                        if entry
                        else CRYPTO_CONTRACT
                    ),
                    status=entry.get("status", STATUS_DELISTED) if entry else STATUS_DELISTED,
                    first_month=months[0],
                    last_month=months[-1],
                    n_months=len(months),
                    source="exchangeInfo" if entry else "archive-only",
                )
            )

    universe = Universe(
        timeframe=timeframe,
        discovered_at=datetime.now(tz=UTC).isoformat(timespec="seconds"),
        symbols=tuple(metas),
    )
    logger.info("universo descubierto: {}", universe.summary())
    return universe


def save_universe(universe: Universe, path: Path | None = None) -> Path:
    """Congela el universo a JSON versionado. Es lo que hace el run reproducible."""
    target = path or ARTIFACTS_DIR / f"universe-{universe.timeframe}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timeframe": universe.timeframe,
        "discovered_at": universe.discovered_at,
        "summary": universe.summary(),
        "symbols": [asdict(m) for m in universe.symbols],
    }
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    logger.info("universo escrito en {}", target)
    return target


def load_universe(path: Path | None = None, timeframe: str = "1d") -> Universe:
    """Lee el universo congelado. **No toca la red**: es el camino del runner."""
    target = path or ARTIFACTS_DIR / f"universe-{timeframe}.json"
    if not target.exists():
        raise FileNotFoundError(
            f"no hay universo congelado en {target}. Correr primero: "
            "uv run python -m bob.data.download_panel --discover"
        )
    payload = json.loads(target.read_text(encoding="utf-8"))
    return Universe(
        timeframe=payload["timeframe"],
        discovered_at=payload["discovered_at"],
        symbols=tuple(SymbolMeta(**m) for m in payload["symbols"]),
    )


@dataclass(frozen=True)
class PanelData:
    """Panel alineado: `S` símbolos × `T` fechas, con NaN donde no hay dato.

    El eje de fechas es la **unión** de las fechas de todos los símbolos, no la
    intersección. La intersección parece más limpia y está mal: recorta el
    panel al símbolo más joven y vuelve a seleccionar sobrevivientes, que es el
    sesgo que este módulo existe para evitar. Un símbolo que todavía no listaba
    —o que ya se deslistó— tiene NaN en esas columnas, y la máscara de
    elegibilidad decide qué hacer con ellas.

    Todos los arrays de precio tienen forma `(S, T)` y comparten índices:
    `close[i, j]` es el símbolo `symbols[i]` en la fecha `open_time[j]`.
    """

    symbols: tuple[str, ...]
    timeframe: str
    open_time: np.ndarray  # (T,) int64, epoch ms UTC
    open: np.ndarray  # (S, T) float64
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    quote_volume: np.ndarray
    taker_buy_volume: np.ndarray
    n_trades: np.ndarray

    @property
    def n_symbols(self) -> int:
        return len(self.symbols)

    @property
    def n_dates(self) -> int:
        return int(self.open_time.shape[0])

    @property
    def interval_ms(self) -> int:
        return INTERVAL_MS[self.timeframe]

    @property
    def observed(self) -> np.ndarray:
        """Máscara `(S, T)` de dónde hay vela de verdad."""
        return np.isfinite(self.close)

    def date_gaps(self) -> list[tuple[int, int]]:
        """Huecos del eje de fechas — días que ningún símbolo reportó.

        Se reportan, no se rellenan: misma regla que `OHLCVSeries.gaps`.
        """
        if self.n_dates < 2:
            return []
        step = self.interval_ms
        deltas = np.diff(self.open_time)
        idx = np.flatnonzero(deltas > step)
        return [(int(self.open_time[i]), int(self.open_time[i + 1])) for i in idx]

    def index_of(self, symbol: str) -> int:
        return self.symbols.index(symbol)


def load_panel(
    symbols: Sequence[str],
    timeframe: str = "1d",
    start_time: int | None = None,
    end_time: int | None = None,
    session: Session | None = None,
) -> PanelData:
    """Carga el panel desde SQLite en **una** consulta y lo alinea a la grilla.

    Una consulta por símbolo serían cientos de round-trips contra la misma
    tabla; con 685 símbolos eso domina el tiempo de la corrida entera.
    """
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None

    wanted = list(dict.fromkeys(s.upper() for s in symbols))
    try:
        # mypy: los overloads tipados de `select` no llegan a 10 columnas. El
        # SQL es correcto y está cubierto por tests; traer la entidad entera
        # para complacer al type checker costaría el ORM sobre 625.000 filas.
        stmt = select(  # type: ignore[call-overload]
            CandleRecord.symbol,
            CandleRecord.open_time,
            CandleRecord.open,
            CandleRecord.high,
            CandleRecord.low,
            CandleRecord.close,
            CandleRecord.volume,
            CandleRecord.quote_volume,
            CandleRecord.taker_buy_volume,
            CandleRecord.n_trades,
        ).where(
            col(CandleRecord.symbol).in_(wanted),
            CandleRecord.timeframe == timeframe,
        )
        if start_time is not None:
            stmt = stmt.where(CandleRecord.open_time >= start_time)
        if end_time is not None:
            stmt = stmt.where(CandleRecord.open_time <= end_time)
        rows = list(session.exec(stmt))
    finally:
        if owns:
            session.close()

    return _panel_from_rows(wanted, timeframe, rows)


def _panel_from_rows(
    symbols: Sequence[str],
    timeframe: str,
    rows: Iterable[tuple[str, int, str, str, str, str, str, str, str, int]],
) -> PanelData:
    """Arma la grilla `(S, T)` desde filas sueltas. Puro salvo por el tipo de entrada."""
    materialized = list(rows)
    present = sorted({r[0] for r in materialized})
    # Se conserva el orden pedido, pero solo los símbolos que tienen alguna vela:
    # una fila entera de NaN no aporta y desordena los rangos transversales.
    ordered = tuple(s for s in symbols if s in set(present))
    dates = np.array(sorted({r[1] for r in materialized}), dtype=np.int64)

    s_idx = {s: i for i, s in enumerate(ordered)}
    d_idx = {int(t): j for j, t in enumerate(dates)}
    shape = (len(ordered), int(dates.shape[0]))

    grids = {
        name: np.full(shape, np.nan, dtype=np.float64)
        for name in (
            "open", "high", "low", "close",
            "volume", "quote_volume", "taker_buy_volume", "n_trades",
        )
    }
    for sym, open_time, op, hi, lo, cl, vol, qvol, tbv, ntr in materialized:
        i = s_idx.get(sym)
        if i is None:
            continue
        j = d_idx[int(open_time)]
        grids["open"][i, j] = float(op)
        grids["high"][i, j] = float(hi)
        grids["low"][i, j] = float(lo)
        grids["close"][i, j] = float(cl)
        grids["volume"][i, j] = float(vol)
        grids["quote_volume"][i, j] = float(qvol)
        grids["taker_buy_volume"][i, j] = float(tbv)
        grids["n_trades"][i, j] = float(ntr)

    return PanelData(
        symbols=ordered,
        timeframe=timeframe,
        open_time=dates,
        **grids,
    )


def panel_coverage(
    symbols: Sequence[str], timeframe: str = "1d", session: Session | None = None
) -> dict[str, dict[str, int]]:
    """Qué hay persistido por símbolo, en una sola consulta agregada."""
    owns = session is None
    if owns:
        init_db()
        session = get_session()
    assert session is not None
    wanted = [s.upper() for s in symbols]
    try:
        from sqlalchemy import func  # local: solo lo usa esta función

        open_time_col = col(CandleRecord.open_time)
        stmt = (
            select(
                CandleRecord.symbol,
                func.count(open_time_col),
                func.min(open_time_col),
                func.max(open_time_col),
            )
            .where(
                col(CandleRecord.symbol).in_(wanted),
                CandleRecord.timeframe == timeframe,
            )
            .group_by(col(CandleRecord.symbol))
        )
        return {
            sym: {
                "n_candles": int(n),
                "first_open_time": int(lo or 0),
                "last_open_time": int(hi or 0),
            }
            for sym, n, lo, hi in session.exec(stmt)
        }
    finally:
        if owns:
            session.close()
