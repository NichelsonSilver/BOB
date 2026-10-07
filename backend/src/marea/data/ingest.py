"""CLI de ingesta de MAREA. Idempotente e incremental.

    uv run python -m marea.data.ingest fred              # todo el catálogo
    uv run python -m marea.data.ingest fred --series M2SL NFCI
    uv run python -m marea.data.ingest fred --full       # re-baja desde cero
    uv run python -m marea.data.ingest etf               # ETFs (Tiingo)
    uv run python -m marea.data.ingest cot               # posicionamiento CFTC
    uv run python -m marea.data.ingest bcch              # Banco Central de Chile
    uv run python -m marea.data.ingest stable            # stablecoins (DefiLlama)
    uv run python -m marea.data.ingest todo              # las cinco, en orden

Incremental: si FRED no actualizó la serie desde la última corrida
(`last_updated` igual) no se pide nada. Si sí, se piden solo las vintages desde
la última registrada y se cosen con `pit.stitch` contra lo que ya estaba.

Los huecos (valor "." en FRED) se guardan como NULL y se reportan; no se rellenan.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from loguru import logger

from bob.utils.console import enable_utf8_stdout
from marea.config import DB_PATH, MareaSettings
from marea.data import ingest_bcch, ingest_cot, ingest_etf, ingest_stable, store
from marea.data.bcch import BcchClient
from marea.data.catalog import (
    BCCH_BY_ID,
    BCCH_SERIES,
    BY_ID,
    COT_BY_CODE,
    COT_MARKETS,
    ETF_BY_TICKER,
    ETFS,
    FRED_SERIES,
    STABLE_BY_ID,
    STABLE_SERIES,
)
from marea.data.cftc import CftcClient
from marea.data.defillama import DefiLlamaClient
from marea.data.fred import FredClient, FredError
from marea.data.tiingo import TiingoClient
from marea.pit import Period, first_release_lag_days, stitch


@dataclass(frozen=True)
class SyncReport:
    series_id: str
    status: str  # "nuevo" | "actualizado" | "sin cambios" | "error: ..."
    periods: int = 0
    observations: int = 0
    missing: int = 0
    first_obs: date | None = None
    last_obs: date | None = None
    first_vintage: date | None = None
    fallback_lag_days: int | None = None


def _summary(series_id: str, status: str, periods: list[Period], lag: int | None) -> SyncReport:
    # Rango y huecos se leen sobre la vintage VIGENTE: las observaciones que
    # FRED borró siguen en el historial (con "." desde que se borraron) y no
    # son huecos de la serie actual.
    current = [p for p in periods if p.realtime_end.year == 9999]
    valid = [p.obs_date for p in current if p.value is not None]
    first_obs, last_obs = (min(valid), max(valid)) if valid else (None, None)
    return SyncReport(
        series_id=series_id,
        status=status,
        periods=len(periods),
        observations=len(valid),
        missing=sum(
            1
            for p in current
            if p.value is None
            and first_obs is not None
            and last_obs is not None
            and first_obs <= p.obs_date <= last_obs
        ),
        first_obs=first_obs,
        last_obs=last_obs,
        first_vintage=min((p.realtime_start for p in periods), default=None),
        fallback_lag_days=lag,
    )


def sync_series(
    client: FredClient, conn: sqlite3.Connection, series_id: str, full: bool = False
) -> SyncReport:
    info = client.series_info(series_id)
    meta = store.series_meta(conn, series_id)
    existing = store.load_periods(conn, series_id)

    if not full and meta is not None and existing and meta["last_updated"] == info["last_updated"]:
        return _summary(series_id, "sin cambios", existing, meta["fallback_lag_days"])

    if full or meta is None or not existing or meta["last_vintage"] is None:
        periods = client.periods(series_id)
        status = "nuevo" if not existing else "rehecho"
    else:
        since = date.fromisoformat(meta["last_vintage"])
        fetched = client.periods(series_id, since)
        base = [p for p in existing if p.realtime_start < since]
        periods = stitch(base, fetched, boundary=since)
        status = "actualizado"

    first_vintage = min((p.realtime_start for p in periods), default=None)
    last_vintage = max((p.realtime_start for p in periods), default=None)
    lag = first_release_lag_days(periods, first_vintage) if first_vintage else None
    store.replace_periods(conn, series_id, periods)
    store.upsert_series_meta(conn, info, first_vintage, last_vintage, lag)
    return _summary(series_id, status, periods, lag)


def _print(reports: list[SyncReport], requests_made: int) -> None:
    head = f"{'serie':<14}{'estado':<14}{'obs':>7}{'períodos':>10}{'huecos':>8}"
    head += f"  {'rango de datos':<25}{'1ª vintage':<12}{'rezago máx':>10}"
    print(head)
    print("-" * len(head))
    for r in reports:
        rng = f"{r.first_obs} .. {r.last_obs}" if r.first_obs else "-"
        lag = f"{r.fallback_lag_days} d" if r.fallback_lag_days is not None else "-"
        print(
            f"{r.series_id:<14}{r.status[:13]:<14}{r.observations:>7}{r.periods:>10}"
            f"{r.missing:>8}  {rng:<25}{str(r.first_vintage or '-'):<12}{lag:>10}"
        )
    errors = [r for r in reports if r.status.startswith("error")]
    print(f"\n{len(reports)} series, {len(errors)} con error, {requests_made} requests a FRED")
    for r in errors:
        print(f"  {r.series_id}: {r.status}")
    print(
        "\n'rezago máx' = mayor demora medida entre la fecha del dato y su primera "
        "publicación; se usa para los datos anteriores a la 1ª vintage, que quedan "
        "marcados como no exactos."
    )


def main(argv: list[str] | None = None) -> int:
    enable_utf8_stdout()
    parser = argparse.ArgumentParser(prog="marea.data.ingest")
    sub = parser.add_subparsers(dest="source", required=True)
    p_fred = sub.add_parser("fred", help="series de FRED/ALFRED con vintages")
    p_fred.add_argument("--series", nargs="+", help="ids del catálogo (default: todos)")
    p_fred.add_argument("--full", action="store_true", help="re-bajar desde cero")
    p_etf = sub.add_parser("etf", help="precios diarios de ETFs (Tiingo)")
    p_etf.add_argument("--tickers", nargs="+", help="tickers del catálogo (default: todos)")
    p_cot = sub.add_parser("cot", help="posicionamiento COT de la CFTC")
    p_cot.add_argument("--codes", nargs="+", help="códigos de contrato (default: todos)")
    p_bcch = sub.add_parser("bcch", help="series diarias del Banco Central de Chile")
    p_bcch.add_argument("--series", nargs="+", dest="bcch_series", help="ids del catálogo")
    p_stable = sub.add_parser("stable", help="oferta de stablecoins (DefiLlama)")
    p_stable.add_argument("--series", nargs="+", dest="stable_series", help="ids del catálogo")
    sub.add_parser("todo", help="las cinco fuentes, con sus catálogos completos")
    args = parser.parse_args(argv)

    if args.source == "todo":
        return _main_all(parser)
    if args.source == "bcch":
        return _main_bcch(parser, args.bcch_series)
    if args.source == "stable":
        return _main_stable(parser, args.stable_series)
    if args.source == "etf":
        return _main_etf(parser, args.tickers)
    if args.source == "cot":
        return _main_cot(parser, args.codes)
    return _main_fred(parser, args.series, args.full)


def _main_all(parser: argparse.ArgumentParser) -> int:
    """Corre las cinco fuentes. Una que falle (sin key, sin red) no frena a las otras."""
    steps: list[tuple[str, Callable[[], int]]] = [
        ("FRED", lambda: _main_fred(parser, None, full=False)),
        ("Tiingo", lambda: _main_etf(parser, None)),
        ("CFTC", lambda: _main_cot(parser, None)),
        ("BCCh", lambda: _main_bcch(parser, None)),
        ("DefiLlama", lambda: _main_stable(parser, None)),
    ]
    failed: list[str] = []
    for name, step in steps:
        print(f"\n=== {name} ===")
        try:
            code = step()
        except Exception as exc:  # noqa: BLE001 — una fuente caída no tira las demás
            logger.error("{}: {}", name, exc)
            code = 1
        if code:
            failed.append(name)
    tail = f"; fallaron: {', '.join(failed)}" if failed else ""
    print(f"\n=== resumen: {len(steps) - len(failed)}/{len(steps)} fuentes sin error{tail}")
    return 1 if failed else 0


def _main_fred(parser: argparse.ArgumentParser, requested: list[str] | None, full: bool) -> int:
    ids: list[str] = requested or [s.id for s in FRED_SERIES]
    unknown = [i for i in ids if i not in BY_ID]
    if unknown:
        parser.error(f"fuera del catálogo: {', '.join(unknown)} (ver marea.data.catalog)")

    key = MareaSettings().api_fred
    client = FredClient(key.get_secret_value() if key else "")
    conn = store.connect(DB_PATH)
    reports: list[SyncReport] = []
    try:
        for sid in ids:
            try:
                reports.append(sync_series(client, conn, sid, full=full))
            except FredError as exc:
                logger.error("{}: {}", sid, exc)
                reports.append(SyncReport(sid, f"error: {exc}"))
            except Exception as exc:  # noqa: BLE001 — una serie rota no tira la corrida
                logger.exception("{} falló", sid)
                reports.append(SyncReport(sid, f"error: {type(exc).__name__}: {exc}"))
    finally:
        client.close()
        conn.close()
    _print(reports, client.requests_made)
    return 1 if any(r.status.startswith("error") for r in reports) else 0


def _main_etf(parser: argparse.ArgumentParser, requested: list[str] | None) -> int:
    tickers = requested or [e.ticker for e in ETFS]
    unknown = [t for t in tickers if t not in ETF_BY_TICKER]
    if unknown:
        parser.error(f"fuera del catálogo: {', '.join(unknown)} (ver marea.data.catalog)")
    key = MareaSettings().api_tiingo
    client = TiingoClient(key.get_secret_value() if key else "")
    conn = store.connect(DB_PATH)
    try:
        reports = ingest_etf.run(client, conn, tickers)
    finally:
        client.close()
        conn.close()
    ingest_etf.print_reports(reports, client.requests_made)
    return 1 if any(r.status.startswith("error") for r in reports) else 0


def _main_bcch(parser: argparse.ArgumentParser, requested: list[str] | None) -> int:
    ids = requested or [s.id for s in BCCH_SERIES]
    unknown = [i for i in ids if i not in BCCH_BY_ID]
    if unknown:
        parser.error(f"fuera del catálogo: {', '.join(unknown)} (ver marea.data.catalog)")
    cfg = MareaSettings()
    client = BcchClient(
        cfg.bcch_user or "", cfg.bcch_pass.get_secret_value() if cfg.bcch_pass else ""
    )
    conn = store.connect(DB_PATH)
    try:
        today = datetime.now(UTC).date()
        reports = ingest_bcch.run(client, conn, [BCCH_BY_ID[i] for i in ids], today)
    finally:
        client.close()
        conn.close()
    ingest_bcch.print_reports(reports, client.requests_made)
    return 1 if any(r.status.startswith("error") for r in reports) else 0


def _main_stable(parser: argparse.ArgumentParser, requested: list[str] | None) -> int:
    ids = requested or [s.id for s in STABLE_SERIES]
    unknown = [i for i in ids if i not in STABLE_BY_ID]
    if unknown:
        parser.error(f"fuera del catálogo: {', '.join(unknown)} (ver marea.data.catalog)")
    client = DefiLlamaClient()
    conn = store.connect(DB_PATH)
    try:
        today = datetime.now(UTC).date()
        reports = ingest_stable.run(client, conn, [STABLE_BY_ID[i] for i in ids], today)
    finally:
        client.close()
        conn.close()
    ingest_stable.print_reports(reports, client.requests_made)
    return 1 if any(r.status.startswith("error") for r in reports) else 0


def _main_cot(parser: argparse.ArgumentParser, requested: list[str] | None) -> int:
    codes = requested or [m.code for m in COT_MARKETS]
    unknown = [c for c in codes if c not in COT_BY_CODE]
    if unknown:
        parser.error(f"fuera del catálogo: {', '.join(unknown)} (ver marea.data.catalog)")
    client = CftcClient()
    conn = store.connect(DB_PATH)
    try:
        reports = ingest_cot.run(client, conn, [COT_BY_CODE[c] for c in codes])
    finally:
        client.close()
        conn.close()
    ingest_cot.print_reports(reports, client.requests_made)
    return 1 if any(r.status.startswith("error") for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
