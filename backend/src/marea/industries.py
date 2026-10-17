"""Crecimiento del mercado por industria (M7). Descriptivo.

    uv run python -m marea.industries --weeks 26

Dos mercados, cada uno medido con lo que da gratis su fuente:

1. **Bolsa de EE.UU., por sector GICS.** Los 11 Select Sector SPDR (Tiingo).
   *Crecimiento* = retorno total compuesto del ETF en la ventana. Como cada
   uno replica un índice ponderado por capitalización, aproxima cuánto creció
   el valor de mercado del sector; **no** cuenta emisiones ni recompras de
   acciones. *Contra el mercado* = el mismo retorno menos el de SPY. Los once
   particionan el S&P 500, así que la referencia es SPY y no una mediana como
   en `marea.strength`.
2. **DeFi, por categoría.** TVL de DefiLlama sumado por categoría
   (`marea.defi_categories`): crecimiento a 1, 7 y 30 días con los mismos
   protocolos, y la serie que se acumula con una foto por día.

**Ninguna de las dos es flujo de dinero**: un precio que sube agranda el
sector o el TVL sin que entre un dólar. Y **ninguna es pronóstico**: la M3
midió que el orden de la fuerza relativa no se sostiene hacia adelante.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections.abc import Sequence
from datetime import date
from typing import Any

import numpy as np
from numpy.typing import NDArray

from marea.config import DB_PATH
from marea.data import store
from marea.data.catalog import SECTOR_ETFS
from marea.defi_categories import WINDOWS, CategoryTvl
from marea.returns import total_returns
from marea.strength import Panel, align, window_paths

#: Ventanas de la tabla de sectores, en semanas de 5 sesiones.
TABLE_WEEKS: tuple[int, ...] = (4, 13, 26, 52)
#: Ventanas que ofrece el gráfico.
CHART_WEEKS: tuple[int, ...] = (13, 26, 52)
BENCHMARK = "SPY"

SECTOR_LABEL: dict[str, str] = {
    "tecnologia": "Tecnología",
    "financiero": "Financiero",
    "salud": "Salud",
    "consumo_discrecional": "Consumo discrecional",
    "comunicaciones": "Comunicaciones",
    "industrial": "Industrial",
    "consumo_basico": "Consumo básico",
    "energia": "Energía",
    "utilities": "Utilities",
    "inmobiliario": "Inmobiliario",
    "materiales": "Materiales",
}

#: Categorías DeFi con nombre propio en la tabla; el resto va a "Otras".
TOP_CATEGORIES = 12
OTHER = "Otras"


def _r(v: float | None) -> float | None:
    return None if v is None or not math.isfinite(v) else round(float(v), 6)


# --- Bolsa por sector ----------------------------------------------------------


def build_equity(
    panel: Panel, chart_weeks: int, table_weeks: Sequence[int] = TABLE_WEEKS
) -> dict[str, Any]:
    days = panel[0]
    if BENCHMARK not in panel[1]:
        raise ValueError(f"falta {BENCHMARK}, la referencia de los sectores")

    table: dict[int, dict[str, float]] = {}
    for w in table_weeks:
        if len(days) < 5 * w + 1:
            continue
        _, paths = window_paths(panel, 5 * w)
        table[w] = {t: float(p[-1]) for t, p in paths.items()}

    cdays, paths = window_paths(panel, 5 * chart_weeks)
    if BENCHMARK not in paths:
        raise ValueError(f"{BENCHMARK} no tiene datos completos en la ventana")
    bench = paths[BENCHMARK]
    order = sorted(
        (s for s in SECTOR_ETFS if s.ticker in paths), key=lambda s: -paths[s.ticker][-1]
    )

    sectors = []
    for s in order:
        row: dict[str, Any] = {
            "ticker": s.ticker,
            "sector": s.sector,
            "label": SECTOR_LABEL[s.sector],
            "caveat": s.caveat,
            "ret_window": _r(paths[s.ticker][-1]),
            "rel_window": _r(paths[s.ticker][-1] - bench[-1]),
        }
        for w in table_weeks:
            t = table.get(w, {})
            ret, ref = t.get(s.ticker), t.get(BENCHMARK)
            row[f"ret_{w}s"] = _r(ret)
            row[f"rel_{w}s"] = None if ret is None or ref is None else _r(ret - ref)
        sectors.append(row)

    series = []
    for i, d in enumerate(cdays):
        pt: dict[str, Any] = {"day": d.isoformat(), BENCHMARK: _r(bench[i])}
        for s in order:
            pt[s.ticker] = _r(paths[s.ticker][i])
        series.append(pt)

    universe = {s.ticker for s in SECTOR_ETFS} & set(panel[1])
    return {
        "as_of": days[-1].isoformat(),
        "window_weeks": chart_weeks,
        "window_from": cdays[0].isoformat(),
        "table_weeks": list(table_weeks),
        "benchmark": BENCHMARK,
        "benchmark_window": _r(bench[-1]),
        "benchmark_by_weeks": {
            str(w): _r(table[w].get(BENCHMARK)) if w in table else None for w in table_weeks
        },
        "excluded": sorted(universe - set(paths)),
        "missing": sorted({s.ticker for s in SECTOR_ETFS} - universe),
        "sectors": sectors,
        "series": series,
    }


def load_equity_panel(conn: sqlite3.Connection) -> Panel:
    series: dict[str, tuple[Sequence[date], NDArray[np.float64]]] = {}
    for t in [BENCHMARK, *(s.ticker for s in SECTOR_ETFS)]:
        bars = store.load_bars(conn, t)
        if len(bars) < 2:
            continue
        r = total_returns(
            np.array([b.close for b in bars]),
            np.array([b.div_cash for b in bars]),
            np.array([b.split_factor for b in bars]),
        )
        series[t] = ([b.day for b in bars[1:]], r[1:])
    return align(series)


# --- DeFi por categoría --------------------------------------------------------


def _merge(name: str, rows: Sequence[CategoryTvl]) -> CategoryTvl:
    def tot(f: str) -> float:
        return float(sum(getattr(r, f) for r in rows))

    return CategoryTvl(
        name,
        tot("tvl"),
        int(sum(r.protocols for r in rows)),
        tot("now_day"),
        tot("prev_day"),
        tot("now_week"),
        tot("prev_week"),
        tot("now_month"),
        tot("prev_month"),
        tot("tvl_unmatched_month"),
    )


def build_defi(
    snapshots: dict[date, tuple[str, list[CategoryTvl]]], top: int = TOP_CATEGORIES
) -> dict[str, Any] | None:
    if not snapshots:
        return None
    last_day = max(snapshots)
    captured_at, cats = snapshots[last_day]
    named = cats[:top]
    rest = cats[top:]
    shown = [*named, _merge(OTHER, rest)] if rest else list(named)
    total = sum(c.tvl for c in cats)

    rows = []
    for c in shown:
        row: dict[str, Any] = {
            "category": c.category,
            "other": c.category == OTHER and bool(rest),
            "members": len(rest) if c.category == OTHER and rest else None,
            "tvl": round(c.tvl, 2),
            "share": _r(c.tvl / total if total else None),
            "protocols": c.protocols,
            "unmatched_month": round(c.tvl_unmatched_month, 2),
        }
        for suffix, _, n in WINDOWS:
            row[f"growth_{n}d"] = _r(c.growth(suffix))
        rows.append(row)

    names = [c.category for c in named]
    history = []
    for d in sorted(snapshots):
        by = {c.category: c for c in snapshots[d][1]}
        pt: dict[str, Any] = {"day": d.isoformat()}
        for name in names:
            pt[name] = round(by[name].tvl, 2) if name in by else None
        history.append(pt)

    return {
        "as_of": last_day.isoformat(),
        "captured_at": captured_at,
        "snapshots": len(snapshots),
        "first_snapshot": min(snapshots).isoformat(),
        "categories_total": len(cats),
        "tvl_sum": round(total, 2),
        "categories": rows,
        "history": history,
    }


# --- Reporte -------------------------------------------------------------------

METHOD: dict[str, str] = {
    "equity_growth": (
        "retorno total del ETF sectorial en la ventana: aproxima el crecimiento del valor de "
        "mercado del sector, sin contar emisiones ni recompras"
    ),
    "equity_relative": "retorno del sector menos el de SPY (S&P 500) en la misma ventana",
    "defi_tvl": "TVL de DefiLlama sumado por categoría; fuera los CEX y lo que no tiene categoría",
    "defi_growth": (
        "TVL de hoy contra el de hace 1, 7 o 30 días, solo en los protocolos con los dos valores"
    ),
    "defi_double_count": (
        "la suma de categorías tiene doble conteo: un token de liquid staking depositado en "
        "lending cuenta en las dos"
    ),
    "not_flow": (
        "no es flujo de dinero: un precio que sube agranda el sector o el TVL sin que entre un "
        "dólar"
    ),
}


def from_db(conn: sqlite3.Connection, chart_weeks: int) -> dict[str, Any]:
    equity: dict[str, Any] | None
    equity_error: str | None = None
    try:
        equity = build_equity(load_equity_panel(conn), chart_weeks)
    except ValueError as exc:
        equity, equity_error = None, str(exc)
    return {
        "evidence": "medido (descriptivo: pasado, no pronóstico; no es flujo de dinero)",
        "equity": equity,
        "equity_error": equity_error,
        "defi": build_defi(store.load_defi_categories(conn)),
        "method": dict(METHOD),
    }


def _pct(v: float | None) -> str:
    return f"{'—':>8}" if v is None else f"{100 * v:>+7.1f}%"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m marea.industries", description=__doc__)
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

    eq = rep["equity"]
    if eq is None:
        print(f"Sectores: sin datos ({rep['equity_error']}). Correr `ingest etf`.")
    else:
        print(f"Sectores de EE.UU. · al {eq['as_of']} · {rep['evidence']}")
        weeks = eq["table_weeks"]
        print(f"{'sector':<24}{'ETF':<6}" + "".join(f"{w:>5} sem " for w in weeks) + "  vs SPY")
        for s in eq["sectors"]:
            cells = "".join(f"{_pct(s[f'ret_{w}s'])} " for w in weeks)
            print(f"{s['label']:<24}{s['ticker']:<6}{cells}  {_pct(s['rel_window'])}")
        bench = "".join(f"{_pct(eq['benchmark_by_weeks'][str(w)])} " for w in weeks)
        print(f"{'S&P 500':<24}{'SPY':<6}{bench}")

    df = rep["defi"]
    print()
    if df is None:
        print("DeFi: sin fotos. Correr `uv run python -m marea.data.ingest industrias`.")
        return 0
    print(
        f"DeFi por categoría · foto {df['captured_at']} · {df['snapshots']} fotos desde "
        f"{df['first_snapshot']}"
    )
    print(f"{'categoría':<28}{'TVL (mil M)':>12}{'peso':>8}{'1 d':>9}{'7 d':>9}{'30 d':>9}")
    for c in df["categories"]:
        print(
            f"{c['category'][:27]:<28}{c['tvl'] / 1e9:>12,.1f}{100 * (c['share'] or 0):>7.1f}%"
            f"{_pct(c['growth_1d'])} {_pct(c['growth_7d'])} {_pct(c['growth_30d'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
