"""Ensamblado del feature vector — PURO, sin I/O.

Todos los features son **estacionarios y adimensionales** por construcción:
ratios, retornos log, z-scores y percentiles móviles. Ninguno es un precio
en USD.

No es cosmética. Un modelo entrenado sobre niveles de precio aprende el
rango histórico del símbolo y se rompe en cuanto el precio sale de él —
además de hacer imposible la promesa de CLAUDE.md de que el motor sea
agnóstico del símbolo. Con features adimensionales, el modelo entrenado en
ETH aplica a SOL sin reentrenar.

**Causalidad**: `build_features` devuelve `valid_from`, el primer índice en
que todas las ventanas están llenas. Las filas anteriores contienen NaN y
deben descartarse — nunca imputarse hacia atrás.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

from bob.data.store import OHLCVSeries
from bob.signals import numeric as nm

#: Horizontes en horas de los que se derivan las ventanas. Se traducen a
#: barras según el timeframe, así el mismo set de features significa lo
#: mismo en 5m, 15m o 1h.
HORIZONS_H: Final[tuple[float, ...]] = (1.0, 4.0, 12.0, 24.0, 72.0)

#: Ventana larga para percentiles y z-scores de contexto (7 días).
CONTEXT_H: Final[float] = 168.0


@dataclass(frozen=True)
class FeatureSet:
    """Matriz de features alineada 1:1 con la serie de velas.

    `X[i]` son los features observables **al cierre de la vela i**. Un modelo
    que predice desde `X[i]` solo puede usar información hasta ese cierre.
    """

    X: np.ndarray  # (n_bars, n_features) float64
    names: list[str]
    open_time: np.ndarray  # int64, alineado con X
    valid_from: int  # primer índice sin NaN por warm-up
    symbol: str
    timeframe: str

    def __len__(self) -> int:
        return int(self.X.shape[0])

    @property
    def n_features(self) -> int:
        return int(self.X.shape[1])

    def index_of(self, name: str) -> int:
        return self.names.index(name)

    def column(self, name: str) -> np.ndarray:
        return self.X[:, self.index_of(name)]


def bars_per_hour(timeframe_ms: int) -> float:
    return 3_600_000.0 / timeframe_ms


def _safe_log(x: np.ndarray) -> np.ndarray:
    """Log con piso, preservando los NaN de entrada como NaN.

    El piso evita -inf en barras de volumen cero; los NaN se conservan para
    que la ventana que los contiene quede marcada como inválida en vez de
    producir un número plausible sobre datos que no existen.
    """
    x = np.asarray(x, dtype=np.float64)
    out = np.log(np.maximum(x, nm.EPS))
    return np.where(np.isfinite(x), out, np.nan)


def _w(hours: float, tf_ms: int, minimum: int = 2) -> int:
    """Traduce horas a barras del timeframe, con piso para que la ventana exista."""
    return max(minimum, int(round(hours * bars_per_hour(tf_ms))))


def build_features(series: OHLCVSeries) -> FeatureSet:
    """Construye la matriz de features causales desde una serie de velas."""
    n = len(series)
    if n == 0:
        return FeatureSet(
            X=np.zeros((0, 0)),
            names=[],
            open_time=series.open_time,
            valid_from=0,
            symbol=series.symbol,
            timeframe=series.timeframe,
        )

    tf_ms = series.interval_ms
    o, h, low, c = series.open, series.high, series.low, series.close
    vol, qvol, tbv = series.volume, series.quote_volume, series.taker_buy_volume
    ntr = series.n_trades.astype(np.float64)

    r1 = nm.log_returns(c)
    atr14 = nm.atr(h, low, c, 14)
    atr_pct = nm.safe_div(atr14, c)

    cols: list[np.ndarray] = []
    names: list[str] = []

    def add(name: str, values: np.ndarray) -> None:
        cols.append(np.asarray(values, dtype=np.float64))
        names.append(name)

    # ------------------------------------------------------------------ #
    # 1. Momentum / tendencia
    #    Retorno acumulado a distintos horizontes, escalado por volatilidad:
    #    un +2% en régimen tranquilo no significa lo mismo que en uno agitado.
    # ------------------------------------------------------------------ #
    for hrs in HORIZONS_H:
        w = _w(hrs, tf_ms)
        ret = nm.rolling_sum(r1, w)
        add(f"mom_{hrs:g}h", ret)
        add(f"mom_{hrs:g}h_vol_adj", nm.safe_div(ret, nm.realized_vol(r1, w)))

    for hrs in (4.0, 24.0, 72.0):
        w = _w(hrs, tf_ms)
        ema = nm.ewma(c, w)
        add(f"ema_dist_{hrs:g}h", nm.safe_div(c - ema, np.maximum(atr14, nm.EPS)))

    ema_fast = nm.ewma(c, _w(3.0, tf_ms))
    ema_slow = nm.ewma(c, _w(12.0, tf_ms))
    add("ema_cross", nm.safe_div(ema_fast - ema_slow, np.maximum(atr14, nm.EPS)))

    macd = nm.ewma(c, 12) - nm.ewma(c, 26)
    add("macd_hist", nm.safe_div(macd - nm.ewma(macd, 9), np.maximum(atr14, nm.EPS)))

    # ------------------------------------------------------------------ #
    # 2. Volatilidad — el bloque con señal predictiva real (clustering).
    #    Tres estimadores porque miden cosas distintas: la realizada usa
    #    cierres, Parkinson el recorrido intrabarra, Garman-Klass el OHLC
    #    completo. Su divergencia informa sobre gaps y colas.
    # ------------------------------------------------------------------ #
    w_ctx = _w(CONTEXT_H, tf_ms)
    for hrs in (1.0, 4.0, 24.0):
        w = _w(hrs, tf_ms)
        rv = nm.realized_vol(r1, w)
        add(f"rv_{hrs:g}h", rv)
        add(f"rv_{hrs:g}h_rank", nm.rolling_rank(rv, w_ctx))

    add("parkinson_4h", nm.parkinson_vol(h, low, _w(4.0, tf_ms)))
    add("garman_klass_4h", nm.garman_klass_vol(o, h, low, c, _w(4.0, tf_ms)))
    add("atr_pct", atr_pct)
    add("atr_pct_rank", nm.rolling_rank(atr_pct, w_ctx))

    # Term structure de volatilidad: >1 = agitación reciente sobre la basal.
    rv_short = nm.realized_vol(r1, _w(1.0, tf_ms))
    rv_long = nm.realized_vol(r1, _w(24.0, tf_ms))
    scale = np.sqrt(_w(24.0, tf_ms) / _w(1.0, tf_ms))
    add("vol_term_structure", nm.safe_div(rv_short * scale, rv_long, fill=1.0))
    add("vol_of_vol", nm.safe_div(nm.rolling_std(rv_short, w_ctx), rv_long))

    # ------------------------------------------------------------------ #
    # 3. Osciladores / reversión a la media
    # ------------------------------------------------------------------ #
    add("rsi_14", nm.rsi(c, 14) / 100.0)
    add("rsi_48", nm.rsi(c, 48) / 100.0)

    for hrs in (4.0, 24.0):
        w = _w(hrs, tf_ms)
        vwap = nm.rolling_vwap(c, h, low, vol, w)
        add(f"vwap_dist_{hrs:g}h", nm.safe_div(c - vwap, np.maximum(atr14, nm.EPS)))

    w_bb = _w(4.0, tf_ms)
    ma = nm.rolling_mean(c, w_bb)
    sd = nm.rolling_std(c, w_bb)
    add("bollinger_pctb", nm.safe_div(c - ma, 2.0 * np.maximum(sd, nm.EPS), fill=0.0))

    # ------------------------------------------------------------------ #
    # 4. Estructura de precio — dónde está el precio en su rango reciente
    # ------------------------------------------------------------------ #
    for hrs in (4.0, 24.0, 72.0):
        w = _w(hrs, tf_ms)
        hi = nm.rolling_max(h, w)
        lo = nm.rolling_min(low, w)
        add(f"donchian_pos_{hrs:g}h", nm.safe_div(c - lo, hi - lo, fill=0.5))
        add(f"donchian_width_{hrs:g}h", nm.safe_div(hi - lo, np.maximum(atr14, nm.EPS)))

    add("close_rank_ctx", nm.rolling_rank(c, w_ctx))

    # Cuerpo vs mecha de la última barra: presión de rechazo.
    bar_range = np.maximum(h - low, nm.EPS)
    add("bar_body_ratio", nm.safe_div(c - o, bar_range))
    add("bar_upper_wick", nm.safe_div(h - np.maximum(o, c), bar_range))
    add("bar_lower_wick", nm.safe_div(np.minimum(o, c) - low, bar_range))

    # ------------------------------------------------------------------ #
    # 5. Microestructura — gratis en la kline, sin stream adicional.
    #    Relevante porque ~70-90% del volumen es algorítmico y deja huella.
    # ------------------------------------------------------------------ #
    taker_ratio = nm.safe_div(tbv, vol, fill=0.5)
    add("taker_buy_ratio", taker_ratio)
    for hrs in (1.0, 4.0):
        w = _w(hrs, tf_ms)
        add(f"taker_buy_ratio_ma_{hrs:g}h", nm.rolling_mean(taker_ratio, w) - 0.5)

    # Volume delta normalizado: (compras - ventas) / total, en [-1, 1].
    add("volume_delta", nm.safe_div(2.0 * tbv - vol, vol))
    add("volume_delta_ma_4h", nm.rolling_mean(nm.safe_div(2.0 * tbv - vol, vol), _w(4.0, tf_ms)))

    # Logaritmo puro, no log1p: bajo un cambio de escala (mismo par a otro
    # nivel de precio, u otro símbolo con otro volumen típico) log(k·x) =
    # log(k) + log(x), y el z-score borra el desplazamiento constante. Con
    # log1p el "+1" rompe esa invarianza y el feature pasa a depender del
    # nivel absoluto, que es justo lo que hace que un modelo entrenado en
    # ETH no sirva en otro par.
    add("volume_z", nm.zscore(_safe_log(vol), w_ctx))
    add("n_trades_z", nm.zscore(_safe_log(ntr), w_ctx))

    # Tamaño medio de trade: sube cuando entra tamaño institucional.
    avg_trade = nm.safe_div(qvol, np.maximum(ntr, 1.0))
    add("avg_trade_size_z", nm.zscore(_safe_log(avg_trade), w_ctx))

    # Iliquidez de Amihud: cuánto precio mueve cada dólar operado.
    # Se computa como log|r| - log(qvol) en vez de log(|r|/qvol) para que el
    # piso numérico caiga sobre |r|, que es invariante a escala. Aplicarlo
    # sobre el cociente hace que el recorte se active a distinto precio en
    # cada símbolo y reintroduce la dependencia del nivel.
    amihud_log = _safe_log(np.abs(r1)) - _safe_log(qvol)
    add("amihud_illiq", nm.zscore(amihud_log, w_ctx))

    # ------------------------------------------------------------------ #
    # 6. Estacionalidad intradía — sesiones Asia / Europa / EE.UU.
    #    Codificada en seno/coseno para que 23:59 y 00:01 queden contiguas.
    # ------------------------------------------------------------------ #
    hour_of_day = (series.open_time % 86_400_000) / 3_600_000.0
    add("hour_sin", np.sin(2.0 * np.pi * hour_of_day / 24.0))
    add("hour_cos", np.cos(2.0 * np.pi * hour_of_day / 24.0))
    # epoch ms 0 fue jueves; el +4 alinea el índice 0 al lunes.
    day_of_week = ((series.open_time // 86_400_000) + 4) % 7
    add("dow_sin", np.sin(2.0 * np.pi * day_of_week / 7.0))
    add("dow_cos", np.cos(2.0 * np.pi * day_of_week / 7.0))

    X = np.column_stack(cols) if cols else np.zeros((n, 0))

    finite_rows = np.all(np.isfinite(X), axis=1)
    valid_idx = np.flatnonzero(finite_rows)
    valid_from = int(valid_idx[0]) if valid_idx.size else n

    return FeatureSet(
        X=X,
        names=names,
        open_time=series.open_time,
        valid_from=valid_from,
        symbol=series.symbol,
        timeframe=series.timeframe,
    )


def feature_families(names: list[str]) -> dict[str, list[str]]:
    """Agrupa features por familia — alimenta el desglose de la Analysis page."""
    families: dict[str, list[str]] = {
        "momentum": [],
        "volatilidad": [],
        "osciladores": [],
        "estructura": [],
        "microestructura": [],
        "estacionalidad": [],
    }
    for name in names:
        if name.startswith(("mom_", "ema_", "macd")):
            families["momentum"].append(name)
        elif name.startswith(("rv_", "parkinson", "garman", "atr_", "vol_")):
            families["volatilidad"].append(name)
        elif name.startswith(("rsi_", "vwap_", "bollinger")):
            families["osciladores"].append(name)
        elif name.startswith(("donchian", "close_rank", "bar_")):
            families["estructura"].append(name)
        elif name.startswith(("taker_", "volume_", "n_trades", "avg_trade", "amihud")):
            families["microestructura"].append(name)
        elif name.startswith(("hour_", "dow_")):
            families["estacionalidad"].append(name)
    return families
