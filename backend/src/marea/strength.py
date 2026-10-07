"""Fuerza relativa por clase de activo, para el panel de flujos. Descriptivo.

    uv run python -m marea.strength --weeks 26

**No es flujo de dinero.** El flujo neto hacia una clase (creaciones y
redenciones de ETFs) no está en ninguna fuente gratuita del proyecto. Esto mide
*a quién premió el mercado*: retorno total de cada ETF menos la mediana del
universo en la misma ventana. Tampoco es pronóstico: la M3 midió si este tipo de
orden se sostiene hacia adelante y no lo hace (IC +0,036, IC95% contiene 0).

Definiciones, todas declaradas porque cada una es una elección:

- Retorno de un ETF en la ventana: retorno total compuesto desde el cierre del
  día de inicio (`marea.returns.total_returns`).
- Referencia: **mediana** del universo en cada fecha, la misma de
  `marea.brief.relative_strength`. La mediana y no la media para que un ETF
  extremo (UNG) no mueva el cero de todos los demás.
- Clase: **promedio simple** de la fuerza relativa de sus miembros. Una clase
  con un solo ETF (divisas = UUP) es ese ETF.
- Un ETF entra a una ventana solo si tiene retorno en **todas** sus fechas. No
  se rellena: si le falta un día, queda fuera y se informa.

El último punto de cada línea coincide por construcción con la cifra de la
tabla para esa misma ventana. Hay un test que lo fija.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import numpy as np
from numpy.typing import NDArray

from marea.config import DB_PATH
from marea.data import store
from marea.data.catalog import ETFS
from marea.returns import total_returns

#: Ventanas de la tabla, en semanas de 5 sesiones.
TABLE_WEEKS: tuple[int, ...] = (4, 13, 26)
#: Ventanas que ofrece el gráfico.
CHART_WEEKS: tuple[int, ...] = (13, 26, 52)

CLASS_LABEL: dict[str, str] = {
    "acciones": "Acciones",
    "renta_fija": "Renta fija",
    "reserva_valor": "Oro y plata",
    "commodities": "Commodities",
    "real_estate": "Inmobiliario (REITs)",
    "divisas": "Dólar",
}

Panel = tuple[list[date], dict[str, NDArray[np.float64]]]


def align(series: Mapping[str, tuple[Sequence[date], NDArray[np.float64]]]) -> Panel:
    """Lleva los retornos diarios de cada ETF a un calendario común (unión).

    Una fecha que un ETF no tiene queda en NaN: antes de su lanzamiento o un
    hueco. No se rellena.
    """
    days = sorted({d for ds, _ in series.values() for d in ds})
    idx = {d: i for i, d in enumerate(days)}
    out: dict[str, NDArray[np.float64]] = {}
    for t, (ds, r) in series.items():
        col = np.full(len(days), np.nan)
        col[[idx[d] for d in ds]] = r
        out[t] = col
    return days, out


def window_paths(panel: Panel, bars: int) -> tuple[list[date], dict[str, NDArray[np.float64]]]:
    """Retorno acumulado de cada ETF en las últimas `bars` sesiones.

    Devuelve `bars + 1` fechas: la primera es el cierre de partida (0%). Los
    ETFs sin retorno en alguna fecha de la ventana quedan fuera.
    """
    days, rets = panel
    if len(days) < bars + 1:
        raise ValueError(f"hacen falta {bars + 1} fechas y hay {len(days)}")
    paths: dict[str, NDArray[np.float64]] = {}
    for t, r in rets.items():
        w = r[-bars:]
        if not np.all(np.isfinite(w)):
            continue
        paths[t] = np.expm1(np.concatenate([[0.0], np.cumsum(np.log1p(w))]))
    return days[-bars - 1 :], paths


def relative_paths(
    paths: Mapping[str, NDArray[np.float64]],
) -> tuple[NDArray[np.float64], dict[str, NDArray[np.float64]]]:
    """Mediana del universo por fecha y la distancia de cada ETF a ella."""
    mat = np.vstack(list(paths.values()))
    med = np.median(mat, axis=0)
    return med, {t: p - med for t, p in paths.items()}


def class_paths(
    rel: Mapping[str, NDArray[np.float64]], block_of: Mapping[str, str]
) -> dict[str, tuple[list[str], NDArray[np.float64]]]:
    """Promedio simple de la fuerza relativa de los miembros de cada clase."""
    members: dict[str, list[str]] = {}
    for t in rel:
        members.setdefault(block_of[t], []).append(t)
    return {b: (ts, np.mean(np.vstack([rel[t] for t in ts]), axis=0)) for b, ts in members.items()}


def _r(v: float) -> float | None:
    return None if not math.isfinite(v) else round(float(v), 6)


def build(
    panel: Panel,
    block_of: Mapping[str, str],
    chart_weeks: int,
    table_weeks: Sequence[int] = TABLE_WEEKS,
) -> dict[str, Any]:
    days = panel[0]
    universe = sorted(panel[1])

    # Tabla: la última cifra de cada ventana.
    table: dict[int, dict[str, Any]] = {}
    for w in table_weeks:
        _, paths = window_paths(panel, 5 * w)
        med, rel = relative_paths(paths)
        table[w] = {
            "median": float(med[-1]),
            "etf": {t: float(v[-1]) for t, v in rel.items()},
            "etf_abs": {t: float(p[-1]) for t, p in paths.items()},
            "cls": {b: float(v[-1]) for b, (_, v) in class_paths(rel, block_of).items()},
        }

    # Gráfico: la trayectoria entera de la ventana elegida.
    cdays, paths = window_paths(panel, 5 * chart_weeks)
    med, rel = relative_paths(paths)
    cls = class_paths(rel, block_of)
    order = sorted(cls, key=lambda b: -cls[b][1][-1])

    classes = []
    for b in order:
        ts, path = cls[b]
        row: dict[str, Any] = {
            "block": b,
            "label": CLASS_LABEL.get(b, b),
            "members": sorted(ts),
            "rel_window": _r(path[-1]),
        }
        for w in table_weeks:
            v = table[w]["cls"].get(b)
            row[f"rel_{w}s"] = None if v is None else _r(v)
        classes.append(row)

    etfs = []
    for e in ETFS:
        if e.ticker not in universe:
            continue
        row = {"ticker": e.ticker, "block": e.block, "name": e.why, "caveat": e.caveat}
        for w in table_weeks:
            v = table[w]["etf"].get(e.ticker)
            a = table[w]["etf_abs"].get(e.ticker)
            row[f"rel_{w}s"] = None if v is None else _r(v)
            row[f"ret_{w}s"] = None if a is None else _r(a)
        etfs.append(row)

    series = []
    for i, d in enumerate(cdays):
        pt: dict[str, Any] = {"day": d.isoformat(), "median": _r(med[i])}
        for b in order:
            pt[b] = _r(cls[b][1][i])
        series.append(pt)

    return {
        "evidence": "medido (descriptivo: pasado, no pronóstico; no es flujo de dinero)",
        "as_of": days[-1].isoformat(),
        "window_weeks": chart_weeks,
        "window_from": cdays[0].isoformat(),
        "table_weeks": list(table_weeks),
        "median_window": _r(med[-1]),
        "median_by_weeks": {str(w): _r(table[w]["median"]) for w in table_weeks},
        "excluded": sorted(set(universe) - set(paths)),
        "classes": classes,
        "etfs": etfs,
        "series": series,
        "method": {
            "measure": "retorno total del ETF menos la mediana del universo en la misma ventana",
            "class": "promedio simple de los ETFs de la clase",
            "reference": "mediana de los ETFs con dato completo en la ventana",
            "missing": "un ETF sin retorno en alguna fecha de la ventana queda fuera, sin rellenar",
        },
    }


def load_panel(conn: sqlite3.Connection) -> tuple[Panel, dict[str, str]]:
    series: dict[str, tuple[Sequence[date], NDArray[np.float64]]] = {}
    for e in ETFS:
        bars = store.load_bars(conn, e.ticker)
        if len(bars) < 2:
            continue
        r = total_returns(
            np.array([b.close for b in bars]),
            np.array([b.div_cash for b in bars]),
            np.array([b.split_factor for b in bars]),
        )
        series[e.ticker] = ([b.day for b in bars[1:]], r[1:])
    return align(series), {e.ticker: e.block for e in ETFS}


def from_db(conn: sqlite3.Connection, chart_weeks: int) -> dict[str, Any]:
    panel, block_of = load_panel(conn)
    return build(panel, block_of, chart_weeks)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m marea.strength", description=__doc__)
    p.add_argument("--weeks", type=int, choices=CHART_WEEKS, default=26)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    conn = store.connect(DB_PATH)
    try:
        rep = from_db(conn, args.weeks)
    finally:
        conn.close()
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    print(f"Fuerza relativa por clase · al {rep['as_of']} · {rep['evidence']}")
    print(f"{'clase':<22}{'n':>3}" + "".join(f"{w:>4} sem  " for w in rep["table_weeks"]))
    for c in rep["classes"]:
        cells = "".join(
            f"{'—':>9}  " if c[f'rel_{w}s'] is None else f"{100 * c[f'rel_{w}s']:>+8.2f}%  "
            for w in rep["table_weeks"]
        )
        print(f"{c['label']:<22}{len(c['members']):>3}{cells}")
    if rep["excluded"]:
        w = rep["window_weeks"]
        print(f"fuera de la ventana de {w} sem por datos incompletos: {rep['excluded']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
