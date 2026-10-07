"""Métricas del libro de opciones de Deribit. PURO.

- **Put/call por open interest**: contratos put abiertos / call abiertos. Por
  encima de 1, el mercado tiene más cobertura bajista abierta que apuestas
  alcistas.
- **Put/call por volumen de 24 h**: lo mismo con lo negociado en el día. Es
  más ruidoso y reacciona antes.
- **Put/call de vencimientos cercanos** (≤ 30 días): separa el posicionamiento
  táctico del estructural, que vive en los vencimientos trimestrales.
- **Open interest nocional en USD**: contratos × precio del subyacente.

Cada snapshot es una foto: se conoce en `captured_at`. Monitoreo, sin
pronóstico.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

NEAR_DAYS = 30
_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
    )
}


@dataclass(frozen=True)
class Instrument:
    expiry: datetime
    strike: float
    is_call: bool


def parse_instrument(name: str) -> Instrument:
    """`BTC-27NOV26-73000-P` → vence 2026-11-27 08:00 UTC, strike 73000, put.

    Deribit vence a las 08:00 UTC. El strike de ETH puede traer `d` como punto
    decimal en algunos instrumentos (`ETH-...-1d5-C`), y se respeta.
    """
    try:
        _, exp, strike, kind = name.split("-")
        day, mon, yy = int(exp[:-5]), _MONTHS[exp[-5:-2]], int(exp[-2:])
    except (ValueError, KeyError) as exc:
        raise ValueError(f"instrumento no reconocido: {name}") from exc
    if kind not in ("C", "P"):
        raise ValueError(f"instrumento no reconocido: {name}")
    expiry = datetime(2000 + yy, mon, day, 8, tzinfo=UTC)
    return Instrument(expiry, float(strike.replace("d", ".")), kind == "C")


@dataclass(frozen=True)
class OptionsSnapshot:
    currency: str
    captured_at: datetime
    instruments: int
    underlying: float
    oi_calls: float
    oi_puts: float
    vol_calls: float
    vol_puts: float
    oi_usd: float
    near_oi_calls: float
    near_oi_puts: float

    @staticmethod
    def _ratio(a: float, b: float) -> float:
        return a / b if b > 0 else math.nan

    @property
    def pc_oi(self) -> float:
        return self._ratio(self.oi_puts, self.oi_calls)

    @property
    def pc_volume(self) -> float:
        return self._ratio(self.vol_puts, self.vol_calls)

    @property
    def pc_oi_near(self) -> float:
        return self._ratio(self.near_oi_puts, self.near_oi_calls)


def _num(row: dict[str, Any], key: str) -> float:
    v = row.get(key)
    return float(v) if isinstance(v, int | float) and math.isfinite(v) else 0.0


def summarize(
    currency: str, rows: Sequence[dict[str, Any]], captured_at: datetime
) -> OptionsSnapshot:
    """Agrega los instrumentos vigentes (los ya vencidos se ignoran)."""
    oi = {True: 0.0, False: 0.0}
    vol = {True: 0.0, False: 0.0}
    near = {True: 0.0, False: 0.0}
    oi_usd = 0.0
    prices: list[float] = []
    n = 0
    horizon = captured_at + timedelta(days=NEAR_DAYS)
    for row in rows:
        inst = parse_instrument(str(row["instrument_name"]))
        if inst.expiry <= captured_at:
            continue
        n += 1
        o = _num(row, "open_interest")
        px = _num(row, "underlying_price")
        oi[inst.is_call] += o
        vol[inst.is_call] += _num(row, "volume")
        oi_usd += o * px
        if px > 0:
            prices.append(px)
        if inst.expiry <= horizon:
            near[inst.is_call] += o
    prices.sort()
    return OptionsSnapshot(
        currency=currency,
        captured_at=captured_at,
        instruments=n,
        underlying=prices[len(prices) // 2] if prices else math.nan,
        oi_calls=oi[True],
        oi_puts=oi[False],
        vol_calls=vol[True],
        vol_puts=vol[False],
        oi_usd=oi_usd,
        near_oi_calls=near[True],
        near_oi_puts=near[False],
    )
