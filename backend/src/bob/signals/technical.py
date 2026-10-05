"""Análisis técnico estructural: pivotes, niveles, liquidez y order blocks. PURO.

Este módulo existe para dos usos que NO pueden divergir:

1. **El mapa del dashboard**: qué soportes, resistencias, zonas de liquidez y
   order blocks están vivos en la última barra. Es contexto, no señal.
2. **El backtest pre-registrado** de dos reglas clásicas —barrido de liquidez
   y retorno a un order block— (`docs/PREREGISTRO_TECNICO.md`). Si una regla
   se mide con una definición y se dibuja con otra, el número que el panel
   cita al lado del dibujo no describe al dibujo.

Por eso hay **un solo** recorrido (`scan`) que produce los eventos históricos
y el estado final a la vez.

**Causalidad, que es donde este tipo de código miente.** Un pivote de `k`
barras a cada lado en el índice `j` solo se conoce al cierre de la barra
`j + k`: dibujar el "mínimo de ayer" como soporte desde el propio mínimo es
lookahead de la regla 5, y es el error más común de los indicadores de
estructura de mercado. Acá un pivote entra al estado recién en `j + k`, y un
evento en la barra `i` solo puede usar niveles confirmados hasta `i - 1`.
Hay un test que muta el futuro y exige que el pasado no cambie.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

import bob.signals.numeric as nm

LONG = "long"
SHORT = "short"


@dataclass(frozen=True)
class TechnicalParams:
    """Parámetros de detección. Los defaults son los del pre-registro (§3)."""

    #: Barras a cada lado para que un extremo cuente como pivote.
    pivot_k: int = 3
    atr_period: int = 14
    #: Cuántas barras sigue viva una zona de liquidez sin ser tomada.
    pool_lookback: int = 192
    #: Tolerancia para agrupar pivotes en un nivel (y para "máximos iguales").
    level_tol_atr: float = 0.25
    #: Cuerpo mínimo de la vela de desplazamiento que crea un order block.
    impulse_atr: float = 1.0
    #: Cuántas velas atrás se busca la última vela contraria.
    ob_search: int = 5
    #: Cuántas barras sigue vivo un order block sin ser tocado.
    ob_max_age: int = 96
    #: Margen del stop más allá del extremo estructural.
    stop_buffer_atr: float = 0.1


# --------------------------------------------------------------------------- #
# Pivotes
# --------------------------------------------------------------------------- #
def swing_points(high: np.ndarray, low: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Máscaras de pivote alto y bajo **en el índice del extremo**.

    Alto en `j`: estrictamente mayor que los `k` anteriores y mayor o igual
    que los `k` siguientes (la asimetría evita contar dos veces una meseta).
    El pivote se conoce recién en `j + k` — quien use estas máscaras tiene que
    respetar ese retraso; `scan` lo hace.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    n = high.size
    is_high = np.zeros(n, dtype=bool)
    is_low = np.zeros(n, dtype=bool)
    if k < 1 or n < 2 * k + 1:
        return is_high, is_low
    wh = sliding_window_view(high, 2 * k + 1)
    wl = sliding_window_view(low, 2 * k + 1)
    ch = wh[:, k]
    cl = wl[:, k]
    is_high[k : n - k] = (ch > wh[:, :k].max(axis=1)) & (ch >= wh[:, k + 1 :].max(axis=1))
    is_low[k : n - k] = (cl < wl[:, :k].min(axis=1)) & (cl <= wl[:, k + 1 :].min(axis=1))
    return is_high, is_low


# --------------------------------------------------------------------------- #
# Estado y eventos
# --------------------------------------------------------------------------- #
@dataclass
class Level:
    """Un pivote no tomado: hay stops del otro lado. Zona de liquidez."""

    price: float
    pivot_idx: int
    confirmed_idx: int
    side: str  # "high" o "low"


@dataclass
class OrderBlock:
    """Última vela contraria antes de un desplazamiento que rompe estructura."""

    direction: str  # LONG (zona de demanda) o SHORT (zona de oferta)
    top: float
    bottom: float
    candle_idx: int
    created_idx: int  # barra del desplazamiento; vivo desde created_idx + 1
    atr: float


@dataclass(frozen=True)
class SweepEvent:
    """Barrido: la barra perfora niveles de liquidez y cierra de vuelta adentro.

    `direction` es la del trade de reversión: barrer máximos da SHORT.
    """

    idx: int
    direction: str
    level: float
    extreme: float  # high (SHORT) o low (LONG) de la barra del barrido
    n_levels: int  # niveles tomados a la vez; >= 2 son "máximos/mínimos iguales"
    atr: float


@dataclass(frozen=True)
class OrderBlockTouch:
    """Primer retorno del precio a un order block vivo."""

    idx: int  # barra en que el precio entra a la zona
    direction: str
    top: float
    bottom: float
    created_idx: int
    atr: float


@dataclass
class TechnicalScan:
    """Lo que produce un recorrido: eventos históricos y estado al final."""

    n_bars: int
    atr: np.ndarray
    sweeps: list[SweepEvent] = field(default_factory=list)
    ob_touches: list[OrderBlockTouch] = field(default_factory=list)
    levels: list[Level] = field(default_factory=list)  # vivos al final
    order_blocks: list[OrderBlock] = field(default_factory=list)  # vivos al final
    #: Todos los pivotes confirmados (para soportes/resistencias del mapa).
    pivots: list[Level] = field(default_factory=list)


def scan(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    params: TechnicalParams | None = None,
) -> TechnicalScan:
    """Un solo recorrido causal barra a barra.

    En la barra `i` se decide con lo conocido al **cierre** de `i`, y los
    niveles y order blocks usados tienen que existir desde antes de que `i`
    abra. El orden dentro del bucle es lo que garantiza eso: primero se evalúa
    la barra contra el estado heredado, y recién después se agrega lo que la
    barra confirma.
    """
    p = params or TechnicalParams()
    o = np.asarray(open_, dtype=np.float64)
    h = np.asarray(high, dtype=np.float64)
    lo = np.asarray(low, dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    n = c.size
    atr = nm.atr(h, lo, c, p.atr_period)
    out = TechnicalScan(n_bars=n, atr=atr)
    if n == 0:
        return out

    is_high, is_low = swing_points(h, lo, p.pivot_k)
    k = p.pivot_k
    highs: list[Level] = []
    lows: list[Level] = []
    obs: list[OrderBlock] = []
    last_swing_high: float = float("nan")
    last_swing_low: float = float("nan")

    for i in range(n):
        a_prev = atr[i - 1] if i > 0 else atr[0]

        # --- 1. barrido contra los niveles heredados ------------------------ #
        highs = [lv for lv in highs if i - lv.pivot_idx <= p.pool_lookback]
        lows = [lv for lv in lows if i - lv.pivot_idx <= p.pool_lookback]
        taken_h = [lv for lv in highs if h[i] > lv.price]
        taken_l = [lv for lv in lows if lo[i] < lv.price]
        sweep_h = bool(taken_h) and c[i] < min(lv.price for lv in taken_h)
        sweep_l = bool(taken_l) and c[i] > max(lv.price for lv in taken_l)
        # Una vela que barre los dos lados no dice nada de dirección.
        if sweep_h and not sweep_l:
            out.sweeps.append(
                SweepEvent(
                    idx=i,
                    direction=SHORT,
                    level=max(lv.price for lv in taken_h),
                    extreme=float(h[i]),
                    n_levels=len(taken_h),
                    atr=float(atr[i]),
                )
            )
        if sweep_l and not sweep_h:
            out.sweeps.append(
                SweepEvent(
                    idx=i,
                    direction=LONG,
                    level=min(lv.price for lv in taken_l),
                    extreme=float(lo[i]),
                    n_levels=len(taken_l),
                    atr=float(atr[i]),
                )
            )
        # Barrido o ruptura, el nivel tomado deja de ser liquidez.
        if taken_h:
            highs = [lv for lv in highs if not h[i] > lv.price]
        if taken_l:
            lows = [lv for lv in lows if not lo[i] < lv.price]

        # --- 2. toque de order blocks heredados ----------------------------- #
        alive: list[OrderBlock] = []
        for ob in obs:
            if i - ob.created_idx > p.ob_max_age:
                continue
            if ob.direction == LONG:
                if o[i] < ob.bottom:  # abrió debajo de la zona: invalidado
                    continue
                if lo[i] <= ob.top:
                    out.ob_touches.append(
                        OrderBlockTouch(
                            idx=i,
                            direction=LONG,
                            top=ob.top,
                            bottom=ob.bottom,
                            created_idx=ob.created_idx,
                            atr=ob.atr,
                        )
                    )
                    continue
            else:
                if o[i] > ob.top:
                    continue
                if h[i] >= ob.bottom:
                    out.ob_touches.append(
                        OrderBlockTouch(
                            idx=i,
                            direction=SHORT,
                            top=ob.top,
                            bottom=ob.bottom,
                            created_idx=ob.created_idx,
                            atr=ob.atr,
                        )
                    )
                    continue
            alive.append(ob)
        obs = alive

        # --- 3. order blocks nuevos: desplazamiento + ruptura de estructura -- #
        body = c[i] - o[i]
        if np.isfinite(a_prev) and a_prev > 0 and i > 0:
            if (
                body >= p.impulse_atr * a_prev
                and np.isfinite(last_swing_high)
                and c[i] > last_swing_high >= c[i - 1]
            ):
                j = _last_opposite(o, c, i, p.ob_search, bearish=True)
                if j is not None:
                    obs.append(OrderBlock(LONG, float(h[j]), float(lo[j]), j, i, float(a_prev)))
            elif (
                -body >= p.impulse_atr * a_prev
                and np.isfinite(last_swing_low)
                and c[i] < last_swing_low <= c[i - 1]
            ):
                j = _last_opposite(o, c, i, p.ob_search, bearish=False)
                if j is not None:
                    obs.append(OrderBlock(SHORT, float(h[j]), float(lo[j]), j, i, float(a_prev)))

        # --- 4. pivotes que se confirman al cierre de i ---------------------- #
        # La ventana derecha de un pivote en j = i - k termina justo en i: la
        # máscara usa solo barras ya cerradas.
        j = i - k
        if j >= 0:
            if is_high[j]:
                lv = Level(float(h[j]), j, i, "high")
                highs.append(lv)
                out.pivots.append(lv)
                last_swing_high = float(h[j])
            if is_low[j]:
                lv = Level(float(lo[j]), j, i, "low")
                lows.append(lv)
                out.pivots.append(lv)
                last_swing_low = float(lo[j])

    out.levels = highs + lows
    out.order_blocks = obs
    return out


def _last_opposite(
    o: np.ndarray, c: np.ndarray, i: int, search: int, *, bearish: bool
) -> int | None:
    """Última vela del color contrario en `[i - search, i - 1]`."""
    for j in range(i - 1, max(i - search, 0) - 1, -1):
        if (c[j] < o[j]) if bearish else (c[j] > o[j]):
            return j
    return None


# --------------------------------------------------------------------------- #
# Soportes y resistencias: pivotes agrupados por cercanía
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SRLevel:
    price: float
    touches: int
    last_idx: int  # índice del pivote más reciente del grupo
    kind: str  # "support" o "resistance" respecto del precio actual


def cluster_levels(
    pivots: list[Level], tol: float, price_now: float, since_idx: int = 0
) -> list[SRLevel]:
    """Agrupa pivotes a menos de `tol` (en precio) en un nivel.

    Un nivel con más toques es más visible para todos — por eso importa, no
    porque tenga propiedades predictivas probadas (no las tiene, ver el
    pre-registro). El precio del grupo es la media de sus pivotes.
    """
    pts = sorted((pv for pv in pivots if pv.pivot_idx >= since_idx), key=lambda pv: pv.price)
    groups: list[list[Level]] = []
    for pv in pts:
        if groups and pv.price - groups[-1][-1].price <= tol:
            groups[-1].append(pv)
        else:
            groups.append([pv])
    out: list[SRLevel] = []
    for g in groups:
        price = float(np.mean([pv.price for pv in g]))
        out.append(
            SRLevel(
                price=price,
                touches=len(g),
                last_idx=max(pv.pivot_idx for pv in g),
                kind="resistance" if price > price_now else "support",
            )
        )
    return out
