"""Corre la medición de potencia de la M3 y la congela en artefacto.

    uv run python -m marea.power_run

Criterio y método: `marea.power` (fijado y commiteado antes de correr esto).
El artefacto lleva solo métricas, nunca series de precios: la licencia de
Tiingo permite mostrar lo calculado, no lo crudo.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from marea import power
from marea.config import DB_PATH
from marea.data import store
from marea.data.catalog import ETFS
from marea.returns import total_returns

ARTIFACTS = Path(__file__).resolve().parent.parent.parent / "artifacts"

#: (nombre, tickers excluidos). La primaria va primero y es la única que decide.
VARIANTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("primaria: 23 ETFs", ()),
    ("22 ETFs sin REET", ("REET",)),
    ("21 ETFs sin REET ni CPER", ("REET", "CPER")),
)
HORIZONS = (1, 4, 13)
PRIMARY_H = 4


def peak_memory_mb() -> float | None:
    """Working set pico del proceso (Windows). None en otros sistemas."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    c = Counters()
    c.cb = ctypes.sizeof(c)
    # Sin declarar los tipos, ctypes trunca el pseudo-handle (-1) a 32 bits y
    # la llamada falla con ERROR_INVALID_HANDLE: el número saldría "no medido".
    kernel32 = ctypes.WinDLL("kernel32")
    psapi = ctypes.WinDLL("psapi")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(Counters),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    ok = psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.PeakWorkingSetSize / 2**20 if ok else None


def load_daily(tickers: list[str]) -> tuple[list[date], np.ndarray, int]:
    """Retornos totales diarios en el calendario COMÚN a todos los tickers.

    Devuelve (días, matriz días×activos, días descartados). Un día que falta en
    un solo ETF partiría su retorno en dos; con 0 huecos medidos en la M1 no
    debería descartarse ninguno, y si pasa se reporta.
    """
    conn = store.connect(DB_PATH)
    try:
        per: dict[str, dict[date, float]] = {}
        for t in tickers:
            bars = store.load_bars(conn, t)
            r = total_returns(
                np.array([b.close for b in bars]),
                np.array([b.div_cash for b in bars]),
                np.array([b.split_factor for b in bars]),
            )
            per[t] = {b.day: float(x) for b, x in zip(bars[1:], r[1:], strict=True)}
    finally:
        conn.close()
    start = max(min(d.keys()) for d in per.values())
    union = sorted({d for m in per.values() for d in m if d >= start})
    common = [d for d in union if all(d in per[t] for t in tickers)]
    mat = np.array([[per[t][d] for t in tickers] for d in common])
    return common, mat, len(union) - len(common)


def measure(tickers: list[str]) -> dict[str, Any]:
    days, daily, dropped = load_daily(tickers)
    ends = power.weekly_grid(days)
    weekly = power.compound(daily, ends)
    week_days = [days[i] for i in ends[1:]]

    out: dict[str, Any] = {
        "n_assets": len(tickers),
        "first_week": week_days[0].isoformat(),
        "last_week": week_days[-1].isoformat(),
        "n_weeks": len(week_days),
        "days_dropped": dropped,
        "effective_n_raw": asdict(power.effective_n(weekly)),
        "effective_n_demeaned": asdict(power.effective_n(power.demean_rows(weekly))),
        "null_ic_sd": 1.0 / np.sqrt(len(tickers) - 1),
        "horizons": {},
    }
    signals = {"momentum_12_1": power.momentum(weekly), "reversion_4s": power.reversal(weekly)}
    for h in HORIZONS:
        fwd = power.forward_returns(weekly, h)
        per_signal: dict[str, Any] = {}
        for name, sig in signals.items():
            ic = power.ic_by_date(sig, fwd)
            v = ic[np.isfinite(ic)]
            # Ciego: solo dispersión y dependencia, NUNCA la media (ver marea.power).
            per_signal[name] = {
                "n_dates": int(v.size),
                "ic_sd": float(np.std(v, ddof=1)),
                "ic_lag1_autocorr": float(np.corrcoef(v[:-1], v[1:])[0, 1]),
                "se_block": power.block_se(ic, h),
            }
        se = max(s["se_block"] for s in per_signal.values())
        mde = power.MDE_FACTOR * se
        out["horizons"][str(h)] = {
            "signals": per_signal,
            "se_used": se,
            "mde": mde,
            "independent_blocks": per_signal["momentum_12_1"]["n_dates"] / h,
            "verdict": power.verdict(mde),
        }
    return out


def render(rep: dict[str, Any]) -> str:
    lines = [
        "MEDICIÓN DE POTENCIA — RANKING MACRO (M3, paso 1)",
        f"run {rep['run_id']} · criterio: marea/power.py (commit d48db5c, previo a esta corrida)",
        f"MDE = {power.MDE_FACTOR:.2f} · SE (α=0,05 dos colas, potencia 80%) · "
        f"holgada ≤ {power.MDE_AMPLE} · sin potencia > {power.MDE_MAX}",
        "Ciego: no se reporta la media del IC de las señales triviales.",
        "",
    ]
    for name, v in rep["variants"].items():
        en_r, en_d = v["effective_n_raw"], v["effective_n_demeaned"]
        lines += [
            f"== {name}  ({v['first_week']} .. {v['last_week']}, {v['n_weeks']} semanas,"
            f" {v['days_dropped']} días descartados)",
            f"   N efectivo crudo:     PR {en_r['participation_ratio']:.2f} · PC1 "
            f"{en_r['pc1_share']:.1%} · 80% con {en_r['n80']} · 90% con {en_r['n90']}",
            f"   N efectivo demediado: PR {en_d['participation_ratio']:.2f} · PC1 "
            f"{en_d['pc1_share']:.1%} · 80% con {en_d['n80']} · 90% con {en_d['n90']}",
            f"   sd del IC bajo el nulo de permutación: {v['null_ic_sd']:.3f}",
            f"   {'H':>3} {'fechas':>7} {'bloques':>8} {'sd mom':>7} {'sd rev':>7} "
            f"{'ρ1 mom':>7} {'SE':>7} {'MDE':>7}  veredicto",
        ]
        for h, hv in v["horizons"].items():
            m, r = hv["signals"]["momentum_12_1"], hv["signals"]["reversion_4s"]
            mark = "  <- decide" if name.startswith("primaria") and int(h) == PRIMARY_H else ""
            lines.append(
                f"   {h:>3} {m['n_dates']:>7} {hv['independent_blocks']:>8.0f} "
                f"{m['ic_sd']:>7.3f} {r['ic_sd']:>7.3f} {m['ic_lag1_autocorr']:>7.2f} "
                f"{hv['se_used']:>7.4f} {hv['mde']:>7.3f}  {hv['verdict']}{mark}"
            )
        lines.append("")
    p = rep["primary"]
    lines += [
        f"VEREDICTO (primaria, H={PRIMARY_H}): MDE {p['mde']:.3f} -> {p['verdict'].upper()}",
        f"recursos: {rep['runtime_s']:.1f} s · RAM pico "
        + (f"{rep['peak_memory_mb']:.0f} MB" if rep["peak_memory_mb"] else "no medida"),
    ]
    return "\n".join(lines)


def main() -> int:
    from bob.utils.console import enable_utf8_stdout

    enable_utf8_stdout()
    t0 = time.perf_counter()
    all_tickers = [e.ticker for e in ETFS]
    variants = {
        name: measure([t for t in all_tickers if t not in excluded]) for name, excluded in VARIANTS
    }
    primary_name = VARIANTS[0][0]
    prim = variants[primary_name]["horizons"][str(PRIMARY_H)]
    run_id = "marea-power-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rep: dict[str, Any] = {
        "run_id": run_id,
        "criterion_commit": "d48db5c",
        "primary": {"variant": primary_name, "horizon_weeks": PRIMARY_H, **prim},
        "variants": variants,
        "runtime_s": time.perf_counter() - t0,
        "peak_memory_mb": peak_memory_mb(),
    }
    text = render(rep)
    print(text)
    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS / f"{run_id}.txt").write_text(text + "\n", encoding="utf-8")
    (ARTIFACTS / f"{run_id}.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    print(f"\nartefacto: artifacts/{run_id}.txt|json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
