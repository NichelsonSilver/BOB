"""Monitor de liquidez global — Fase M2. Capa de I/O sobre `marea.liquidity`.

    uv run python -m marea.monitor                    # estado de hoy, se guarda
    uv run python -m marea.monitor --as-of 2022-06-17 # lo que se sabía ese día
    uv run python -m marea.monitor --no-save

Lee `marea.db`, arma el reporte y lo persiste en `liquidity_report`, que es lo
que sirve la API. Calcularlo en cada request costaría segundos de CPU en el
mismo proceso que corre el feed; el reporte cambia una vez por semana.

**Qué entra al régimen y qué no.** El compuesto usa cinco componentes de
liquidez con historia larga y point-in-time: liquidez neta de la Fed, M2,
dólar amplio, tasa real y condiciones financieras. Todo lo demás se muestra
como contexto, cada uno con su motivo:

- **Bancos centrales globales en USD**: contiene al balance de la Fed (lo
  contaría dos veces) y mezcla política monetaria con tipo de cambio.
- **Spread high yield**: FRED sirve solo 3 años por licencia ICE; no alcanza
  para un percentil de 3 años de su cambio a 13 semanas. El crédito entra vía
  NFCI, que lo incluye.
- **Pendiente 10a-2a**: su signo es ambiguo para la liquidez (un empinamiento
  por recortes y uno por venta de largos se leen opuesto).
- **VIX**: es riesgo, no liquidez.
- **Chile** y **COT**: lente local y posicionamiento, no liquidez global.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from marea.config import DB_PATH
from marea.data import store
from marea.data.catalog import BCCH_BY_ID, COT_MARKETS
from marea.liquidity import (
    H_LONG_DAYS,
    H_SHORT_DAYS,
    MIN_HISTORY,
    Realtime,
    change,
    combine,
    composite,
    expanding_percentile,
    label,
    product,
    realtime,
    weekly_dates,
)
from marea.pit import OPEN_END, Period

#: Primera fecha de evaluación. WALCL arranca en 2002-12.
GRID_START = date(2003, 1, 3)

#: La liquidez neta exige los repos inversos con dato diario. El ON RRP diario
#: arrancó el 2013-09-23; antes FRED publica "." casi todos los días (medido:
#: 186 de 261 en 2013, 12 por año desde 2014 = feriados). Se exige que las dos
#: puntas del cambio a 13 semanas caigan dentro del régimen diario.
FED_NET_START = date(2014, 1, 1)

#: COT: concentración contra los últimos 3 años, la convención del "COT index".
#: El posicionamiento es un nivel, no un flujo: una ventana expansiva desde 2006
#: compararía contra mercados con otra estructura de participantes.
COT_WINDOW = 156
COT_MIN_HISTORY = 52

Group = Literal["nucleo", "contexto", "chile"]

_FREQ_DAYS = {"D": 7, "W": 7, "M": 31}


@dataclass(frozen=True)
class Indicator:
    key: str
    name: str
    group: Group
    #: +1 si subir es expansivo, −1 si es contractivo, 0 si es ambiguo.
    sign: int
    kind: Literal["log", "diff"]
    unit: str
    #: Factor para mostrar el nivel (millones de USD → miles de millones).
    display_scale: float = 1.0
    pit: Literal["medido", "declarado"] = "medido"
    caveat: str = ""
    start: date | None = None

    @property
    def in_composite(self) -> bool:
        return self.group == "nucleo"


INDICATORS: tuple[Indicator, ...] = (
    Indicator(
        "fed_net",
        "Liquidez neta de la Fed (balance − TGA − RRP)",
        "nucleo",
        +1,
        "log",
        "miles de millones USD",
        display_scale=1e-3,
        caveat="desde 2014: antes no había RRP overnight diario",
        start=FED_NET_START,
    ),
    Indicator(
        "m2",
        "M2 EE.UU.",
        "nucleo",
        +1,
        "log",
        "miles de millones USD",
        caveat="mensual, ~2 meses de rezago y revisiones frecuentes",
    ),
    Indicator(
        "dolar",
        "Dólar amplio",
        "nucleo",
        -1,
        "log",
        "índice 2006=100",
        caveat="un dólar que sube drena liquidez global",
    ),
    Indicator("tasa_real", "Tasa real 10 años (TIPS)", "nucleo", -1, "diff", "%"),
    Indicator(
        "nfci",
        "Condiciones financieras (NFCI)",
        "nucleo",
        -1,
        "diff",
        "índice",
        caveat="re-estimado entero cada semana: se lee la vintage de cada fecha",
    ),
    Indicator(
        "global_cb",
        "Bancos centrales Fed + BCE + BoJ, en USD",
        "contexto",
        +1,
        "log",
        "miles de millones USD",
        display_scale=1e-3,
        caveat="contiene a la Fed y se mueve con EUR y JPY; "
        "BoJ mensual con hasta 65 días de rezago",
    ),
    Indicator(
        "reservas",
        "Reservas bancarias en la Fed",
        "contexto",
        +1,
        "log",
        "miles de millones USD",
        display_scale=1e-3,
    ),
    Indicator(
        "hy",
        "Spread high yield",
        "contexto",
        -1,
        "diff",
        "pp",
        caveat="licencia ICE: FRED sirve 3 años, sin historia para percentil",
    ),
    Indicator(
        "pendiente",
        "Pendiente 10a − 2a",
        "contexto",
        0,
        "diff",
        "pp",
        caveat="signo ambiguo para liquidez: no se clasifica",
    ),
    Indicator("vix", "VIX", "contexto", -1, "diff", "puntos", caveat="riesgo, no liquidez"),
    Indicator("tpm", "TPM Chile", "chile", -1, "diff", "%", pit="declarado"),
    Indicator(
        "usdclp",
        "Dólar observado (CLP por USD)",
        "chile",
        -1,
        "log",
        "CLP",
        pit="declarado",
        caveat="sube = el peso se deprecia",
    ),
    Indicator("cobre", "Cobre refinado BML", "chile", +1, "log", "USD/lb", pit="declarado"),
    Indicator("bcp10", "BCP 10 años", "chile", -1, "diff", "%", pit="declarado"),
)

BY_KEY = {i.key: i for i in INDICATORS}

_SIMPLE_FRED = {
    "m2": "M2SL",
    "dolar": "DTWEXBGS",
    "tasa_real": "DFII10",
    "nfci": "NFCI",
    "reservas": "WRESBAL",
    "hy": "BAMLH0A0HYM2",
    "pendiente": "T10Y2Y",
    "vix": "VIXCLS",
}
_BCCH = {
    "tpm": "F022.TPM.TIN.D001.NO.Z.D",
    "usdclp": "F073.TCO.PRE.Z.D",
    "cobre": "F019.PPB.PRE.100.D",
    "bcp10": "F022.BCLP.TIS.AN10.NO.Z.D",
}


# --------------------------------------------------------------------------- carga


def _fred(conn: sqlite3.Connection, sid: str, dates: Sequence[date]) -> Realtime:
    meta = store.series_meta(conn, sid)
    if meta is None:
        raise LookupError(f"{sid} no está ingestada: correr `python -m marea.data.ingest fred`")
    lag = meta["fallback_lag_days"]
    fv = date.fromisoformat(meta["first_vintage"]) if meta["first_vintage"] else None
    # Un dato vencido no describe el presente: rezago máximo medido + un
    # período de la serie + una semana de margen (feriados, cargas tarde).
    max_age = (lag or 0) + _FREQ_DAYS.get(meta["frequency"], 31) + 7
    return realtime(store.load_periods(conn, sid), dates, max_age, fv, lag)


def _known_from(rows: Sequence[tuple[date, date, float]]) -> list[Period]:
    """Series sin vintages (BCCh, COT): el dato vale desde su fecha de
    disponibilidad declarada y no se revisa."""
    return [Period(d, a, OPEN_END, v) for d, a, v in rows]


def _bcch(conn: sqlite3.Connection, sid: str, dates: Sequence[date]) -> Realtime:
    rows = store.load_bcch(conn, sid)
    if not rows:
        raise LookupError(f"{sid} no está ingestada: correr `python -m marea.data.ingest bcch`")
    return realtime(_known_from(rows), dates, BCCH_BY_ID[sid].lag_days + 14)


def _mask_before(rt: Realtime, dates: Sequence[date], start: date) -> Realtime:
    keep = np.array([d >= start for d in dates])
    return Realtime(
        [o if k else None for o, k in zip(rt.obs_date, keep, strict=True)],
        np.where(keep, rt.level, np.nan),
        np.where(keep, rt.past_short, np.nan),
        np.where(keep, rt.past_long, np.nan),
        rt.exact & keep,
    )


def load_realtime(conn: sqlite3.Connection, dates: Sequence[date]) -> dict[str, Realtime]:
    walcl = _fred(conn, "WALCL", dates)
    tga = _fred(conn, "WTREGEN", dates)
    rrp = _fred(conn, "RRPONTSYD", dates)  # miles de millones: x1000 a millones
    out: dict[str, Realtime] = {
        "fed_net": _mask_before(
            combine([(walcl, 1.0), (tga, -1.0), (rrp, -1000.0)]), dates, FED_NET_START
        ),
    }
    ecb_usd = product(_fred(conn, "ECBASSETSW", dates), _fred(conn, "DEXUSEU", dates))
    # BoJ en 100 millones de JPY / (JPY por USD) -> millones de USD.
    boj_usd = product(_fred(conn, "JPNASSETS", dates), _fred(conn, "DEXJPUS", dates), -1.0)
    out["global_cb"] = combine([(walcl, 1.0), (ecb_usd, 1.0), (boj_usd, 100.0)])
    for key, sid in _SIMPLE_FRED.items():
        out[key] = _fred(conn, sid, dates)
    for key, sid in _BCCH.items():
        out[key] = _bcch(conn, sid, dates)
    return out


# --------------------------------------------------------------------------- cálculo


@dataclass
class IndicatorState:
    spec: Indicator
    rt: Realtime
    chg_short: NDArray[np.float64]
    chg_long: NDArray[np.float64]
    #: Percentil de la señal ORIENTADA (alto = expansivo). Para signo 0, del
    #: cambio crudo.
    pct: NDArray[np.float64]


@dataclass
class Monitor:
    dates: list[date]
    states: dict[str, IndicatorState]
    composite_raw: NDArray[np.float64]
    composite_pct: NDArray[np.float64]
    cot: list[dict[str, Any]] = field(default_factory=list)


def compute(series: dict[str, Realtime], dates: list[date]) -> Monitor:
    states: dict[str, IndicatorState] = {}
    for spec in INDICATORS:
        rt = series[spec.key]
        long_ = change(rt, spec.kind, long=True)
        short = change(rt, spec.kind, long=False)
        oriented = long_ * (spec.sign if spec.sign != 0 else 1)
        states[spec.key] = IndicatorState(spec, rt, short, long_, expanding_percentile(oriented))
    core = [states[s.key].pct for s in INDICATORS if s.in_composite]
    raw = composite(core)
    return Monitor(dates, states, raw, expanding_percentile(raw))


def cot_table(conn: sqlite3.Connection, known_at: date) -> list[dict[str, Any]]:
    """Neto especulativo como % del interés abierto, conocido en `known_at`."""
    out: list[dict[str, Any]] = []
    for m in COT_MARKETS:
        rows = [r for r in store.load_cot(conn, m.code) if r.available_date <= known_at]
        if not rows:
            continue
        net = np.array(
            [
                (r.spec_long - r.spec_short) / r.open_interest if r.open_interest > 0 else np.nan
                for r in rows
            ]
        )
        pct = expanding_percentile(net, COT_MIN_HISTORY, window=COT_WINDOW)
        last = rows[-1]
        out.append(
            {
                "code": m.code,
                "name": m.name,
                "etf": m.etf,
                "report_date": last.report_date.isoformat(),
                "available_date": last.available_date.isoformat(),
                "net_pct_oi": _f(net[-1]),
                "net_pct_oi_4w": _f(net[-5]) if net.size >= 5 else None,
                "pct_3y": _f(pct[-1]),
            }
        )
    return out


# --------------------------------------------------------------------------- reporte


def _f(x: float) -> float | None:
    return float(x) if math.isfinite(x) else None


def _label_since(pcts: NDArray[np.float64], dates: Sequence[date]) -> str | None:
    cur = label(float(pcts[-1]))
    if cur is None:
        return None
    i = len(pcts) - 1
    while i > 0 and label(float(pcts[i - 1])) == cur:
        i -= 1
    return dates[i].isoformat()


def _first_valid(x: NDArray[np.float64], dates: Sequence[date]) -> str | None:
    idx = np.flatnonzero(np.isfinite(x))
    return dates[int(idx[0])].isoformat() if idx.size else None


def report(mon: Monitor) -> dict[str, Any]:
    dates = mon.dates
    last = len(dates) - 1

    def back(weeks: int) -> int:
        return max(0, last - weeks)

    indicators = []
    for s in mon.states.values():
        spec = s.spec
        obs = s.rt.obs_date[last]
        pct = float(s.pct[last])
        indicators.append(
            {
                "key": spec.key,
                "name": spec.name,
                "group": spec.group,
                "in_composite": spec.in_composite,
                "sign": spec.sign,
                "kind": spec.kind,
                "unit": spec.unit,
                "pit": spec.pit,
                "caveat": spec.caveat,
                "obs_date": obs.isoformat() if obs else None,
                "exact": bool(s.rt.exact[last]),
                "level": _f(s.rt.level[last] * spec.display_scale),
                "chg_4w": _f(s.chg_short[last]),
                "chg_13w": _f(s.chg_long[last]),
                "pct": _f(pct),
                "label": label(pct) if spec.sign != 0 else None,
                "label_13w_ago": label(float(s.pct[back(13)])) if spec.sign != 0 else None,
                "pct_from": _first_valid(s.pct, dates),
            }
        )

    cp = mon.composite_pct
    history = [
        {
            "date": d.isoformat(),
            "composite_pct": _f(cp[i]),
            "label": label(float(cp[i])),
            "fed_net": _f(mon.states["fed_net"].rt.level[i] * 1e-3),
            "global_cb": _f(mon.states["global_cb"].rt.level[i] * 1e-3),
            "components": {
                s.spec.key: _f(s.pct[i]) for s in mon.states.values() if s.spec.in_composite
            },
        }
        for i, d in enumerate(dates)
    ]
    return {
        "as_of": dates[last].isoformat(),
        "computed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "method": {
            "signal": f"cambio a {H_LONG_DAYS // 7} semanas, orientado (alto = expansivo)",
            "short_change_weeks": H_SHORT_DAYS // 7,
            "percentile": "contra el pasado estricto, ventana expansiva, point-in-time",
            "min_history_weeks": MIN_HISTORY,
            "composite": "promedio con pesos iguales de los 5 componentes del núcleo; "
            "etiqueta por tercil de su propio percentil",
            "weights": "iguales y declarados, no ajustados a retornos",
            "forecast": False,
        },
        "regime": {
            "label": label(float(cp[last])),
            "composite_pct": _f(cp[last]),
            "composite_raw": _f(mon.composite_raw[last]),
            "label_4w_ago": label(float(cp[back(4)])),
            "label_13w_ago": label(float(cp[back(13)])),
            "since": _label_since(cp, dates),
            "available_from": _first_valid(cp, dates),
        },
        "indicators": indicators,
        "cot": mon.cot,
        "history": history,
    }


def build(conn: sqlite3.Connection, as_of_day: date) -> dict[str, Any]:
    dates = weekly_dates(GRID_START, as_of_day)
    mon = compute(load_realtime(conn, dates), dates)
    mon.cot = cot_table(conn, as_of_day)
    return report(mon)


# --------------------------------------------------------------------------- CLI


def _fmt(v: float | None, kind: str) -> str:
    if v is None:
        return "—"
    return f"{v * 100:+.2f}%" if kind == "log" else f"{v:+.3f}"


def render(rep: dict[str, Any]) -> str:
    r = rep["regime"]
    cp = r["composite_pct"]
    lines = [
        f"MONITOR DE LIQUIDEZ — conocido al {rep['as_of']} (monitoreo, sin pronóstico)",
        "",
        f"Régimen: {r['label'] or 'sin dato'}"
        + (f"  (percentil compuesto {cp:.2f}, desde {r['since']})" if cp is not None else ""),
        f"  hace 4 semanas: {r['label_4w_ago']}   hace 13 semanas: {r['label_13w_ago']}",
        "",
        f"{'indicador':44} {'dato':>10} {'nivel':>12} {'4s':>9} {'13s':>9} {'pct':>5}  lectura",
    ]
    group = None
    for i in rep["indicators"]:
        if i["group"] != group:
            group = i["group"]
            lines.append(f"-- {group}")
        lvl = "—" if i["level"] is None else f"{i['level']:,.2f}"
        pct = "—" if i["pct"] is None else f"{i['pct']:.2f}"
        flag = "" if i["exact"] or i["obs_date"] is None else " ~"
        lines.append(
            f"{i['name'][:44]:44} {i['obs_date'] or '—':>10} {lvl:>12} "
            f"{_fmt(i['chg_4w'], i['kind']):>9} {_fmt(i['chg_13w'], i['kind']):>9} {pct:>5}  "
            f"{i['label'] or ('ambiguo' if i['sign'] == 0 else '—')}{flag}"
        )
    if rep["cot"]:
        lines += [
            "",
            f"{'COT: neto especulativo % OI':32} {'reporte':>10} {'neto':>8} {'pct 3a':>7}",
        ]
        for c in rep["cot"]:
            net = "—" if c["net_pct_oi"] is None else f"{c['net_pct_oi'] * 100:+.1f}%"
            p = "—" if c["pct_3y"] is None else f"{c['pct_3y']:.2f}"
            lines.append(f"{c['name'][:32]:32} {c['report_date']:>10} {net:>8} {p:>7}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    from bob.utils.console import enable_utf8_stdout

    enable_utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--as-of", type=date.fromisoformat, default=None)
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args(argv)

    as_of_day: date = args.as_of or datetime.now(UTC).date()
    conn = store.connect(DB_PATH)
    try:
        rep = build(conn, as_of_day)
        print(render(rep))
        # Una reconstrucción histórica no pisa el reporte vigente que sirve la API.
        if not args.no_save and args.as_of is None:
            store.save_liquidity_report(conn, rep["as_of"], json.dumps(rep))
            print(f"\nguardado en {DB_PATH.name} (liquidity_report)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
