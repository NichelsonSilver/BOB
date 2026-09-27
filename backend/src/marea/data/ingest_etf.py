"""Ingesta de ETFs desde Tiingo: incremental, con solape y tres controles.

1. **Solape de `OVERLAP_DAYS`.** Cada corrida vuelve a pedir los últimos días
   ya guardados. Si un cierre crudo cambió, se reemplaza y **se cuenta**: un
   cierre revisado es información, no ruido que se tapa.
2. **Huecos contra el calendario del propio universo.** No hay calendario de
   NYSE en el repo, así que un día hábil es uno en que cotizó al menos la mitad
   del universo. Un ETF que falta en un día así, después de su primera barra,
   es un hueco. Se reporta, no se rellena.
3. **Control cruzado contra `adjClose`.** El retorno total calculado desde el
   crudo (`marea.returns`) se compone y se compara con la razón de `adjClose`
   de Tiingo. La primera versión de este docstring predecía una brecha "chica
   y no nula", suponiendo que Tiingo ajusta con el factor `1 − div/close_prev`.
   Era falso: medido el 2026-09-27 sobre los 23 ETFs —incluidos 289
   dividendos de TLT y 4 splits inversos de UNG— la brecha es de ~1e-10 %, o
   sea Tiingo usa la misma convención aditiva. El control es entonces una
   **identidad a precisión de máquina**: por encima de `ADJ_TOLERANCE_PCT` hay
   un dividendo o un split mal leído, y el ETF sale con error.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from marea.data import store
from marea.data.tiingo import DailyBar, TiingoClient
from marea.returns import total_returns

OVERLAP_DAYS = 14
ADJ_TOLERANCE_PCT = 1e-6
HISTORY_START = date(1990, 1, 1)


@dataclass(frozen=True)
class EtfReport:
    ticker: str
    status: str
    bars: int = 0
    first: date | None = None
    last: date | None = None
    new: int = 0
    revised: int = 0
    gaps: int = 0
    adj_gap_pct: float | None = None


def sync_ticker(
    client: TiingoClient, conn: sqlite3.Connection, ticker: str
) -> tuple[str, int, int]:
    """Baja lo nuevo con solape. Devuelve (estado, barras nuevas, cierres revisados)."""
    last = store.last_bar_day(conn, ticker)
    start = last - timedelta(days=OVERLAP_DAYS) if last else HISTORY_START
    fetched = client.daily(ticker, start)
    old = {b.day: b.close for b in store.load_bars(conn, ticker) if b.day >= start}
    revised = sum(1 for b in fetched if b.day in old and old[b.day] != b.close)
    new = sum(1 for b in fetched if b.day not in old)
    store.upsert_bars(conn, ticker, fetched)
    return ("nuevo" if last is None else "actualizado"), new, revised


def adj_close_gap_pct(bars: list[DailyBar]) -> float | None:
    """Brecha final, en %, entre el retorno total compuesto y la razón de adjClose."""
    if len(bars) < 2:
        return None
    close = np.array([b.close for b in bars])
    div = np.array([b.div_cash for b in bars])
    split = np.array([b.split_factor for b in bars])
    adj = np.array([b.adj_close for b in bars])
    ours = float(np.prod(1.0 + total_returns(close, div, split)[1:]))
    theirs = float(adj[-1] / adj[0])
    return (ours / theirs - 1.0) * 100.0


def calendar_gaps(days_by_ticker: dict[str, list[date]]) -> dict[str, int]:
    """Días hábiles del universo (cotizó ≥ la mitad) que le faltan a cada ETF."""
    counts = Counter(d for days in days_by_ticker.values() for d in days)
    quorum = len(days_by_ticker) / 2
    trading = sorted(d for d, n in counts.items() if n >= quorum)
    out: dict[str, int] = {}
    for ticker, days in days_by_ticker.items():
        have = set(days)
        first = min(days) if days else None
        out[ticker] = sum(1 for d in trading if first and d >= first and d not in have)
    return out


def print_reports(reports: list[EtfReport], requests_made: int) -> None:
    head = f"{'ETF':<6}{'estado':<13}{'barras':>7}  {'rango':<25}{'nuevas':>7}{'revis.':>7}"
    head += f"{'huecos':>7}{'vs adjClose':>13}"
    print(head)
    print("-" * len(head))
    for r in reports:
        rng = f"{r.first} .. {r.last}" if r.first else "-"
        adj = f"{r.adj_gap_pct:+.1e}%" if r.adj_gap_pct is not None else "-"
        print(
            f"{r.ticker:<6}{r.status[:12]:<13}{r.bars:>7}  {rng:<25}{r.new:>7}{r.revised:>7}"
            f"{r.gaps:>7}{adj:>13}"
        )
    errors = [r for r in reports if r.status.startswith("error")]
    print(f"\n{len(reports)} ETFs, {len(errors)} con error, {requests_made} requests a Tiingo")
    for r in errors:
        print(f"  {r.ticker}: {r.status}")
    print(
        "\n'vs adjClose' = brecha entre el retorno total compuesto desde el crudo y la razón "
        f"de adjClose de Tiingo. Es una identidad: por encima de {ADJ_TOLERANCE_PCT:g}% hay "
        "un dividendo o un split mal leído."
    )


def run(client: TiingoClient, conn: sqlite3.Connection, tickers: list[str]) -> list[EtfReport]:
    partial: dict[str, tuple[str, int, int]] = {}
    for t in tickers:
        try:
            partial[t] = sync_ticker(client, conn, t)
        except Exception as exc:  # noqa: BLE001 — un ETF roto no tira la corrida
            partial[t] = (f"error: {type(exc).__name__}: {exc}", 0, 0)

    bars = {t: store.load_bars(conn, t) for t in tickers}
    for t, bs in bars.items():
        gap = adj_close_gap_pct(bs)
        if (
            gap is not None
            and abs(gap) > ADJ_TOLERANCE_PCT
            and not partial[t][0].startswith("error")
        ):
            partial[t] = (
                f"error: retorno total no cuadra con adjClose ({gap:+.2e}%)",
                *partial[t][1:],
            )
    gaps = calendar_gaps({t: [b.day for b in bs] for t, bs in bars.items() if bs})
    return [
        EtfReport(
            ticker=t,
            status=partial[t][0],
            bars=len(bars[t]),
            first=bars[t][0].day if bars[t] else None,
            last=bars[t][-1].day if bars[t] else None,
            new=partial[t][1],
            revised=partial[t][2],
            gaps=gaps.get(t, 0),
            adj_gap_pct=adj_close_gap_pct(bars[t]),
        )
        for t in tickers
    ]
