"""Ingesta de stablecoins desde DefiLlama, con vintages construidas acá.

DefiLlama no publica historial de revisiones y reescribe su pasado (al sumar
una stablecoin rellena su historia). Si se guardara solo la última versión, una
fecha pasada se leería con datos que en esa fecha no existían: el mismo
lookahead que `marea.pit` evita en FRED. Por eso cada corrida baja la serie
completa y la compara con lo guardado (`marea.pit.apply_snapshot`):

- lo que no cambió no se toca;
- lo que cambió cierra su período y abre otro, y la corrida queda registrada
  en `stable_run` con cuántas filas revisó y cuánto;
- lo anterior a la primera corrida no tiene fecha de publicación conocida y
  se lee como **no exacto**, con `FALLBACK_LAG_DAYS` declarado (no medido):
  la fila del día D se cierra al terminar D.

Cuesta 8 requests por corrida (una para los dos totales y una por token).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from marea.data import store
from marea.data.catalog import StableSeries
from marea.data.defillama import DefiLlamaClient
from marea.pit import OPEN_END, apply_snapshot
from marea.stablecoins import FALLBACK_LAG_DAYS


@dataclass(frozen=True)
class StableReport:
    series_id: str
    status: str
    observations: int = 0
    first_obs: date | None = None
    last_obs: date | None = None
    new: int = 0
    revised: int = 0
    removed: int = 0
    max_revision: float = 0.0
    runs: int = 0
    first_vintage: date | None = None


def sync_series(
    conn: sqlite3.Connection,
    series_id: str,
    snapshot: list[tuple[date, float]],
    vintage: date,
) -> StableReport:
    existing = store.load_stable_periods(conn, series_id)
    periods, diff = apply_snapshot(existing, snapshot, vintage)
    store.save_stable_run(
        conn, series_id, periods, vintage, diff.new, diff.revised, diff.removed, diff.max_revision
    )
    current = [p for p in periods if p.realtime_end == OPEN_END and p.value is not None]
    runs = store.stable_runs(conn, series_id)
    return StableReport(
        series_id=series_id,
        status="nuevo" if not existing else "actualizado",
        observations=len(current),
        first_obs=min((p.obs_date for p in current), default=None),
        last_obs=max((p.obs_date for p in current), default=None),
        new=diff.new,
        revised=diff.revised,
        removed=diff.removed,
        max_revision=diff.max_revision,
        runs=len(runs),
        first_vintage=min((p.realtime_start for p in periods), default=None),
    )


def run(
    client: DefiLlamaClient,
    conn: sqlite3.Connection,
    series: list[StableSeries],
    today: date,
) -> list[StableReport]:
    """`today` es la fecha UTC de la corrida: es la vintage, y el día que se excluye."""
    reports: list[StableReport] = []
    totals: dict[str, list[tuple[date, float]]] | None = None
    for s in series:
        try:
            if s.llama_id is None:
                if totals is None:
                    totals = client.total(today)
                snapshot = totals["usd" if s.id == "STABLE_USD" else "all"]
            else:
                snapshot = client.token(s.llama_id, today)
            reports.append(sync_series(conn, s.id, snapshot, today))
        except Exception as exc:  # noqa: BLE001 — una serie rota no tira la corrida
            reports.append(StableReport(s.id, f"error: {type(exc).__name__}: {exc}"))
    return reports


def print_reports(reports: list[StableReport], requests_made: int) -> None:
    head = f"{'serie':<12}{'estado':<13}{'obs':>6}  {'rango':<25}{'nuevas':>7}{'revis.':>7}"
    head += f"{'sacadas':>8}{'rev. máx':>10}{'corridas':>9}"
    print(head)
    print("-" * len(head))
    for r in reports:
        rng = f"{r.first_obs} .. {r.last_obs}" if r.first_obs else "-"
        rev = f"{r.max_revision:.1e}" if r.revised else "-"
        print(
            f"{r.series_id:<12}{r.status[:12]:<13}{r.observations:>6}  {rng:<25}{r.new:>7}"
            f"{r.revised:>7}{r.removed:>8}{rev:>10}{r.runs:>9}"
        )
    errors = [r for r in reports if r.status.startswith("error")]
    print(f"\n{len(reports)} series, {len(errors)} con error, {requests_made} requests a DefiLlama")
    for r in errors:
        print(f"  {r.series_id}: {r.status}")
    print(
        "\n'revis.' = filas cuyo valor DefiLlama cambió desde la corrida anterior; "
        "'rev. máx' = mayor cambio relativo. Lo anterior a la primera corrida se lee como "
        f"no exacto, con {FALLBACK_LAG_DAYS} día de rezago declarado."
    )
