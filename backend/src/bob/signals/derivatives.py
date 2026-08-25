"""Features de derivados — open interest, posicionamiento y funding. PURO.

Familia 4 del KPI 1. La pregunta que responde esta familia no la contesta el
precio: *quién* está del otro lado y *cuánto* está pagando por estarlo.

El feature más informativo de todos no es ninguna serie por separado sino su
**interacción con el precio**. Precio que sube con OI que sube es dinero nuevo
tomando riesgo direccional; precio que sube con OI que baja es short covering,
que se agota solo. Las dos cosas se ven idénticas en un gráfico de velas.

**Causalidad — la trampa central de este módulo.** Los derivados llegan en una
grilla distinta (5m para `metrics`, 8h para funding) y *con retraso de
publicación*. Alinear "el punto más cercano" a cada barra mete lookahead de la
peor especie: silencioso, y justo en la familia que más señal aporta. Por eso
`align_to_bars` solo acepta puntos cuyo timestamp **más el retraso de
publicación** ya ocurrió al cierre de la barra, y devuelve NaN cuando el último
punto disponible está demasiado viejo.

Todos los features salen adimensionales: logs de ratios, cambios log de OI y
z-scores. Ninguno es un notional en USDT.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

from bob.data.store import DerivativesSeries, OHLCVSeries
from bob.signals import numeric as nm
from bob.signals.features import CONTEXT_H, HORIZONS_H, window_bars

#: Retraso con el que Binance publica un punto de `/futures/data/*`: el bucket
#: que cierra a las 10:05 no está disponible a las 10:05. Un bucket entero de
#: margen es conservador — de más, nunca de menos.
DEFAULT_PUBLICATION_LAG_MS: Final = 300_000

#: Si el último punto observable quedó más viejo que esto, la barra sale NaN.
#: Arrastrar un OI de hace tres horas como si fuera el actual es inventar
#: información, que es exactamente lo que la regla 5 prohíbe.
DEFAULT_MAX_STALENESS_MS: Final = 3_600_000

#: El funding se cobra cada 8h y entre cobros la tasa vigente NO cambia: es una
#: escalera, no una serie muestreada. Su staleness tolerable es un ciclo entero.
FUNDING_MAX_STALENESS_MS: Final = 28_800_000


@dataclass(frozen=True)
class DerivativeFeatures:
    """Columnas de derivados alineadas 1:1 con la serie de velas."""

    X: np.ndarray  # (n_bars, n_features) float64
    names: list[str]
    open_time: np.ndarray  # int64

    def __len__(self) -> int:
        return int(self.X.shape[0])


def align_to_bars(
    point_time: np.ndarray,
    values: np.ndarray,
    bar_open_time: np.ndarray,
    interval_ms: int,
    *,
    publication_lag_ms: int = DEFAULT_PUBLICATION_LAG_MS,
    max_staleness_ms: int = DEFAULT_MAX_STALENESS_MS,
) -> np.ndarray:
    """Lleva una serie de otra grilla a la de las velas, sin mirar el futuro.

    Para cada barra `i` devuelve el **último** valor cuyo timestamp más el
    retraso de publicación no supera el cierre de esa barra, o NaN si ese valor
    quedó más viejo que `max_staleness_ms`.

    `point_time` debe venir ordenado ascendente (lo garantiza `load_derivatives`).
    """
    n = bar_open_time.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n == 0 or point_time.shape[0] == 0:
        return out

    # Cierre de la barra i. Binance cierra en `open + interval - 1`, pero para
    # decidir observabilidad da igual el milisegundo: lo que importa es que la
    # ventana sea [.., cierre] y no [.., apertura de la siguiente].
    bar_close = bar_open_time + interval_ms - 1
    observable_at = point_time.astype(np.int64) + publication_lag_ms

    # searchsorted 'right' - 1 = índice del último punto ya observable.
    idx = np.searchsorted(observable_at, bar_close, side="right") - 1
    has_point = idx >= 0
    if not np.any(has_point):
        return out

    safe_idx = np.where(has_point, idx, 0)
    candidate = values[safe_idx]
    age = bar_close - observable_at[safe_idx]
    fresh = has_point & (age <= max_staleness_ms)
    out[fresh] = candidate[fresh]
    return out


def _log_ratio(x: np.ndarray) -> np.ndarray:
    """log de un ratio positivo, NaN donde el ratio no es utilizable.

    Un ratio long/short es multiplicativo: 2.0 y 0.5 son la misma magnitud de
    desequilibrio en direcciones opuestas, y solo el log lo dice así. Además
    centra la serie en 0, que es lo que espera un modelo lineal.
    """
    return nm.log_positive(x)


def _log_change(x: np.ndarray, window: int) -> np.ndarray:
    """Cambio log de una magnitud positiva sobre `window` barras.

    NaN se propaga: si el OI de hace 4 horas no existe, el cambio a 4 horas
    tampoco. `np.roll` no sirve acá porque envuelve el array por el otro
    extremo, y eso sí sería lookahead.
    """
    out = np.full(x.shape[0], np.nan, dtype=np.float64)
    if x.shape[0] <= window:
        return out
    prev, curr = x[:-window], x[window:]
    valid = (prev > 0.0) & (curr > 0.0)
    ratio = np.where(valid, curr / np.where(valid, prev, 1.0), np.nan)
    out[window:] = nm.log_positive(ratio)
    return out


def build_derivative_features(
    series: OHLCVSeries,
    derivatives: DerivativesSeries,
    funding: DerivativesSeries | None = None,
    *,
    publication_lag_ms: int = DEFAULT_PUBLICATION_LAG_MS,
) -> DerivativeFeatures:
    """Construye las columnas de derivados alineadas a `series`.

    `derivatives` viene de `load_derivatives(symbol, "5m"|"15m"|...)` y
    `funding` de `load_derivatives(symbol, "funding")`. Ambos son opcionales en
    el sentido de que pueden venir vacíos: las columnas salen todas NaN y el
    modelo las trata como faltantes, que es el comportamiento que exige tener
    dos fuentes con coberturas distintas.
    """
    tf_ms = series.interval_ms
    ot = series.open_time
    n = len(series)

    def align(values: np.ndarray, max_staleness_ms: int = DEFAULT_MAX_STALENESS_MS) -> np.ndarray:
        return align_to_bars(
            derivatives.timestamp,
            values,
            ot,
            tf_ms,
            publication_lag_ms=publication_lag_ms,
            max_staleness_ms=max_staleness_ms,
        )

    oi = align(derivatives.open_interest)
    oi_value = align(derivatives.open_interest_value)
    ls_ratio = align(derivatives.long_short_ratio)
    taker = align(derivatives.taker_buy_sell_ratio)
    top_account = align(derivatives.top_trader_account_ratio)
    top_position = align(derivatives.top_trader_position_ratio)

    cols: list[np.ndarray] = []
    names: list[str] = []

    def add(name: str, values: np.ndarray) -> None:
        cols.append(values)
        names.append(name)

    ctx = window_bars(CONTEXT_H, tf_ms)

    # --- Open interest -------------------------------------------------------
    # El nivel de OI es dimensional (contratos) y sube con los años: solo entran
    # sus cambios y su posición relativa dentro de la ventana de contexto.
    log_oi = _log_ratio(oi)
    for hours in HORIZONS_H:
        add(f"oi_chg_{hours:g}h", _log_change(oi, window_bars(hours, tf_ms)))
    add("oi_z_ctx", nm.zscore(log_oi, ctx))

    # OI notional / OI en contratos es el precio implícito del archivo. No
    # aporta dirección, pero su z-score delata desalineamientos entre las dos
    # columnas — un canario de que el día ingerido vino corrupto.
    implied_px = np.where(oi > 0.0, oi_value / np.where(oi > 0.0, oi, 1.0), np.nan)
    add("oi_notional_ratio_z", nm.zscore(implied_px, ctx))

    # --- Interacción OI × precio (el feature que justifica la familia) -------
    r1 = nm.log_returns(series.close)
    for hours in (4.0, 24.0):
        w = window_bars(hours, tf_ms)
        d_oi = _log_change(oi, w)
        d_px = _log_change(series.close, w)
        # Producto de signos: +1 = OI y precio en la misma dirección (dinero
        # nuevo direccional), -1 = divergen (cobertura de posiciones abiertas).
        add(f"oi_px_agree_{hours:g}h", np.sign(d_oi) * np.sign(d_px))
        # Magnitud: cuánto OI se movió por cada punto de precio.
        add(f"oi_per_px_{hours:g}h", nm.safe_div(d_oi, np.abs(d_px) + nm.EPS, fill=np.nan))

    # --- Posicionamiento ------------------------------------------------------
    log_ls = _log_ratio(ls_ratio)
    add("ls_ratio_log", log_ls)
    add("ls_ratio_z", nm.zscore(log_ls, ctx))
    add("ls_ratio_chg_24h", _log_change(ls_ratio, window_bars(24.0, tf_ms)))

    log_top_acct = _log_ratio(top_account)
    log_top_pos = _log_ratio(top_position)
    add("top_account_log", log_top_acct)
    add("top_position_log", log_top_pos)
    add("top_position_z", nm.zscore(log_top_pos, ctx))
    # Ballenas contra multitud: el archivo da las dos poblaciones y la señal
    # está en la brecha, no en cada una por separado.
    add("top_vs_crowd", log_top_pos - log_ls)
    # Concentración: cuando el ratio por notional se despega del ratio por
    # cuentas, pocas cuentas grandes están cargando un lado.
    add("top_concentration", log_top_pos - log_top_acct)

    # --- Flujo taker ---------------------------------------------------------
    log_taker = _log_ratio(taker)
    add("taker_ratio_log", log_taker)
    add("taker_ratio_z", nm.zscore(log_taker, ctx))
    add("taker_ratio_mean_4h", nm.rolling_mean(log_taker, window_bars(4.0, tf_ms)))

    # --- Funding -------------------------------------------------------------
    # El funding ya es adimensional (fracción por período) y suele vivir en
    # 1e-4: entra tal cual, escalado a puntos básicos para que no quede pegado
    # al cero numérico de un modelo lineal.
    if funding is not None and len(funding) > 0:
        rate = align_to_bars(
            funding.timestamp,
            funding.funding_rate,
            ot,
            tf_ms,
            publication_lag_ms=0,  # el funding se conoce al momento del cobro
            max_staleness_ms=FUNDING_MAX_STALENESS_MS,
        )
    else:
        rate = np.full(n, np.nan, dtype=np.float64)
    add("funding_bps", rate * 10_000.0)
    add("funding_z", nm.zscore(rate, ctx))
    add("funding_cum_24h", nm.rolling_sum(rate, window_bars(24.0, tf_ms)) * 10_000.0)
    # Funding caro contra retorno reciente: pagar mucho por una posición que no
    # se mueve es la firma de un lado sobrecargado.
    add("funding_vs_return", np.sign(rate) * np.sign(nm.rolling_sum(r1, window_bars(24.0, tf_ms))))

    X = np.column_stack(cols) if cols else np.zeros((n, 0), dtype=np.float64)
    return DerivativeFeatures(X=X, names=names, open_time=ot)
