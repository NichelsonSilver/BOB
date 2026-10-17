"""Ingesta del TVL de DeFi por categoría (M7): una foto por corrida.

DefiLlama no da historia gratis por categoría, así que la historia es la suma
de las fotos que guarda esta ingesta, una por día UTC. Un día sin correr es un
día que falta para siempre: por eso va en la corrida diaria
(`ingest diario`), junto a Deribit y las stablecoins.

El listado sí trae el TVL de hace 1, 7 y 30 días, así que el crecimiento a un
mes existe desde la primera foto; lo que se acumula es la serie más larga.

Dos controles antes de guardar; si alguno falla, la foto no se guarda:

1. **Cambio de un día del propio listado**, mismos protocolos, dentro de
   ±`MAX_DAY_CHANGE`. Medido sobre `DEFI_TVL` (M5.7) desde 2020: el peor día
   fue −21,0% (2020-03-13) y el mejor +22,2% (2020-08-15). Algo fuera de eso
   es un listado con valores rotos, no un día de mercado.
2. **Listado truncado**: si trae más de `MAX_PROTOCOL_DROP` menos protocolos
   que la foto anterior. DefiLlama no borra los protocolos muertos, los deja
   con TVL 0, así que el conteo no debería bajar. Umbral declarado, no medido.

**No se concilia contra el TVL total de la M5.7**: se intentó y no cierra
(ver `marea.defi_categories`). Un control calibrado probando exclusiones hasta
que una calce sería ajustarlo a los datos.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from marea.data import store
from marea.data.defillama import DefiLlamaClient
from marea.defi_categories import CategoryTvl, aggregate

MAX_DAY_CHANGE = 0.30
MAX_PROTOCOL_DROP = 0.10


@dataclass(frozen=True)
class IndustriesReport:
    status: str
    categories: int = 0
    protocols: int = 0
    tvl_sum: float = 0.0
    day_change: float | None = None
    snapshots: int = 0


def day_change(cats: list[CategoryTvl]) -> float | None:
    """Cambio de un día del listado entero, con los mismos protocolos."""
    prev = sum(c.prev_day for c in cats)
    return sum(c.now_day for c in cats) / prev - 1.0 if prev > 0 else None


def check(cats: list[CategoryTvl], previous_protocols: int | None) -> str | None:
    """Motivo para no guardar la foto, o None si pasa los dos controles."""
    ch = day_change(cats)
    if ch is None:
        return "el listado no trae TVL de ayer para ningún protocolo"
    if abs(ch) > MAX_DAY_CHANGE:
        return f"cambio de un día {ch:+.1%}, fuera de ±{MAX_DAY_CHANGE:.0%}"
    n = sum(c.protocols for c in cats)
    if previous_protocols and n < (1 - MAX_PROTOCOL_DROP) * previous_protocols:
        return f"{n} protocolos contra {previous_protocols} en la foto anterior: listado truncado"
    return None


def run(client: DefiLlamaClient, conn: sqlite3.Connection, now: datetime) -> IndustriesReport:
    try:
        cats = aggregate(client.protocols())
    except Exception as exc:  # noqa: BLE001 — se reporta, no tira la corrida diaria
        return IndustriesReport(f"error: {type(exc).__name__}: {exc}")
    if not cats:
        return IndustriesReport("error: el listado no trajo ninguna categoría con TVL")
    snapshots = store.load_defi_categories(conn)
    today = now.date()
    earlier = [d for d in snapshots if d < today]
    previous = sum(c.protocols for c in snapshots[max(earlier)][1]) if earlier else None
    n = sum(c.protocols for c in cats)
    tvl_sum = sum(c.tvl for c in cats)
    reason = check(cats, previous)
    if reason:
        return IndustriesReport(
            f"error: {reason}; no se guarda", len(cats), n, tvl_sum, day_change(cats)
        )
    store.save_defi_categories(conn, now, cats)
    return IndustriesReport(
        "guardada", len(cats), n, tvl_sum, day_change(cats), len(store.load_defi_categories(conn))
    )


def print_report(r: IndustriesReport, requests_made: int) -> None:
    if r.status.startswith("error"):
        print(f"categorías DeFi: {r.status}")
    else:
        ch = f"{r.day_change:+.2%}" if r.day_change is not None else "—"
        print(
            f"categorías DeFi: {r.status} · {r.categories} categorías, {r.protocols} protocolos · "
            f"suma {r.tvl_sum / 1e9:,.1f} mil millones USD · cambio de un día {ch} · "
            f"{r.snapshots} fotos acumuladas"
        )
    print(f"{requests_made} requests a DefiLlama")
    print(
        "\nLa suma de categorías tiene doble conteo (liquid staking depositado en lending "
        "cuenta en las dos): no es el TVL total de DeFi."
    )
