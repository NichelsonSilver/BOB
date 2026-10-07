"""Point-in-time: qué valor de una serie se CONOCÍA en una fecha dada. PURO.

La fecha de un dato macro no es la fecha en que se conoce. Medido sobre FRED
el 2026-09-27: el USD/EUR del 15-09 se publicó el 21-09 (el H.10 sale semanal),
el M2 de agosto el 22-09, y el M2 de octubre de 2019 cambió de valor nueve veces
entre enero y junio de 2020. Leer la historia "actual" de una serie y alinearla
por fecha del dato es lookahead (regla 5), y es invisible: el backtest se ve
mejor de lo que es y no se reproduce en vivo.

El objeto base es el **período de tiempo real** de ALFRED: una observación
(`obs_date`) tuvo el valor `value` entre `realtime_start` y `realtime_end`,
ambos **inclusive**. Una serie es la lista de esos períodos.

Límite declarado: ALFRED no registra vintages anteriores a cierta fecha
(2011 en la mayoría de las series de mercado). Antes de la primera vintage no
se sabe CUÁNDO se conoció cada dato, así que `as_of` usa un rezago de
publicación conservador medido sobre la propia serie y marca el punto como
**no exacto**. Quien necesite point-in-time estricto filtra por `exact`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

#: Fin abierto de un período vigente, tal como lo escribe FRED.
OPEN_END = date(9999, 12, 31)


@dataclass(frozen=True)
class Period:
    obs_date: date
    realtime_start: date
    realtime_end: date
    #: `None` cuando FRED publica "." (dato faltante). Se guarda así, no se
    #: rellena: un faltante es información.
    value: float | None


@dataclass(frozen=True)
class AsOfPoint:
    obs_date: date
    value: float | None
    #: False cuando el punto sale del rezago de respaldo (antes de la primera
    #: vintage) y no de un período de tiempo real registrado.
    exact: bool


def _same_value(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return a == b or (math.isnan(a) and math.isnan(b))


def stitch(earlier: Iterable[Period], later: Iterable[Period], boundary: date) -> list[Period]:
    """Une dos tramos pedidos con ventanas de tiempo real contiguas.

    FRED recorta cada período al borde de la ventana pedida: uno que siguió
    vigente después de `boundary - 1 día` aparece en el tramo `earlier` con
    `realtime_end = boundary - 1` y en `later` con `realtime_start = boundary`.
    Si el valor es el mismo es un único período y se une; si cambió, la
    revisión cayó exactamente en `boundary` y quedan dos.

    `earlier` puede ser también lo que ya está en la DB (ingesta incremental):
    ahí los períodos vigentes tienen `realtime_end = OPEN_END`, y se tratan
    igual que los recortados.
    """
    cut = boundary - timedelta(days=1)
    open_before: dict[date, Period] = {}
    out: list[Period] = []
    for p in earlier:
        if p.realtime_end in (cut, OPEN_END) and p.realtime_start < boundary:
            open_before[p.obs_date] = p
        else:
            out.append(p)
    for p in later:
        prev = open_before.get(p.obs_date)
        if prev is not None and p.realtime_start == boundary and _same_value(prev.value, p.value):
            out.append(replace(prev, realtime_end=p.realtime_end))
            del open_before[p.obs_date]
            continue
        if prev is not None and p.realtime_start == boundary:
            # Revisión justo en el borde: el período anterior termina el día previo.
            out.append(replace(prev, realtime_end=cut))
            del open_before[p.obs_date]
        out.append(p)
    # Lo que quedó abierto antes del borde y no reapareció después: la
    # observación dejó de publicarse (FRED la borró). Se cierra en `cut`
    # solo si venía recortada por la ventana; si venía de la DB con fin
    # abierto, el tramo nuevo no la trae porque no la tocó -> sigue abierta.
    out.extend(open_before.values())
    out.sort(key=lambda p: (p.obs_date, p.realtime_start))
    return out


def first_release_lag_days(periods: Sequence[Period], first_vintage: date) -> int | None:
    """Rezago máximo, en días, entre la fecha del dato y su primera publicación.

    Solo cuentan las observaciones que **extendieron el final de la serie** en
    la vintage en que aparecieron. Las demás no son publicaciones sino rellenos
    de historia: FRED agrega tramos viejos en vintages posteriores (DGS10 se
    extendió hacia 1962 después de 2005, NFCI se re-estima entero), y medirlas
    daba "rezagos" de 44 años. Tampoco cuentan las que aparecen en la primera
    vintage: su `realtime_start` es el inicio de ALFRED, no su publicación.

    Se usa el máximo y no un percentil porque este número decide cuándo un dato
    "ya se conocía" en el período sin vintages, y ahí equivocarse hacia corto
    es lookahead.
    """
    first_seen: dict[date, date] = {}
    for p in periods:
        cur = first_seen.get(p.obs_date)
        if cur is None or p.realtime_start < cur:
            first_seen[p.obs_date] = p.realtime_start

    by_vintage: dict[date, list[date]] = {}
    for obs, seen in first_seen.items():
        by_vintage.setdefault(seen, []).append(obs)

    frontier: date | None = None
    lags: list[int] = []
    for vintage in sorted(by_vintage):
        new = by_vintage[vintage]
        if vintage > first_vintage and frontier is not None:
            lags.extend((vintage - obs).days for obs in new if obs > frontier)
        top = max(new)
        frontier = top if frontier is None else max(frontier, top)
    return max(lags) if lags else None


def as_of(
    periods: Sequence[Period],
    known_at: date,
    first_vintage: date | None,
    fallback_lag_days: int | None,
) -> list[AsOfPoint]:
    """La serie tal como se conocía el día `known_at`, ordenada por `obs_date`.

    - Si hay un período que cubre `known_at`, ese es el valor, exacto.
    - Si `known_at` es anterior a la primera vintage, se usa el valor más viejo
      registrado de cada observación, solo si `obs_date + fallback_lag_days <=
      known_at`, y se marca `exact=False`. Sin rezago medido no se devuelve
      nada: mejor un hueco que un dato inventado.
    - Una observación que todavía no se había publicado no aparece.
    """
    by_obs: dict[date, list[Period]] = {}
    for p in periods:
        by_obs.setdefault(p.obs_date, []).append(p)

    out: list[AsOfPoint] = []
    for obs in sorted(by_obs):
        ps = sorted(by_obs[obs], key=lambda p: p.realtime_start)
        hit = next((p for p in ps if p.realtime_start <= known_at <= p.realtime_end), None)
        if hit is not None:
            out.append(AsOfPoint(obs, hit.value, exact=True))
            continue
        if (
            first_vintage is not None
            and fallback_lag_days is not None
            and known_at < first_vintage
            and ps[0].realtime_start == first_vintage
            and obs + timedelta(days=fallback_lag_days) <= known_at
        ):
            out.append(AsOfPoint(obs, ps[0].value, exact=False))
    return out


#: Tolerancia relativa para decidir que un valor cambió entre dos corridas.
#: DefiLlama recalcula sus totales sumando floats; diferencias por debajo de
#: esto son redondeo, no revisiones.
REVISION_RTOL = 1e-9


@dataclass(frozen=True)
class SnapshotDiff:
    new: int
    revised: int
    removed: int
    #: Mayor revisión relativa |nuevo/viejo − 1| entre las revisadas.
    max_revision: float


def _differs(old: float | None, new: float | None) -> bool:
    if old is None or new is None:
        return not (old is None and new is None)
    if old == new:
        return False
    scale = max(abs(old), abs(new))
    return scale == 0 or abs(new - old) / scale > REVISION_RTOL


def apply_snapshot(
    periods: Sequence[Period],
    snapshot: Iterable[tuple[date, float | None]],
    vintage: date,
) -> tuple[list[Period], SnapshotDiff]:
    """Construye vintages propias para una fuente que no las publica.

    Cada corrida es una vintage: `snapshot` es la serie completa tal como la
    fuente la sirve el día `vintage`. Contra los períodos vigentes:

    - una observación nueva abre un período desde `vintage`;
    - una que cambió de valor cierra el vigente en `vintage − 1` y abre otro;
      si el vigente ya había empezado en `vintage` (segunda corrida del mismo
      día) se reemplaza, porque la resolución de tiempo real es el día;
    - una que desapareció de la fuente se cierra en `vintage − 1`. No se
      borra: que la fuente la haya sacado es información.

    El resultado se lee con `as_of` igual que una serie de ALFRED.
    """
    cut = vintage - timedelta(days=1)
    closed: list[Period] = []
    current: dict[date, Period] = {}
    for p in periods:
        if p.realtime_end == OPEN_END:
            current[p.obs_date] = p
        else:
            closed.append(p)

    had_history = {p.obs_date for p in closed}
    out = closed
    new = revised = 0
    max_rev = 0.0
    for obs, value in snapshot:
        prev = current.pop(obs, None)
        if prev is None:
            if obs in had_history:
                revised += 1
            else:
                new += 1
            out.append(Period(obs, vintage, OPEN_END, value))
            continue
        if not _differs(prev.value, value):
            out.append(prev)
            continue
        revised += 1
        if prev.value and value is not None:
            max_rev = max(max_rev, abs(value / prev.value - 1.0))
        if prev.realtime_start < vintage:
            out.append(replace(prev, realtime_end=cut))
        out.append(Period(obs, vintage, OPEN_END, value))

    removed = 0
    for prev in current.values():
        removed += 1
        if prev.realtime_start < vintage:
            out.append(replace(prev, realtime_end=cut))
    out.sort(key=lambda p: (p.obs_date, p.realtime_start))
    return out, SnapshotDiff(new, revised, removed, max_rev)
