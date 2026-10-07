"""Derivadas de la oferta de stablecoins. PURO.

- `known_series`: la serie tal como se conocía en una fecha, con las vintages
  construidas en `marea.data.ingest_stable`.
- `net_mints`: emisiones netas diarias, promediadas en una ventana móvil.
- `btc_supply` y `ssr`: Stablecoin Supply Ratio = capitalización de BTC /
  oferta de stablecoins. Un SSR bajo dice que hay mucho "polvo seco" en
  stablecoins en relación al tamaño de BTC; uno alto, que hay poco.

Todo es monitoreo (docs/M5_ALCANCE.md §5): nada de esto pronostica.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date, timedelta

from marea.pit import AsOfPoint, Period, as_of

#: Rezago declarado para lo anterior a la primera corrida: la fila del día D
#: se cierra al terminar D. DefiLlama no lo documenta, así que no es medido.
FALLBACK_LAG_DAYS = 1


def known_series(periods: Sequence[Period], known_at: date) -> list[AsOfPoint]:
    """La serie como se conocía el día `known_at` (no exacta antes de la 1ª corrida)."""
    if not periods:
        return []
    first_vintage = min(p.realtime_start for p in periods)
    return as_of(periods, known_at, first_vintage, FALLBACK_LAG_DAYS)


def net_mints(series: Sequence[tuple[date, float]], window: int = 7) -> list[tuple[date, float]]:
    """Promedio móvil de `window` días de la variación diaria de la oferta.

    Un día solo tiene variación si el anterior existe, y el promedio solo sale
    si la ventana está completa: un hueco no se rellena ni se promedia de menos.
    """
    if window < 1:
        raise ValueError("window debe ser >= 1")
    values = dict(series)
    diffs: dict[date, float] = {}
    for d, v in values.items():
        prev = values.get(d - timedelta(days=1))
        if prev is not None:
            diffs[d] = v - prev
    out: list[tuple[date, float]] = []
    for d in sorted(diffs):
        span = [diffs.get(d - timedelta(days=k)) for k in range(window)]
        if all(x is not None for x in span):
            out.append((d, sum(x for x in span if x is not None) / window))
    return out


#: Supply exacto de BTC en cada halving (bloques 210.000 × n) y su fecha UTC.
HALVINGS: tuple[tuple[date, float], ...] = (
    (date(2009, 1, 3), 0.0),
    (date(2012, 11, 28), 10_500_000.0),
    (date(2016, 7, 9), 15_750_000.0),
    (date(2020, 5, 11), 18_375_000.0),
    (date(2024, 4, 20), 19_687_500.0),
)
#: Emisión de la época actual: 3,125 BTC × 144 bloques por día.
CURRENT_DAILY_ISSUANCE = 3.125 * 144
#: Antes de esta fecha llega el 5º halving (bloque 1.050.000, ~2028-04) y la
#: extrapolación deja de valer. Se falla en voz alta en vez de seguir.
VALID_UNTIL = date(2028, 1, 1)


def btc_supply(day: date) -> float:
    """Supply de BTC aproximado: interpolación lineal entre halvings.

    El supply es determinista por altura de bloque, pero la altura en una fecha
    depende de cuán rápido salieron los bloques (entre ~137 y ~155 por día). El
    error de interpolar por tiempo está acotado por esa variación dentro de una
    época: menos de 0,2 % del supply, despreciable para el SSR.
    """
    if day < HALVINGS[0][0]:
        return 0.0
    if day >= VALID_UNTIL:
        raise ValueError(f"btc_supply no vale desde {VALID_UNTIL}: agregar la fecha del 5º halving")
    for (d0, s0), (d1, s1) in zip(HALVINGS, HALVINGS[1:], strict=False):
        if d0 <= day < d1:
            return s0 + (s1 - s0) * (day - d0).days / (d1 - d0).days
    last_day, last_supply = HALVINGS[-1]
    return last_supply + CURRENT_DAILY_ISSUANCE * (day - last_day).days


def ssr(
    btc_price: Sequence[tuple[date, float]], stable_supply: Sequence[tuple[date, float]]
) -> list[tuple[date, float]]:
    """SSR en los días que tienen las dos series y oferta positiva."""
    supply = dict(stable_supply)
    out: list[tuple[date, float]] = []
    for d, price in sorted(btc_price):
        s = supply.get(d)
        if s is None or s <= 0 or not math.isfinite(price):
            continue
        out.append((d, price * btc_supply(d) / s))
    return out
