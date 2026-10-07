"""Ingesta incremental del funding de Binance (BTC y ETH).

Pide solo lo posterior a la última liquidación guardada. El funding no se
revisa una vez publicado, así que no hace falta solape.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from marea.data import store
from marea.data.binance_funding import FundingClient

SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")


@dataclass(frozen=True)
class FundingReport:
    symbol: str
    status: str
    points: int = 0
    new: int = 0
    first: datetime | None = None
    last: datetime | None = None


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


def run(
    client: FundingClient, conn: sqlite3.Connection, symbols: tuple[str, ...] = SYMBOLS
) -> list[FundingReport]:
    out: list[FundingReport] = []
    for sym in symbols:
        try:
            last = store.last_funding_time(conn, sym)
            new = store.upsert_funding(conn, client.history(sym, last))
            pts = store.load_funding(conn, sym)
            out.append(
                FundingReport(
                    sym,
                    "nuevo" if last is None else "actualizado",
                    len(pts),
                    new,
                    _dt(pts[0].funding_time) if pts else None,
                    _dt(pts[-1].funding_time) if pts else None,
                )
            )
        except Exception as exc:  # noqa: BLE001 — un símbolo roto no tira la corrida
            out.append(FundingReport(sym, f"error: {type(exc).__name__}: {exc}"))
    return out


def print_reports(reports: list[FundingReport], requests_made: int) -> None:
    print(f"{'símbolo':<10}{'estado':<13}{'puntos':>8}{'nuevos':>8}  rango (UTC)")
    for r in reports:
        rng = f"{r.first:%Y-%m-%d %H:%M} .. {r.last:%Y-%m-%d %H:%M}" if r.first and r.last else "-"
        print(f"{r.symbol:<10}{r.status[:12]:<13}{r.points:>8}{r.new:>8}  {rng}")
    errors = [r for r in reports if r.status.startswith("error")]
    print(f"\n{len(reports)} símbolos, {len(errors)} con error, {requests_made} requests a Binance")
    for r in errors:
        print(f"  {r.symbol}: {r.status}")
