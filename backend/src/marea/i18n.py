"""Inglés para lo que MAREA sirve en español (M6.8, paso 2). PURO.

Los reportes de MAREA se calculan y se guardan en español (`marea.monitor`
deja el JSON en la DB; `strength` y el veredicto M3 arman sus textos al
pedirlos). Traducirlos **al servirlos** deja intacto todo lo que ya está
calculado y versionado: el artefacto de la M3, el reporte guardado y las
pruebas que reproducen la historia bit a bit no se enteran del idioma.

La traducción va por **clave estable** (la `key` del indicador, el código COT,
el ticker, la `block` de la clase), no por el texto: si un texto en español
cambia, la versión en inglés queda vieja pero no se cruza con otra fila. Lo
que no tiene traducción se deja como está — mejor en español que vacío — y
hay un test que exige que todo lo que existe hoy la tenga.

**Ninguna cifra pasa por acá.** Solo se reemplazan textos.
"""

from __future__ import annotations

import copy
from typing import Any

#: Indicadores del monitor: key → (nombre, nota). La nota es None si no hay.
INDICATORS_EN: dict[str, tuple[str, str | None]] = {
    "fed_net": ("Fed net liquidity (balance sheet − TGA − RRP)", "since 2014: before that there was no daily overnight RRP"),
    "m2": ("US M2", "monthly, ~2 months of lag and frequent revisions"),
    "dolar": ("Broad dollar", "a rising dollar drains global liquidity"),
    "tasa_real": ("10-year real rate (TIPS)", None),
    "nfci": ("Financial conditions (NFCI)", "fully re-estimated every week: each date's vintage is read"),
    "global_cb": (
        "Central banks Fed + ECB + BoJ, in USD",
        "includes the Fed and moves with EUR and JPY; BoJ monthly with up to 65 days of lag",
    ),
    "reservas": ("Bank reserves at the Fed", None),
    "hy": ("High-yield spread", "ICE license: FRED serves 3 years, no history for a percentile"),
    "pendiente": ("10y − 2y slope", "ambiguous sign for liquidity: not classified"),
    "vix": ("VIX", "risk, not liquidity"),
    "baa": ("Baa − 10-year Treasury spread", "since 1986: the credit history that ICE's HY lacks"),
    "sloos": ("Banks tightening C&I credit (SLOOS)", "quarterly: changes 4 times a year"),
    "credito_bancario": ("Bank loans (H.8)", None),
    "depositos": ("Bank deposits (H.8)", "breaks from reclassification"),
    "stablecoins": ("Stablecoin supply in USD", "liquidity flowing into crypto; point-in-time only since 2026-10-07"),
    "breakeven5": ("5-year expected inflation", None),
    "breakeven10": ("10-year expected inflation", None),
    "sahm": ("Sahm rule (real time)", "cycle, not liquidity; 0.5 or more has signalled recession"),
    "claims": ("Initial jobless claims", "cycle, not liquidity"),
    "tpm": ("Chile policy rate (TPM)", None),
    "usdclp": ("Observed dollar (CLP per USD)", "up = the peso depreciates"),
    "cobre": ("Refined copper (LME)", None),
    "bcp10": ("BCP 10-year (Chile)", None),
}

UNITS_EN: dict[str, str] = {
    "miles de millones USD": "billion USD",
    "índice 2006=100": "index 2006=100",
    "índice": "index",
    "puntos": "points",
    "% neto": "% net",
    "solicitudes": "claims",
}

#: Mercados COT: código CFTC → nombre.
COT_EN: dict[str, str] = {
    "13874A": "E-mini S&P 500",
    "042601": "2-year Treasury",
    "043602": "10-year Treasury",
    "020601": "Treasury bond (long)",
    "099741": "Euro FX",
    "097741": "Yen",
    "098662": "Dollar index (ICE)",
    "133741": "Bitcoin (CME)",
    "088691": "Gold",
    "084691": "Silver",
    "067651": "WTI crude oil",
    "023651": "Natural gas",
    "085692": "Copper",
}

#: Clases de activo de `strength`: block → nombre.
CLASSES_EN: dict[str, str] = {
    "acciones": "Equities",
    "renta_fija": "Bonds",
    "reserva_valor": "Gold & silver",
    "commodities": "Commodities",
    "real_estate": "Real estate (REITs)",
    "divisas": "US dollar",
}

#: ETFs: ticker → (nombre, nota).
ETFS_EN: dict[str, tuple[str, str | None]] = {
    "SPY": ("S&P 500", None),
    "QQQ": ("Nasdaq 100", None),
    "IWM": ("Russell 2000 (US small caps)", None),
    "EFA": ("Developed ex-US", None),
    "EEM": ("Emerging markets", None),
    "FXI": ("China large caps", None),
    "EWJ": ("Japan", None),
    "ECH": ("Chile", "since 2007-11"),
    "SHY": ("Treasury 1-3 years", None),
    "IEF": ("Treasury 7-10 years", None),
    "TLT": ("Treasury 20+ years", None),
    "TIP": ("Inflation-indexed Treasury", None),
    "LQD": ("Investment-grade corporates", None),
    "HYG": ("High yield", None),
    "GLD": ("Gold", None),
    "SLV": ("Silver", None),
    "USO": ("Oil", "futures fund: the roll separates it from spot in contango"),
    "UNG": ("Natural gas", "futures fund: severe roll"),
    "DBA": ("Agriculture", "futures fund"),
    "CPER": ("Copper", "since 2011-11; futures fund"),
    "VNQ": ("US REITs", "declared proxy: not physical real estate"),
    "REET": ("Global REITs", "since 2014-07; declared proxy"),
    "UUP": ("Dollar (DXY futures)", "futures fund"),
}

STRENGTH_METHOD_EN: dict[str, str] = {
    "measure": "ETF total return minus the universe median over the same window",
    "class": "simple average of the class's ETFs",
    "reference": "median of the ETFs with complete data in the window",
    "missing": "an ETF with no return on some date in the window is left out, not filled in",
}

#: Lectura del veredicto M3 (`ranking.py`), por su texto exacto.
READING_EN: dict[str, str] = {
    "no hay orden detectable a esta potencia": "no detectable ranking at this power",
    "el ranking trivial captura lo que hay": "the trivial ranking captures what there is",
    "viable: habilita validación forward, no operar": "viable: enables forward validation, not trading",
}

SPY_SIGN_EN: dict[str, str] = {"SPY sube": "SPY up", "SPY cae": "SPY down"}


def _liquidity_method_en(method: dict[str, Any]) -> dict[str, Any]:
    out = dict(method)
    weeks = method.get("short_change_weeks")
    # El monitor escribe "cambio a 13 semanas"; el número se conserva tal cual.
    signal = str(method.get("signal", ""))
    digits = "".join(ch for ch in signal if ch.isdigit())
    out["signal"] = f"{digits}-week change, oriented (high = expansionary)" if digits else signal
    out["percentile"] = "against the strict past, expanding window, point-in-time"
    out["composite"] = (
        "equally weighted average of the 5 core components; label by tercile of its own percentile"
    )
    out["weights"] = "equal and declared, not fitted to returns"
    if weeks is not None:
        out["short_change_weeks"] = weeks
    return out


def liquidity(rep: dict[str, Any], lang: str) -> dict[str, Any]:
    """El reporte de liquidez en el idioma pedido. Las etiquetas de régimen
    (`expansivo`, `neutro`, `contractivo`) son códigos y no se tocan: las
    traduce quien las dibuja."""
    if lang != "en":
        return rep
    out = copy.deepcopy(rep)
    for ind in out.get("indicators", []):
        en = INDICATORS_EN.get(ind.get("key", ""))
        if en:
            ind["name"] = en[0]
            if ind.get("caveat") and en[1]:
                ind["caveat"] = en[1]
        if ind.get("unit") in UNITS_EN:
            ind["unit"] = UNITS_EN[ind["unit"]]
    for row in out.get("cot", []):
        row["name"] = COT_EN.get(row.get("code", ""), row.get("name"))
    if isinstance(out.get("method"), dict):
        out["method"] = _liquidity_method_en(out["method"])
    return out


def strength(rep: dict[str, Any], lang: str) -> dict[str, Any]:
    if lang != "en":
        return rep
    out = copy.deepcopy(rep)
    for c in out.get("classes", []):
        c["label"] = CLASSES_EN.get(c.get("block", ""), c.get("label"))
    for e in out.get("etfs", []):
        en = ETFS_EN.get(e.get("ticker", ""))
        if en:
            e["name"] = en[0]
            if e.get("caveat") and en[1]:
                e["caveat"] = en[1]
    if isinstance(out.get("method"), dict):
        out["method"] = {**out["method"], **STRENGTH_METHOD_EN}
    return out


def ranking_verdict(v: dict[str, Any], lang: str) -> dict[str, Any]:
    if lang != "en":
        return v
    out = dict(v)
    out["reading"] = READING_EN.get(str(v.get("reading")), v.get("reading"))
    sign = v.get("ic_by_spy_sign")
    if isinstance(sign, dict):
        out["ic_by_spy_sign"] = {SPY_SIGN_EN.get(k, k): s for k, s in sign.items()}
    return out
