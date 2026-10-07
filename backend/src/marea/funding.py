"""Funding diario de BTC y ETH y su spread. PURO + CLI.

El funding mide cuánto pagan los largos a los cortos (positivo) o al revés.
El spread BTC − ETH dice dónde está más cargado el apalancamiento largo: si
ETH paga mucho más que BTC, la especulación se fue a ETH.

- **Diario = suma de las tasas del día UTC**, no tasa × 3: el intervalo de
  liquidación no es fijo entre símbolos ni en el tiempo.
- **Anualizado** = diario × 365, en %. Es la convención de la industria;
  no se compone.
- Un día cuenta solo si está **cerrado**: ya hay una liquidación posterior a
  su fin. Así una corrida a mitad de día no publica un día a medio sumar.

Monitoreo, sin pronóstico.

    uv run python -m marea.funding
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from marea.data.binance_funding import FundingPoint

DAY_MS = 86_400_000


def daily_funding(points: Sequence[FundingPoint]) -> dict[date, float]:
    """Suma de las tasas por día UTC, solo de días cerrados."""
    if not points:
        return {}
    last = max(p.funding_time for p in points)
    out: dict[date, float] = {}
    for p in points:
        day = datetime.fromtimestamp(p.funding_time / 1000, UTC).date()
        out[day] = out.get(day, 0.0) + p.rate
    end_of = {
        d: int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000) + DAY_MS
        for d in out
    }
    return {d: v for d, v in sorted(out.items()) if last >= end_of[d]}


def annualized_pct(daily: float) -> float:
    return daily * 365.0 * 100.0


def spread(a: dict[date, float], b: dict[date, float]) -> list[tuple[date, float]]:
    """a − b en los días que tienen los dos."""
    return [(d, a[d] - b[d]) for d in sorted(a.keys() & b.keys())]


def trailing_mean(series: Sequence[tuple[date, float]], window: int) -> list[tuple[date, float]]:
    """Media de `window` días calendario consecutivos; un hueco corta la ventana."""
    if window < 1:
        raise ValueError("window debe ser >= 1")
    values = dict(series)
    out: list[tuple[date, float]] = []
    for d in sorted(values):
        span = [values.get(d - timedelta(days=k)) for k in range(window)]
        if all(x is not None for x in span):
            out.append((d, sum(x for x in span if x is not None) / window))
    return out


def main(argv: list[str] | None = None) -> int:  # pragma: no cover — I/O
    from bob.utils.console import enable_utf8_stdout
    from marea.config import DB_PATH
    from marea.data import store

    enable_utf8_stdout()
    conn = store.connect(DB_PATH)
    try:
        btc = daily_funding(store.load_funding(conn, "BTCUSDT"))
        eth = daily_funding(store.load_funding(conn, "ETHUSDT"))
    finally:
        conn.close()
    if not btc or not eth:
        print("sin funding: correr `marea.data.ingest funding`")
        return 1
    sp = spread(eth, btc)
    m7 = dict(trailing_mean(sp, 7))
    b7, e7 = dict(trailing_mean(list(btc.items()), 7)), dict(trailing_mean(list(eth.items()), 7))
    print("Funding anualizado (%), media de 7 días. Spread = ETH − BTC.")
    print(f"{'día':<12}{'BTC':>8}{'ETH':>8}{'ETH−BTC':>10}")
    for d in sorted(m7)[-5:]:
        print(
            f"{d!s:<12}{annualized_pct(b7[d]):>8.2f}{annualized_pct(e7[d]):>8.2f}"
            f"{annualized_pct(m7[d]):>10.2f}"
        )
    vals = sorted(annualized_pct(v) for v in m7.values())
    last = annualized_pct(m7[max(m7)])
    rank = sum(v < last for v in vals) / len(vals)
    print(f"\nEl spread de hoy está por encima del {100 * rank:.0f}% de los días desde {min(m7)}.")
    print("Monitoreo: dice dónde está cargado el apalancamiento, no hacia dónde va el precio.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
