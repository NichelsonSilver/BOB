"""Proyección operativa de un setup — KPI 2, y la distancia a liquidación.

Módulo PURO (regla 3): entra un número de volatilidad y una probabilidad, sale
la aritmética completa del trade. No sabe qué es Binance ni qué es una vela.

**Por qué esto se apoya en la volatilidad y no en la dirección.** El gate de la
Fase 4 fue explícito: el modelo de dirección calibra pero no discrimina
(AUC 0.52). El de volatilidad realizada, en cambio, gana con holgura — R² de
+0.40 contra la media y +0.37 contra EWMA, y le gana a HAR-RV y GARCH con
p=0.0000 en el test de Diebold-Mariano. Dimensionar bien TP, SL y leverage con
una sigma bien pronosticada ya es valor operativo aunque nadie sepa hacia dónde
va el precio: define cuánto arriesgar, dónde poner las barreras para que el
costo no se coma el edge, y **a qué distancia queda la liquidación**.

Para un usuario que ya pasó por una liquidación, ese último número es tan
importante como el profit — por eso la proyección lo devuelve siempre, junto
con el leverage máximo que deja el stop por delante de la liquidación.

Convenciones (las mismas de `labeling.py`, a propósito — si divergen, el
backtest deja de describir lo que el dashboard muestra):
  * los retornos y las distancias son fracciones (0.01 = 1%), no porcentajes;
  * `sigma_horizon` es la sigma del horizonte COMPLETO, no la de una barra;
  * los costos entran en el EV, nunca se descuentan después.

Lo que este módulo NO hace: cuantizar precios al tickSize (eso es
presentación, vive en `signals/indicators.py` con Decimal) ni afirmar que la
probabilidad que recibe está calibrada — la honestidad de ese número es
problema de quien lo produce.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Final

from bob.models.labeling import BarrierConfig

#: Maintenance margin rate de referencia (tier 1 de Binance USDⓈ-M para
#: notional chico). Binance usa *brackets*: el MMR sube con el tamaño de la
#: posición y viene con un "maintenance amount" que corrige el escalón. Con
#: capital real hay que leer el bracket vigente del símbolo — un MMR
#: subestimado pone la liquidación más lejos de lo que está, que es el error
#: más caro posible en este cálculo.
DEFAULT_MMR: Final = 0.005

#: Cuánto más lejos que el stop debe quedar la liquidación para considerar el
#: leverage seguro. 1.5 no es un número mágico: es "que el precio pueda
#: excederse un 50% más allá del stop —slippage, mecha, gap— y aun así el
#: trade termine en el stop y no en liquidación".
DEFAULT_SAFETY_BUFFER: Final = 1.5

MS_PER_HOUR: Final = 3_600_000.0


@dataclass(frozen=True)
class LeverageProfile:
    """Cómo el usuario piensa entrar: apalancamiento y régimen de margen."""

    leverage: float = 1.0
    maintenance_margin_rate: float = DEFAULT_MMR
    #: `cumB / qty` del bracket de Binance. 0 es el caso del tier 1 y es
    #: conservador para los demás (deja la liquidación más cerca, no más lejos).
    maintenance_amount_per_unit: float = 0.0
    safety_buffer: float = DEFAULT_SAFETY_BUFFER

    def __post_init__(self) -> None:
        if self.leverage <= 0:
            raise ValueError("el leverage debe ser > 0")
        if not 0.0 <= self.maintenance_margin_rate < 1.0:
            raise ValueError("el maintenance margin rate debe estar en [0, 1)")


@dataclass(frozen=True)
class SetupProjection:
    """Todo lo que hay que saber de un setup antes de apretar el botón.

    `warnings` es deliberadamente parte del resultado y no de la vista: si un
    setup se liquida antes de llegar al stop, eso no es un detalle de
    presentación que un frontend pueda olvidar de dibujar.
    """

    direction: str
    entry_price: float
    take_profit: float
    stop_loss: float
    tp_pct: float
    sl_pct: float
    risk_reward: float

    probability: float
    breakeven_probability: float
    edge_pp: float  # probabilidad − equilibrio, en puntos porcentuales

    gross_ev_pct: float
    cost_pct: float
    net_ev_pct: float

    horizon_bars: int
    horizon_hours: float
    sigma_horizon: float

    leverage: float
    margin_pct: float  # margen inicial como fracción del notional
    roe_pct: float  # EV neto sobre el margen (lo que el usuario "siente")
    roe_tp_pct: float
    roe_sl_pct: float

    liquidation_price: float
    liq_distance_pct: float
    liq_distance_sigmas: float
    prob_touch_liquidation: float
    liquidation_before_stop: bool
    max_safe_leverage: float

    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_actionable(self) -> bool:
        """EV positivo y con la liquidación detrás del stop. No dice nada sobre
        si la probabilidad está calibrada: eso lo decide el gate, no acá."""
        return self.net_ev_pct > 0 and not self.liquidation_before_stop

    def as_dict(self) -> dict[str, Any]:
        """Serializable para la API, el WS y el log de la señal (regla 10)."""
        return {
            "direction": self.direction,
            "entry_price": self.entry_price,
            "take_profit": self.take_profit,
            "stop_loss": self.stop_loss,
            "tp_pct": self.tp_pct,
            "sl_pct": self.sl_pct,
            "risk_reward": self.risk_reward,
            "probability": self.probability,
            "breakeven_probability": self.breakeven_probability,
            "edge_pp": self.edge_pp,
            "gross_ev_pct": self.gross_ev_pct,
            "cost_pct": self.cost_pct,
            "net_ev_pct": self.net_ev_pct,
            "horizon_bars": self.horizon_bars,
            "horizon_hours": self.horizon_hours,
            "sigma_horizon": self.sigma_horizon,
            "leverage": self.leverage,
            "margin_pct": self.margin_pct,
            "roe_pct": self.roe_pct,
            "roe_tp_pct": self.roe_tp_pct,
            "roe_sl_pct": self.roe_sl_pct,
            "liquidation_price": self.liquidation_price,
            "liq_distance_pct": self.liq_distance_pct,
            "liq_distance_sigmas": self.liq_distance_sigmas,
            "prob_touch_liquidation": self.prob_touch_liquidation,
            "liquidation_before_stop": self.liquidation_before_stop,
            "max_safe_leverage": self.max_safe_leverage,
            "is_actionable": self.is_actionable,
            "warnings": list(self.warnings),
        }


# --------------------------------------------------------------------- #
# Piezas (cada una testeable por separado)
# --------------------------------------------------------------------- #


def barrier_prices(
    entry_price: float, sigma_horizon: float, config: BarrierConfig, direction: str
) -> tuple[float, float]:
    """Precios de TP y SL para el setup, dimensionados por sigma.

    Misma aritmética que `labeling.triple_barrier_labels`: si acá se dimensiona
    distinto que en el etiquetado, la probabilidad que devuelve el modelo
    describe un trade que no es el que el usuario va a poner.
    """
    _validate_direction(direction)
    if entry_price <= 0:
        raise ValueError("el precio de entrada debe ser > 0")
    if sigma_horizon <= 0:
        raise ValueError("la sigma del horizonte debe ser > 0")

    tp_dist = config.tp_mult * sigma_horizon
    sl_dist = config.sl_mult * sigma_horizon
    if direction == "long":
        return entry_price * (1.0 + tp_dist), entry_price * (1.0 - sl_dist)
    return entry_price * (1.0 - tp_dist), entry_price * (1.0 + sl_dist)


def funding_cost_pct(config: BarrierConfig, timeframe_ms: int, bars: int) -> float:
    """Funding esperado por mantener la posición `bars` barras.

    Se cobra como costo en ambas direcciones (igual que en el etiquetado): un
    short suele *cobrar* funding con tasa positiva, pero contarlo como ingreso
    infla el EV justo en el escenario donde el mercado está en contra.
    """
    return config.funding_per_bar(timeframe_ms) * max(bars, 0)


def breakeven_probability(tp_pct: float, sl_pct: float, cost_pct: float) -> float:
    """P mínima de TP para que el EV sea 0: (SL + costo) / (TP + SL)."""
    total = tp_pct + sl_pct
    if total <= 0:
        return float("nan")
    return (sl_pct + cost_pct) / total


def net_expected_value(prob: float, tp_pct: float, sl_pct: float, cost_pct: float) -> float:
    """EV = p·TP − (1−p)·SL − costos, todo en fracción del notional."""
    if not 0.0 <= prob <= 1.0:
        raise ValueError("la probabilidad debe estar en [0, 1]")
    return prob * tp_pct - (1.0 - prob) * sl_pct - cost_pct


def liquidation_price(entry_price: float, direction: str, profile: LeverageProfile) -> float:
    """Precio de liquidación con margen AISLADO y una sola posición.

    De igualar margen + PnL no realizado al margen de mantenimiento:

        long : P = [entry·(1 − 1/L) − cumB/qty] / (1 − MMR)
        short: P = [entry·(1 + 1/L) + cumB/qty] / (1 + MMR)

    Simplificaciones declaradas: margen aislado (en cross entra todo el saldo
    de la cuenta y la liquidación queda mucho más lejos), una sola posición,
    sin margen extra agregado a mano y sin descontar las fees de cierre. Es
    una estimación conservadora, no el número exacto que calcula el exchange:
    para operar con capital hay que contrastarla con el que muestra Binance.
    """
    _validate_direction(direction)
    if entry_price <= 0:
        raise ValueError("el precio de entrada debe ser > 0")

    mmr = profile.maintenance_margin_rate
    cum_b = profile.maintenance_amount_per_unit
    inv_lev = 1.0 / profile.leverage

    if direction == "long":
        price = (entry_price * (1.0 - inv_lev) - cum_b) / (1.0 - mmr)
        return max(price, 0.0)
    return (entry_price * (1.0 + inv_lev) + cum_b) / (1.0 + mmr)


def max_safe_leverage(
    sl_pct: float, mmr: float = DEFAULT_MMR, safety_buffer: float = DEFAULT_SAFETY_BUFFER
) -> float:
    """Mayor leverage que deja la liquidación más lejos que el stop.

    La distancia relativa a la liquidación sale de la fórmula de arriba:

        long : (1/L − MMR) / (1 − MMR)
        short: (1/L − MMR) / (1 + MMR)     ← siempre la más estrecha

    Se usa la del short para las dos direcciones: pedirle a un long un poco
    más de margen del estrictamente necesario cuesta nada; quedarse corto en
    un short cuesta la cuenta. Exigiendo distancia ≥ buffer · SL:

        L ≤ 1 / (buffer·SL·(1 + MMR) + MMR)

    Devuelve el valor continuo: redondear hacia abajo es decisión de quien lo
    muestre, nunca hacia arriba.
    """
    if sl_pct <= 0:
        return float("inf")
    denom = safety_buffer * sl_pct * (1.0 + mmr) + mmr
    if denom <= 0:  # pragma: no cover — imposible con MMR y SL válidos
        return float("inf")
    return 1.0 / denom


def prob_touch(distance_pct: float, sigma_horizon: float) -> float:
    """P(el precio toque una barrera a `distance_pct` dentro del horizonte).

    Primer paso de un movimiento browniano sin deriva: P = erfc(d / (σ·√2)),
    que es exactamente 2·Φ(−d/σ). Es una **aproximación declarada**, no una
    probabilidad calibrada: asume caminata aleatoria, sin deriva y con colas
    normales, y las colas de cripto son más gordas — así que subestima. Sirve
    para dimensionar riesgo ("la liquidación está a 3σ"), no para operar.
    """
    if sigma_horizon <= 0:
        return float("nan")
    if distance_pct <= 0:
        return 1.0
    return min(1.0, math.erfc(distance_pct / (sigma_horizon * math.sqrt(2.0))))


def project_setup(
    *,
    entry_price: float,
    sigma_horizon: float,
    probability: float,
    config: BarrierConfig,
    timeframe_ms: int,
    direction: str = "long",
    profile: LeverageProfile | None = None,
    bars_held: int | None = None,
) -> SetupProjection:
    """Arma la proyección completa del setup: KPI 2 + distancia a liquidación.

    `bars_held` permite cobrar el funding por la duración esperada real (del
    KPI 3) en vez del horizonte completo; por defecto asume el peor caso, que
    es aguantar hasta la barrera vertical.
    """
    _validate_direction(direction)
    profile = profile or LeverageProfile()
    tp_price, sl_price = barrier_prices(entry_price, sigma_horizon, config, direction)

    tp_pct = config.tp_mult * sigma_horizon
    sl_pct = config.sl_mult * sigma_horizon
    bars = config.horizon_bars if bars_held is None else bars_held
    cost = config.cost_pct + funding_cost_pct(config, timeframe_ms, bars)

    net_ev = net_expected_value(probability, tp_pct, sl_pct, cost)  # valida la probabilidad
    gross_ev = probability * tp_pct - (1.0 - probability) * sl_pct
    breakeven = breakeven_probability(tp_pct, sl_pct, cost)

    liq = liquidation_price(entry_price, direction, profile)
    liq_distance = abs(liq - entry_price) / entry_price
    safe_lev = max_safe_leverage(sl_pct, profile.maintenance_margin_rate, profile.safety_buffer)

    warnings: list[str] = []
    if net_ev <= 0:
        warnings.append("EV neto no positivo: los costos se comen la ventaja")
    if probability < breakeven:
        warnings.append(
            f"probabilidad {probability:.1%} bajo el equilibrio {breakeven:.1%}: "
            "el setup pierde en promedio"
        )
    liquidation_before_stop = liq_distance <= sl_pct
    if liquidation_before_stop:
        warnings.append(
            f"la liquidación ({liq_distance:.2%}) llega antes que el stop ({sl_pct:.2%}): "
            f"con este leverage el stop no protege — máximo seguro {safe_lev:.1f}x"
        )
    elif liq_distance < profile.safety_buffer * sl_pct:
        warnings.append(
            f"la liquidación está a solo {liq_distance / sl_pct:.1f}× el stop: "
            f"sin margen para mechas — máximo seguro {safe_lev:.1f}x"
        )

    return SetupProjection(
        direction=direction,
        entry_price=entry_price,
        take_profit=tp_price,
        stop_loss=sl_price,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        risk_reward=tp_pct / sl_pct if sl_pct > 0 else float("inf"),
        probability=probability,
        breakeven_probability=breakeven,
        edge_pp=(probability - breakeven) * 100.0,
        gross_ev_pct=gross_ev,
        cost_pct=cost,
        net_ev_pct=net_ev,
        horizon_bars=config.horizon_bars,
        horizon_hours=config.horizon_bars * timeframe_ms / MS_PER_HOUR,
        sigma_horizon=sigma_horizon,
        leverage=profile.leverage,
        margin_pct=1.0 / profile.leverage,
        roe_pct=net_ev * profile.leverage,
        roe_tp_pct=(tp_pct - cost) * profile.leverage,
        roe_sl_pct=-(sl_pct + cost) * profile.leverage,
        liquidation_price=liq,
        liq_distance_pct=liq_distance,
        liq_distance_sigmas=liq_distance / sigma_horizon if sigma_horizon > 0 else float("nan"),
        prob_touch_liquidation=prob_touch(liq_distance, sigma_horizon),
        liquidation_before_stop=liquidation_before_stop,
        max_safe_leverage=safe_lev,
        warnings=tuple(warnings),
    )


def _validate_direction(direction: str) -> None:
    if direction not in ("long", "short"):
        raise ValueError("direction debe ser 'long' o 'short'")
