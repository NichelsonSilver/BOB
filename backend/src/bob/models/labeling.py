"""Etiquetado de targets — PURO, sin I/O.

Tres targets, uno por cada cosa que BOB necesita saber:

1. `triple_barrier_labels` → ¿toca TP antes que SL? (clasificación binaria,
   alimenta el KPI 1 "Seguridad").
2. `forward_volatility` → ¿cuánta volatilidad viene? (regresión; es el único
   de los tres con señal predictiva fuerte y real).
3. `forward_return` → retorno log a H barras (regresión; alimenta el cono
   de precio conformal).

Decisiones de diseño que protegen la honestidad del label
-----------------------------------------------------------
- **Entrada al open siguiente.** La decisión se toma al cierre de la vela i,
  el fill ocurre en el open de i+1. Etiquetar con el close de i como precio
  de entrada sería lookahead de una barra — el error clásico que infla el
  win rate del backtest y desaparece en vivo.
- **Empate intrabarra resuelto contra el trader.** Si en la misma vela el
  high toca el TP y el low toca el SL, el OHLC no dice cuál ocurrió primero.
  Se asume SL. Cualquier otro supuesto regala probabilidad que no existe, y
  el usuario opera apalancado.
- **Barreras a nivel de mercado, costos en el retorno.** El TP y el SL se
  colocan a `mult · sigma_H` del precio de entrada: son los precios que el
  usuario efectivamente escribe en Binance, y así "P(toca TP antes que SL)"
  es literalmente lo que el KPI promete — una pregunta sobre el camino del
  precio.

  Se probó la alternativa de descontar los costos *dentro* de las barreras
  (desplazar el TP hacia afuera y el SL hacia adentro para netear el
  objetivo exacto). Es aritméticamente correcta pero rompe el KPI: con
  fricción de 0.14% y sigma_H de 1%, un setup nominalmente simétrico de
  ±0.5 sigma queda con barreras brutas de +0.64% / −0.36%, o sea 1.8:1 en
  contra, y la "probabilidad" deja de corresponder a los precios que el
  usuario ve en pantalla.

  Los costos no desaparecen: entran completos en `net_return` (fees +
  slippage + drag de funding por barra), que es lo que alimenta el EV del
  KPI 2 y la regla de emisión. Un setup cuyo TP no supera el costo es
  imposible de ganar y se descarta al etiquetar, no después.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

from bob.signals import numeric as nm

#: Binance Futures VIP0 taker: 0.05% por lado → 0.10% round-trip.
DEFAULT_FEE_ROUNDTRIP_PCT: Final = 0.0010
#: Slippage asumido en entrada+salida para un par líquido en 15m.
DEFAULT_SLIPPAGE_PCT: Final = 0.0004
#: Funding típico 0.01% cada 8h, prorrateado por barra. Se aplica como costo
#: a ambas direcciones: conservador (un short suele *cobrar* funding cuando
#: la tasa es positiva). Fase 7 puede cablear la serie real de funding.
DEFAULT_FUNDING_PCT_PER_8H: Final = 0.0001


@dataclass(frozen=True)
class BarrierConfig:
    """Definición de un setup: cuánto se busca, cuánto se arriesga, por cuánto tiempo.

    Los múltiplos son sobre volatilidad, no porcentajes fijos. Un TP de
    "+1%" es trivial en un día agitado e inalcanzable en uno plano; un TP de
    "1.5 sigmas" significa lo mismo en ambos, que es lo que permite entrenar
    un solo modelo sobre dos años de regímenes distintos.

    **La sigma es la del horizonte completo**, no la de una barra:
    sigma_H = sigma_barra · sqrt(H). Escalar por barra parece equivalente y
    no lo es: en 15m la sigma por barra de ETH ronda 0.25%, así que un SL de
    "1.0 sigma" pediría arriesgar 0.25% neto cuando la fricción de
    ida y vuelta ya son 0.14%. Las barreras salen ~5:1 en contra y el setup
    es imposible de ganar por aritmética, no por falta de señal. Con sigma
    del horizonte (~1% a H=16), el costo pesa ~14% del riesgo y el problema
    vuelve a ser predecir, que es de lo que se trata.
    """

    # Defaults elegidos por barrido empírico sobre 2 años de ETHUSDT 15m
    # (ver docs/PROBABILITY_MODEL.md §"Elección de barreras"), no por
    # intuición. Criterios: que la barrera vertical no domine el label
    # (aquí ~9% de los casos, contra 47% con barreras de 1.0 sigma) y que
    # queden suficientes muestras efectivas tras ponderar por unicidad.
    tp_mult: float = 0.5
    sl_mult: float = 0.5
    horizon_bars: int = 16
    vol_window_bars: int = 96
    fee_roundtrip_pct: float = DEFAULT_FEE_ROUNDTRIP_PCT
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT
    funding_pct_per_8h: float = DEFAULT_FUNDING_PCT_PER_8H

    @property
    def cost_pct(self) -> float:
        """Costo fijo round-trip: fees + slippage."""
        return self.fee_roundtrip_pct + self.slippage_pct

    def funding_per_bar(self, timeframe_ms: int) -> float:
        bars_per_8h = (8 * 3_600_000) / timeframe_ms
        return self.funding_pct_per_8h / max(bars_per_8h, 1.0)

    def breakeven_probability(self, sigma: float) -> float:
        """Probabilidad mínima de TP para que el setup tenga EV >= 0.

        De P·TP − (1−P)·SL − costo = 0 sale P = (SL + costo) / (TP + SL),
        todo en unidades de precio. Es el número contra el que hay que leer
        el KPI: una Seguridad de 45% puede ser excelente o ruinosa según
        dónde caiga este umbral.
        """
        tp = self.tp_mult * sigma
        sl = self.sl_mult * sigma
        total = tp + sl
        if total <= 0:
            return float("nan")
        return (sl + self.cost_pct) / total


@dataclass(frozen=True)
class BarrierLabels:
    """Resultado del etiquetado triple-barrier, alineado con la serie de velas.

    `label[i] == -1` marca una fila sin label utilizable (no hay H barras
    futuras, o la volatilidad no estaba definida). Nunca se rellena con 0:
    un "no sé" disfrazado de "perdió" sesga el modelo hacia abajo.
    """

    label: np.ndarray  # int8: 1 = TP primero, 0 = SL o vertical, -1 = sin label
    resolution: np.ndarray  # int8: 1 = TP, 0 = SL, 2 = expiró en la vertical, -1 = sin label
    touch_idx: np.ndarray  # int64: índice de barra donde se resolvió, -1 si sin label
    net_return: np.ndarray  # float64: retorno neto realizado del setup
    tp_price: np.ndarray  # float64: barrera superior efectiva (bruta)
    sl_price: np.ndarray  # float64: barrera inferior efectiva (bruta)
    entry_price: np.ndarray  # float64: open de la barra i+1
    direction: str  # "long" | "short"

    @property
    def usable(self) -> np.ndarray:
        """Máscara booleana de filas con label válido."""
        return self.label >= 0

    def resolution_mix(self) -> dict[str, float]:
        """Fracción de setups que terminó en TP, en SL y en la barrera vertical.

        Diagnóstico obligatorio antes de confiar en el label: si la vertical
        domina, el target dejó de ser "TP antes que SL" y pasó a ser
        "¿se movió algo?", que es otra pregunta y con otra tasa base.
        """
        res = self.resolution[self.usable]
        n = max(res.size, 1)
        return {
            "tp": float((res == 1).sum() / n),
            "sl": float((res == 0).sum() / n),
            "vertical": float((res == 2).sum() / n),
            "n": float(res.size),
        }


def target_volatility(
    close: np.ndarray, window: int, horizon: int = 1, min_vol: float = 1e-5
) -> np.ndarray:
    """Volatilidad esperada del movimiento a `horizon` barras.

    Estima la sigma por barra sobre una ventana pasada y la escala por
    sqrt(horizon) (regla de raíz del tiempo). Causal: en el índice i usa
    solo retornos hasta i. `min_vol` evita barreras degeneradas en tramos
    de precio congelado.
    """
    r = nm.log_returns(close)
    per_bar = nm.realized_vol(r, window) / np.sqrt(window)
    scaled = np.asarray(per_bar * np.sqrt(max(horizon, 1)), dtype=np.float64)
    return np.maximum(np.nan_to_num(scaled, nan=0.0), min_vol)


def triple_barrier_labels(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    open_: np.ndarray,
    config: BarrierConfig,
    timeframe_ms: int,
    direction: str = "long",
) -> BarrierLabels:
    """Etiqueta cada barra con el resultado del setup definido por `config`.

    Para cada barra i: se entra al open de i+1 y se recorren las barras
    i+1..i+H mirando si el precio toca primero la barrera de TP o la de SL.
    """
    if direction not in ("long", "short"):
        raise ValueError("direction debe ser 'long' o 'short'")

    n = close.shape[0]
    horizon = config.horizon_bars
    sigma = target_volatility(close, config.vol_window_bars, horizon)
    cost = config.cost_pct
    funding = config.funding_per_bar(timeframe_ms)
    is_long = direction == "long"

    label = np.full(n, -1, dtype=np.int8)
    resolution = np.full(n, -1, dtype=np.int8)
    touch_idx = np.full(n, -1, dtype=np.int64)
    net_return = np.zeros(n, dtype=np.float64)
    tp_price = np.full(n, np.nan, dtype=np.float64)
    sl_price = np.full(n, np.nan, dtype=np.float64)
    entry_price = np.full(n, np.nan, dtype=np.float64)

    # El warm-up de sigma marca desde dónde hay barreras confiables.
    first = config.vol_window_bars
    last = n - horizon - 1  # se necesita open[i+1] y hasta close[i+horizon]

    for i in range(first, last + 1):
        entry = open_[i + 1]
        if not np.isfinite(entry) or entry <= 0:
            continue

        s = sigma[i]
        # Distancias de mercado: los precios que el usuario pone en Binance.
        tp_dist = config.tp_mult * s
        sl_dist = config.sl_mult * s
        if tp_dist <= cost:
            # Ganar el TP completo no alcanza para pagar la fricción: el
            # setup es imposible de ganar por aritmética, no por mercado.
            continue

        if is_long:
            tp_level = entry * (1.0 + tp_dist)
            sl_level = entry * (1.0 - sl_dist)
        else:
            tp_level = entry * (1.0 - tp_dist)
            sl_level = entry * (1.0 + sl_dist)

        tp_price[i] = tp_level
        sl_price[i] = sl_level
        entry_price[i] = entry

        outcome = 0
        how = 2  # por defecto expira en la barrera vertical
        resolved_at = i + horizon
        exit_px = close[i + horizon]

        for k in range(1, horizon + 1):
            bar = i + k
            bar_hi, bar_lo = high[bar], low[bar]
            if is_long:
                hit_tp = bar_hi >= tp_level
                hit_sl = bar_lo <= sl_level
            else:
                hit_tp = bar_lo <= tp_level
                hit_sl = bar_hi >= sl_level

            if hit_sl:
                # Empate intrabarra: se resuelve contra el trader (ver docstring).
                outcome, how = 0, 0
                resolved_at = bar
                exit_px = sl_level
                break
            if hit_tp:
                outcome, how = 1, 1
                resolved_at = bar
                exit_px = tp_level
                break

        bars_held = resolved_at - i
        gross = (exit_px / entry - 1.0) if is_long else (1.0 - exit_px / entry)
        label[i] = outcome
        resolution[i] = how
        touch_idx[i] = resolved_at
        net_return[i] = gross - cost - funding * bars_held

    return BarrierLabels(
        label=label,
        resolution=resolution,
        touch_idx=touch_idx,
        net_return=net_return,
        tp_price=tp_price,
        sl_price=sl_price,
        entry_price=entry_price,
        direction=direction,
    )


def forward_volatility(close: np.ndarray, horizon: int) -> np.ndarray:
    """Volatilidad realizada de las próximas `horizon` barras. NaN al final.

    Este es el target de regresión con señal real: la volatilidad tiene
    clustering (Mandelbrot 1963) y es predecible out-of-sample, a diferencia
    de la dirección del precio.
    """
    n = close.shape[0]
    r = nm.log_returns(close)
    out = np.full(n, np.nan)
    r2 = np.nan_to_num(r * r, nan=0.0)
    csum = np.cumsum(np.insert(r2, 0, 0.0))
    for i in range(n - horizon):
        # Retornos de las barras i+1 .. i+horizon.
        out[i] = np.sqrt(csum[i + horizon + 1] - csum[i + 1])
    return out


def forward_return(close: np.ndarray, horizon: int) -> np.ndarray:
    """Retorno log a `horizon` barras vista. NaN donde no hay futuro."""
    n = close.shape[0]
    out = np.full(n, np.nan)
    if n > horizon:
        out[: n - horizon] = np.log(
            np.maximum(close[horizon:], nm.EPS) / np.maximum(close[: n - horizon], nm.EPS)
        )
    return out


def label_span(touch_idx: np.ndarray, horizon: int) -> np.ndarray:
    """Índice de barra en que termina la influencia de cada label.

    Para filas sin label devuelve -1. Es el insumo de `uniqueness_weights`.
    """
    span = touch_idx.copy()
    span[span < 0] = -1
    return span


def concurrency(span: np.ndarray, n: int) -> np.ndarray:
    """Cuántos labels están "vivos" en cada barra.

    Un label emitido en i y resuelto en `span[i]` ocupa las barras i+1..span[i].
    Dos labels que comparten barras comparten información: por eso no son
    observaciones independientes y hay que ponderarlos.
    """
    counts = np.zeros(n + 1, dtype=np.float64)
    for i, end in enumerate(span):
        if end < 0:
            continue
        counts[i + 1] += 1.0
        counts[min(end + 1, n)] -= 1.0
    return np.cumsum(counts)[:n]


def uniqueness_weights(span: np.ndarray, n: int) -> np.ndarray:
    """Peso de muestra por unicidad promedio (López de Prado, cap. 4).

    Los labels de triple-barrier se solapan: la barra i y la i+1 comparten
    casi todo su futuro. Tratarlas como independientes le da al modelo la
    ilusión de tener H veces más datos de los que tiene, y hace que la
    validación mienta. El peso de cada muestra es el promedio de 1/c_t sobre
    las barras que ocupa: una muestra que no comparte futuro con nadie pesa
    1, una que comparte con otras 3 pesa ~0.25.
    """
    conc = concurrency(span, n)
    weights = np.zeros(n, dtype=np.float64)
    inv = np.where(conc > 0, 1.0 / np.maximum(conc, 1.0), 0.0)
    cum_inv = np.cumsum(np.insert(inv, 0, 0.0))
    for i, end in enumerate(span):
        if end < 0:
            continue
        start = i + 1
        stop = min(end + 1, n)
        length = stop - start
        if length <= 0:
            continue
        weights[i] = (cum_inv[stop] - cum_inv[start]) / length
    return weights
