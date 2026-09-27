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


AssetBlock = Literal[
    "acciones", "renta_fija", "reserva_valor", "commodities", "real_estate", "divisas"
]


@dataclass(frozen=True)
class Etf:
    ticker: str
    block: AssetBlock
    why: str
    caveat: str = ""


#: Universo de ETFs (Tiingo). Verificado el 2026-09-27: los 23 existen. La
#: historia COMÚN a todos arranca en 2014-07 (REET); sin REET, en 2011-11
#: (CPER); sin los dos, en 2007-11 (ECH). Eso importa para la potencia de la M3.
ETFS: tuple[Etf, ...] = (
    Etf("SPY", "acciones", "S&P 500"),
    Etf("QQQ", "acciones", "Nasdaq 100"),
    Etf("IWM", "acciones", "Russell 2000 (small caps EE.UU.)"),
    Etf("EFA", "acciones", "Desarrollados ex-EE.UU."),
    Etf("EEM", "acciones", "Emergentes"),
    Etf("FXI", "acciones", "China large caps"),
    Etf("EWJ", "acciones", "Japón"),
    Etf("ECH", "acciones", "Chile", caveat="desde 2007-11"),
    Etf("SHY", "renta_fija", "Tesoro 1-3 años"),
    Etf("IEF", "renta_fija", "Tesoro 7-10 años"),
    Etf("TLT", "renta_fija", "Tesoro 20+ años"),
    Etf("TIP", "renta_fija", "Tesoro indexado a inflación"),
    Etf("LQD", "renta_fija", "Corporativos grado de inversión"),
    Etf("HYG", "renta_fija", "High yield"),
    Etf("GLD", "reserva_valor", "Oro"),
    Etf("SLV", "reserva_valor", "Plata"),
    Etf(
        "USO",
        "commodities",
        "Petróleo",
        caveat="fondo de futuros: el roll lo separa del spot en contango",
    ),
    Etf("UNG", "commodities", "Gas natural", caveat="fondo de futuros: roll severo"),
    Etf("DBA", "commodities", "Agrícolas", caveat="fondo de futuros"),
    Etf("CPER", "commodities", "Cobre", caveat="desde 2011-11; fondo de futuros"),
    Etf("VNQ", "real_estate", "REITs EE.UU.", caveat="proxy declarado: no es inmobiliario físico"),
    Etf("REET", "real_estate", "REITs globales", caveat="desde 2014-07; proxy declarado"),
    Etf("UUP", "divisas", "Dólar (futuros DXY)", caveat="fondo de futuros"),
)

ETF_BY_TICKER: dict[str, Etf] = {e.ticker: e for e in ETFS}


@dataclass(frozen=True)
class CotMarket:
    code: str
    report: Literal["tff", "disagg"]
    name: str
    #: ETF del universo cuyo subyacente es este futuro (para cruzar flujo y precio).
    etf: str | None


#: Mercados del COT, verificados por CÓDIGO el 2026-09-27: 1.059 semanas desde
#: 2006-06 sin huecos (bitcoin: 442 desde 2018-04). Los nombres cambiaron en
#: 2022-02; el código no.
COT_MARKETS: tuple[CotMarket, ...] = (
    CotMarket("13874A", "tff", "E-mini S&P 500", "SPY"),
    CotMarket("042601", "tff", "Tesoro 2 años", "SHY"),
    CotMarket("043602", "tff", "Tesoro 10 años", "IEF"),
    CotMarket("020601", "tff", "Tesoro bono largo", "TLT"),
    CotMarket("099741", "tff", "Euro FX", None),
    CotMarket("097741", "tff", "Yen", None),
    CotMarket("098662", "tff", "Índice dólar (ICE)", "UUP"),
    CotMarket("133741", "tff", "Bitcoin (CME)", None),
    CotMarket("088691", "disagg", "Oro", "GLD"),
    CotMarket("084691", "disagg", "Plata", "SLV"),
    CotMarket("067651", "disagg", "Petróleo WTI", "USO"),
    CotMarket("023651", "disagg", "Gas natural", "UNG"),
    CotMarket("085692", "disagg", "Cobre", "CPER"),
)

COT_BY_CODE: dict[str, CotMarket] = {m.code: m for m in COT_MARKETS}
