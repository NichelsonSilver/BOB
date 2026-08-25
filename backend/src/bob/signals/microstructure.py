"""Features de microestructura — el libro de órdenes. PURO.

Familia 3 del KPI 1. CLAUDE.md la justifica así: ~70-90% del volumen es
algorítmico y deja huellas sistemáticas. La huella más directa es la forma del
libro — cuánto notional hay esperando de cada lado y a qué distancia del mid.

Entra por `data/vision.py` desde el archivo `bookDepth/`, que trae un snapshot
cada ~30s con la profundidad **acumulada** a ±0,2/1/5% del mid. En una barra de
15m son ~30 fotos, ya promediadas por barra al ingerir.

La familia se parte en dos por una razón de datos, no de diseño: **el núcleo
(1% y 5%) existe en todo el archivo; el near-touch (0,2%) solo desde
~2026-01-15**. `core_names()` y `near_names` dicen cuál es cuál, y la máscara
`near_available` marca dónde vale la segunda mitad.

**Qué mide cada nivel.** No son tres versiones de lo mismo:

- **0,2%** es el near-touch: la liquidez que un market order de tamaño medio
  consume ahora. Su desbalance predice minutos, no horas. ⚠️ **Binance lo
  agregó al archivo recién alrededor de 2026-01-15**: antes de esa fecha estas
  columnas salen NaN y `near_available` queda en False. No es una carencia que
  se pueda rellenar — el dato no se registró.
- **1%** es lo que absorbe un impulso completo.
- **5%** es el muro de fondo: se mueve lento y dice cuán dispuesto está el
  mercado a defender el rango.

**Causalidad**: la barra `i` agrega snapshots ocurridos *dentro* de `i`, así que
es observable recién a su cierre — la misma convención que `features.py`. La
profundidad NO se arrastra hacia adelante: una barra sin datos de libro sale
NaN, porque un libro de hace media hora no describe el actual.

**Adimensionalidad**: el archivo trae notional en USDT, que crece con el precio
y con el tamaño del mercado. Acá solo salen ratios entre lados, ratios entre
niveles, z-scores y profundidad relativa al volumen de la propia barra.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

from bob.data.store import BookDepthSeries, OHLCVSeries
from bob.signals import numeric as nm
from bob.signals.features import CONTEXT_H, window_bars

#: Mínimo de snapshots dentro de la barra para que su promedio signifique algo.
#: Con cadencia de ~30s una barra de 15m trae ~30; menos de 5 es una barra que
#: el archivo cubrió a medias y su promedio es ruido con cara de dato.
MIN_SNAPSHOTS: Final = 5


@dataclass(frozen=True)
class MicrostructureFeatures:
    """Columnas de microestructura alineadas 1:1 con la serie de velas."""

    X: np.ndarray  # (n_bars, n_features) float64
    names: list[str]
    open_time: np.ndarray  # int64
    #: True donde la barra tiene libro utilizable (niveles 1% y 5%). El modelo
    #: lo usa para saber qué filas puede entrenar con esta familia y cuáles no.
    available: np.ndarray  # bool
    #: True donde además hay near-touch (0,2%). Es un subconjunto de
    #: `available` y arranca ~2026-01-15: las columnas `*_02` valen solo ahí.
    near_available: np.ndarray  # bool

    #: Columnas que dependen del near-touch. Se declara al construir, no se
    #: deduce del nombre: `book_slope_*` dependía del 0,2% sin decirlo en el
    #: nombre, y una heurística de substring no lo veía.
    near_names: list[str]

    def core_names(self) -> list[str]:
        """Columnas utilizables en toda la historia del archivo."""
        near = set(self.near_names)
        return [n for n in self.names if n not in near]

    def __len__(self) -> int:
        return int(self.X.shape[0])


def reindex_to_bars(
    book_open_time: np.ndarray,
    values: np.ndarray,
    bar_open_time: np.ndarray,
) -> np.ndarray:
    """Coloca cada barra de libro en su barra de velas homónima, o NaN.

    Es un join exacto por `open_time`, no una interpolación ni un forward-fill:
    las dos series ya viven en la misma grilla, y lo único que puede pasar es
    que al libro le falten barras. Rellenarlas sería inventar liquidez.
    """
    out = np.full(bar_open_time.shape[0], np.nan, dtype=np.float64)
    if book_open_time.shape[0] == 0 or bar_open_time.shape[0] == 0:
        return out
    idx = np.searchsorted(book_open_time, bar_open_time)
    within = idx < book_open_time.shape[0]
    safe = np.where(within, idx, 0)
    hit = within & (book_open_time[safe] == bar_open_time)
    out[hit] = values[safe][hit]
    return out


def _imbalance(bid: np.ndarray, ask: np.ndarray) -> np.ndarray:
    """(bid - ask) / (bid + ask), acotado en [-1, 1]. NaN si no hay libro.

    Se prefiere a `log(bid/ask)` porque acota: un lado que se vacía manda el log
    a -inf y arrastra cualquier z-score posterior, mientras el imbalance se
    satura ordenadamente en ±1.
    """
    total = bid + ask
    return np.where(total > nm.EPS, (bid - ask) / np.where(total > nm.EPS, total, 1.0), np.nan)


def _log_level_ratio(outer: np.ndarray, inner: np.ndarray) -> np.ndarray:
    """log(profundidad lejana / profundidad cercana) — la pendiente del libro.

    Alto = el libro está hueco cerca del mid y grueso lejos (el precio se mueve
    fácil hasta chocar). Bajo = liquidez concentrada en el touch.
    """
    valid = (outer > nm.EPS) & (inner > nm.EPS)
    return nm.log_positive(np.where(valid, outer / np.where(valid, inner, 1.0), np.nan))


def build_microstructure_features(
    series: OHLCVSeries,
    book: BookDepthSeries,
    *,
    min_snapshots: int = MIN_SNAPSHOTS,
) -> MicrostructureFeatures:
    """Construye las columnas de libro alineadas a `series`.

    `book` puede venir vacío: todas las columnas salen NaN y `available` queda
    en False, que es la forma honesta de decir "esta familia no cubre este
    tramo" en vez de rellenar con ceros.
    """
    tf_ms = series.interval_ms
    ot = series.open_time
    n = len(series)
    ctx = window_bars(CONTEXT_H, tf_ms)

    n_snap = reindex_to_bars(book.open_time, book.n_snapshots.astype(np.float64), ot)
    available = np.isfinite(n_snap) & (n_snap >= min_snapshots)

    def take(values: np.ndarray) -> np.ndarray:
        aligned = reindex_to_bars(book.open_time, values, ot)
        return np.where(available, aligned, np.nan)

    bid_02, ask_02 = take(book.bid_02), take(book.ask_02)
    bid_1, ask_1 = take(book.bid_1), take(book.ask_1)
    bid_5, ask_5 = take(book.bid_5), take(book.ask_5)
    # El near-touch llega como NaN en todo el tramo previo a ~2026-01-15: no
    # hace falta un flag aparte en la serie, la ausencia del número ES el flag.
    near_available = available & np.isfinite(bid_02) & np.isfinite(ask_02)

    cols: list[np.ndarray] = []
    names: list[str] = []
    near_names: list[str] = []

    def add(name: str, values: np.ndarray, *, near: bool = False) -> None:
        """Agrega una columna. `near=True` la marca como dependiente del 0,2%."""
        cols.append(values)
        names.append(name)
        if near:
            near_names.append(name)

    imb_02 = _imbalance(bid_02, ask_02)
    imb_1 = _imbalance(bid_1, ask_1)
    imb_5 = _imbalance(bid_5, ask_5)
    total_1 = bid_1 + ask_1

    # ======================================================================= #
    # NÚCLEO — niveles 1% y 5%, presentes en toda la historia del archivo.
    # ======================================================================= #

    add("book_imbalance_1", imb_1)
    add("book_imbalance_5", imb_5)
    add("book_imbalance_1_z", nm.zscore(imb_1, ctx))
    add("book_imbalance_5_z", nm.zscore(imb_5, ctx))

    # Persistencia: un libro torcido durante horas pesa distinto de uno que se
    # torció en la última barra.
    for hours in (1.0, 4.0):
        add(f"book_imbalance_1_mean_{hours:g}h", nm.rolling_mean(imb_1, window_bars(hours, tf_ms)))

    # Lo cercano contra el muro: cuando divergen, la liquidez que rodea al
    # precio contradice al fondo del libro.
    add("book_imbalance_spread_1_5", imb_1 - imb_5)

    # Forma del libro medida sobre el núcleo. Que la pendiente use 5%/1% en vez
    # de 5%/0,2% no es una degradación: sigue midiendo cuán rápido se acumula
    # la profundidad al alejarse del mid, y lo hace sobre los 720 días en vez
    # de sobre los últimos 220.
    slope_bid = _log_level_ratio(bid_5, bid_1)
    slope_ask = _log_level_ratio(ask_5, ask_1)
    add("book_slope_bid", slope_bid)
    add("book_slope_ask", slope_ask)
    add("book_slope_asym", slope_bid - slope_ask)

    log_total = nm.log_positive(total_1)
    add("book_depth_z", nm.zscore(log_total, ctx))
    add("book_depth_chg_1h", log_total - nm.rolling_mean(log_total, window_bars(1.0, tf_ms)))

    # Profundidad relativa al volumen de la propia barra: cuántas barras de
    # volumen como la actual caben en el libro hasta 1%. Es la medida de
    # resiliencia y es adimensional sin necesidad de z-score.
    cover = nm.safe_div(total_1, series.quote_volume, fill=np.nan)
    add("book_cover_ratio_log", nm.log_positive(cover))

    # El taker buy de la vela consume el ask. Si el flujo comprador es grande
    # respecto de lo ofrecido, el precio tiene que subir para llenarse.
    taker_buy_quote = series.taker_buy_volume * series.close
    taker_sell_quote = series.quote_volume - taker_buy_quote
    add("taker_vs_ask_1", nm.safe_div(taker_buy_quote, ask_1, fill=np.nan))
    add("taker_vs_bid_1", nm.safe_div(taker_sell_quote, bid_1, fill=np.nan))

    # ======================================================================= #
    # NEAR-TOUCH — solo desde ~2026-01-15. NaN antes, nunca cero.
    # ======================================================================= #

    add("book_imbalance_02", imb_02, near=True)
    add("book_imbalance_02_z", nm.zscore(imb_02, ctx), near=True)
    for hours in (1.0, 4.0):
        add(
            f"book_imbalance_02_mean_{hours:g}h",
            nm.rolling_mean(imb_02, window_bars(hours, tf_ms)),
            near=True,
        )
    # El touch contra el muro: típico de spoofing o de un lado retirando
    # cotizaciones justo antes de un movimiento.
    add("book_imbalance_spread_02_5", imb_02 - imb_5, near=True)
    # Qué fracción de la liquidez del núcleo está pegada al precio.
    add("book_near_share", _log_level_ratio(bid_02 + ask_02, total_1), near=True)
    # Presión taker contra lo que hay realmente al alcance de un market order.
    add("taker_vs_ask_02", nm.safe_div(taker_buy_quote, ask_02, fill=np.nan), near=True)
    add("taker_vs_bid_02", nm.safe_div(taker_sell_quote, bid_02, fill=np.nan), near=True)

    X = np.column_stack(cols) if cols else np.zeros((n, 0), dtype=np.float64)
    return MicrostructureFeatures(
        X=X,
        names=names,
        open_time=ot,
        available=available,
        near_available=near_available,
        near_names=near_names,
    )
