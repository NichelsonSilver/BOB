"""Risk de ciclo de BTC: point-in-time contra la versión con lookahead. PURO + CLI.

La idea es la del "Outliers Risk v2" que Nichelson usa (`docs/M5_ALCANCE.md`):
un compuesto de 0 a 1 con 60 % de Mayer Multiple (cierre / SMA200) y 40 % de la
desviación del precio contra una regresión log-log sobre los días desde el
bloque génesis. Outliers fija sus "anchors" **con toda la historia**, o sea
mira el futuro, y eso explica por qué su gráfico se pone rojo en cada techo.

Acá se construyen las dos versiones con la misma receta y se publican juntas:

- **completa** (`risk_full`): regresión y normalización sobre toda la muestra.
  Es la réplica del método con lookahead; no se usa para decidir nada.
- **point-in-time** (`risk_pit`): la regresión se ajusta solo con lo anterior
  (mínimos cuadrados con sumas acumuladas) y cada componente se normaliza por
  su percentil contra el **pasado estricto**, como el monitor de la M2.

La normalización es por percentil y no min-max porque los extremos de BTC son
de a uno por ciclo: un min-max point-in-time salta cada vez que aparece un
máximo nuevo. Que no reproduzca el número exacto de Outliers es esperable: su
normalización no está publicada.

Monitoreo, sin pronóstico (§5 del alcance): con 3 o 4 ciclos no hay potencia
para afirmar que "anticipa techos".

    uv run python -m marea.cycle
"""

from __future__ import annotations

import bisect
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from marea.pit import Period

GENESIS = date(2009, 1, 3)
SMA_DAYS = 200
MAYER_WEIGHT = 0.6
#: Historia mínima antes de emitir el valor point-in-time: un ciclo completo.
MIN_HISTORY_DAYS = 4 * 365
#: El precio de un día se usa como estaba publicado este número de días después.
PRICE_LAG_DAYS = 1
REFERENCE_DATES: tuple[tuple[str, date], ...] = (
    ("techo 2021", date(2021, 11, 10)),
    ("piso 2022", date(2022, 11, 21)),
    ("ETF 2024", date(2024, 3, 13)),
)


def settled_prices(
    periods: Sequence[Period], lag_days: int = PRICE_LAG_DAYS
) -> list[tuple[date, float]]:
    """El precio de cada día tal como se conocía `lag_days` después.

    `CBBTCUSD` publica el día D con un precio intradía y lo corrige al cierre
    al día siguiente (451 observaciones revisadas, mediana 1 %, máximo 10 %,
    medido el 07-10). Las revisiones posteriores no se usan. Antes de la
    primera vintage solo existe un período y se usa ese.
    """
    by_obs: dict[date, list[Period]] = {}
    for p in periods:
        by_obs.setdefault(p.obs_date, []).append(p)
    out: list[tuple[date, float]] = []
    for obs in sorted(by_obs):
        ps = sorted(by_obs[obs], key=lambda p: p.realtime_start)
        known = obs + timedelta(days=lag_days)
        hit = next((p for p in ps if p.realtime_start <= known <= p.realtime_end), ps[0])
        if hit.value is not None and hit.value > 0:
            out.append((obs, hit.value))
    return out


@dataclass(frozen=True)
class CycleRow:
    day: date
    price: float
    mayer: float
    risk_full: float
    #: None durante el warm-up de MIN_HISTORY_DAYS.
    risk_pit: float | None


def _pct_of_past(values: np.ndarray) -> np.ndarray:
    """Percentil de cada valor contra los anteriores (estricto). NaN sin pasado."""
    out = np.full(len(values), np.nan)
    seen: list[float] = []
    for i, v in enumerate(values):
        if not math.isfinite(v):
            continue
        if seen:
            out[i] = bisect.bisect_left(seen, v) / len(seen)
        bisect.insort(seen, v)
    return out


def _pct_full(values: np.ndarray) -> np.ndarray:
    ok = np.isfinite(values)
    ranks = np.full(len(values), np.nan)
    order = np.sort(values[ok])
    ranks[ok] = np.searchsorted(order, values[ok], side="left") / max(len(order) - 1, 1)
    return ranks


def cycle_risk(prices: Sequence[tuple[date, float]]) -> list[CycleRow]:
    """Las dos versiones del compuesto, día por día, sobre una serie diaria.

    El SMA200 es de **observaciones**; con huecos (35 en `CBBTCUSD`) cubre algo
    más de 200 días. No se rellena.
    """
    days = [d for d, _ in prices]
    p = np.array([v for _, v in prices], dtype=float)
    n = len(p)
    if n <= SMA_DAYS:
        return []
    logp = np.log(p)
    x = np.log(np.array([(d - GENESIS).days for d in days], dtype=float))

    csum = np.concatenate(([0.0], np.cumsum(p)))
    sma = np.full(n, np.nan)
    sma[SMA_DAYS - 1 :] = (csum[SMA_DAYS:] - csum[:-SMA_DAYS]) / SMA_DAYS
    mayer = np.log(p / sma)

    # Regresión completa (lookahead) y expansiva (point-in-time), y = a + b·x.
    b_full, a_full = np.polyfit(x, logp, 1)
    dev_full = logp - (a_full + b_full * x)
    k = np.arange(1, n + 1, dtype=float)
    sx, sy = np.cumsum(x), np.cumsum(logp)
    sxx, sxy = np.cumsum(x * x), np.cumsum(x * logp)
    var = sxx - sx * sx / k
    with np.errstate(divide="ignore", invalid="ignore"):
        b_pit = (sxy - sx * sy / k) / var
    a_pit = (sy - b_pit * sx) / k
    dev_pit = logp - (a_pit + b_pit * x)

    risk_full = MAYER_WEIGHT * _pct_full(mayer) + (1 - MAYER_WEIGHT) * _pct_full(dev_full)
    risk_pit = MAYER_WEIGHT * _pct_of_past(mayer) + (1 - MAYER_WEIGHT) * _pct_of_past(dev_pit)
    start = days[0] + timedelta(days=MIN_HISTORY_DAYS)

    return [
        CycleRow(
            day=days[i],
            price=float(p[i]),
            mayer=float(math.exp(mayer[i])),
            risk_full=float(risk_full[i]),
            risk_pit=float(risk_pit[i])
            if days[i] >= start and math.isfinite(risk_pit[i])
            else None,
        )
        for i in range(n)
        if math.isfinite(mayer[i])
    ]


@dataclass(frozen=True)
class Divergence:
    n: int
    correlation: float
    mean_abs_diff: float
    #: Fracción de días en que las dos versiones difieren en más de 0,20.
    share_far: float


def divergence(rows: Sequence[CycleRow]) -> Divergence:
    both = [(r.risk_pit, r.risk_full) for r in rows if r.risk_pit is not None]
    if len(both) < 2:
        return Divergence(len(both), math.nan, math.nan, math.nan)
    a = np.array([b[0] for b in both])
    f = np.array([b[1] for b in both])
    diff = np.abs(a - f)
    return Divergence(
        n=len(both),
        correlation=float(np.corrcoef(a, f)[0, 1]),
        mean_abs_diff=float(diff.mean()),
        share_far=float((diff > 0.20).mean()),
    )


def main(argv: list[str] | None = None) -> int:  # pragma: no cover — I/O
    from bob.utils.console import enable_utf8_stdout
    from marea.config import DB_PATH
    from marea.data import store

    enable_utf8_stdout()
    conn = store.connect(DB_PATH)
    try:
        rows = cycle_risk(settled_prices(store.load_periods(conn, "CBBTCUSD")))
    finally:
        conn.close()
    if not rows:
        print("sin datos de CBBTCUSD: correr `marea.data.ingest fred --series CBBTCUSD`")
        return 1
    last = rows[-1]
    by_day = {r.day: r for r in rows}
    print(f"Risk de ciclo BTC al {last.day} (precio {last.price:,.0f}, Mayer {last.mayer:.2f})")
    print(
        f"  point-in-time : {last.risk_pit:.3f}"
        if last.risk_pit is not None
        else "  point-in-time : —"
    )
    print(f"  con lookahead : {last.risk_full:.3f}")
    print("\nfechas de referencia      PIT    completa")
    for label, d in REFERENCE_DATES:
        r = by_day.get(d)
        if r is not None:
            pit = f"{r.risk_pit:.3f}" if r.risk_pit is not None else "  —  "
            print(f"  {label:<14} {d}  {pit}   {r.risk_full:.3f}")
    dv = divergence(rows)
    print(
        f"\nPIT vs completa sobre {dv.n} días: correlación {dv.correlation:.3f}, "
        f"diferencia media {dv.mean_abs_diff:.3f}, {100 * dv.share_far:.1f}% de días a más de 0,20"
    )
    print("Monitoreo: describe dónde está el precio en su historia, no pronostica.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
