# ruff: noqa: E501 — lineas de formato del reporte
"""Insumos medidos del memo semanal — fase M4. Solo lectura.

    uv run python -m marea.brief            # texto para el coordinador
    uv run python -m marea.brief --json

Un solo comando junta lo que el memo puede citar, cada bloque con su nivel de
evidencia. Existe por dos razones: que **cada cifra del memo salga de un
comando** (regla de los agentes de MAREA) y que el coordinador no gaste una
sesión de agente por capa recalculando lo que ya está medido.

No escribe nada: ni `marea.db`, ni `bob.db`, ni el registro de narrativa.

**Ciego al IC del forward del panel cripto.** El brief reporta el estado
OPERATIVO de la Fase 10-bis —fechas válidas, backfill, huecos, ritmo— y no su
IC. Mientras se evalúa enmendar su pre-registro (miradas intermedias con regla
de parada), cualquier mirada al IC le quita valor a esa enmienda.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from marea import narrative, panorama
from marea.config import DB_PATH
from marea.data import store
from marea.data.catalog import ETFS
from marea.power_run import ARTIFACTS
from marea.returns import total_returns

BOB_DB = DB_PATH.parent / "bob.db"
UNIVERSE_JSON = DB_PATH.parent / "artifacts" / "universe-1d.json"
PANEL_CUTOFF_VALID_DAYS = 180  # bob.paper.panel_forward.PRIMARY_CUTOFF_DAYS
DAY_MS = 86_400_000


def _latest_artifact(prefix: str) -> dict[str, Any] | None:
    files = sorted(ARTIFACTS.glob(f"{prefix}-*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def freshness(conn: sqlite3.Connection, today: date) -> list[dict[str, Any]]:
    def row(name: str, last: str | None) -> dict[str, Any]:
        age = (today - date.fromisoformat(last[:10])).days if last else None
        return {"source": name, "last": last, "age_days": age}

    q = conn.execute
    return [
        row(
            "FRED (última obs. más reciente)",
            q("SELECT MAX(observation_end) FROM fred_series").fetchone()[0],
        ),
        row("Tiingo ETFs", q("SELECT MAX(day) FROM etf_bar").fetchone()[0]),
        row("CFTC COT (reporte)", q("SELECT MAX(report_date) FROM cot").fetchone()[0]),
        row("BCCh", q("SELECT MAX(day) FROM bcch_obs").fetchone()[0]),
        row("reporte de liquidez", q("SELECT MAX(as_of) FROM liquidity_report").fetchone()[0]),
        row(
            "DefiLlama stablecoins",
            q(
                "SELECT MAX(obs_date) FROM stable_period WHERE realtime_end = '9999-12-31'"
            ).fetchone()[0],
        ),
        row("Binance funding (día cerrado)", _funding_last(conn)),
        row(
            "Deribit (última foto)",
            q("SELECT SUBSTR(MAX(captured_at), 1, 10) FROM options_snapshot").fetchone()[0],
        ),
    ]


def _funding_last(conn: sqlite3.Connection) -> str | None:
    ms = conn.execute("SELECT MAX(funding_time) FROM funding").fetchone()[0]
    return datetime.fromtimestamp(ms / 1000, UTC).date().isoformat() if ms else None


def liquidity(conn: sqlite3.Connection) -> dict[str, Any] | None:
    raw = store.latest_liquidity_report(conn)
    if raw is None:
        return None
    rep = json.loads(raw)
    r = rep["regime"]
    return {
        "evidence": "medido (monitoreo, sin pronóstico)",
        "as_of": rep["as_of"],
        "label": r["label"],
        "composite_pct": r["composite_pct"],
        "since": r["since"],
        "label_4w_ago": r["label_4w_ago"],
        "label_13w_ago": r["label_13w_ago"],
        "median_run_weeks": r.get("persistence", {}).get("median_run_weeks"),
        "core": [
            {k: i[k] for k in ("key", "name", "obs_date", "chg_13w", "pct", "label")}
            for i in rep["indicators"]
            if i["in_composite"]
        ],
        "chile": [
            {k: i[k] for k in ("key", "name", "obs_date", "chg_13w", "label")}
            for i in rep["indicators"]
            if i["group"] == "chile"
        ],
    }


def relative_strength(
    conn: sqlite3.Connection, weeks: tuple[int, ...] = (4, 13, 26)
) -> dict[str, Any]:
    """Retorno total de cada ETF menos la mediana del universo. Descriptivo."""
    rows = []
    last_days = []
    for e in ETFS:
        bars = store.load_bars(conn, e.ticker)
        if len(bars) < 2:
            continue
        r = total_returns(
            np.array([b.close for b in bars]),
            np.array([b.div_cash for b in bars]),
            np.array([b.split_factor for b in bars]),
        )
        logc = np.concatenate([[0.0], np.cumsum(np.log1p(r[1:]))])
        last_days.append(bars[-1].day)
        row: dict[str, Any] = {"ticker": e.ticker, "block": e.block}
        for w in weeks:
            n = 5 * w
            row[f"ret_{w}s"] = float(math.expm1(logc[-1] - logc[-1 - n])) if logc.size > n else None
        rows.append(row)
    for w in weeks:
        vals = [x[f"ret_{w}s"] for x in rows if x[f"ret_{w}s"] is not None]
        med = float(np.median(vals)) if vals else 0.0
        for x in rows:
            v = x[f"ret_{w}s"]
            x[f"rel_{w}s"] = None if v is None else v - med
    rows.sort(key=lambda x: -(x["rel_13s"] if x["rel_13s"] is not None else -math.inf))
    power = _latest_artifact("marea-power")
    return {
        "evidence": "medido (descriptivo: pasado, no pronóstico)",
        "as_of": max(last_days).isoformat() if last_days else None,
        "effective_n": (
            power["variants"][next(iter(power["variants"]))]["effective_n_demeaned"][
                "participation_ratio"
            ]
            if power
            else None
        ),
        "rows": rows,
    }


def ranking_verdict() -> dict[str, Any] | None:
    art = _latest_artifact("marea-ranking")
    if art is None:
        return None
    v = art["primary"]["verdict"]
    return {
        "evidence": "gate M3 — NO VIABLE" if not v["viable"] else "gate M3 — viable offline",
        "artifact": art["run_id"],
        "viable": v["viable"],
        "reading": v["reading"],
        "ic_mean": v["c1_ic_mean"],
        "ic_ci95": v["c1_ic_ci95"],
    }


def panel_forward_status(path: Path, today: date) -> dict[str, Any] | None:
    """Estado operativo de la Fase 10-bis. NO lee probabilidades ni resultados."""
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT open_time, MAX(is_backfill) FROM panelforecastrecord GROUP BY open_time ORDER BY open_time"
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    if not rows:
        return None
    dates = [datetime.fromtimestamp(ot / 1000, UTC).date() for ot, _ in rows]
    valid = [d for (_, bf), d in zip(rows, dates, strict=True) if not bf]
    backfill = [d for (_, bf), d in zip(rows, dates, strict=True) if bf]
    span = [dates[0] + timedelta(days=i) for i in range((dates[-1] - dates[0]).days + 1)]
    missing = sorted(set(span) - set(dates))
    elapsed = (dates[-1] - dates[0]).days + 1
    rate = len(valid) / elapsed if elapsed else 0.0
    remaining = PANEL_CUTOFF_VALID_DAYS - len(valid)
    eta = today + timedelta(days=math.ceil(remaining / rate)) if rate > 0 else None
    return {
        "evidence": "validado offline; forward en curso (sin veredicto antes del corte)",
        "first_decision": dates[0].isoformat(),
        "last_decision": dates[-1].isoformat(),
        "decision_dates": elapsed,
        "valid": len(valid),
        "backfill": [d.isoformat() for d in backfill],
        "never_emitted": [d.isoformat() for d in missing],
        "valid_rate": rate,
        "cutoff_valid_days": PANEL_CUTOFF_VALID_DAYS,
        "eta_at_current_rate": eta.isoformat() if eta else None,
        "eta_if_every_day_valid": (today + timedelta(days=remaining)).isoformat(),
        "ic": "no se reporta (ciego, ver docstring)",
    }


def build(today: date | None = None, bob_db: Path = BOB_DB) -> dict[str, Any]:
    day = today or datetime.now(UTC).date()
    conn = store.connect(DB_PATH)
    try:
        out = {
            "as_of": day.isoformat(),
            "freshness": freshness(conn, day),
            "liquidity": liquidity(conn),
            "relative_strength": relative_strength(conn),
        }
        out["cycle_crypto"] = panorama.build(conn, bob_db, UNIVERSE_JSON, day)
    finally:
        conn.close()
    out["ranking_m3"] = ranking_verdict()
    out["panel_forward"] = panel_forward_status(bob_db, day)
    out["narrative_7d"] = [e.model_dump() for e in narrative.recent(narrative.read(), 7)]
    return out


def _pct(v: float | None, d: int = 1) -> str:
    return "—" if v is None else f"{v * 100:+.{d}f}%"


def render(b: dict[str, Any]) -> str:
    L = [f"INSUMOS DEL MEMO MAREA — {b['as_of']} (cada bloque con su nivel de evidencia)", ""]
    L.append("FRESCURA")
    for f in b["freshness"]:
        warn = "  ⚠ más de 8 días" if f["age_days"] is not None and f["age_days"] > 8 else ""
        L.append(f"  {f['source']:34} {f['last'] or '—':>12}  ({f['age_days']} d){warn}")
    liq = b["liquidity"]
    L += ["", "1. LIQUIDEZ — " + (liq["evidence"] if liq else "sin reporte: correr marea.monitor")]
    if liq:
        L.append(
            f"  régimen {liq['label']} (pct {liq['composite_pct']:.2f}) desde {liq['since']}; "
            f"hace 4s {liq['label_4w_ago']}, hace 13s {liq['label_13w_ago']}; "
            f"una etiqueta dura {liq['median_run_weeks']} semanas en la mediana"
        )
        for i in liq["core"]:
            L.append(
                f"    {i['name'][:40]:40} {i['obs_date']}  13s {i['chg_13w']:+.4f}  pct {i['pct']:.2f}  {i['label']}"
            )
        L.append(
            "  Chile: "
            + " · ".join(f"{i['name'].split(' (')[0]} {i['label']}" for i in liq["chile"])
        )
    rs = b["relative_strength"]
    L += [
        "",
        f"2. FUERZA RELATIVA — {rs['evidence']} · al {rs['as_of']} · N efectivo ≈ {rs['effective_n']:.1f}",
    ]
    L.append(f"  {'ETF':5} {'bloque':14} {'rel 4s':>8} {'rel 13s':>8} {'rel 26s':>8}")
    for r in rs["rows"]:
        L.append(
            f"  {r['ticker']:5} {r['block']:14} {_pct(r['rel_4s']):>8} {_pct(r['rel_13s']):>8} {_pct(r['rel_26s']):>8}"
        )
    rk = b["ranking_m3"]
    L += ["", "3. RANKING PRONOSTICADO — " + (rk["evidence"] if rk else "sin gate corrido")]
    if rk:
        L.append(
            f"  {rk['reading']}: IC {rk['ic_mean']:+.4f} [{rk['ic_ci95'][0]:+.4f}, {rk['ic_ci95'][1]:+.4f}]"
            f" ({rk['artifact']}). No hay ranking esperado que citar."
        )
    pf = b["panel_forward"]
    L += ["", "4. CRIPTO — panel diario: " + (pf["evidence"] if pf else "sin registros forward")]
    if pf:
        L.append(
            f"  {pf['valid']} fechas válidas de {pf['decision_dates']} ({pf['valid_rate']:.0%}); "
            f"backfill {len(pf['backfill'])}, nunca emitidas {len(pf['never_emitted'])}"
        )
        L.append(
            f"  corte a {pf['cutoff_valid_days']} válidas: {pf['eta_at_current_rate']} al ritmo actual, "
            f"{pf['eta_if_every_day_valid']} si todas valieran. IC: {pf['ic']}"
        )
        L.append("  15m: congelado como módulo (no corre en vivo).")
    L += _render_cycle_crypto(b.get("cycle_crypto"))
    L += [
        "",
        f"5. NARRATIVA — últimos 7 días ({len(b['narrative_7d'])} entradas; peso bajo, solo divergencias)",
    ]
    entries = [narrative.Entry.model_validate(e) for e in b["narrative_7d"]]
    L.append("  " + narrative.render(entries).replace("\n", "\n  "))
    return "\n".join(L)


def _render_cycle_crypto(p: dict[str, Any] | None) -> list[str]:
    """Bloque 4b: ciclo y cripto de la M5. Monitoreo; cada línea dice su fecha."""
    if not p:
        return []
    L = ["", "4b. CICLO Y CRIPTO (M5) — medido, monitoreo sin pronóstico"]

    def err(block: dict[str, Any]) -> bool:
        if "error" in block:
            L.append(f"    sin dato: {block['error']}")
            return True
        return False

    r = p["recession"]
    L.append("  Recesión EE.UU. a 12 meses (probit NY Fed):")
    if not err(r):
        sahm = r["sahm"]
        L.append(
            f"    {r['probability']:.1%} para {r['target'][:7]} (spread de {r['as_of'][:7]}: {r['spread']:+.2f}); "
            f"{r['caveat']}. Sahm {sahm['value']:.2f} ({sahm['as_of'][:7]})"
        )
    c = p["btc_cycle"]
    L.append("  Ciclo BTC (Risk 0-1):")
    if not err(c):
        pit = "—" if c["risk_pit"] is None else f"{c['risk_pit']:.3f}"
        L.append(
            f"    point-in-time {pit} ({c['zone']}), con lookahead {c['risk_full']:.3f}; "
            f"Mayer {c['mayer']:.2f}; al {c['as_of']}"
        )
    br_ = p["breadth"]
    L.append("  Amplitud cripto:")
    if not err(br_):
        alt = "—" if br_["altseason"] is None else f"{br_['altseason']:.2f}"
        L.append(
            f"    {br_['above_sma200']:.0%} sobre su SMA200 y {br_['above_sma20w']:.0%} sobre 20 semanas "
            f"(n={br_['n_symbols']}); Altcoin Season {alt}, {br_['altseason_label']}; al {br_['as_of']}"
        )
    f = p["funding"]
    L.append("  Funding (media 7 d, anualizado):")
    if not err(f):
        L.append(
            f"    BTC {f['btc_annual_pct']:.2f}% · ETH {f['eth_annual_pct']:.2f}% · ETH−BTC "
            f"{f['spread_eth_btc_pct']:+.2f}% (sobre el {f['spread_rank']:.0%} de los días desde "
            f"{f['rank_since'][:7]}); al {f['as_of']}"
        )
    o = p["options"]
    L.append("  Opciones Deribit (put/call por OI):")
    if not err(o):
        for cur in ("btc", "eth"):
            x = o[cur]
            L.append(
                f"    {cur.upper()} {x['pc_oi']:.2f} (≤30 d {x['pc_oi_near']:.2f}), OI {x['oi_usd'] / 1e9:.1f} mil M USD; "
                f"{x['snapshots']} fotos desde {x['first_snapshot'][:10]}"
            )
        L.append(f"    {o['evidence']}")
    st = p["stablecoins"]
    L.append("  Stablecoins:")
    if not err(st):
        chg = st["change_30d_usd"]
        rank = st["ssr_rank_since_2020"]
        L.append(
            f"    oferta {st['supply_usd'] / 1e9:.1f} mil M USD"
            + (f", {chg / 1e9:+.2f} mil M en 30 d" if chg is not None else "")
            + (
                f"; SSR {st['ssr']:.2f} (sobre el {rank:.0%} de los días desde 2020)"
                if rank is not None
                else ""
            )
            + f"; al {st['as_of']}"
        )
    return L


def main(argv: list[str] | None = None) -> int:
    from bob.utils.console import enable_utf8_stdout

    enable_utf8_stdout()
    ap = argparse.ArgumentParser(description="insumos medidos del memo de MAREA")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    b = build()
    print(json.dumps(b, indent=2, ensure_ascii=False) if args.json else render(b))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
