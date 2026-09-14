"""Diagnóstico de la corrida de validación forward (cierre de la Fase 5).

El reporte del tracker (`bob.paper.tracker --artifact`) congela el **veredicto**:
cobertura del cono con su IC, sigma contra baselines, Diebold-Mariano, EV. Este
script responde las otras cuatro preguntas que ese artefacto no contesta y de
las que dependen conclusiones del cierre. Existe porque esas conclusiones no
pueden apoyarse en un cálculo hecho a mano y tirado a la basura.

**1. ¿Cuántas barras se perdieron, y por culpa de qué?** Es la pregunta que el
punto 12 de la Fase 5 enseñó a hacer: "sin pronóstico" no es lo mismo que "sin
datos". Se separa *proceso abajo o suspendido* —la pérdida que el diseño
acepta— de *analista mudo con el proceso vivo*, que es la clase de bug. El
discriminador es la actividad real en `backend.log`, y no el reinicio limpio:
un equipo que Windows durmió con el backend arriba vuelve sin haber escrito una
sola vela, y desde el log de arranques aparenta haber corrido todo el tiempo.

**2. ¿Hasta dónde llega la sigma que el modelo puede pronosticar?** Un GBM de
media condicional encoge hacia el centro de su distribución de entrenamiento.
Si el máximo que llega a emitir está muy por debajo del máximo realizado, las
barreras quedan cortas exactamente en el pico — la dirección que lastima a un
usuario apalancado, y la explicación de por qué el cono sub-cubre en la cola.

**3. ¿QLIKE cambió de verdad?** En el corte de n=288 se argumentó que QLIKE "no
depende de la dispersión de la muestra". Es escala-libre *por observación*,
pero su **media está dominada por la cola**, así que en la práctica depende de
la ventana tanto como el R², por otra vía. Acá se mide: qué fracción del total
aporta el decil superior, y mediana contra media por observación.

**4. ¿Hay degradación medible contra el gate?** Con solapamiento de H barras, n
registros son ~n/H observaciones independientes. Antes de leer un movimiento de
QLIKE o de la pendiente de Mincer-Zarnowitz como hallazgo hay que ponerle su IC
por bloques y ver si el valor del gate cae adentro. Es la misma corrección que
en el corte de n=288 dio vuelta la lectura de la cobertura del cono: tratar
observaciones solapadas como independientes convierte ruido en hallazgo.

Las referencias del gate **se leen del artefacto** de la variante y el
estimador que produjeron los pronósticos, no se copian acá. Un diagnóstico con
sus propios números de referencia podría contradecir al artefacto, y esa
contradicción es justo la que no puede existir en este proyecto.

Es de **solo lectura** sobre la DB (`mode=ro`): no resuelve pronósticos ni
escribe nada. Correrlo con el backend arriba o abajo da lo mismo.

    uv run python scripts/diagnose_forward.py
    uv run python scripts/diagnose_forward.py --symbol ETHUSDT --horizon 16
"""

from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from bob.models.metrics import mincer_zarnowitz, qlike
from bob.paper.forward_eval import coverage_interval, effective_blocks
from bob.utils.console import enable_utf8_stdout

BACKEND_DIR = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = BACKEND_DIR / "artifacts"
DEFAULT_DB = BACKEND_DIR / "bob.db"
DEFAULT_LOG = BACKEND_DIR / "logs" / "backend.log"

MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}

#: Timestamp al inicio de línea de loguru, en hora **local**.
_LOG_TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d+")
#: Sufijo de fecha de un run del gate: ...-YYYYmmddHHMMSS
_RUN_TS = re.compile(r"-(\d{14})$")

SEP = "  " + "─" * 70


def _utc(ms: float) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%m-%d %H:%M")


# ─────────────────────────────── datos ───────────────────────────────


def load_forward(db: Path, symbol: str, timeframe: str) -> dict[str, Any]:
    """Pronósticos resueltos, emitidos y velas. Solo lectura: la DB puede estar en uso."""
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        resolved = con.execute(
            "select open_time, sigma_forecast, realized_vol from forecastrecord "
            "where symbol=? and timeframe=? and status='resolved' "
            "and sigma_forecast is not null and realized_vol is not null "
            "order by open_time",
            (symbol, timeframe),
        ).fetchall()
        emitted = [
            t
            for (t,) in con.execute(
                "select open_time from forecastrecord where symbol=? and "
                "timeframe=? order by open_time",
                (symbol, timeframe),
            )
        ]
        versions = [
            v
            for (v,) in con.execute(
                "select distinct model_version from forecastrecord where symbol=? and timeframe=?",
                (symbol, timeframe),
            )
        ]
        n_gap = con.execute(
            "select count(*) from forecastrecord where symbol=? and timeframe=? and status='gap'",
            (symbol, timeframe),
        ).fetchone()[0]
        candles = [
            t
            for (t,) in con.execute(
                "select open_time from candlerecord where symbol=? and "
                "timeframe=? order by open_time",
                (symbol, timeframe),
            )
        ]
    finally:
        con.close()
    return {
        "open_time": np.array([r[0] for r in resolved], dtype=np.int64),
        "sigma": np.array([r[1] for r in resolved], dtype=np.float64),
        "realized": np.array([r[2] for r in resolved], dtype=np.float64),
        "emitted": np.array(emitted, dtype=np.int64),
        "candles": np.array(candles, dtype=np.int64),
        "versions": versions,
        "n_gap": int(n_gap),
    }


def log_activity(log: Path) -> list[int]:
    """Instantes (ms UTC) con actividad en el log del backend.

    El log escribe en hora local; se lleva a UTC con el offset del sistema, que
    es lo único que hace comparables las dos series. Es la prueba de vida del
    proceso: el analista deja al menos una línea por vela cerrada.
    """
    if not log.is_file():
        return []
    out: list[int] = []
    for line in log.open(encoding="utf-8", errors="replace"):
        m = _LOG_TS.match(line)
        if m:
            naive = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            out.append(int(naive.astimezone(dt.UTC).timestamp() * 1000))
    out.sort()
    return out


def gate_reference(variant: str, vol_kind: str) -> tuple[str, dict[str, Any]] | None:
    """Métricas de volatilidad del run del gate de esa variante y ese estimador.

    Se filtra por variante **y** estimador, y se ordena por el timestamp del
    nombre y no por el nombre completo: desde la Fase 4-bis el `run_id` lleva el
    estimador, así que ordenar alfabéticamente pone `gbm` antes que `xgb` y "el
    más reciente de esta variante" devolvería el otro modelo. Es el mismo bug
    que se encontró en `/api/backtest/gate` y se evita igual.
    """
    best: tuple[str, str, dict[str, Any]] | None = None
    for path in sorted(ARTIFACTS_DIR.glob("*.json")):
        if path.name.startswith("forward-"):
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            cfg = raw["config"]
        except (OSError, ValueError, KeyError):
            continue  # artefacto de otro esquema: se omite, no se rompe
        name = "price"
        if cfg.get("use_derivatives"):
            name = "price+deriv"
        if cfg.get("use_book"):
            name = "full+near" if cfg.get("use_book_near") else "full"
        if name != variant or (cfg.get("vol_kind") or "gbm") != vol_kind:
            continue
        m = _RUN_TS.search(path.stem)
        stamp = m.group(1) if m else ""
        model = raw.get("volatility", {}).get("model")
        if model and (best is None or stamp > best[0]):
            best = (stamp, path.name, model)
    return (best[1], best[2]) if best else None


# ───────────────────────── bootstrap por bloques ─────────────────────────


def _ols_slope(y: np.ndarray, f: np.ndarray, axis: int = -1) -> np.ndarray:
    """Pendiente de y = a + b·f en forma cerrada, vectorizable sobre filas."""
    fm = f.mean(axis=axis, keepdims=True)
    ym = y.mean(axis=axis, keepdims=True)
    var = ((f - fm) ** 2).mean(axis=axis)
    cov = ((f - fm) * (y - ym)).mean(axis=axis)
    return np.where(var > 0, cov / np.where(var > 0, var, 1.0), np.nan)


def beta_interval(
    y: np.ndarray, f: np.ndarray, block: int, *, n_boot: int = 20_000, seed: int = 0
) -> tuple[float, float]:
    """IC de la pendiente de Mincer-Zarnowitz por bootstrap de bloques móviles.

    `coverage_interval` sirve para cualquier **media** por observación —y la de
    QLIKE lo es—, pero una pendiente de regresión no se promedia: hay que
    remuestrear los pares y reajustar. Se usan sus mismas convenciones (bloques
    de largo `block`, semilla fija, 95%) para que los dos ICs del reporte sean
    comparables entre sí.

    La pendiente se calcula en forma cerrada para poder vectorizar las 20.000
    réplicas; es la misma cantidad que `mincer_zarnowitz`, y se verifica sobre
    la muestra completa antes de usarla en vez de darlo por supuesto.
    """
    n = y.size
    if n < 2:
        return (float("nan"), float("nan"))
    ref = mincer_zarnowitz(y, f)[1]
    mine = float(_ols_slope(y, f))
    if np.isfinite(ref) and not np.isclose(ref, mine, rtol=1e-9, atol=1e-12):
        raise AssertionError(
            f"la pendiente cerrada ({mine}) no coincide con mincer_zarnowitz ({ref})"
        )
    block = max(1, min(int(block), n))
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=(n_boot, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1)
    idx = idx[:, :n]
    betas = _ols_slope(y[idx], f[idx], axis=1)
    betas = betas[np.isfinite(betas)]
    if betas.size == 0:
        return (float("nan"), float("nan"))
    lo, hi = np.percentile(betas, [2.5, 97.5])
    return (float(lo), float(hi))


def qlike_per_obs(y: np.ndarray, f: np.ndarray) -> np.ndarray:
    """QLIKE barra a barra, con la misma aritmética que `metrics.qlike` promedia."""
    ratio = np.maximum(y**2, 1e-12) / np.maximum(f**2, 1e-12)
    out: np.ndarray = ratio - np.log(ratio) - 1.0
    return out


# ───────────────────────────── secciones ─────────────────────────────


def report_emission(data: dict[str, Any], log_ts: list[int], bar_ms: int) -> list[str]:
    emitted = data["emitted"]
    lo, hi = int(emitted[0]), int(emitted[-1])
    grid = list(range(lo, hi + bar_ms, bar_ms))
    have = set(emitted.tolist())
    missing = [t for t in grid if t not in have]
    kset = set(data["candles"].tolist())

    out = [
        "EMISIÓN — ¿cuántas barras no se pronosticaron, y por culpa de qué?",
        SEP,
        f"  ventana {_utc(lo)} .. {_utc(hi)} UTC",
        f"  barras en la ventana: {len(grid)}   emitidos: {emitted.size}   "
        f"sin pronóstico: {len(missing)} ({100 * len(missing) / len(grid):.1f}%)",
        f"  descartados por hueco (status='gap'): {data['n_gap']}",
        f"  huecos de klines que quedan en la ventana: {sum(1 for t in grid if t not in kset)}",
    ]
    if not missing:
        return out
    if not log_ts:
        return out + ["", "  sin backend.log: no se puede atribuir la causa"]

    def alive(a: int, b: int) -> bool:
        """¿Hubo alguna línea de log en [a, b)? Prueba de vida del proceso."""
        i = bisect.bisect_left(log_ts, a)
        return i < len(log_ts) and log_ts[i] < b

    tramos: list[list[int]] = []
    for t in missing:
        if not tramos or t - tramos[-1][-1] != bar_ms:
            tramos.append([])
        tramos[-1].append(t)

    n_down = n_mute = 0
    rows: list[str] = []
    for tr in tramos:
        # La barra cierra en t+bar_ms: ahí el analista debería dejar una línea.
        mute = sum(1 for t in tr if alive(t + bar_ms, t + 2 * bar_ms))
        down = len(tr) - mute
        n_down += down
        n_mute += mute
        if mute == 0:
            causa = "proceso abajo o suspendido"
        elif down == 0:
            causa = "ANALISTA MUDO (proceso vivo)"
        else:
            causa = f"mixto: {down} abajo / {mute} mudo"
        rows.append(f"    {_utc(tr[0])} → {_utc(tr[-1])} {len(tr):4d}  {causa}")

    return (
        out
        + ["", f"  {len(tramos)} tramos:"]
        + rows
        + [
            "",
            f"  {n_down:4d} con el proceso abajo o suspendido → pérdida aceptada por diseño",
            f"  {n_mute:4d} con el ANALISTA MUDO y el proceso vivo → la clase de bug del punto 12",
        ]
    )


def report_ceiling(data: dict[str, Any]) -> list[str]:
    f, y, t = data["sigma"], data["realized"], data["open_time"]
    over = int((y > f.max()).sum())
    out = [
        "TECHO DE LA SIGMA — ¿hasta dónde puede pronosticar el modelo?",
        SEP,
        f"  {'':14s} {'mediana':>10} {'p95':>10} {'máximo':>10}",
        f"  {'pronosticada':14s} {np.median(f):>10.5f} "
        f"{np.percentile(f, 95):>10.5f} {f.max():>10.5f}",
        f"  {'realizada':14s} {np.median(y):>10.5f} {np.percentile(y, 95):>10.5f} {y.max():>10.5f}",
        "",
        f"  el máximo realizado es {y.max() / f.max():.2f}× el máximo que el modelo llegó a emitir",
        f"  barras con sigma realizada por encima de ese techo: {over} de "
        f"{y.size} ({100 * over / y.size:.1f}%)",
        f"  razón realizada/pronosticada: media {np.mean(y / f):.3f}   "
        f"mediana {np.median(y / f):.3f}",
        "    (mediana < 1 con media > 1 = encoge hacia el centro: sobra en la",
        "     calma y falta en el pico, que es la dirección que duele apalancado)",
        "",
        "  las 5 barras donde la sigma quedó más corta:",
    ]
    for i in np.argsort(-(y / f))[:5]:
        out.append(
            f"    {_utc(int(t[i]))} UTC  pronosticada {f[i]:.5f}  "
            f"realizada {y[i]:.5f}  ×{y[i] / f[i]:.2f}"
        )
    return out


def report_qlike_tail(data: dict[str, Any]) -> list[str]:
    q = qlike_per_obs(data["realized"], data["sigma"])
    top = np.sort(q)[-max(1, q.size // 10) :]
    return [
        "QLIKE ES UNA ESTADÍSTICA DE COLA",
        SEP,
        f"  media {q.mean():.4f}   mediana por observación {np.median(q):.4f}",
        f"  el decil más alto aporta el {100 * top.sum() / q.sum():.0f}% del total",
        "    Es escala-libre por observación, pero su MEDIA la fija un puñado de",
        "    barras de pico: depende de la ventana tanto como el R², por otra vía.",
    ]


def report_vs_gate(
    data: dict[str, Any], horizon: int, ref: tuple[str, dict[str, Any]] | None
) -> list[str]:
    y, f = data["realized"], data["sigma"]
    q_lo, q_hi = coverage_interval(qlike_per_obs(y, f), horizon)
    beta = mincer_zarnowitz(y, f)[1]
    b_lo, b_hi = beta_interval(y, f, horizon)
    out = [
        "¿HAY DEGRADACIÓN MEDIBLE CONTRA EL GATE?",
        SEP,
        f"  n={y.size} solapados = ~{effective_blocks(y.size, horizon):.0f} "
        f"observaciones independientes (H={horizon}); el IC se calcula sobre eso",
    ]
    ref_q = ref_b = None
    if ref is None:
        out.append("  no se encontró artefacto del gate para esta variante")
    else:
        name, model = ref
        ref_q = model.get("qlike")
        ref_b = model.get("mincer_zarnowitz_beta")
        out.append(f"  referencia del gate: {name}")
    out.append("")
    for label, val, lo, hi, gate in (
        ("QLIKE", qlike(y, f), q_lo, q_hi, ref_q),
        ("MZ beta", beta, b_lo, b_hi, ref_b),
    ):
        line = f"  {label:9s} forward {val:8.4f}   IC95% por bloques [{lo:.4f}, {hi:.4f}]"
        if gate is not None:
            line += f"   gate {gate:.4f} → {'DENTRO' if lo <= gate <= hi else 'FUERA'}"
        out.append(line)
    out.append(
        f"  {'':9s} beta=1 (pronóstico eficiente) → "
        f"{'DENTRO' if b_lo <= 1.0 <= b_hi else 'FUERA'} del IC"
    )
    return out + [
        "",
        "    Si el valor del gate cae DENTRO, el movimiento no es degradación:",
        "    es lo que esta cantidad de bloques independientes puede resolver.",
    ]


def report_subwindows(data: dict[str, Any], chunks: int = 4) -> list[str]:
    y, f, t = data["realized"], data["sigma"], data["open_time"]
    out = [
        f"ESTABILIDAD POR SUB-VENTANA ({chunks} tramos iguales)",
        SEP,
        f"  {'desde':<14} {'n':>5} {'QLIKE':>9} {'MZ beta':>9} {'ratio med':>10}",
    ]
    n = y.size
    for s in range(chunks):
        sl = slice(s * n // chunks, (s + 1) * n // chunks)
        out.append(
            f"  {_utc(int(t[sl][0])):<14} {y[sl].size:>5} "
            f"{qlike(y[sl], f[sl]):>9.4f} "
            f"{mincer_zarnowitz(y[sl], f[sl])[1]:>9.3f} "
            f"{np.median(y[sl] / f[sl]):>10.3f}"
        )
    return out


def main(argv: list[str] | None = None) -> int:
    enable_utf8_stdout()
    p = argparse.ArgumentParser(
        description="Diagnóstico de la corrida forward: emisión, techo de la "
        "sigma, y si un movimiento de métrica es hallazgo o ruido."
    )
    p.add_argument("--symbol", default="ETHUSDT")
    p.add_argument("--timeframe", default="15m")
    p.add_argument("--horizon", type=int, default=16, help="H en barras")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--log", type=Path, default=DEFAULT_LOG)
    p.add_argument("--variant", default="price+deriv", help="variante de features del vivo")
    args = p.parse_args(argv)

    symbol = args.symbol.upper()
    if args.timeframe not in MINUTES:
        print(f"timeframe desconocido: {args.timeframe}")
        return 1
    bar_ms = MINUTES[args.timeframe] * 60_000
    data = load_forward(args.db, symbol, args.timeframe)
    if data["sigma"].size == 0:
        print("no hay pronósticos resueltos: nada que diagnosticar")
        return 1

    vol_kind = "gbm"
    for v in data["versions"]:
        if v and "vol=" in v:
            vol_kind = v.split("vol=")[-1]

    print("=" * 72)
    print(f"DIAGNÓSTICO FORWARD — {symbol} {args.timeframe}")
    print(f"model_version: {', '.join(sorted(v or '—' for v in data['versions']))}")
    if len(data["versions"]) > 1:
        print("⚠ MUESTRA MIXTA: conviven dos model_version en la muestra")
    print("=" * 72)
    for section in (
        report_emission(data, log_activity(args.log), bar_ms),
        report_ceiling(data),
        report_qlike_tail(data),
        report_vs_gate(data, args.horizon, gate_reference(args.variant, vol_kind)),
        report_subwindows(data),
    ):
        print()
        print("\n".join(section))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
