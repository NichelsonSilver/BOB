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
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

VALIDATED = "validado"
DESCRIPTIVE = "descriptivo"
NO_EDGE = "sin ventaja medida"
SCOPE = "alcance"


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
    return f"{x * 100:.{digits}f}%".replace(".", ",")


def _signed_pct(x: float, digits: int = 2) -> str:
    s = _pct(abs(x), digits)
    return f"+{s}" if x > 0 else (f"−{s}" if x < 0 else s)


def _px(x: float) -> str:
    # 2.686,29 — el mismo formato que usa el dashboard en español.
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _num(x: float, digits: int = 2) -> str:
    return f"{x:.{digits}f}".replace(".", ",")


def _f(features: dict[str, float], key: str) -> float | None:
    v = features.get(key)
    return float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None


def _vol_word(percentile: float | None) -> str:
    if percentile is None:
        return "sin referencia"
    if percentile < 20:
        return "baja"
    if percentile < 60:
        return "normal"
    if percentile < 80:
        return "algo alta"
    return "alta"


# --------------------------------------------------------------------------- #
# Secciones
# --------------------------------------------------------------------------- #
def _volatility(s: MarketSnapshot) -> dict[str, Any]:
    move = s.sigma_horizon * s.price
    parts = [
        f"Para las próximas {s.horizon_hours:.0f} horas se espera un movimiento típico de "
        f"±{_pct(s.sigma_horizon)} (unos ±{_px(move)} USD)."
    ]
    if s.regime_percentile is not None:
        parts.append(
            f"Es una volatilidad {_vol_word(s.regime_percentile)} para {s.symbol}: está en el "
            f"percentil {s.regime_percentile:.0f} de lo que el modelo suele pronosticar."
        )
    if s.sigma_ratio is not None:
        if s.sigma_ratio > 1.15:
            parts.append(
                "Viene más movimiento que el de las últimas horas: el mercado debería "
                "despertarse respecto de lo reciente."
            )
        elif s.sigma_ratio < 0.85:
            parts.append(
                "Se espera menos movimiento que el de las últimas horas: el mercado debería "
                "calmarse respecto de lo reciente."
            )
        else:
            parts.append("Se espera un ritmo parecido al de las últimas horas.")
    if s.cone80 is not None:
        lo, hi = s.cone80
        parts.append(
            f"Con la cobertura medida del cono (80%), el precio debería estar entre "
            f"{_px(lo)} y {_px(hi)} al cierre del horizonte."
        )
    if s.regime_elevated:
        parts.append(
            "El filtro de régimen está ELEVADO: es el tipo de momento en que se concentran "
            "las liquidaciones, y la sigma subestima los picos. Lo prudente es esperar o "
            "entrar con menos riesgo."
        )
    elif s.regime_elevated is not None:
        parts.append("El filtro de régimen está en normal: no hay alerta de volatilidad.")
    tone = "warn" if s.regime_elevated else "ok"
    return {
        "title": "Volatilidad y riesgo",
        "basis": VALIDATED,
        "tone": tone,
        "text": " ".join(parts),
    }


def _trend(s: MarketSnapshot) -> dict[str, Any]:
    parts: list[str] = []
    if s.change_24h is not None:
        verb = "sube" if s.change_24h > 0 else "cae"
        parts.append(f"En las últimas 24 horas {s.symbol} {verb} {_pct(abs(s.change_24h))}.")
    m = s.map_1h or s.map_15m
    tf = "1h" if s.map_1h else "15m"
    emas = {int(k): float(v) for k, v in m["emas"].items()}
    above = [k for k, v in sorted(emas.items()) if s.price > v]
    if len(above) == len(emas):
        parts.append(
            f"El precio está por encima de sus tres medias de {tf} (EMA 20, 50 y 200): "
            "la estructura reciente es alcista."
        )
    elif not above:
        parts.append(
            f"El precio está por debajo de sus tres medias de {tf} (EMA 20, 50 y 200): "
            "la estructura reciente es bajista."
        )
    else:
        above_txt = ", ".join(f"EMA{k}" for k in above)
        parts.append(
            f"El precio está entre medias en {tf} (por encima de {above_txt}): "
            "sin una tendencia clara."
        )
    rsi = float(m["rsi"])
    if rsi >= 70:
        zone = "en sobrecompra"
    elif rsi <= 30:
        zone = "en sobreventa"
    elif rsi >= 55:
        zone = "con impulso comprador moderado"
    elif rsi <= 45:
        zone = "con impulso vendedor moderado"
    else:
        zone = "neutral"
    parts.append(f"El RSI de {tf} está en {rsi:.0f}, {zone}.")
    parts.append(
        "Esto describe dónde está el precio, no hacia dónde va: medias y RSI no "
        "anticiparon dirección en el gate (AUC 0,52)."
    )
    return {
        "title": "Precio y tendencia",
        "basis": DESCRIPTIVE,
        "tone": "neutral",
        "text": " ".join(parts),
    }


def _structure(s: MarketSnapshot) -> dict[str, Any]:
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
        parts.append(
            f"El soporte más cercano está en {_px(p)} ({_signed_pct(p / s.price - 1)}, "
            f"{t} toque{'s' if t != 1 else ''})."
        )
    if above:
        p, _, t = above[0]
        parts.append(
            f"La resistencia más cercana, en {_px(p)} ({_signed_pct(p / s.price - 1)}, "
            f"{t} toque{'s' if t != 1 else ''})."
        )
    equal = [z for z in m["pools"] if int(z["equal"]) >= 2]
    if equal:
        z = min(equal, key=lambda z: abs(float(z["distance_pct"])))
        side = "máximos" if z["side"] == "high" else "mínimos"
        where = "por encima" if z["side"] == "high" else "por debajo"
        parts.append(
            f"Hay {side} iguales en {_px(float(z['price']))}: liquidez acumulada {where}, "
            "donde suelen descansar stops."
        )
    obs = m["order_blocks"]
    if obs:
        kinds = {"long": "demanda", "short": "oferta"}
        txt = "; ".join(
            f"{kinds[o['direction']]} en {_px(float(o['bottom']))}–{_px(float(o['top']))}"
            for o in obs[:3]
        )
        parts.append(f"Order blocks vivos: {txt}.")
    sweeps = m["sweeps"]
    if sweeps:
        last = sweeps[-1]
        bars_ago = len([t for t in m["series"]["time"] if t > last["time"]])
        if bars_ago <= 16:
            what = "por debajo de" if last["direction"] == "long" else "por encima de"
            back = "arriba" if last["direction"] == "long" else "abajo"
            parts.append(
                f"Hace {bars_ago} vela{'s' if bars_ago != 1 else ''} de 15m el precio "
                f"barrió liquidez {what} {_px(float(last['level']))} y volvió a cerrar "
                f"{back}."
            )
    if not parts:
        parts.append("No hay niveles cercanos marcados en la ventana visible.")
    caveat = (
        "Estas zonas sirven para ubicar el stop detrás de un nivel. Operarlas no mostró ventaja"
    )
    if s.technical_mean_r:
        vals = ", ".join(f"{_signed_r(v)}" for v in s.technical_mean_r.values())
        caveat += f" en el backtest ({vals} por trade)"
    parts.append(caveat + ".")
    return {
        "title": "Estructura y liquidez",
        "basis": NO_EDGE,
        "tone": "neutral",
        "text": " ".join(parts),
    }


def _signed_r(x: float) -> str:
    s = f"{abs(x):.2f}".replace(".", ",")
    return f"+{s} R" if x > 0 else f"−{s} R"


def _derivatives(s: MarketSnapshot) -> dict[str, Any] | None:
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
            parts.append(f"El interés abierto casi no cambió en 24 h ({_signed_pct(chg)}).")
        else:
            up_oi = chg > 0
            px_up = (s.change_24h or 0.0) > 0
            if up_oi and px_up:
                read = "entra dinero nuevo y el precio sube: se abren posiciones compradoras"
            elif up_oi and not px_up:
                read = "entra dinero nuevo mientras el precio baja: se abren posiciones vendedoras"
            elif not up_oi and px_up:
                read = "sale dinero mientras el precio sube: típico de cierre de cortos"
            else:
                read = "sale dinero mientras el precio baja: típico de cierre de largos"
            parts.append(
                f"El interés abierto {'subió' if up_oi else 'bajó'} {_signed_pct(chg)} "
                f"en 24 h; {read}."
            )
    if funding is not None:
        pct8h = funding / 10_000.0
        if funding > 1.0:
            ftxt = f"positivo y alto ({_pct(pct8h, 4)} cada 8 h): los largos pagan caro por estar"
        elif funding > 0.0:
            ftxt = f"levemente positivo ({_pct(pct8h, 4)} cada 8 h): los largos pagan poco"
        elif funding < 0.0:
            ftxt = f"negativo ({_pct(pct8h, 4)} cada 8 h): los cortos pagan por estar"
        else:
            ftxt = "en cero"
        parts.append(f"El funding está {ftxt}.")
    if ls is not None:
        ratio = math.exp(ls)
        crowd = (
            f"La multitud está cargada a largo ({_num(ratio)} cuentas en largo por cada una "
            f"en corto)"
            if ratio > 1
            else f"La multitud está cargada a corto ({_num(1 / ratio)} cuentas en corto por cada "
            f"una en largo)"
        )
        if top_vs is not None:
            crowd += (
                ", y los grandes traders están más largos que ella"
                if top_vs > 0
                else ", y los grandes traders están menos largos que ella"
            )
        parts.append(crowd + ".")
    if taker4 is not None:
        r = math.exp(taker4)
        if r > 1.05:
            parts.append(f"En las últimas 4 h dominan las compras agresivas (×{_num(r)}).")
        elif r < 0.95:
            parts.append(f"En las últimas 4 h dominan las ventas agresivas (×{_num(1 / r)}).")
        else:
            parts.append("En las últimas 4 h compras y ventas agresivas están equilibradas.")
    parts.append(
        "Estos datos se probaron como features y no mejoraron la predicción de "
        "dirección; sirven para entender quién está posicionado."
    )
    return {
        "title": "Derivados y posicionamiento",
        "basis": DESCRIPTIVE,
        "tone": "neutral",
        "text": " ".join(parts),
    }


def _headline(s: MarketSnapshot) -> str:
    vol = _vol_word(s.regime_percentile)
    chg = f", {_signed_pct(s.change_24h)} en 24 h" if s.change_24h is not None else ""
    regime = "régimen ELEVADO, mejor no entrar" if s.regime_elevated else "régimen normal"
    return (
        f"{s.symbol} en {_px(s.price)}{chg}. Volatilidad {vol} (±{_pct(s.sigma_horizon)} "
        f"esperado en {s.horizon_hours:.0f} h), {regime}."
    )


def market_narrative(s: MarketSnapshot) -> dict[str, Any]:
    sections = [_volatility(s), _trend(s), _structure(s)]
    der = _derivatives(s)
    if der is not None:
        sections.append(der)
    sections.append(
        {
            "title": "Lo que esto no dice",
            "basis": SCOPE,
            "tone": "muted",
            "text": (
                "Nada de esta lectura anticipa la dirección del precio. BOB no tiene una "
                "ventaja direccional demostrada: el KPI de Seguridad sigue sin pasar el gate. "
                "Lo que sí está medido es cuánto se va a mover y cuándo conviene no entrar. "
                "La dirección la decides tú, con el tamaño que fija la calculadora."
            ),
        }
    )
    return {"headline": _headline(s), "sections": sections}
