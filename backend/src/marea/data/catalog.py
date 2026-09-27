"""Catálogo de series macro. Cada entrada se verificó contra la API de FRED el
2026-09-27 (existencia, frecuencia, rango y última actualización); lo que no
pasó esa verificación no está acá, está en `EXCLUDED` con el motivo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Block = Literal["liquidez", "tasas", "credito", "fx", "riesgo", "commodities", "cripto"]


@dataclass(frozen=True)
class FredSeries:
    id: str
    block: Block
    why: str
    #: Límite conocido de la serie que quien la lea tiene que tener a la vista.
    caveat: str = ""


FRED_SERIES: tuple[FredSeries, ...] = (
    # --- Liquidez: balances de bancos centrales y drenajes ---
    FredSeries("WALCL", "liquidez", "Activos totales de la Fed (semanal, miércoles)"),
    FredSeries("WTREGEN", "liquidez", "Cuenta del Tesoro en la Fed (TGA): drena liquidez"),
    FredSeries("RRPONTSYD", "liquidez", "Repos inversos overnight: drena liquidez"),
    FredSeries("WRESBAL", "liquidez", "Reservas bancarias en la Fed"),
    FredSeries("M2SL", "liquidez", "M2 de EE.UU.", caveat="se revisa casi todas las semanas"),
    FredSeries(
        "ECBASSETSW",
        "liquidez",
        "Activos del BCE (EUR; convertir con DEXUSEU)",
        caveat="en millones de EUR",
    ),
    FredSeries(
        "JPNASSETS",
        "liquidez",
        "Activos del Banco de Japón (JPY; convertir con DEXJPUS)",
        caveat="mensual, en 100 millones de JPY",
    ),
    # --- Tasas ---
    FredSeries("DFF", "tasas", "Tasa efectiva de fondos federales"),
    FredSeries("DGS2", "tasas", "Tesoro 2 años"),
    FredSeries("DGS10", "tasas", "Tesoro 10 años"),
    FredSeries("DFII10", "tasas", "Tasa real 10 años (TIPS)"),
    FredSeries("T10Y2Y", "tasas", "Pendiente 10a-2a"),
    # --- Crédito y condiciones financieras ---
    FredSeries(
        "BAMLH0A0HYM2",
        "credito",
        "Spread high yield (ICE BofA)",
        caveat="licencia ICE: FRED solo sirve los últimos 3 años",
    ),
    FredSeries(
        "BAMLC0A0CM",
        "credito",
        "Spread grado de inversión (ICE BofA)",
        caveat="licencia ICE: FRED solo sirve los últimos 3 años",
    ),
    FredSeries(
        "NFCI",
        "credito",
        "Condiciones financieras (Fed de Chicago)",
        caveat="índice re-estimado ENTERO cada semana: sin vintages es lookahead",
    ),
    # --- Dólar y divisas ---
    FredSeries("DTWEXBGS", "fx", "Dólar amplio nominal: un dólar fuerte drena liquidez global"),
    FredSeries(
        "DEXUSEU", "fx", "USD por EUR", caveat="H.10: se publica semanal, ~6 días de rezago"
    ),
    FredSeries("DEXJPUS", "fx", "JPY por USD", caveat="H.10: se publica semanal"),
    FredSeries("DEXCHUS", "fx", "CNY por USD", caveat="H.10: se publica semanal"),
    # --- Riesgo ---
    FredSeries("VIXCLS", "riesgo", "VIX"),
    # --- Commodities ---
    FredSeries("DCOILWTICO", "commodities", "Petróleo WTI spot"),
    FredSeries("DHHNGSP", "commodities", "Gas Henry Hub spot"),
    FredSeries(
        "PCOPPUSDM",
        "commodities",
        "Cobre, precio global (FMI)",
        caveat="mensual; el diario sale de Tiingo/BCCh",
    ),
    # --- Cripto (referencia macro; el detalle vive en el módulo bob) ---
    FredSeries("CBBTCUSD", "cripto", "Bitcoin (Coinbase)"),
    FredSeries("CBETHUSD", "cripto", "Ethereum (Coinbase)"),
)

#: Candidatas descartadas en la verificación, con el motivo. Se dejan escritas
#: para que nadie las "agregue" sin saber por qué no están.
EXCLUDED: dict[str, str] = {
    "MYAGM2EZM196N": "M2 eurozona: discontinuada en FRED desde 2017-03",
    "MYAGM2CNM189N": "M2 China: discontinuada en FRED desde 2019-08",
    "MYAGM2JPM189S": "M2 Japón: discontinuada en FRED desde 2017-02",
    "SP500": "licencia S&P: FRED solo sirve 10 años; el índice sale de Tiingo (SPY)",
}

BY_ID: dict[str, FredSeries] = {s.id: s for s in FRED_SERIES}
