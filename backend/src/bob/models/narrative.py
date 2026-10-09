"""Lectura del mercado en lenguaje natural para la página Signal. PURO.

Convierte los números que BOB ya calcula en un parte escrito, al estilo de
un pronóstico del tiempo. **No hay un LLM en este camino**, por dos
decisiones fijadas el 26-09 en el CLAUDE.md: no hay LLM en el cálculo ni
costo de API en runtime, y una cifra de un texto que no sale del código es
un bug tan grave como un lookahead. Cada frase es una plantilla que se llena
con una cifra que se puede rastrear hasta su fuente.

Cada sección declara su **respaldo**, porque no todo pesa lo mismo:

- `validado`: volatilidad, filtro de régimen y cono, que pasaron el gate y
  el forward.
- `descriptivo`: precio contra medias, RSI y derivados. Dicen dónde está el
  mercado, no hacia dónde va; como features, no anticiparon dirección.
- `sin ventaja medida`: zonas de liquidez y order blocks. Las reglas que las
  operan se midieron (05-10) y no superaron al azar.

El texto nunca dice "sube" o "baja" como pronóstico. Cuando describe una
tendencia, habla del pasado ("viene cayendo"), no del futuro.

**Idioma (M6.8).** `lang` elige inglés o español; el default es `"es"`, así
que quien la llamaba antes no nota nada. Las dos plantillas van juntas en cada
frase y llevan **las mismas cifras**: solo cambia el texto. `basis` es un
código y no se traduce; lo traduce quien lo dibuja.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

VALIDATED = "validado"
DESCRIPTIVE = "descriptivo"
NO_EDGE = "sin ventaja medida"
SCOPE = "alcance"

Lang = Literal["es", "en"]


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    price: float
    horizon_hours: float
    sigma_horizon: float  # sigma pronosticada del horizonte (fracción)
    sigma_ratio: float | None  # pronosticada / realizada reciente
    regime_percentile: float | None
    regime_elevated: bool | None
    cone80: tuple[float, float] | None
    cone95: tuple[float, float] | None
    change_24h: float | None  # fracción
    #: Mapas técnicos (salida de `signals.technical.technical_map`).
    map_15m: dict[str, Any]
    map_1h: dict[str, Any] | None
    #: Vector de features del pronóstico (regla 10), para los derivados.
    features: dict[str, float] = field(default_factory=dict)
    #: Expectancy medida de las reglas técnicas en 15m, si hay artefacto.
    technical_mean_r: dict[str, float] = field(default_factory=dict)


def _pct(x: float, digits: int = 2) -> str:
    return f"{x * 100:.{digits}f}%"


def _signed_pct(x: float, digits: int = 2) -> str:
    s = _pct(abs(x), digits)
    return f"+{s}" if x > 0 else (f"−{s}" if x < 0 else s)


def _px(x: float) -> str:
    # 2,686.29 — la misma convención que `lib/format.ts` (M6.5): punto decimal
    # en todo el dashboard. Con coma, el texto decía "2.686,29" al lado de un
    # encabezado que decía "2,686.29".
    return f"{x:,.2f}"


def _num(x: float, digits: int = 2) -> str:
    return f"{x:.{digits}f}"


def _f(features: dict[str, float], key: str) -> float | None:
    v = features.get(key)
    return float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None


def _t(lang: Lang, en: str, es: str) -> str:
    return en if lang == "en" else es


def _plural(n: int, lang: Lang, en: str, es: str) -> str:
    """'1 touch' / '2 touches', '1 toque' / '2 toques'."""
    word = _t(lang, en, es)
    if n == 1:
        return word
    return word + ("es" if lang == "en" and word.endswith("h") else "s")


def _vol_word(percentile: float | None, lang: Lang = "es") -> str:
    if percentile is None:
        return _t(lang, "with no reference", "sin referencia")
    if percentile < 20:
        return _t(lang, "low", "baja")
    if percentile < 60:
        return _t(lang, "normal", "normal")
    if percentile < 80:
        return _t(lang, "somewhat high", "algo alta")
    return _t(lang, "high", "alta")


# --------------------------------------------------------------------------- #
# Secciones
# --------------------------------------------------------------------------- #
def _volatility(s: MarketSnapshot, lang: Lang) -> dict[str, Any]:
    move = s.sigma_horizon * s.price
    h = f"{s.horizon_hours:.0f}"
    parts = [
        _t(
            lang,
            f"Over the next {h} hours a typical move of ±{_pct(s.sigma_horizon)} "
            f"(about ±{_px(move)} USD) is expected.",
            f"Para las próximas {h} horas se espera un movimiento típico de "
            f"±{_pct(s.sigma_horizon)} (unos ±{_px(move)} USD).",
        )
    ]
    if s.regime_percentile is not None:
        vol = _vol_word(s.regime_percentile, lang)
        p = f"{s.regime_percentile:.0f}"
        parts.append(
            _t(
                lang,
                f"That is {vol} volatility for {s.symbol}: it sits at percentile {p} of what "
                "the model usually forecasts.",
                f"Es una volatilidad {vol} para {s.symbol}: está en el percentil {p} de lo "
                "que el modelo suele pronosticar.",
            )
        )
    if s.sigma_ratio is not None:
        if s.sigma_ratio > 1.15:
            parts.append(
                _t(
                    lang,
                    "More movement is coming than in the last few hours: the market should "
                    "wake up relative to the recent past.",
                    "Viene más movimiento que el de las últimas horas: el mercado debería "
                    "despertarse respecto de lo reciente.",
                )
            )
        elif s.sigma_ratio < 0.85:
            parts.append(
                _t(
                    lang,
                    "Less movement is expected than in the last few hours: the market should "
                    "calm down relative to the recent past.",
                    "Se espera menos movimiento que el de las últimas horas: el mercado debería "
                    "calmarse respecto de lo reciente.",
                )
            )
        else:
            parts.append(
                _t(
                    lang,
                    "A pace similar to the last few hours is expected.",
                    "Se espera un ritmo parecido al de las últimas horas.",
                )
            )
    if s.cone80 is not None:
        lo, hi = s.cone80
        parts.append(
            _t(
                lang,
                f"With the cone's measured coverage (80%), the price should be between "
                f"{_px(lo)} and {_px(hi)} at the end of the horizon.",
                f"Con la cobertura medida del cono (80%), el precio debería estar entre "
                f"{_px(lo)} y {_px(hi)} al cierre del horizonte.",
            )
        )
    if s.regime_elevated:
        parts.append(
            _t(
                lang,
                "The regime filter is ELEVATED: this is the kind of moment when liquidations "
                "cluster and the same stop gets hit much more often. The prudent move is to "
                "wait or to enter with less risk.",
                "El filtro de régimen está ELEVADO: es el tipo de momento en que se concentran "
                "las liquidaciones y en que el mismo stop salta bastante más seguido. Lo prudente "
                "es esperar o entrar con menos riesgo.",
            )
        )
    elif s.regime_elevated is not None:
        parts.append(
            _t(
                lang,
                "The regime filter is normal: no volatility alert.",
                "El filtro de régimen está en normal: no hay alerta de volatilidad.",
            )
        )
    tone = "warn" if s.regime_elevated else "ok"
    return {
        "title": _t(lang, "Volatility and risk", "Volatilidad y riesgo"),
        "basis": VALIDATED,
        "tone": tone,
        "text": " ".join(parts),
    }


def _trend(s: MarketSnapshot, lang: Lang) -> dict[str, Any]:
    parts: list[str] = []
    if s.change_24h is not None:
        chg = _pct(abs(s.change_24h))
        up = s.change_24h > 0
        parts.append(
            _t(
                lang,
                f"Over the last 24 hours {s.symbol} is {'up' if up else 'down'} {chg}.",
                f"En las últimas 24 horas {s.symbol} {'sube' if up else 'cae'} {chg}.",
            )
        )
    m = s.map_1h or s.map_15m
    tf = "1h" if s.map_1h else "15m"
    emas = {int(k): float(v) for k, v in m["emas"].items()}
    above = [k for k, v in sorted(emas.items()) if s.price > v]
    if len(above) == len(emas):
        parts.append(
            _t(
                lang,
                f"The price is above its three {tf} moving averages (EMA 20, 50 and 200): "
                "the recent structure is bullish.",
                f"El precio está por encima de sus tres medias de {tf} (EMA 20, 50 y 200): "
                "la estructura reciente es alcista.",
            )
        )
    elif not above:
        parts.append(
            _t(
                lang,
                f"The price is below its three {tf} moving averages (EMA 20, 50 and 200): "
                "the recent structure is bearish.",
                f"El precio está por debajo de sus tres medias de {tf} (EMA 20, 50 y 200): "
                "la estructura reciente es bajista.",
            )
        )
    else:
        above_txt = ", ".join(f"EMA{k}" for k in above)
        parts.append(
            _t(
                lang,
                f"The price is between averages on {tf} (above {above_txt}): no clear trend.",
                f"El precio está entre medias en {tf} (por encima de {above_txt}): "
                "sin una tendencia clara.",
            )
        )
    rsi = float(m["rsi"])
    if rsi >= 70:
        zone = _t(lang, "overbought", "en sobrecompra")
    elif rsi <= 30:
        zone = _t(lang, "oversold", "en sobreventa")
    elif rsi >= 55:
        zone = _t(lang, "with moderate buying momentum", "con impulso comprador moderado")
    elif rsi <= 45:
        zone = _t(lang, "with moderate selling momentum", "con impulso vendedor moderado")
    else:
        zone = _t(lang, "neutral", "neutral")
    parts.append(
        _t(lang, f"The {tf} RSI is at {rsi:.0f}, {zone}.", f"El RSI de {tf} está en {rsi:.0f}, {zone}.")
    )
    parts.append(
        _t(
            lang,
            "This describes where the price is, not where it is going: the direction model, "
            "which uses these same measures, did not pass its test (see Lab).",
            "Esto describe dónde está el precio, no hacia dónde va: el modelo de "
            "dirección, que usa estas mismas medidas, no pasó la prueba (ver Laboratorio).",
        )
    )
    return {
        "title": _t(lang, "Price and trend", "Precio y tendencia"),
        "basis": DESCRIPTIVE,
        "tone": "neutral",
        "text": " ".join(parts),
    }


def _structure(s: MarketSnapshot, lang: Lang) -> dict[str, Any]:
    m = s.map_15m
    parts: list[str] = []
    levels = [
        (
            float(z["price"]),
            "soporte" if z["kind"] == "support" else "resistencia",
            int(z["touches"]),
        )
        for z in m["sr"]
    ]
    below = sorted((lv for lv in levels if lv[0] < s.price), key=lambda lv: -lv[0])
    above = sorted((lv for lv in levels if lv[0] > s.price), key=lambda lv: lv[0])
    if below:
        p, _, t = below[0]
        d = _signed_pct(p / s.price - 1)
        touches = f"{t} {_plural(t, lang, 'touch', 'toque')}"
        parts.append(
            _t(
                lang,
                f"The nearest support is at {_px(p)} ({d}, {touches}).",
                f"El soporte más cercano está en {_px(p)} ({d}, {touches}).",
            )
        )
    if above:
        p, _, t = above[0]
        d = _signed_pct(p / s.price - 1)
        touches = f"{t} {_plural(t, lang, 'touch', 'toque')}"
        parts.append(
            _t(
                lang,
                f"The nearest resistance, at {_px(p)} ({d}, {touches}).",
                f"La resistencia más cercana, en {_px(p)} ({d}, {touches}).",
            )
        )
    equal = [z for z in m["pools"] if int(z["equal"]) >= 2]
    if equal:
        z = min(equal, key=lambda z: abs(float(z["distance_pct"])))
        high = z["side"] == "high"
        zp = _px(float(z["price"]))
        parts.append(
            _t(
                lang,
                f"There are equal {'highs' if high else 'lows'} at {zp}: liquidity stacked "
                f"{'above' if high else 'below'}, where stops tend to rest.",
                f"Hay {'máximos' if high else 'mínimos'} iguales en {zp}: liquidez acumulada "
                f"{'por encima' if high else 'por debajo'}, donde suelen descansar stops.",
            )
        )
    obs = m["order_blocks"]
    if obs:
        kinds = (
            {"long": "demand", "short": "supply"}
            if lang == "en"
            else {"long": "demanda", "short": "oferta"}
        )
        at = _t(lang, "at", "en")
        txt = "; ".join(
            f"{kinds[o['direction']]} {at} {_px(float(o['bottom']))}–{_px(float(o['top']))}"
            for o in obs[:3]
        )
        parts.append(_t(lang, f"Live order blocks: {txt}.", f"Order blocks vivos: {txt}."))
    sweeps = m["sweeps"]
    if sweeps:
        last = sweeps[-1]
        bars_ago = len([t for t in m["series"]["time"] if t > last["time"]])
        if bars_ago <= 16:
            lng = last["direction"] == "long"
            lvl = _px(float(last["level"]))
            parts.append(
                _t(
                    lang,
                    f"{bars_ago} 15m {_plural(bars_ago, lang, 'bar', 'vela')} ago the price swept "
                    f"liquidity {'below' if lng else 'above'} {lvl} and closed back "
                    f"{'above' if lng else 'below'}.",
                    f"Hace {bars_ago} {_plural(bars_ago, lang, 'bar', 'vela')} de 15m el precio "
                    f"barrió liquidez {'por debajo de' if lng else 'por encima de'} {lvl} y volvió "
                    f"a cerrar {'arriba' if lng else 'abajo'}.",
                )
            )
    if not parts:
        parts.append(
            _t(
                lang,
                "No nearby levels are marked in the visible window.",
                "No hay niveles cercanos marcados en la ventana visible.",
            )
        )
    caveat = _t(
        lang,
        "These zones help place the stop behind a level. Trading them showed no edge",
        "Estas zonas sirven para ubicar el stop detrás de un nivel. Operarlas no mostró ventaja",
    )
    if s.technical_mean_r:
        vals = ", ".join(f"{_signed_r(v)}" for v in s.technical_mean_r.values())
        caveat += _t(lang, f" in the backtest ({vals} per trade)", f" en el backtest ({vals} por trade)")
    parts.append(caveat + ".")
    return {
        "title": _t(lang, "Structure and liquidity", "Estructura y liquidez"),
        "basis": NO_EDGE,
        "tone": "neutral",
        "text": " ".join(parts),
    }


def _signed_r(x: float) -> str:
    s = f"{abs(x):.2f}"
    return f"+{s} R" if x > 0 else f"−{s} R"


def _derivatives(s: MarketSnapshot, lang: Lang) -> dict[str, Any] | None:
    f = s.features
    oi24 = _f(f, "oi_chg_24h")
    funding = _f(f, "funding_bps")
    ls = _f(f, "ls_ratio_log")
    top_vs = _f(f, "top_vs_crowd")
    taker4 = _f(f, "taker_ratio_mean_4h")
    if all(v is None for v in (oi24, funding, ls, taker4)):
        return None
    parts: list[str] = []
    if oi24 is not None:
        chg = math.exp(oi24) - 1.0
        if abs(chg) < 0.005:
            parts.append(
                _t(
                    lang,
                    f"Open interest barely changed in 24 h ({_signed_pct(chg)}).",
                    f"El interés abierto casi no cambió en 24 h ({_signed_pct(chg)}).",
                )
            )
        else:
            up_oi = chg > 0
            px_up = (s.change_24h or 0.0) > 0
            if up_oi and px_up:
                read = _t(
                    lang,
                    "new money is coming in while the price rises: buy positions are being opened",
                    "entra dinero nuevo y el precio sube: se abren posiciones compradoras",
                )
            elif up_oi and not px_up:
                read = _t(
                    lang,
                    "new money is coming in while the price falls: sell positions are being opened",
                    "entra dinero nuevo mientras el precio baja: se abren posiciones vendedoras",
                )
            elif not up_oi and px_up:
                read = _t(
                    lang,
                    "money is leaving while the price rises: typical of shorts closing",
                    "sale dinero mientras el precio sube: típico de cierre de cortos",
                )
            else:
                read = _t(
                    lang,
                    "money is leaving while the price falls: typical of longs closing",
                    "sale dinero mientras el precio baja: típico de cierre de largos",
                )
            parts.append(
                _t(
                    lang,
                    f"Open interest {'rose' if up_oi else 'fell'} {_signed_pct(chg)} in 24 h; {read}.",
                    f"El interés abierto {'subió' if up_oi else 'bajó'} {_signed_pct(chg)} "
                    f"en 24 h; {read}.",
                )
            )
    if funding is not None:
        p8 = _pct(funding / 10_000.0, 4)
        if funding > 1.0:
            ftxt = _t(
                lang,
                f"positive and high ({p8} every 8 h): longs pay a lot to stay in",
                f"positivo y alto ({p8} cada 8 h): los largos pagan caro por estar",
            )
        elif funding > 0.0:
            ftxt = _t(
                lang,
                f"slightly positive ({p8} every 8 h): longs pay little",
                f"levemente positivo ({p8} cada 8 h): los largos pagan poco",
            )
        elif funding < 0.0:
            ftxt = _t(
                lang,
                f"negative ({p8} every 8 h): shorts pay to stay in",
                f"negativo ({p8} cada 8 h): los cortos pagan por estar",
            )
        else:
            ftxt = _t(lang, "at zero", "en cero")
        parts.append(_t(lang, f"Funding is {ftxt}.", f"El funding está {ftxt}."))
    if ls is not None:
        ratio = math.exp(ls)
        if ratio > 1:
            crowd = _t(
                lang,
                f"The crowd is loaded long ({_num(ratio)} long accounts for each short one)",
                f"La multitud está cargada a largo ({_num(ratio)} cuentas en largo por cada una "
                f"en corto)",
            )
        else:
            crowd = _t(
                lang,
                f"The crowd is loaded short ({_num(1 / ratio)} short accounts for each long one)",
                f"La multitud está cargada a corto ({_num(1 / ratio)} cuentas en corto por cada "
                f"una en largo)",
            )
        if top_vs is not None:
            crowd += (
                _t(lang, ", and the large traders are longer than it is", ", y los grandes traders están más largos que ella")
                if top_vs > 0
                else _t(lang, ", and the large traders are less long than it is", ", y los grandes traders están menos largos que ella")
            )
        parts.append(crowd + ".")
    if taker4 is not None:
        r = math.exp(taker4)
        if r > 1.05:
            parts.append(
                _t(
                    lang,
                    f"Over the last 4 h aggressive buying dominates (×{_num(r)}).",
                    f"En las últimas 4 h dominan las compras agresivas (×{_num(r)}).",
                )
            )
        elif r < 0.95:
            parts.append(
                _t(
                    lang,
                    f"Over the last 4 h aggressive selling dominates (×{_num(1 / r)}).",
                    f"En las últimas 4 h dominan las ventas agresivas (×{_num(1 / r)}).",
                )
            )
        else:
            parts.append(
                _t(
                    lang,
                    "Over the last 4 h aggressive buying and selling are balanced.",
                    "En las últimas 4 h compras y ventas agresivas están equilibradas.",
                )
            )
    parts.append(
        _t(
            lang,
            "These data were tested as features and did not improve the direction forecast; "
            "they help understand who is positioned.",
            "Estos datos se probaron como features y no mejoraron la predicción de "
            "dirección; sirven para entender quién está posicionado.",
        )
    )
    return {
        "title": _t(lang, "Derivatives and positioning", "Derivados y posicionamiento"),
        "basis": DESCRIPTIVE,
        "tone": "neutral",
        "text": " ".join(parts),
    }


def _headline(s: MarketSnapshot, lang: Lang) -> str:
    vol = _vol_word(s.regime_percentile, lang)
    h = f"{s.horizon_hours:.0f}"
    if lang == "en":
        chg = f", {_signed_pct(s.change_24h)} in 24 h" if s.change_24h is not None else ""
        regime = "ELEVATED regime, better not to enter" if s.regime_elevated else "normal regime"
        return (
            f"{s.symbol} at {_px(s.price)}{chg}. Volatility {vol} (±{_pct(s.sigma_horizon)} "
            f"expected over {h} h), {regime}."
        )
    chg = f", {_signed_pct(s.change_24h)} en 24 h" if s.change_24h is not None else ""
    regime = "régimen ELEVADO, mejor no entrar" if s.regime_elevated else "régimen normal"
    return (
        f"{s.symbol} en {_px(s.price)}{chg}. Volatilidad {vol} (±{_pct(s.sigma_horizon)} "
        f"esperado en {h} h), {regime}."
    )


def market_narrative(s: MarketSnapshot, lang: Lang = "es") -> dict[str, Any]:
    sections = [_volatility(s, lang), _trend(s, lang), _structure(s, lang)]
    der = _derivatives(s, lang)
    if der is not None:
        sections.append(der)
    sections.append(
        {
            "title": _t(lang, "What this does not say", "Lo que esto no dice"),
            "basis": SCOPE,
            "tone": "muted",
            "text": _t(
                lang,
                "Nothing in this reading anticipates the price direction. MAREA has no proven "
                "edge on direction: the model that forecasts it did not pass its test. What is "
                "measured is how much the price will move and when it is better not to enter. "
                "You decide the direction, with the size the calculator sets.",
                "Nada de esta lectura anticipa la dirección del precio. MAREA no tiene una "
                "ventaja demostrada para la dirección: el modelo que la pronostica no pasó la prueba. "
                "Lo que sí está medido es cuánto se va a mover y cuándo conviene no entrar. "
                "La dirección la decides tú, con el tamaño que fija la calculadora.",
            ),
        }
    )
    return {"headline": _headline(s, lang), "sections": sections}
