"""Lecturas de ciclo y cripto de la M5, en un solo lugar. Solo lectura.

Junta lo que la M5 midió y que no es parte del régimen de liquidez:
probabilidad de recesión, Risk de ciclo de BTC, amplitud y Altcoin Season,
funding, opciones y stablecoins. Lo usan `marea.brief` (memo) y la API
(`/api/macro/panorama`), así que la cifra que ve el usuario y la que cita el
memo salen de la misma función.

Cada bloque trae su **nivel de evidencia** y su **fecha de dato**, y si falla
devuelve `{"error": ...}` en vez de tirar el resto: un bloque caído no apaga
el panorama. Nada de esto es pronóstico (`docs/M5_ALCANCE.md` §5).
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from marea import breadth as br
from marea import cycle
from marea.data import store
from marea.funding import annualized_pct, daily_funding, spread, trailing_mean
from marea.pit import OPEN_END, as_of
from marea.recession import recession_series
from marea.stablecoins import known_series, net_mints, ssr

MONITOR = "medido (monitoreo, sin pronóstico)"


def _pct_rank(values: list[float], x: float) -> float | None:
    """Fracción de `values` estrictamente por debajo de `x`."""
    vals = [v for v in values if math.isfinite(v)]
    return sum(v < x for v in vals) / len(vals) if vals else None


def _fred_asof(conn: sqlite3.Connection, sid: str, known_at: date) -> list[Any]:
    meta = store.series_meta(conn, sid)
    if meta is None:
        raise LookupError(f"falta {sid}: correr `marea.data.ingest fred`")
    fv = date.fromisoformat(meta["first_vintage"]) if meta["first_vintage"] else None
    return as_of(store.load_periods(conn, sid), known_at, fv, meta["fallback_lag_days"])


def recession(conn: sqlite3.Connection, today: date) -> dict[str, Any]:
    rows = recession_series(_fred_asof(conn, "DGS10", today), _fred_asof(conn, "DTB3", today))
    last = rows[-1]
    peak = max((r for r in rows if r.month >= date(2022, 1, 1)), key=lambda r: r.probability)
    sahm = [p for p in _fred_asof(conn, "SAHMREALTIME", today) if p.value is not None]
    return {
        "evidence": "modelo publicado (NY Fed), reproducido desde FRED; no es pronóstico propio",
        "as_of": last.month.isoformat(),
        "probability": last.probability,
        "target": last.target.isoformat(),
        "spread": last.spread,
        "history_12m": [
            {"month": r.month.isoformat(), "probability": r.probability} for r in rows[-12:]
        ],
        "peak_since_2022": {"month": peak.month.isoformat(), "probability": peak.probability},
        "caveat": "llegó a {:.0%} en {} y la recesión no llegó".format(
            peak.probability, peak.month.strftime("%Y-%m")
        ),
        "sahm": {"as_of": sahm[-1].obs_date.isoformat(), "value": sahm[-1].value} if sahm else None,
    }


def btc_cycle(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = cycle.cycle_risk(cycle.settled_prices(store.load_periods(conn, "CBBTCUSD")))
    if not rows:
        raise LookupError("falta CBBTCUSD")
    last = rows[-1]
    dv = cycle.divergence(rows)
    return {
        "evidence": MONITOR,
        "as_of": last.day.isoformat(),
        "price": last.price,
        "mayer": last.mayer,
        "risk_pit": last.risk_pit,
        "risk_full": last.risk_full,
        "zone": _zone(last.risk_pit),
        "pit_vs_full": {
            "correlation": dv.correlation,
            "mean_abs_diff": dv.mean_abs_diff,
            "share_far": dv.share_far,
        },
        "history": [
            {"day": r.day.isoformat(), "risk_pit": r.risk_pit, "risk_full": r.risk_full}
            for r in rows[-365:]
        ],
    }


def _zone(risk: float | None) -> str | None:
    """Bandas de lectura de Outliers (0,30 / 0,60 / 0,75), solo como vocabulario."""
    if risk is None:
        return None
    if risk < 0.30:
        return "baja del ciclo"
    if risk < 0.60:
        return "media del ciclo"
    if risk < 0.75:
        return "alta del ciclo"
    return "extrema del ciclo"


def crypto_breadth(bob_db: Path, universe_json: Path) -> dict[str, Any]:
    from bob.signals.universe import eligibility_mask

    panel = br.load_panel(bob_db, universe_json)
    rows = br.breadth(panel, eligibility_mask(panel.close, panel.quote_volume))
    last = rows[-1]
    a200, n200 = last.above[200]
    a140, n140 = last.above[140]
    return {
        "evidence": MONITOR + "; Altcoin Season con top 50 por volumen (proxy declarado)",
        "as_of": last.day.isoformat(),
        "above_sma200": a200,
        "above_sma20w": a140,
        "n_symbols": n200,
        "altseason": last.altseason,
        "altseason_label": br.season_label(last.altseason),
        "history": [
            {
                "day": r.day.isoformat(),
                "above_sma200": r.above[200][0] if math.isfinite(r.above[200][0]) else None,
                "altseason": r.altseason,
            }
            for r in rows[-365:]
        ],
    }


def funding(conn: sqlite3.Connection) -> dict[str, Any]:
    btc = daily_funding(store.load_funding(conn, "BTCUSDT"))
    eth = daily_funding(store.load_funding(conn, "ETHUSDT"))
    if not btc or not eth:
        raise LookupError("falta funding: correr `marea.data.ingest funding`")
    b7 = dict(trailing_mean(list(btc.items()), 7))
    e7 = dict(trailing_mean(list(eth.items()), 7))
    s7 = dict(trailing_mean(spread(eth, btc), 7))
    day = max(s7)
    past_spread = [annualized_pct(v) for d, v in s7.items() if d < day]
    past_btc = [annualized_pct(v) for d, v in b7.items() if d < day]
    return {
        "evidence": MONITOR,
        "as_of": day.isoformat(),
        "btc_annual_pct": annualized_pct(b7[day]),
        "eth_annual_pct": annualized_pct(e7[day]),
        "spread_eth_btc_pct": annualized_pct(s7[day]),
        "spread_rank": _pct_rank(past_spread, annualized_pct(s7[day])),
        "btc_rank": _pct_rank(past_btc, annualized_pct(b7[day])),
        "rank_since": min(s7).isoformat(),
    }


def options(conn: sqlite3.Connection) -> dict[str, Any]:
    out: dict[str, Any] = {
        "evidence": "foto diaria sin historia previa: sin percentil hasta acumular meses",
    }
    for cur in ("BTC", "ETH"):
        snaps = store.load_options(conn, cur)
        if not snaps:
            raise LookupError("sin fotos de Deribit: correr `marea.data.ingest options`")
        s = snaps[-1]
        out[cur.lower()] = {
            "captured_at": s.captured_at.isoformat(),
            "pc_oi": s.pc_oi,
            "pc_volume": s.pc_volume,
            "pc_oi_near": s.pc_oi_near,
            "oi_usd": s.oi_usd,
            "snapshots": len(snaps),
            "first_snapshot": snaps[0].captured_at.isoformat(),
        }
    return out


def stablecoins(conn: sqlite3.Connection, today: date) -> dict[str, Any]:
    pts = known_series(store.load_stable_periods(conn, "STABLE_USD"), today)
    supply = [(p.obs_date, p.value) for p in pts if p.value is not None]
    if not supply:
        raise LookupError("falta STABLE_USD: correr `marea.data.ingest stable`")
    values = dict(supply)
    last_day, last = supply[-1]
    d30 = values.get(last_day - timedelta(days=30))
    nm = net_mints(supply, 7)
    btc = [
        (p.obs_date, p.value)
        for p in store.load_periods(conn, "CBBTCUSD")
        if p.realtime_end == OPEN_END and p.value is not None
    ]
    s = ssr(btc, supply)
    ssr_since = date(2020, 1, 1)  # antes, las stablecoins eran marginales
    s_recent = [(d, v) for d, v in s if d >= ssr_since]
    return {
        "evidence": MONITOR + "; point-in-time solo desde la primera corrida (2026-10-07)",
        "as_of": last_day.isoformat(),
        "supply_usd": last,
        "change_30d_usd": last - d30 if d30 is not None else None,
        "change_30d_pct": last / d30 - 1.0 if d30 else None,
        "net_mints_7d_usd": nm[-1][1] if nm else None,
        "ssr": s_recent[-1][1] if s_recent else None,
        "ssr_rank_since_2020": _pct_rank([v for _, v in s_recent[:-1]], s_recent[-1][1])
        if s_recent
        else None,
        "history": [{"day": d.isoformat(), "supply_usd": v} for d, v in supply[-365:]],
    }


def _safe(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — un bloque caído no apaga el panorama
        return {"error": f"{type(exc).__name__}: {exc}"}


def build(
    conn: sqlite3.Connection, bob_db: Path, universe_json: Path, today: date | None = None
) -> dict[str, Any]:
    day = today or datetime.now(UTC).date()
    return {
        "as_of": day.isoformat(),
        "recession": _safe(lambda: recession(conn, day)),
        "btc_cycle": _safe(lambda: btc_cycle(conn)),
        "breadth": _safe(lambda: crypto_breadth(bob_db, universe_json)),
        "funding": _safe(lambda: funding(conn)),
        "options": _safe(lambda: options(conn)),
        "stablecoins": _safe(lambda: stablecoins(conn, day)),
    }
