"""Probabilidad de recesión a 12 meses por la curva (modelo de la NY Fed). PURO.

El modelo es el probit de Estrella y Trubin (2006) que la NY Fed publica cada
mes en `allmonth.xls`:

    P(recesión en m + 12) = Φ(α + β · spread_m)

con `spread_m` = promedio mensual del Tesoro a 10 años − promedio mensual de
la letra a 3 meses llevada a base de bono. Verificado contra el archivo el
2026-10-07:

- **α y β se recuperan exactos** de las 795 filas que traen spread y
  probabilidad: residuo 2e-15 en la escala del probit. Son constantes, no se
  reestiman, y por eso no hay lookahead en los parámetros **desde 2006**; antes
  de 2006 son dentro de muestra, y se declara.
- **El spread se reproduce con FRED** (`DGS10` y `DTB3`): promedios mensuales
  redondeados a 2 decimales —como los publica el H.15— y la conversión de la
  tasa de descuento a base de bono. Coinciden 750 de 760 meses; los otros 10
  difieren en 0,01 por empates de redondeo, y la probabilidad, en 0,23 pp como
  máximo. `T10Y3M` NO sirve: usa el plazo constante a 3 meses y difiere hasta
  0,13 puntos.

Nota de registro: el gauge "NY Fed RECPRO" de Outliers marcaba 0,6 % el 07-10
mientras el archivo de la NY Fed da 13,9 % para 2027-08. Ese 0,6 % es casi
seguro `RECPROUSM156N` (Chauvet-Piger: probabilidad de estar en recesión HOY),
que es otro modelo y otra pregunta.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from marea.pit import AsOfPoint

ALPHA = -0.5333
BETA = -0.6330
#: Hasta acá llegó la muestra de estimación: antes, la probabilidad es dentro
#: de muestra.
ESTIMATION_END = date(2006, 1, 1)
HORIZON_MONTHS = 12


def bond_equivalent(discount_pct: float) -> float:
    """Tasa de descuento de una letra a 91 días → rendimiento en base de bono, en %."""
    d = discount_pct / 100.0
    return 100.0 * 365.0 * d / (360.0 - 91.0 * d)


def probability(spread: float) -> float:
    """Φ(α + β · spread)."""
    z = ALPHA + BETA * spread
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def month_end(d: date) -> date:
    nxt = date(d.year + d.month // 12, d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return month_end(date(d.year + y, m + 1, 1))


def monthly_means(points: Sequence[AsOfPoint]) -> dict[date, float]:
    """Promedio por mes calendario, redondeado a 2 decimales como el H.15.

    Solo meses **cerrados**: un mes cuenta si la serie ya tiene un dato del mes
    siguiente. Así no hace falta un calendario de días hábiles y un mes a medio
    publicar no se promedia de menos.
    """
    by_month: dict[date, list[float]] = {}
    last_obs: date | None = None
    for p in points:
        if p.value is None:
            continue
        by_month.setdefault(month_end(p.obs_date), []).append(p.value)
        last_obs = p.obs_date if last_obs is None else max(last_obs, p.obs_date)
    if last_obs is None:
        return {}
    return {m: round(sum(v) / len(v), 2) for m, v in by_month.items() if m < last_obs}


@dataclass(frozen=True)
class RecessionPoint:
    month: date  # mes del spread (fin de mes)
    target: date  # mes al que se refiere la probabilidad
    spread: float
    probability: float
    #: False si algún insumo salió del rezago de respaldo (antes de la 1ª vintage)
    #: o si el mes es anterior al fin de la muestra de estimación.
    exact: bool


def recession_series(dgs10: Sequence[AsOfPoint], dtb3: Sequence[AsOfPoint]) -> list[RecessionPoint]:
    """Una probabilidad por mes cerrado en los dos insumos, tal como se conocían."""
    g10, tb3 = monthly_means(dgs10), monthly_means(dtb3)
    inexact = {month_end(p.obs_date) for p in (*dgs10, *dtb3) if not p.exact}
    out: list[RecessionPoint] = []
    for m in sorted(g10.keys() & tb3.keys()):
        s = g10[m] - bond_equivalent(tb3[m])
        out.append(
            RecessionPoint(
                month=m,
                target=add_months(m, HORIZON_MONTHS),
                spread=s,
                probability=probability(s),
                exact=m not in inexact and m >= ESTIMATION_END,
            )
        )
    return out


def main(argv: list[str] | None = None) -> int:  # pragma: no cover — I/O
    """Últimos meses de la probabilidad, tal como se conocen hoy.

    uv run python -m marea.recession
    """
    from datetime import UTC, datetime

    from bob.utils.console import enable_utf8_stdout
    from marea.config import DB_PATH
    from marea.data import store
    from marea.pit import as_of

    enable_utf8_stdout()
    today = datetime.now(UTC).date()
    conn = store.connect(DB_PATH)
    try:
        inputs = []
        for sid in ("DGS10", "DTB3"):
            meta = store.series_meta(conn, sid)
            if meta is None:
                print(f"falta {sid}: correr `marea.data.ingest fred --series {sid}`")
                return 1
            inputs.append(
                as_of(
                    store.load_periods(conn, sid),
                    today,
                    date.fromisoformat(meta["first_vintage"]),
                    meta["fallback_lag_days"],
                )
            )
    finally:
        conn.close()
    rows = recession_series(*inputs)
    peak = max((r for r in rows if r.month >= date(2022, 1, 1)), key=lambda r: r.probability)
    print(f"Probabilidad de recesión a 12 meses (probit NY Fed), conocida al {today}")
    print(f"{'mes del spread':<16}{'spread':>8}{'para':>13}{'prob':>8}")
    for r in rows[-6:]:
        print(f"{r.month!s:<16}{r.spread:>8.3f}{r.target!s:>13}{100 * r.probability:>7.1f}%")
    print(
        f"\nMáximo desde 2022: {100 * peak.probability:.1f}% con el spread de {peak.month}; "
        "la recesión no llegó. Monitoreo, no pronóstico propio."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())
