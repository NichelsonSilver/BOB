"""Amplitud de la sección cripto y Altcoin Season. PURO + carga de solo lectura.

Sale del panel diario de la Fase 10 (`bob.db`, universo congelado con los
deslistados). Dos medidas, las dos point-in-time por construcción:

- **% sobre la media**: de los símbolos elegibles hoy, qué fracción cierra por
  encima de su media de 200 días y de 20 semanas (140 días). Un símbolo entra
  a la cuenta solo si tiene la ventana completa, sin huecos.
- **Altcoin Season**: de las alts más grandes, qué fracción le ganó a BTC en
  los últimos 90 días. La definición pública (blockchaincenter) usa el top 50
  por capitalización; acá no hay capitalización histórica gratis, así que el
  top 50 es por **volumen mediano en USD de los 30 días previos** — proxy
  declarado. Por encima de 0,75 se lee "temporada de alts"; por debajo de
  0,25, "temporada de BTC".

Elegibilidad: la misma regla pre-registrada del panel
(`bob.signals.universe.eligibility_mask`), importada sin cambios (regla 12).
Solo se lee `bob.db`, en modo de solo lectura.

Monitoreo, sin pronóstico.

    uv run python -m marea.breadth
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np

from bob.signals.universe import eligibility_mask, trailing_dollar_volume

#: No son monedas con precio propio: índices de Binance, una stablecoin y oro
#: tokenizado. Verificado sobre `universe-1d.json` el 2026-10-07.
EXCLUDED: frozenset[str] = frozenset(
    {
        "BTCDOMUSDT",
        "DEFIUSDT",
        "BLUEBIRDUSDT",
        "FOOTBALLUSDT",
        "USDCUSDT",
        "PAXGUSDT",
        "XAUTUSDT",
    }
)
BTC = "BTCUSDT"
SMA_WINDOWS = (200, 140)
ALTSEASON_DAYS = 90
ALTSEASON_TOP = 50
ALTSEASON_HIGH = 0.75
ALTSEASON_LOW = 0.25


@dataclass(frozen=True)
class Panel:
    symbols: tuple[str, ...]
    days: tuple[date, ...]
    close: np.ndarray  # (S, T)
    quote_volume: np.ndarray  # (S, T)


def _rolling_full_mean(close: np.ndarray, window: int) -> np.ndarray:
    """Media de las `window` fechas que terminan en j inclusive; NaN si falta alguna."""
    s, t = close.shape
    out = np.full((s, t), np.nan)
    if t < window:
        return out
    finite = np.isfinite(close)
    vals = np.where(finite, close, 0.0)
    csum = np.concatenate([np.zeros((s, 1)), np.cumsum(vals, axis=1)], axis=1)
    ccount = np.concatenate([np.zeros((s, 1)), np.cumsum(finite, axis=1)], axis=1)
    total = csum[:, window:] - csum[:, :-window]
    count = ccount[:, window:] - ccount[:, :-window]
    with np.errstate(invalid="ignore", divide="ignore"):
        out[:, window - 1 :] = np.where(count == window, total / window, np.nan)
    return out


@dataclass(frozen=True)
class BreadthRow:
    day: date
    #: Por ventana: (fracción sobre la media, símbolos contados).
    above: dict[int, tuple[float, int]]
    altseason: float | None
    altseason_n: int


def breadth(panel: Panel, eligible: np.ndarray) -> list[BreadthRow]:
    """Una fila por fecha. `eligible` es la máscara `(S, T)` del universo."""
    close = panel.close
    smas = {w: _rolling_full_mean(close, w) for w in SMA_WINDOWS}
    liquidity = trailing_dollar_volume(panel.quote_volume, 30, observed=np.isfinite(close))
    btc = panel.symbols.index(BTC) if BTC in panel.symbols else None
    is_alt = np.array([s != BTC for s in panel.symbols])

    rows: list[BreadthRow] = []
    for j, day in enumerate(panel.days):
        above: dict[int, tuple[float, int]] = {}
        for w, sma in smas.items():
            ok = eligible[:, j] & np.isfinite(sma[:, j])
            n = int(ok.sum())
            frac = float((close[ok, j] > sma[ok, j]).mean()) if n else float("nan")
            above[w] = (frac, n)

        season: float | None = None
        n_alt = 0
        k = j - ALTSEASON_DAYS
        if btc is not None and k >= 0 and np.isfinite(close[btc, j]) and np.isfinite(close[btc, k]):
            btc_ret = close[btc, j] / close[btc, k] - 1.0
            cand = eligible[:, j] & is_alt & np.isfinite(close[:, k]) & np.isfinite(liquidity[:, j])
            idx = np.flatnonzero(cand)
            if len(idx) >= ALTSEASON_TOP:
                top = idx[np.argsort(-liquidity[idx, j], kind="stable")[:ALTSEASON_TOP]]
                rets = close[top, j] / close[top, k] - 1.0
                season = float((rets > btc_ret).mean())
                n_alt = len(top)
        rows.append(BreadthRow(day, above, season, n_alt))
    return rows


def season_label(value: float | None) -> str:
    if value is None:
        return "sin dato"
    if value >= ALTSEASON_HIGH:
        return "temporada de alts"
    if value <= ALTSEASON_LOW:
        return "temporada de BTC"
    return "neutral"


def load_panel(bob_db: Path, universe_json: Path) -> Panel:  # pragma: no cover — I/O
    """Lee velas diarias de `bob.db` en solo lectura, para el universo congelado."""
    import json
    import sqlite3

    meta = json.loads(universe_json.read_text(encoding="utf-8"))
    wanted = sorted(
        m["symbol"]
        for m in meta["symbols"]
        if m["contract_type"] == "PERPETUAL" and m["symbol"] not in EXCLUDED
    )
    conn = sqlite3.connect(f"file:{bob_db}?mode=ro", uri=True)
    try:
        marks = ",".join("?" * len(wanted))
        rows = conn.execute(
            f"SELECT symbol, open_time, close, quote_volume FROM candlerecord "
            f"WHERE timeframe = '1d' AND close_time < ? AND symbol IN ({marks})",
            # Solo velas cerradas: una ingesta a mitad de día trae la vela en curso.
            [int(datetime.now(UTC).timestamp() * 1000), *wanted],
        ).fetchall()
    finally:
        conn.close()
    present = sorted({r[0] for r in rows})
    times = sorted({int(r[1]) for r in rows})
    si = {s: i for i, s in enumerate(present)}
    ti = {t: j for j, t in enumerate(times)}
    close = np.full((len(present), len(times)), np.nan)
    qv = np.full_like(close, np.nan)
    for sym, t, c, q in rows:
        close[si[sym], ti[int(t)]] = float(c)
        qv[si[sym], ti[int(t)]] = float(q)
    days = tuple(datetime.fromtimestamp(t / 1000, UTC).date() for t in times)
    return Panel(tuple(present), days, close, qv)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover — I/O
    from bob.utils.console import enable_utf8_stdout

    enable_utf8_stdout()
    backend = Path(__file__).resolve().parents[2]
    panel = load_panel(backend / "bob.db", backend / "artifacts" / "universe-1d.json")
    rows = breadth(panel, eligibility_mask(panel.close, panel.quote_volume))
    print(f"Amplitud cripto ({len(panel.symbols)} perpetuos, universo con deslistados)")
    print(f"{'fecha':<12}{'>SMA200':>9}{'n':>5}{'>SMA20s':>9}{'n':>5}{'altseason':>11}  lectura")
    for r in rows[-1:] + [
        x
        for x in rows
        if x.day in (date(2021, 5, 1), date(2021, 11, 10), date(2022, 11, 21), date(2024, 12, 5))
    ]:
        a200, n200 = r.above[200]
        a140, n140 = r.above[140]
        alt = f"{r.altseason:.2f}" if r.altseason is not None else "—"
        label = season_label(r.altseason)
        print(f"{r.day!s:<12}{a200:>9.1%}{n200:>5}{a140:>9.1%}{n140:>5}{alt:>11}  {label}")
    print("\nMonitoreo: describe la sección, no pronostica.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
