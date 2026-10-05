"""Calculadora de tamaño de posición por riesgo fijo. PURO.

Con 500 USD y sin edge direccional medido (gate de la Fase 4, reglas técnicas
del 05-10), lo único que separa una mala racha de una cuenta liquidada es
**cuánto se arriesga por trade**. Este módulo hace esa cuenta en el orden
correcto: primero cuánto de la cuenta se está dispuesto a perder, después
dónde está el stop, y de ahí sale el tamaño. **El leverage no decide el
tamaño.** Solo decide cuánto margen se inmoviliza y a qué distancia queda la
liquidación.

El error que esto previene es elegir el tamaño por el leverage ("entro con
20x") y dejar que el stop quede donde caiga. Así, un stop normal se
convierte en el 10% de la cuenta, o la liquidación queda antes que el stop.

Convenciones:

- Fracciones, no porcentajes (0.01 = 1%), igual que `projection.py`.
- **Los costos entran en la pérdida al stop.** "Arriesgo 5 USD" significa
  que, si salta el stop, la cuenta baja 5 USD con fees y slippage incluidos,
  y no 5 USD más comisiones.
- La cantidad se redondea **hacia abajo** al paso del contrato. Redondear
  hacia arriba arriesgaría más de lo pedido.
- La liquidación es la de `projection.liquidation_price`: margen aislado,
  conservadora. En cross la liquidación queda más lejos, pero lo que se
  arriesga pasa a ser la cuenta entera.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Final

from bob.models.projection import liquidation_price, max_safe_leverage, prob_touch
from bob.venues import BINANCE_USDM, VenueProfile

#: Paso de cantidad y notional mínimo de ETHUSDT en Binance USDⓈ-M. Son de
#: referencia: el exchange los puede cambiar, y se pasan como parámetro.
DEFAULT_QTY_STEP: Final = 0.001
DEFAULT_MIN_NOTIONAL: Final = 20.0

#: Por encima de esto, una racha normal de pérdidas deja la cuenta muy dañada
#: (ver `losing_streaks`). No es un límite del cálculo: es una advertencia.
MAX_PRUDENT_RISK: Final = 0.02

#: Costo que se come más de esta fracción del riesgo → el trade empieza
#: perdiendo una parte grande de lo que arriesga.
COST_HEAVY_SHARE: Final = 0.30

#: Rachas que se muestran. 15 y 20 son las peores rachas medidas en el
#: backtest de reglas técnicas (artifacts/technical-ETHUSDT-*).
STREAKS: Final = (5, 10, 15, 20)

#: Probabilidad de toque por ruido por encima de la cual se avisa. 0,6
#: equivale a un stop a menos de ~0,5σ del horizonte.
STOP_IN_NOISE: Final = 0.60

#: Leverage desde el que el aviso de régimen elevado pasa a rojo. El filtro
#: se validó a 20x; a 10x salió sin potencia y a 5x no hubo un solo evento.
ELEVATED_LEVERAGE: Final = 20.0

#: Distancia mínima liquidación/stop para no avisar. La misma que usa
#: `max_safe_leverage`: el precio puede pasarse un 50% del stop (por mecha,
#: gap o slippage) y aun así salir por el stop y no por liquidación.
SAFE_LIQ_RATIO: Final = 1.5


@dataclass(frozen=True)
class SizingWarning:
    code: str
    level: str  # "danger" o "warn"
    message: str


@dataclass(frozen=True)
class Sizing:
    direction: str
    capital: float
    risk_fraction: float
    risk_budget: float
    entry: float
    stop: float
    leverage: float
    stop_pct: float
    cost_pct: float
    quantity: float
    notional: float
    margin: float
    loss_at_stop: float
    loss_at_stop_fraction: float  # de la cuenta
    cost_usd: float
    cost_share_of_risk: float
    required_leverage: float  # mínimo para abrir esto con todo el capital
    max_safe_leverage: float
    liquidation_price: float
    liquidation_pct: float
    liquidation_to_stop: float  # distancia a liquidación / distancia al stop
    target: float | None = None
    target_pct: float | None = None
    gain_at_target: float | None = None
    reward_risk_net: float | None = None
    breakeven_win_rate: float | None = None
    #: Contexto de volatilidad del analista, si lo hay. `sigma_horizon` es la
    #: sigma pronosticada del horizonte completo (H barras), la misma que usa
    #: `projection.py`.
    sigma_horizon: float | None = None
    stop_sigmas: float | None = None
    p_touch_stop: float | None = None
    p_touch_target: float | None = None
    regime_elevated: bool | None = None
    losing_streaks: dict[int, float] = field(default_factory=dict)
    warnings: list[SizingWarning] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["losing_streaks"] = {str(k): v for k, v in self.losing_streaks.items()}
        return d


def cost_fraction(venue: VenueProfile, *, maker_entry: bool) -> float:
    """Costo round-trip en fracción del notional.

    La salida se asume a mercado: el stop es una orden stop-market, y en el
    caso malo, que es el que dimensiona el riesgo, se sale por ahí. Una
    entrada límite (maker) no paga slippage de entrada.
    """
    slippage_legs = 1.0 if maker_entry else 2.0
    return venue.roundtrip_fee(maker_entry=maker_entry) + venue.slippage_pct * slippage_legs


def size_position(
    capital: float,
    risk_fraction: float,
    entry: float,
    stop: float,
    leverage: float,
    *,
    target: float | None = None,
    venue: VenueProfile = BINANCE_USDM,
    maker_entry: bool = False,
    qty_step: float = DEFAULT_QTY_STEP,
    min_notional: float = DEFAULT_MIN_NOTIONAL,
    sigma_horizon: float | None = None,
    regime_elevated: bool | None = None,
) -> Sizing:
    """Tamaño tal que, si salta el stop, se pierde `capital · risk_fraction`.

    La dirección sale de dónde está el stop: debajo de la entrada es long y
    encima es short. Un stop igual a la entrada no define riesgo y es error.
    """
    if capital <= 0:
        raise ValueError("el capital debe ser > 0")
    if not 0.0 < risk_fraction < 1.0:
        raise ValueError("el riesgo por trade debe estar en (0, 1)")
    if entry <= 0 or stop <= 0:
        raise ValueError("entrada y stop deben ser > 0")
    if stop == entry:
        raise ValueError("el stop no puede ser igual a la entrada")
    if leverage <= 0:
        raise ValueError("el leverage debe ser > 0")

    direction = "long" if stop < entry else "short"
    sign = 1.0 if direction == "long" else -1.0
    stop_pct = abs(entry - stop) / entry
    cost = cost_fraction(venue, maker_entry=maker_entry)
    loss_per_notional = stop_pct + cost
    budget = capital * risk_fraction

    raw_qty = budget / loss_per_notional / entry
    # El epsilon evita que 0.30000000000000004 / 0.001 baje un escalón de más.
    quantity = math.floor(raw_qty / qty_step + 1e-9) * qty_step
    notional = quantity * entry
    loss_at_stop = notional * loss_per_notional
    margin = notional / leverage

    profile = venue.leverage_profile(leverage, notional, quantity)
    liq = liquidation_price(entry, direction, profile)
    liq_pct = sign * (entry - liq) / entry
    liq_to_stop = liq_pct / stop_pct
    safe_lev = max_safe_leverage(stop_pct, profile.maintenance_margin_rate)

    target_pct = gain = rr = be = None
    if target is not None:
        target_pct = sign * (target - entry) / entry
        if target_pct <= 0:
            raise ValueError("el objetivo tiene que estar del lado de la ganancia")
        gain = notional * (target_pct - cost)
        rr = gain / loss_at_stop if loss_at_stop > 0 else None
        # p·(tp − c) = (1 − p)·(sl + c)  →  p = (sl + c) / (tp + sl)
        be = (stop_pct + cost) / (target_pct + stop_pct)

    loss_frac = loss_at_stop / capital
    sizing = Sizing(
        direction=direction,
        capital=capital,
        risk_fraction=risk_fraction,
        risk_budget=budget,
        entry=entry,
        stop=stop,
        leverage=leverage,
        stop_pct=stop_pct,
        cost_pct=cost,
        quantity=quantity,
        notional=notional,
        margin=margin,
        loss_at_stop=loss_at_stop,
        loss_at_stop_fraction=loss_frac,
        cost_usd=notional * cost,
        cost_share_of_risk=cost / loss_per_notional,
        required_leverage=notional / capital,
        max_safe_leverage=safe_lev,
        liquidation_price=liq,
        liquidation_pct=liq_pct,
        liquidation_to_stop=liq_to_stop,
        target=target,
        target_pct=target_pct,
        gain_at_target=gain,
        reward_risk_net=rr,
        breakeven_win_rate=be,
        sigma_horizon=sigma_horizon,
        # Aproximación declarada de `prob_touch`: camino sin deriva y colas
        # normales. Subestima en cripto, y más en régimen elevado (techo de
        # la sigma, Fase 5).
        stop_sigmas=(stop_pct / sigma_horizon) if sigma_horizon else None,
        p_touch_stop=prob_touch(stop_pct, sigma_horizon) if sigma_horizon else None,
        p_touch_target=(
            prob_touch(target_pct, sigma_horizon)
            if sigma_horizon and target_pct is not None
            else None
        ),
        regime_elevated=regime_elevated,
        # Riesgo fijo sobre el saldo que queda: (1 − r)^n.
        losing_streaks={n: 1.0 - (1.0 - risk_fraction) ** n for n in STREAKS},
    )
    sizing.warnings.extend(_warnings(sizing, min_notional))
    return sizing


def _warnings(s: Sizing, min_notional: float) -> list[SizingWarning]:
    out: list[SizingWarning] = []
    if s.quantity <= 0:
        out.append(
            SizingWarning(
                "qty_zero",
                "danger",
                "Con este riesgo y este stop no alcanza ni para el contrato mínimo.",
            )
        )
    elif s.notional < min_notional:
        out.append(
            SizingWarning(
                "below_min_notional",
                "danger",
                f"Notional {s.notional:.2f} USD bajo el mínimo del exchange "
                f"({min_notional:.0f} USD): la orden se rechaza.",
            )
        )
    if s.margin > s.capital:
        out.append(
            SizingWarning(
                "margin_exceeds_capital",
                "danger",
                f"El margen ({s.margin:.2f} USD) supera la cuenta: hace falta "
                f"al menos {s.required_leverage:.1f}x para abrir esto.",
            )
        )
    if s.liquidation_to_stop < 1.0:
        out.append(
            SizingWarning(
                "liq_before_stop",
                "danger",
                "La liquidación llega ANTES que el stop: el stop no protege "
                "y se pierde todo el margen. Bajá el leverage.",
            )
        )
    elif s.liquidation_to_stop < SAFE_LIQ_RATIO:
        out.append(
            SizingWarning(
                "liq_tight",
                "warn",
                f"La liquidación está a solo {s.liquidation_to_stop:.1f}× la "
                "distancia del stop: una mecha o un gap pueden saltarlo. "
                f"Máximo prudente: {s.max_safe_leverage:.1f}x.",
            )
        )
    if s.risk_fraction > MAX_PRUDENT_RISK:
        out.append(
            SizingWarning(
                "risk_high",
                "warn",
                f"Arriesgar {s.risk_fraction:.1%} por trade: 10 pérdidas "
                f"seguidas se llevan el {s.losing_streaks[10]:.0%} de la cuenta.",
            )
        )
    if s.cost_share_of_risk > COST_HEAVY_SHARE:
        out.append(
            SizingWarning(
                "cost_heavy",
                "warn",
                f"Los costos son el {s.cost_share_of_risk:.0%} de lo que se "
                "arriesga: el stop está muy cerca para lo que cobra el exchange.",
            )
        )
    if s.p_touch_stop is not None and s.p_touch_stop > STOP_IN_NOISE:
        out.append(
            SizingWarning(
                "stop_in_noise",
                "warn",
                f"El stop está a {s.stop_sigmas:.2f}σ del movimiento esperado en el "
                f"horizonte: el ruido normal lo toca ~{s.p_touch_stop:.0%} de las veces "
                "aunque tengas razón en la dirección.",
            )
        )
    if s.regime_elevated:
        out.append(
            SizingWarning(
                "regime_elevated",
                "danger" if s.leverage >= ELEVATED_LEVERAGE else "warn",
                "Filtro de régimen ELEVADO: la volatilidad pronosticada está en el "
                "20% más alto de su historia. Es donde se concentran las liquidaciones "
                "(evitarlo evitó el 52% a 20x) y donde el mismo stop salta bastante más "
                "seguido. Mejor no entrar, o hacerlo con menos riesgo y stop más ancho.",
            )
        )
    return out
