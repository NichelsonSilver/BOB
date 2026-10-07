"""Ingesta de snapshots de opciones de Deribit (BTC y ETH).

Una foto por moneda y por corrida. No es incremental como las otras fuentes:
**cada corrida agrega una fila nueva**, porque el dato es el estado del
mercado en ese instante. Dos corridas el mismo día dan dos fotos, y las dos
valen.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from marea.data import store
from marea.data.deribit import CURRENCIES, DeribitClient
from marea.options import OptionsSnapshot, summarize


@dataclass(frozen=True)
class OptionsReport:
    currency: str
    status: str
    snapshot: OptionsSnapshot | None = None
    raw_kb: float = 0.0
    total_snapshots: int = 0


def _utcnow() -> datetime:
    return datetime.now(UTC)


def run(
    client: DeribitClient,
    conn: sqlite3.Connection,
    currencies: tuple[str, ...] = CURRENCIES,
    now: Callable[[], datetime] = _utcnow,
) -> list[OptionsReport]:
    out: list[OptionsReport] = []
    for cur in currencies:
        try:
            rows, raw = client.options_summary(cur)
            snap = summarize(cur, rows, now().replace(microsecond=0))
            store.save_options(conn, snap, raw)
            total = len(store.load_options(conn, cur))
            out.append(OptionsReport(cur, "ok", snap, len(raw) / 1024, total))
        except Exception as exc:  # noqa: BLE001 — una moneda rota no tira la corrida
            out.append(OptionsReport(cur, f"error: {type(exc).__name__}: {exc}"))
    return out


def print_reports(reports: list[OptionsReport], requests_made: int) -> None:
    head = f"{'moneda':<7}{'estado':<8}{'instr.':>7}{'P/C OI':>8}{'P/C vol':>9}"
    head += f"{'P/C 30d':>9}{'OI USD':>12}{'fotos':>7}"
    print(head)
    print("-" * len(head))
    for r in reports:
        s = r.snapshot
        if s is None:
            print(f"{r.currency:<7}{r.status[:80]}")
            continue
        print(
            f"{r.currency:<7}{r.status:<8}{s.instruments:>7}{s.pc_oi:>8.3f}{s.pc_volume:>9.3f}"
            f"{s.pc_oi_near:>9.3f}{s.oi_usd / 1e9:>10.2f} B{r.total_snapshots:>7}"
        )
    errors = [r for r in reports if r.status.startswith("error")]
    print(f"\n{len(reports)} monedas, {len(errors)} con error, {requests_made} requests a Deribit")
    print("Cada corrida agrega una foto: esta fuente no tiene historia recuperable.")
