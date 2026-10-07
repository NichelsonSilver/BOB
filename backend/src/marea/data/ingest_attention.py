"""Ingesta de la capa de contraste de narrativa (M5.7): actividad DeFi y atención.

- **TVL y volumen de DEX** (DefiLlama): mismo problema que las stablecoins —la
  fuente reescribe su historia sin vintages— y la misma solución: cada corrida
  es una vintage (`ingest_stable.sync_series`), guardada en `stable_period`
  con su propio `series_id`.
- **Páginas vistas de Wikipedia**: no se revisan, así que alcanza con una
  ingesta incremental con solape de `OVERLAP_DAYS`.

Nada de esto alimenta un modelo: es para contrastar lo que se dice con lo que
pasa (capa 4).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from marea.data import store
from marea.data.defillama import DefiLlamaClient
from marea.data.ingest_stable import StableReport, sync_series
from marea.data.wikimedia import ARTICLES, EARLIEST, Article, WikimediaClient

DEFI_SERIES: tuple[str, ...] = ("DEFI_TVL", "DEX_VOLUME")
OVERLAP_DAYS = 3


def run_defi(client: DefiLlamaClient, conn: sqlite3.Connection, today: date) -> list[StableReport]:
    out: list[StableReport] = []
    for sid, fetch in (("DEFI_TVL", client.chain_tvl), ("DEX_VOLUME", client.dex_volume)):
        try:
            out.append(sync_series(conn, sid, fetch(today), today))
        except Exception as exc:  # noqa: BLE001 — una serie rota no tira la corrida
            out.append(StableReport(sid, f"error: {type(exc).__name__}: {exc}"))
    return out


@dataclass(frozen=True)
class WikiReport:
    article: str
    status: str
    days: int = 0
    new: int = 0
    first: date | None = None
    last: date | None = None


def run_wiki(
    client: WikimediaClient,
    conn: sqlite3.Connection,
    today: date,
    articles: tuple[Article, ...] = ARTICLES,
) -> list[WikiReport]:
    """El día D se pide recién cuando terminó: hasta `today − 1`."""
    out: list[WikiReport] = []
    end = today - timedelta(days=1)
    for art in articles:
        try:
            last = store.last_wiki_day(conn, art.key)
            start = last - timedelta(days=OVERLAP_DAYS) if last else EARLIEST
            before = store.count_wiki(conn, art.key)
            store.upsert_wiki(conn, art.key, client.daily_views(art, start, end))
            rows = store.load_wiki(conn, art.key)
            out.append(
                WikiReport(
                    art.key,
                    "nuevo" if last is None else "actualizado",
                    len(rows),
                    len(rows) - before,
                    rows[0][0] if rows else None,
                    rows[-1][0] if rows else None,
                )
            )
        except Exception as exc:  # noqa: BLE001
            out.append(WikiReport(art.key, f"error: {type(exc).__name__}: {exc}"))
    return out


def print_reports(defi: list[StableReport], wiki: list[WikiReport], requests: int) -> None:
    print(f"{'serie':<28}{'estado':<13}{'obs':>6}{'nuevas':>8}{'revis.':>8}  rango")
    for r in defi:
        rng = f"{r.first_obs} .. {r.last_obs}" if r.first_obs else "-"
        head = f"{r.series_id:<28}{r.status[:12]:<13}{r.observations:>6}"
        print(f"{head}{r.new:>8}{r.revised:>8}  {rng}")
    for w in wiki:
        rng = f"{w.first} .. {w.last}" if w.first else "-"
        print(f"{w.article:<28}{w.status[:12]:<13}{w.days:>6}{w.new:>8}{'-':>8}  {rng}")
    errors = [r.status for r in defi if r.status.startswith("error")]
    errors += [w.status for w in wiki if w.status.startswith("error")]
    print(f"\n{len(defi) + len(wiki)} series, {len(errors)} con error, {requests} requests")
    for e in errors:
        print(f"  {e}")
