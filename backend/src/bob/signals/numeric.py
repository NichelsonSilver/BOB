"""Primitivas vectorizadas sobre arrays numpy — PURAS, sin I/O.

Por qué existe este módulo además de `indicators.py`: son dos caminos con
requisitos distintos, no una duplicación por descuido.

- `indicators.py` trabaja con `Decimal` sobre pocas velas. Es el camino de
  *presentación*: TP/SL, cuantización al tickSize, precios que el usuario
  copia a Binance. Ahí un error de redondeo en el último decimal importa.
- `numeric.py` (este) trabaja con `float64` sobre decenas de miles de velas.
  Es el camino de *modelado*: features, labels, backtest. Decimal sería
  ~100x más lento y la precisión extra no cambia nada — un feature es una
  cantidad estadística, no un precio ejecutable.

**Invariante de causalidad**: toda función acá es causal. El valor en el
índice `i` depende solo de `x[0..i]`. Las posiciones sin ventana suficiente
son `NaN`, nunca se rellenan hacia atrás. Cualquier `NaN` que aparezca
después del warm-up es un bug, no un dato faltante. Regla 5 de CLAUDE.md.
"""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import numpy.typing as npt

#: Alias del tipo que circula por todo el motor numerico.
FloatArray = npt.NDArray[np.float64]

EPS = 1e-12


def _as_f64(x: npt.ArrayLike) -> FloatArray:
    return np.asarray(x, dtype=np.float64)


def rolling_sum(x: FloatArray, window: int) -> FloatArray:
    """Suma móvil causal. out[i] = sum(x[i-window+1 .. i]); NaN antes.

    Los NaN se tratan como "ventana inválida", no como ceros: una ventana
    que contiene algún NaN devuelve NaN. Es deliberado y no cosmético — el
    cumsum ingenuo propaga un único NaN al resto de la serie para siempre,
    y como las series de retornos empiezan con NaN por construcción, eso
    vaciaría en silencio todos los features derivados.
    """
    if window < 1:
        raise ValueError("window debe ser >= 1")
    x = _as_f64(x)
    out = np.full(x.shape, np.nan)
    if x.size < window:
        return out

    invalid = ~np.isfinite(x)
    clean = np.where(invalid, 0.0, x)

    csum = np.cumsum(np.insert(clean, 0, 0.0))
    totals = csum[window:] - csum[:-window]

    cbad = np.cumsum(np.insert(invalid.astype(np.int64), 0, 0))
    bad_in_window = cbad[window:] - cbad[:-window]

    out[window - 1 :] = np.where(bad_in_window > 0, np.nan, totals)
    return out


def rolling_mean(x: FloatArray, window: int) -> FloatArray:
    """Media móvil causal."""
    return rolling_sum(x, window) / window


def rolling_std(x: FloatArray, window: int, ddof: int = 0) -> FloatArray:
    """Desviación estándar móvil causal (vía E[x²] - E[x]², clampeada a >= 0)."""
    x = _as_f64(x)
    mean = rolling_mean(x, window)
    mean_sq = rolling_mean(x * x, window)
    var = np.maximum(mean_sq - mean * mean, 0.0)
    if ddof:
        n = window
        if n <= ddof:
            return np.full(x.shape, np.nan)
        var = var * n / (n - ddof)
    return np.sqrt(var)


#: Celdas por bloque al recorrer ventanas deslizantes. Materializar la vista
#: completa de una serie de 70k velas con ventana de 672 son ~370 MB; en
#: bloques el pico de memoria queda acotado sin costar tiempo apreciable.
_WINDOW_CHUNK_CELLS = 4_000_000


def _window_blocks(n_rows: int, window: int) -> Iterator[tuple[int, int]]:
    """Genera (inicio, fin) de bloques de filas para recorrer ventanas por partes."""
    rows_per_block = max(1, _WINDOW_CHUNK_CELLS // max(window, 1))
    for start in range(0, n_rows, rows_per_block):
        yield start, min(start + rows_per_block, n_rows)


def _rolling_extreme(x: FloatArray, window: int, kind: str) -> FloatArray:
    x = _as_f64(x)
    out = np.full(x.shape, np.nan)
    if x.size < window:
        return out
    view = np.lib.stride_tricks.sliding_window_view(x, window)
    for start, stop in _window_blocks(view.shape[0], window):
        block = view[start:stop]
        agg = block.max(axis=1) if kind == "max" else block.min(axis=1)
        out[window - 1 + start : window - 1 + stop] = agg
    return out


def rolling_max(x: FloatArray, window: int) -> FloatArray:
    """Máximo móvil causal."""
    return _rolling_extreme(x, window, "max")


def rolling_min(x: FloatArray, window: int) -> FloatArray:
    """Mínimo móvil causal."""
    return _rolling_extreme(x, window, "min")


def rolling_rank(x: FloatArray, window: int) -> FloatArray:
    """Percentil del valor actual dentro de su ventana, en [0, 1].

    Feature robusto a cambios de escala: sirve igual en ETH a 1.500 que a
    4.000 USD, que es lo que hace al modelo agnóstico del símbolo.
    """
    x = _as_f64(x)
    out = np.full(x.shape, np.nan)
    if x.size < window:
        return out
    # Una comparación contra NaN devuelve False en silencio, lo que daría un
    # rank plausible calculado sobre datos que no existen: se marca la
    # ventana como inválida contando NaN con un cumsum barato.
    invalid = ~np.isfinite(x)
    cbad = np.cumsum(np.insert(invalid.astype(np.int64), 0, 0))
    bad_in_window = cbad[window:] - cbad[:-window]

    view = np.lib.stride_tricks.sliding_window_view(x, window)
    for start, stop in _window_blocks(view.shape[0], window):
        block = view[start:stop]
        ranks = (block < block[:, -1][:, None]).sum(axis=1) / (window - 1)
        out[window - 1 + start : window - 1 + stop] = np.where(
            bad_in_window[start:stop] > 0, np.nan, ranks
        )
    return out


def ewma(x: FloatArray, span: int) -> FloatArray:
    """EMA causal con el alpha convencional 2/(span+1), sin warm-up NaN.

    Semilla: el primer valor de la serie (equivalente a `adjust=False`).
    """
    if span < 1:
        raise ValueError("span debe ser >= 1")
    x = _as_f64(x)
    out = np.empty(x.shape)
    if x.size == 0:
        return out
    alpha = 2.0 / (span + 1.0)
    out[0] = x[0]
    for i in range(1, x.size):
        out[i] = alpha * x[i] + (1.0 - alpha) * out[i - 1]
    return out


def wilder_ema(x: FloatArray, period: int) -> FloatArray:
    """Suavizado de Wilder (alpha = 1/period), el que usan ATR y RSI clásicos."""
    if period < 1:
        raise ValueError("period debe ser >= 1")
    x = _as_f64(x)
    out = np.empty(x.shape)
    if x.size == 0:
        return out
    alpha = 1.0 / period
    out[0] = x[0]
    for i in range(1, x.size):
        out[i] = alpha * x[i] + (1.0 - alpha) * out[i - 1]
    return out


def log_returns(close: FloatArray) -> FloatArray:
    """Retornos log de una barra. out[0] = NaN (no hay barra previa)."""
    close = _as_f64(close)
    out = np.full(close.shape, np.nan)
    if close.size < 2:
        return out
    out[1:] = np.log(np.maximum(close[1:], EPS) / np.maximum(close[:-1], EPS))
    return out


def true_range(high: FloatArray, low: FloatArray, close: FloatArray) -> FloatArray:
    """True Range. out[0] = high-low (no hay cierre previo)."""
    high, low, close = _as_f64(high), _as_f64(low), _as_f64(close)
    out = np.empty(high.shape)
    if high.size == 0:
        return out
    out[0] = high[0] - low[0]
    if high.size > 1:
        prev_close = close[:-1]
        out[1:] = np.maximum(
            high[1:] - low[1:],
            np.maximum(np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close)),
        )
    return out


def atr(high: FloatArray, low: FloatArray, close: FloatArray, period: int = 14) -> FloatArray:
    """Average True Range con suavizado de Wilder."""
    return wilder_ema(true_range(high, low, close), period)


def rsi(close: FloatArray, period: int = 14) -> FloatArray:
    """RSI de Wilder en [0, 100]. NaN en el índice 0."""
    close = _as_f64(close)
    out = np.full(close.shape, np.nan)
    if close.size < 2:
        return out
    delta = np.diff(close)
    gains = wilder_ema(np.maximum(delta, 0.0), period)
    losses = wilder_ema(np.maximum(-delta, 0.0), period)
    rs = gains / np.maximum(losses, EPS)
    out[1:] = 100.0 - 100.0 / (1.0 + rs)
    return out


def rolling_vwap(close: FloatArray, high: FloatArray, low: FloatArray,
                 volume: FloatArray, window: int) -> FloatArray:
    """VWAP móvil sobre el precio típico (H+L+C)/3, ponderado por volumen."""
    typical = (_as_f64(high) + _as_f64(low) + _as_f64(close)) / 3.0
    vol = _as_f64(volume)
    num = rolling_sum(typical * vol, window)
    den = rolling_sum(vol, window)
    return num / np.maximum(den, EPS)


def realized_vol(returns: FloatArray, window: int) -> FloatArray:
    """Volatilidad realizada causal: sqrt(sum r² en la ventana).

    Es la raíz de la varianza realizada, la medida estándar de la literatura
    de forecasting de volatilidad (Andersen & Bollerslev). No anualizada:
    queda en unidades del timeframe, que es lo que el modelo necesita.
    """
    r = _as_f64(returns)
    r2 = np.where(np.isnan(r), np.nan, r * r)
    return np.sqrt(rolling_sum(r2, window))


def parkinson_vol(high: FloatArray, low: FloatArray, window: int) -> FloatArray:
    """Estimador de Parkinson (1980) usando el rango high-low.

    ~5x más eficiente que la varianza de cierres: usa el recorrido intrabarra
    en vez de solo el punto final. Subestima si hay gaps, por eso se usa
    junto a la realizada, no en su lugar.
    """
    high, low = _as_f64(high), _as_f64(low)
    hl = np.log(np.maximum(high, EPS) / np.maximum(low, EPS))
    factor = 1.0 / (4.0 * np.log(2.0))
    return np.sqrt(rolling_sum(factor * hl * hl, window))


def garman_klass_vol(
    open_: FloatArray, high: FloatArray, low: FloatArray, close: FloatArray, window: int
) -> FloatArray:
    """Estimador de Garman-Klass (1980): usa OHLC completo.

    El término puede salir negativo en barras patológicas; se clampea a 0
    antes de la raíz.
    """
    o, h, low_, c = _as_f64(open_), _as_f64(high), _as_f64(low), _as_f64(close)
    hl = np.log(np.maximum(h, EPS) / np.maximum(low_, EPS))
    co = np.log(np.maximum(c, EPS) / np.maximum(o, EPS))
    var = 0.5 * hl * hl - (2.0 * np.log(2.0) - 1.0) * co * co
    return np.sqrt(np.maximum(rolling_sum(var, window), 0.0))


def zscore(x: FloatArray, window: int) -> FloatArray:
    """Z-score móvil causal.

    Distingue dos casos que es tentador colapsar en 0 y que no son el mismo:

    - **Ventana plana** (datos válidos, desviación nula): el punto ES su media,
      así que 0 es la respuesta correcta y no una imputación.
    - **Ventana sin datos** (warm-up, o un hueco de la fuente): devuelve NaN.
      Poner 0 ahí diría "exactamente el promedio" justo donde no se sabe nada,
      y esa fila entraría al entrenamiento como si fuera una observación.

    Medido sobre 720 días de ETHUSDT 15m, la versión que imputaba 0 afectaba
    671 filas de warm-up y ninguna llegaba al modelo — pero solo porque otro
    feature tenía la ventana más larga. Depender de esa coincidencia es lo que
    esta versión elimina.
    """
    x = _as_f64(x)
    mean = rolling_mean(x, window)
    std = rolling_std(x, window)
    out = np.where(std > EPS, (x - mean) / np.maximum(std, EPS), 0.0)
    return np.where(np.isnan(mean), np.nan, out)


def safe_div(num: FloatArray, den: FloatArray, fill: float = 0.0) -> FloatArray:
    """División protegida: donde |den| < EPS devuelve `fill`."""
    num, den = _as_f64(num), _as_f64(den)
    return np.where(np.abs(den) > EPS, num / np.where(np.abs(den) > EPS, den, 1.0), fill)
