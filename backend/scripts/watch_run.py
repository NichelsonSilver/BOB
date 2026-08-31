"""Vigilante de la corrida de validación forward (Fase 5).

El runbook dice "mirar `/api/health` cada tanto". Eso funciona con alguien
delante; una corrida de ~72 h no lo tiene. Este script hace ese chequeo solo,
cada N minutos, y deja **una línea por tick en un archivo**, que es lo que
convierte una corrida desatendida en una corrida auditable: si a las 3 AM el
analista dejó de emitir, mañana se ve a qué hora fue y con qué síntoma.

No sustituye al backend ni al tracker: solo observa. Es de solo lectura sobre
la DB y hace un GET a `/api/health`. Correrlo en una **segunda** terminal, no
en la del backend.

Vigila cinco cosas, en orden de gravedad:

1. **Staleness de derivados.** El único fallo irreversible de la corrida.
   Los snapshots recuperan ~41 h por request; pasado eso, esas 26 columnas
   quedan NaN para siempre. Se avisa a las 24 h, o sea con 17 h de margen
   para reaccionar, no cuando ya se perdió.
2. **El analista no emite.** `fitted` en false pasado el arranque, o
   `last_forecast_open_time` congelado más de 2 barras.
3. **El feed se cayó** (`feed.connected`).
4. **`gap` creciendo**: cortes de feed dentro de horizontes. Se rescatan con
   `download --repair`, porque un `gap` se vuelve a mirar cuando el backfill
   lo cierra.
5. **Avance contra el objetivo**, con ETA calculada sobre el ritmo real
   observado y no sobre el nominal.

Uso:

    uv run python scripts/watch_run.py                  # cada 10 min
    uv run python scripts/watch_run.py --every-min 5 --target 280
    uv run python scripts/watch_run.py --once           # un chequeo y sale

Solo stdlib: tiene que poder correr aunque el entorno del backend esté ocupado.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

#: Ventana que recupera un request de snapshots de derivados. Pasado esto el
#: hueco es irrecuperable a cualquier precio (ventana de ~30 días de Binance,
#: pero el endpoint devuelve ~41 h por llamada).
DERIV_CLIFF_HOURS = 41.0
#: Se avisa bastante antes del acantilado: 24 h deja 17 h de margen.
DERIV_WARN_HOURS = 24.0

MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}


def _utc(ms: float | None) -> str:
    if ms is None:
        return "—"
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%m-%d %H:%M")


def _age_hours(ms: float | None, now_ms: float) -> float | None:
    return None if ms is None else (now_ms - ms) / 3_600_000


def fetch_health(url: str, timeout: float = 10.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def read_db(db: Path, symbol: str, timeframe: str) -> dict:
    """Estado de la corrida leído de SQLite. Read-only: el backend es el único
    que escribe."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        counts = dict(
            cur.execute(
                "SELECT status, COUNT(*) FROM forecastrecord "
                "WHERE symbol = ? AND timeframe = ? GROUP BY status",
                (symbol, timeframe),
            ).fetchall()
        )
        first_emit, last_emit = cur.execute(
            "SELECT MIN(open_time), MAX(open_time) FROM forecastrecord "
            "WHERE symbol = ? AND timeframe = ?",
            (symbol, timeframe),
        ).fetchone()
        versions = [
            r[0]
            for r in cur.execute(
                "SELECT DISTINCT model_version FROM forecastrecord "
                "WHERE symbol = ? AND timeframe = ?",
                (symbol, timeframe),
            ).fetchall()
        ]
        last_candle = cur.execute(
            "SELECT MAX(open_time) FROM candlerecord WHERE symbol = ? AND timeframe = ?",
            (symbol, timeframe),
        ).fetchone()[0]
        last_deriv = cur.execute(
            "SELECT MAX(timestamp) FROM derivativesnapshot WHERE symbol = ?",
            (symbol,),
        ).fetchone()[0]
        # Ventana reciente para el ritmo: lo que se proyecta es el ritmo de
        # AHORA, no el promedio de toda la corrida.
        recientes = [
            r[0]
            for r in cur.execute(
                "SELECT open_time FROM forecastrecord "
                "WHERE symbol = ? AND timeframe = ? ORDER BY open_time DESC LIMIT 24",
                (symbol, timeframe),
            ).fetchall()
        ]
        horizon_bars = cur.execute(
            "SELECT MAX(horizon_bars) FROM forecastrecord "
            "WHERE symbol = ? AND timeframe = ?",
            (symbol, timeframe),
        ).fetchone()[0]
    finally:
        con.close()

    return {
        "resolved": counts.get("resolved", 0),
        "open": counts.get("open", 0),
        "gap": counts.get("gap", 0),
        "first_emit": first_emit,
        "last_emit": last_emit,
        "versions": versions,
        "last_candle": last_candle,
        "last_deriv": last_deriv,
        "recientes": recientes,
        "horizon_bars": horizon_bars,
    }


def _ritmo_emision(recientes: list[int]) -> float | None:
    """Pronósticos por hora, medidos sobre la ventana reciente.

    Reciente y no toda la historia a propósito: una hora mala al principio de
    la corrida no debería seguir contaminando la estimación tres días después.
    """
    if len(recientes) < 2:
        return None
    span_h = (max(recientes) - min(recientes)) / 3_600_000
    if span_h <= 0:
        return None
    return (len(recientes) - 1) / span_h


def check(
    db: Path, url: str, symbol: str, timeframe: str, target: int, prev: dict | None
) -> tuple[str, list[str], dict]:
    """Un tick. Devuelve (línea de log, alertas, estado para el próximo tick)."""
    now_ms = time.time() * 1000
    bar_min = MINUTES.get(timeframe, 15)
    state = read_db(db, symbol, timeframe)
    health = fetch_health(url)
    alerts: list[str] = []

    analyst = (health or {}).get("analyst") or {}
    feed = (health or {}).get("feed") or {}

    # 1. Derivados — el único fallo irreversible.
    deriv_age = _age_hours(state["last_deriv"], now_ms)
    if deriv_age is not None and deriv_age >= DERIV_WARN_HOURS:
        margen = DERIV_CLIFF_HOURS - deriv_age
        if margen <= 0:
            alerts.append(
                f"DERIVADOS IRRECUPERABLES: {deriv_age:.1f} h sin snapshot "
                f"(el acantilado son {DERIV_CLIFF_HOURS:.0f} h). El hueco ya no se cierra."
            )
        else:
            alerts.append(
                f"derivados con {deriv_age:.1f} h de atraso — quedan {margen:.1f} h "
                f"antes de que el hueco sea irrecuperable"
            )

    # 2. Backend / analista.
    if health is None:
        alerts.append(f"el backend no responde en {url} — ¿se cerró la terminal?")
    elif not analyst:
        alerts.append("el backend responde pero NO hay analista (¿BOB_LIVE_ANALYST=false?)")
    elif not analyst.get("fitted"):
        alerts.append("analista sin ajustar — normal los primeros ~90 s, alarmante después")
    else:
        emit_age_min = (
            None
            if state["last_emit"] is None
            else (now_ms - state["last_emit"]) / 60_000
        )
        # Una barra de 15m se cierra cada 15 min y el pronóstico sale con ella;
        # dos barras de atraso ya no es jitter.
        if emit_age_min is not None and emit_age_min > 2 * bar_min + 5:
            alerts.append(
                f"sin pronóstico nuevo hace {emit_age_min:.0f} min "
                f"(> 2 barras) — buscar analysis.error en el log del backend"
            )

    # 3. Feed.
    if health is not None and feed and not feed.get("connected"):
        alerts.append(f"feed desconectado — last_error={feed.get('last_error')}")

    # 4. Gaps.
    if prev is not None and state["gap"] > prev.get("gap", 0):
        alerts.append(
            f"gap subió {prev['gap']} → {state['gap']}: hubo corte de feed dentro "
            f"de un horizonte. Correr download --repair (los gap se re-examinan solos)"
        )

    # 5. Mezcla de versiones — invalida la muestra forward en silencio.
    if len(state["versions"]) > 1:
        alerts.append(
            f"MUESTRA CONTAMINADA: {len(state['versions'])} model_version distintas "
            f"en la misma corrida: {state['versions']}"
        )

    # Avance y ETA. El ritmo que manda es el de EMISIÓN, no el de resolución.
    # Todo pronóstico emitido resuelve un horizonte después salvo que caiga en
    # un gap, así que resolver no es un cuello de botella: es un retraso fijo.
    # Medir `resueltos/hora` mezclaba las dos cosas y daba un número sin
    # sentido —con 1 resuelto en 5,7h proyectaba dos meses y saltaba dos días
    # entre chequeos consecutivos— y encima arrastraba para siempre las horas
    # en que el analista estuvo mudo, que son justo las que uno acaba de
    # arreglar. La proyección honesta es: cuánto falta emitir al ritmo actual,
    # más el horizonte que el último tiene que esperar.
    resolved = state["resolved"]
    faltan = max(0, target - resolved)
    eta = "—"
    ritmo = _ritmo_emision(state["recientes"])
    if faltan == 0:
        eta = "objetivo alcanzado"
    elif ritmo:
        h_horizonte = (state["horizon_bars"] or 0) * bar_min / 60
        eta_dt = dt.datetime.now(dt.UTC) + dt.timedelta(
            hours=faltan / ritmo + h_horizonte
        )
        eta = f"{eta_dt:%m-%d %H:%M UTC} ({ritmo:.1f}/h)"

    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    linea = (
        f"{stamp} | resueltos {resolved}/{target} abiertos {state['open']} "
        f"gap {state['gap']} | fitted={analyst.get('fitted')} "
        f"refit={analyst.get('refitting')} bars_since_fit={analyst.get('bars_since_fit')} "
        f"| ult.pronostico {_utc(state['last_emit'])} vela {_utc(state['last_candle'])} "
        f"deriv {_utc(state['last_deriv'])} "
        f"| feed={feed.get('connected')} | ETA {eta}"
    )
    return linea, alerts, state


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--symbol", default="ETHUSDT")
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--target", type=int, default=280, help="pronósticos resueltos objetivo")
    p.add_argument("--every-min", type=float, default=10.0)
    p.add_argument("--url", default="http://127.0.0.1:8000/api/health")
    p.add_argument("--db", default=str(BACKEND_DIR / "bob.db"))
    p.add_argument("--log", default=str(BACKEND_DIR / "logs" / "watch_run.log"))
    p.add_argument("--once", action="store_true", help="un chequeo y termina")
    args = p.parse_args(argv)

    # La consola de Windows sale en cp1252 y las flechas y guiones largos de
    # las líneas revientan al redirigir a un archivo — que es exactamente lo
    # que se hace con un vigilante desatendido. Interactivo funcionaba, así
    # que el fallo aparecía recién al dejarlo corriendo en serio.
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(encoding="utf-8", errors="replace")

    db = Path(args.db)
    if not db.exists():
        print(f"no existe la DB: {db}", file=sys.stderr)
        return 2

    log = Path(args.log)
    log.parent.mkdir(parents=True, exist_ok=True)

    print(f"vigilando {args.symbol} {args.timeframe} → objetivo {args.target} resueltos")
    print(f"log: {log}")
    print(f"cadencia: cada {args.every_min:g} min. Ctrl+C para salir.\n")

    prev: dict | None = None
    while True:
        try:
            linea, alertas, prev = check(
                db, args.url, args.symbol, args.timeframe, args.target, prev
            )
        except sqlite3.Error as exc:  # DB ocupada por un commit del backend
            linea, alertas = f"{dt.datetime.now(dt.UTC)} | DB ocupada: {exc}", []

        print(linea)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(linea + "\n")
            for a in alertas:
                fh.write(f"    !! {a}\n")
        for a in alertas:
            print(f"    !! {a}")

        if args.once:
            return 1 if alertas else 0
        try:
            time.sleep(args.every_min * 60)
        except KeyboardInterrupt:
            print("\nvigilante detenido (el backend sigue corriendo).")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
