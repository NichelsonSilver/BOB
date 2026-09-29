"""Ingesta del BCCh: incremental con solape y conteo de revisiones.

El BCCh no publica vintages, así que no hay point-in-time exacto como en FRED:
cada fila lleva `available_date = día + lag_days`, con el rezago DECLARADO en el
catálogo y su razón al lado. Para vigilar lo único observable —que un valor ya
publicado cambie— cada corrida vuelve a pedir `OVERLAP_DAYS` y cuenta los
cambios.

Los días "ND" hábiles (lunes a viernes) se reportan: incluyen feriados chilenos,
así que no son todos huecos, pero un número que crece de una corrida a otra sí
lo es.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from marea.data import store
from marea.data.bcch import BcchClient
from marea.data.catalog import BcchSeries

OVERLAP_DAYS = 14


@dataclass(frozen=True)
class BcchReport:
    series_id: str
    name: str
    status: str
    obs: int = 0
    first: date | None = None
    last: date | None = None
    new: int = 0
    revised: int = 0
    weekday_nd: int = 0


def sync_series(
    client: BcchClient, conn: sqlite3.Connection, s: BcchSeries, today: date
) -> tuple[str, int, int, int]:
    existing = store.load_bcch(conn, s.id)
    first = (
        existing[-1][0] - timedelta(days=OVERLAP_DAYS) if existing else date.fromisoformat(s.start)
    )
    # Hasta mañana: el dólar observado y el cobre del día hábil siguiente ya están publicados.
    data = client.series(s.id, first, today + timedelta(days=7))
    old = {d: v for d, _a, v in existing if d >= first}
    revised = sum(1 for o in data.obs if o.day in old and old[o.day] != o.value)
    new = sum(1 for o in data.obs if o.day not in old)
    store.upsert_bcch(
        conn, s.id, [(o.day, o.day + timedelta(days=s.lag_days), o.value) for o in data.obs]
    )
    weekday_nd = sum(1 for d in data.no_data_days if d.weekday() < 5 and d <= today)
    return ("nuevo" if not existing else "actualizado"), new, revised, weekday_nd


def run(
    client: BcchClient, conn: sqlite3.Connection, series: list[BcchSeries], today: date
) -> list[BcchReport]:
    out: list[BcchReport] = []
    for s in series:
        try:
            status, new, revised, nd = sync_series(client, conn, s, today)
        except Exception as exc:  # noqa: BLE001 — una serie rota no tira la corrida
            status, new, revised, nd = f"error: {type(exc).__name__}: {exc}", 0, 0, 0
        rows = store.load_bcch(conn, s.id)
        out.append(
            BcchReport(
                series_id=s.id,
                name=s.name,
                status=status,
                obs=len(rows),
                first=rows[0][0] if rows else None,
                last=rows[-1][0] if rows else None,
                new=new,
                revised=revised,
                weekday_nd=nd,
            )
        )
    return out


def print_reports(reports: list[BcchReport], requests_made: int) -> None:
    head = f"{'serie':<28}{'estado':<13}{'obs':>7}  {'rango':<25}{'nuevas':>7}{'revis.':>7}"
    head += f"{'ND háb.':>8}"
    print(head)
    print("-" * len(head))
    for r in reports:
        rng = f"{r.first} .. {r.last}" if r.first else "-"
        print(
            f"{r.name[:27]:<28}{r.status[:12]:<13}{r.obs:>7}  {rng:<25}{r.new:>7}"
            f"{r.revised:>7}{r.weekday_nd:>8}"
        )
    errors = [r for r in reports if r.status.startswith("error")]
    print(f"\n{len(reports)} series, {len(errors)} con error, {requests_made} requests al BCCh")
    for r in errors:
        print(f"  {r.series_id}: {r.status}")
    print(
        "\n'ND háb.' = días lunes-viernes sin dato en lo pedido; incluye feriados chilenos. "
        "El rezago de publicación de cada serie está declarado en marea.data.catalog."
    )
