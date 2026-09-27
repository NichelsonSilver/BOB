"""Ingesta del COT: incremental con solape de `OVERLAP_WEEKS` y conteo de revisiones.

La CFTC corrige reportes ya publicados de vez en cuando. Cada corrida vuelve a
pedir las últimas semanas y cuenta las filas que cambiaron: una revisión es
información y queda en el reporte, no se pisa en silencio.

Huecos: se cuentan las **semanas ISO** sin reporte entre la primera y la
última fila. No los martes: en semanas con feriado la CFTC fecha el reporte en
lunes o miércoles (14 casos en 2006-2026: Navidad, Año Nuevo, 4 de julio,
Veterans Day), y la primera versión de esta métrica los contaba como huecos
cuando no falta ninguna semana.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from marea.data import store
from marea.data.catalog import CotMarket
from marea.data.cftc import CftcClient

OVERLAP_WEEKS = 4
HISTORY_START = date(2006, 1, 1)


@dataclass(frozen=True)
class CotReport:
    code: str
    name: str
    status: str
    weeks: int = 0
    first: date | None = None
    last: date | None = None
    new: int = 0
    revised: int = 0
    gaps: int = 0


def missing_weeks(dates: list[date]) -> int:
    if len(dates) < 2:
        return 0
    have = {d.isocalendar()[:2] for d in dates}
    monday = dates[0] - timedelta(days=dates[0].weekday())
    n = 0
    while monday <= dates[-1]:
        n += monday.isocalendar()[:2] not in have
        monday += timedelta(days=7)
    return n


def sync_market(client: CftcClient, conn: sqlite3.Connection, m: CotMarket) -> tuple[str, int, int]:
    last = store.last_cot_date(conn, m.code)
    since = last - timedelta(weeks=OVERLAP_WEEKS) if last else HISTORY_START
    fetched = client.positions(m.report, m.code, since)
    old = {r.report_date: r for r in store.load_cot(conn, m.code) if r.report_date >= since}
    revised = sum(1 for r in fetched if r.report_date in old and old[r.report_date] != r)
    new = sum(1 for r in fetched if r.report_date not in old)
    store.upsert_cot(conn, fetched)
    return ("nuevo" if last is None else "actualizado"), new, revised


def run(client: CftcClient, conn: sqlite3.Connection, markets: list[CotMarket]) -> list[CotReport]:
    out: list[CotReport] = []
    for m in markets:
        try:
            status, new, revised = sync_market(client, conn, m)
        except Exception as exc:  # noqa: BLE001 — un mercado roto no tira la corrida
            status, new, revised = f"error: {type(exc).__name__}: {exc}", 0, 0
        rows = store.load_cot(conn, m.code)
        dates = [r.report_date for r in rows]
        out.append(
            CotReport(
                code=m.code,
                name=m.name,
                status=status,
                weeks=len(rows),
                first=dates[0] if dates else None,
                last=dates[-1] if dates else None,
                new=new,
                revised=revised,
                gaps=missing_weeks(dates),
            )
        )
    return out


def print_reports(reports: list[CotReport], requests_made: int) -> None:
    head = f"{'código':<8}{'mercado':<20}{'estado':<13}{'semanas':>8}  {'rango':<25}"
    head += f"{'nuevas':>7}{'revis.':>7}{'huecos':>7}"
    print(head)
    print("-" * len(head))
    for r in reports:
        rng = f"{r.first} .. {r.last}" if r.first else "-"
        print(
            f"{r.code:<8}{r.name[:19]:<20}{r.status[:12]:<13}{r.weeks:>8}  {rng:<25}"
            f"{r.new:>7}{r.revised:>7}{r.gaps:>7}"
        )
    errors = [r for r in reports if r.status.startswith("error")]
    print(f"\n{len(reports)} mercados, {len(errors)} con error, {requests_made} requests a la CFTC")
    for r in errors:
        print(f"  {r.code} {r.name}: {r.status}")
    print(
        "\nCada fila se considera conocida el lunes siguiente a su martes (available_date): "
        "la CFTC publica el viernes y los feriados la corren al lunes."
    )
