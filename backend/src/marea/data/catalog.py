"""Catálogo de series macro. Cada entrada se verificó contra la API de FRED el
2026-09-27 (existencia, frecuencia, rango y última actualización); lo que no
pasó esa verificación no está acá, está en `EXCLUDED` con el motivo. Las nueve
series de la M5.1 se verificaron igual el 2026-10-07 (`docs/MAREA_FUENTES.md`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Block = Literal[
    "liquidez", "tasas", "credito", "actividad", "fx", "riesgo", "commodities", "cripto"
]


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
    FredSeries(
        "T10Y3M",
        "tasas",
        "Pendiente 10a-3m: insumo del probit de recesión de la NY Fed",
    ),
    FredSeries("T5YIE", "tasas", "Inflación esperada a 5 años (breakeven)"),
    FredSeries("T10YIE", "tasas", "Inflación esperada a 10 años (breakeven)"),
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
        "BAA10Y",
        "credito",
        "Spread Baa (Moody's) contra Tesoro 10 años",
        caveat="historia desde 1986: cubre lo que ICE recorta a 3 años",
    ),
    FredSeries(
        "DRTSCILM",
        "credito",
        "SLOOS: % neto de bancos que endurecen el crédito C&I",
        caveat="trimestral: contexto, no señal",
    ),
    FredSeries("TOTLL", "credito", "Préstamos y leasings, bancos comerciales (H.8)"),
    FredSeries(
        "DPSACBW027SBOG",
        "credito",
        "Depósitos, bancos comerciales (H.8)",
        caveat="H.8 tiene quiebres por reclasificación",
    ),
    FredSeries(
        "NFCI",
        "credito",
        "Condiciones financieras (Fed de Chicago)",
        caveat="índice re-estimado ENTERO cada semana: sin vintages es lookahead",
    ),
    # --- Dólar y divisas ---
    # --- Actividad: lo más oportuno del ciclo ---
    FredSeries(
        "SAHMREALTIME",
        "actividad",
        "Regla de Sahm en tiempo real",
        caveat="cada valor se calculó con el desempleo publicado en su momento",
    ),
    FredSeries("ICSA", "actividad", "Solicitudes iniciales de desempleo (semanal)"),
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


@dataclass(frozen=True)
class BcchSeries:
    id: str
    name: str
    start: str  # primera observación según el catálogo del BCCh
    #: Días entre la fecha del dato y cuándo se conoce. DECLARADO, no medido:
    #: el BCCh no publica vintages. Ver la razón de cada uno.
    lag_days: int
    lag_why: str


#: Series diarias del BCCh, códigos verificados contra `SearchSeries` el
#: 2026-09-27. El IPSA no está en la base diaria: la renta variable chilena
#: sale de ECH (Tiingo).
BCCH_SERIES: tuple[BcchSeries, ...] = (
    BcchSeries(
        "F073.TCO.PRE.Z.D",
        "Dólar observado (CLP/USD)",
        "1982-08-09",
        0,
        "el valor del día d se calcula con el día hábil anterior y se publica antes de d: "
        "el 27-09 (domingo) ya estaba el del 28-09",
    ),
    BcchSeries(
        "F022.TPM.TIN.D001.NO.Z.D",
        "TPM Chile",
        "1997-02-07",
        0,
        "la tasa vigente en d se anuncia en la reunión previa",
    ),
    BcchSeries(
        "F019.PPB.PRE.100.D",
        "Cobre refinado BML (USD/lb)",
        "2012-10-05",
        0,
        "el BCCh lo estampa como el dólar observado: el 27-09 ya estaba el del 28-09",
    ),
    BcchSeries(
        "F022.BCLP.TIS.AN10.NO.Z.D",
        "BCP 10 años, mercado secundario",
        "2004-07-15",
        1,
        "tasa de transacciones del día d: se conoce al cierre, se usa desde d+1",
    ),
    BcchSeries(
        "F019.TPM.TIN.20.D",
        "Tasa de política BCE",
        "1998-12-23",
        1,
        "la carga el BCCh; margen de un día por el tiempo de carga",
    ),
    BcchSeries(
        "F019.TPM.TIN.30.D",
        "Tasa de política Banco de Japón",
        "2008-10-31",
        1,
        "la carga el BCCh; margen de un día por el tiempo de carga",
    ),
)

BCCH_BY_ID: dict[str, BcchSeries] = {s.id: s for s in BCCH_SERIES}


@dataclass(frozen=True)
class StableSeries:
    id: str
    #: Id de DefiLlama de la stablecoin; `None` para los totales.
    llama_id: int | None
    why: str
    caveat: str = ""


#: Oferta de stablecoins (DefiLlama), verificada el 2026-10-07. Los tokens
#: muertos se incluyen a propósito: BUSD llegó a 23,5 mil millones (2022-11) y
#: UST a 18,8 (2022-05); sin ellos la composición histórica miente.
STABLE_SERIES: tuple[StableSeries, ...] = (
    StableSeries(
        "STABLE_USD",
        None,
        "Oferta total de stablecoins atadas al dólar, en USD",
        caveat="incluye algorítmicas: el colapso de UST (2022-05) se lee como salida",
    ),
    StableSeries("STABLE_ALL", None, "Oferta total de stablecoins, todas las monedas, en USD"),
    StableSeries("USDT", 1, "Tether"),
    StableSeries("USDC", 2, "USD Coin"),
    StableSeries("DAI", 5, "Dai (MakerDAO)", caveat="migra parcialmente a USDS desde 2024-09"),
    StableSeries("USDS", 209, "Sky Dollar, sucesora de DAI", caveat="desde 2024-09"),
    StableSeries(
        "USDE", 146, "Ethena USDe", caveat="sintética: respaldada por posiciones en perpetuos"
    ),
    StableSeries(
        "BUSD", 4, "Binance USD", caveat="emisión detenida en 2023-02 por orden del NYDFS"
    ),
    StableSeries("UST", 3, "TerraUSD (hoy USTC)", caveat="algorítmica; colapsó en 2022-05"),
)

STABLE_BY_ID: dict[str, StableSeries] = {s.id: s for s in STABLE_SERIES}
