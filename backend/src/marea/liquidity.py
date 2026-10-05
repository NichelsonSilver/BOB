"""Monitor de liquidez: el régimen tal como se conocía en cada fecha. PURO.

Fase M2. **Mide, no pronostica** (decisión del 26-09): la salida es un estado
y su cambio, nunca "la liquidez va a subir". Nada de este módulo se ajustó a
retornos de activos; ver "por qué percentiles" abajo.

**Point-in-time en las dos puntas del cambio.** El cambio a 13 semanas que se
ve el día `d` compara la última observación conocida en `d` con la de 13
semanas antes **tal como se la conocía en `d`** (la misma vintage). Así es como
lo habría leído alguien ese día. Cada fecha de la historia se recalcula con lo
que se sabía en ella, de modo que el percentil de hoy se mide contra señales
pasadas que también fueron reales en su momento, no contra la serie revisada.

**Por qué percentiles y pesos iguales.** La escala de cada indicador es
distinta, y no es estacionaria: el balance de la Fed creció diez veces desde
2008. El percentil de la señal en su propia historia —ventana expansiva, solo
el **pasado estricto**— es la normalización que no usa el futuro y no tiene un
parámetro que ajustar. El compuesto es el promedio de los percentiles con
pesos iguales **declarados**: estimar pesos exigiría un objetivo, y el único
disponible serían los retornos, o sea convertir el monitor en un pronóstico sin
pre-registro (regla 2). La etiqueta final sale del percentil del compuesto en
su propia historia, porque el promedio de percentiles se concentra en 0,5 y sus
terciles no serían terciles.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from marea.pit import Period, as_of

#: Horizontes del cambio. La señal que se clasifica es la de 13 semanas; la de
#: 4 se muestra para leer el giro reciente y no entra al percentil.
H_SHORT_DAYS = 28
H_LONG_DAYS = 91

#: Historia mínima, en fechas de evaluación semanales, para emitir un percentil.
#: Tres años: con menos, un solo ciclo de política define qué es "alto".
MIN_HISTORY = 156

Label = Literal["expansivo", "neutro", "contractivo"]


@dataclass(frozen=True)
class Realtime:
    """Una serie leída fecha por fecha con lo que se sabía en cada una.

    Todos los arrays van alineados con las fechas de evaluación. NaN = no
    había dato utilizable en esa fecha (no publicado todavía o vencido).
    """

    obs_date: list[date | None]
    level: NDArray[np.float64]
    past_short: NDArray[np.float64]
    past_long: NDArray[np.float64]
    #: False si el punto sale del rezago de respaldo de ALFRED (antes de la
    #: primera vintage) y no de un período de tiempo real registrado.
    exact: NDArray[np.bool_]


def _latest_at_or_before(pts: Sequence[tuple[date, float]], target: date) -> float:
    i = bisect.bisect_right([p[0] for p in pts], target)
    return pts[i - 1][1] if i > 0 else math.nan


def realtime(
    periods: Sequence[Period],
    eval_dates: Sequence[date],
    max_age_days: int,
    first_vintage: date | None = None,
    fallback_lag_days: int | None = None,
) -> Realtime:
    """Nivel actual y niveles pasados de la serie, conocidos en cada fecha.

    - Actual: la última observación **no nula** conocida en `d`. Si su fecha es
      anterior a `d - max_age_days`, el punto es NaN: un dato vencido no
      describe el presente, y arrastrarlo sería rellenar.
    - Pasado: la última observación no nula con fecha `<= actual - h`, de la
      **misma** vintage.

    Reutiliza `pit.as_of` sobre una ventana de observaciones, así que la
    definición de "qué se sabía" es una sola en todo MAREA.
    """
    ordered = sorted(periods, key=lambda p: (p.obs_date, p.realtime_start))
    obs_keys = [p.obs_date for p in ordered]
    span = timedelta(days=max_age_days + H_LONG_DAYS + 31)

    n = len(eval_dates)
    obs_out: list[date | None] = [None] * n
    level = np.full(n, np.nan)
    p_short = np.full(n, np.nan)
    p_long = np.full(n, np.nan)
    exact = np.zeros(n, dtype=bool)

    for i, d in enumerate(eval_dates):
        lo = bisect.bisect_left(obs_keys, d - span)
        hi = bisect.bisect_right(obs_keys, d)
        pts = as_of(ordered[lo:hi], d, first_vintage, fallback_lag_days)
        known = [(p.obs_date, p.value, p.exact) for p in pts if p.value is not None]
        if not known:
            continue
        cur_obs, cur_val, cur_exact = known[-1]
        if cur_obs < d - timedelta(days=max_age_days):
            continue
        vals = [(o, v) for o, v, _ in known]
        obs_out[i] = cur_obs
        level[i] = cur_val
        exact[i] = cur_exact
        p_short[i] = _latest_at_or_before(vals, cur_obs - timedelta(days=H_SHORT_DAYS))
        p_long[i] = _latest_at_or_before(vals, cur_obs - timedelta(days=H_LONG_DAYS))
    return Realtime(obs_out, level, p_short, p_long, exact)


def combine(parts: Sequence[tuple[Realtime, float]]) -> Realtime:
    """Combinación lineal `Σ coef · serie`, punta por punta.

    Si falta cualquier componente en una fecha, la combinación falta: la
    liquidez neta sin los repos inversos no es liquidez neta. La fecha de la
    combinación es la más vieja de sus componentes, que es la que limita qué
    tan actual es el número.
    """
    first = parts[0][0]
    n = first.level.size
    level = np.zeros(n)
    p_short = np.zeros(n)
    p_long = np.zeros(n)
    exact = np.ones(n, dtype=bool)
    for rt, coef in parts:
        level = level + coef * rt.level
        p_short = p_short + coef * rt.past_short
        p_long = p_long + coef * rt.past_long
        exact &= rt.exact
    obs: list[date | None] = []
    for i in range(n):
        ds = [rt.obs_date[i] for rt, _ in parts]
        obs.append(None if any(x is None for x in ds) else min(x for x in ds if x is not None))
    return Realtime(obs, level, p_short, p_long, exact)


def product(a: Realtime, b: Realtime, b_power: float = 1.0) -> Realtime:
    """`a · b^p` punta por punta (conversión de moneda: EUR·USD/EUR, JPY/JPY por USD)."""
    obs: list[date | None] = [
        None if x is None or y is None else min(x, y)
        for x, y in zip(a.obs_date, b.obs_date, strict=True)
    ]
    return Realtime(
        obs,
        a.level * b.level**b_power,
        a.past_short * b.past_short**b_power,
        a.past_long * b.past_long**b_power,
        a.exact & b.exact,
    )


def change(rt: Realtime, kind: Literal["log", "diff"], long: bool = True) -> NDArray[np.float64]:
    """Cambio entre el nivel actual y el pasado. `log` para niveles con escala
    (balances, M2, dólar), `diff` para tasas e índices que cruzan el cero."""
    past = rt.past_long if long else rt.past_short
    with np.errstate(divide="ignore", invalid="ignore"):
        if kind == "log":
            ok = (rt.level > 0) & (past > 0)
            out = np.where(
                ok, np.log(np.where(ok, rt.level, 1.0) / np.where(ok, past, 1.0)), np.nan
            )
        else:
            out = rt.level - past
    return np.asarray(out, dtype=np.float64)


def expanding_percentile(
    x: NDArray[np.float64], min_history: int = MIN_HISTORY, window: int | None = None
) -> NDArray[np.float64]:
    """Percentil de cada punto contra los puntos **anteriores** válidos.

    Pasado estricto: el punto no se compara consigo mismo ni con nada
    posterior. Un cuantil sobre la muestra completa usaría el futuro para
    decidir qué es "alto" hoy (regla 5). Empates cuentan la mitad.
    `window` limita la historia a los últimos N válidos (COT, ver monitor).
    """
    out = np.full(x.size, np.nan)
    hist: list[float] = []
    for i, v in enumerate(x):
        if not math.isfinite(v):
            continue
        ref = hist if window is None else hist[-window:]
        if len(ref) >= min_history:
            arr = np.asarray(ref)
            out[i] = (np.sum(arr < v) + 0.5 * np.sum(arr == v)) / arr.size
        hist.append(float(v))
    return out


def label(p: float) -> Label | None:
    """Tercil del percentil orientado (alto = expansivo)."""
    if not math.isfinite(p):
        return None
    if p >= 2 / 3:
        return "expansivo"
    if p < 1 / 3:
        return "contractivo"
    return "neutro"


def composite(oriented_pcts: Sequence[NDArray[np.float64]]) -> NDArray[np.float64]:
    """Promedio con pesos iguales; exige TODOS los componentes.

    Un compuesto con componentes que entran y salen cambia de definición a lo
    largo de la historia, y su percentil compararía cosas distintas.
    """
    m = np.vstack(oriented_pcts)
    out = m.mean(axis=0)
    out[~np.all(np.isfinite(m), axis=0)] = np.nan
    return np.asarray(out, dtype=np.float64)


def weekly_dates(start: date, end: date, weekday: int = 4) -> list[date]:
    """Viernes (por defecto) desde `start` hasta `end`, más `end` si no cae en
    viernes: el estado de hoy se lee hoy, no el viernes pasado."""
    d = start + timedelta(days=(weekday - start.weekday()) % 7)
    out: list[date] = []
    while d <= end:
        out.append(d)
        d += timedelta(days=7)
    if not out or out[-1] != end:
        out.append(end)
    return out
