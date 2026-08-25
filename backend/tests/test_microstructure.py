"""Tests de la familia de microestructura — el libro de órdenes.

Las dos invariantes del proyecto aplicadas al libro: sin lookahead (mutar el
futuro no toca el pasado) y adimensionalidad (multiplicar todo el notional por
una constante no puede mover la matriz — si la moviera, el motor dejaría de
servir para otro símbolo o para otro nivel de precio).
"""

from __future__ import annotations

import numpy as np
import pytest

from bob.data.binance_rest import INTERVAL_MS
from bob.data.store import BookDepthSeries, OHLCVSeries
from bob.signals.microstructure import (
    MIN_SNAPSHOTS,
    build_microstructure_features,
    reindex_to_bars,
)

TF = "15m"
STEP = INTERVAL_MS[TF]


def _series(n: int, seed: int = 3) -> OHLCVSeries:
    rng = np.random.default_rng(seed)
    close = 3000.0 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    volume = np.abs(rng.normal(100.0, 10.0, n))
    return OHLCVSeries(
        symbol="ETHUSDT",
        timeframe=TF,
        open_time=np.arange(n, dtype=np.int64) * STEP,
        open=close,
        high=close * 1.001,
        low=close * 0.999,
        close=close,
        volume=volume,
        quote_volume=volume * close,
        taker_buy_volume=volume * 0.55,
        n_trades=np.full(n, 500, dtype=np.int64),
    )


def _book(n: int, seed: int = 5, escala: float = 1.0, n_snapshots: int = 30) -> BookDepthSeries:
    rng = np.random.default_rng(seed)
    bid_02 = np.abs(rng.normal(5e6, 5e5, n)) * escala
    ask_02 = np.abs(rng.normal(5e6, 5e5, n)) * escala
    return BookDepthSeries(
        symbol="ETHUSDT",
        timeframe=TF,
        open_time=np.arange(n, dtype=np.int64) * STEP,
        bid_02=bid_02,
        ask_02=ask_02,
        bid_1=bid_02 * 9.0,
        ask_1=ask_02 * 9.0,
        bid_5=bid_02 * 40.0,
        ask_5=ask_02 * 40.0,
        n_snapshots=np.full(n, n_snapshots, dtype=np.int64),
    )


def _empty_book() -> BookDepthSeries:
    vacio = np.array([], dtype=np.float64)
    return BookDepthSeries(
        symbol="ETHUSDT",
        timeframe=TF,
        open_time=np.array([], dtype=np.int64),
        bid_02=vacio,
        ask_02=vacio,
        bid_1=vacio,
        ask_1=vacio,
        bid_5=vacio,
        ask_5=vacio,
        n_snapshots=np.array([], dtype=np.int64),
    )


# --------------------------------------------------------------------------- #
# reindex_to_bars
# --------------------------------------------------------------------------- #


def test_reindex_es_join_exacto_no_forward_fill():
    """Al libro le falta la barra 1: debe quedar NaN, no heredar la 0."""
    book_time = np.array([0, 2 * STEP], dtype=np.int64)
    values = np.array([10.0, 30.0])
    bars = np.array([0, STEP, 2 * STEP], dtype=np.int64)

    out = reindex_to_bars(book_time, values, bars)

    assert out[0] == 10.0
    assert np.isnan(out[1])
    assert out[2] == 30.0


def test_reindex_ignora_barras_de_libro_fuera_de_la_serie():
    book_time = np.array([5 * STEP, 6 * STEP], dtype=np.int64)
    bars = np.array([0, STEP], dtype=np.int64)

    out = reindex_to_bars(book_time, np.array([1.0, 2.0]), bars)
    assert np.all(np.isnan(out))


def test_reindex_con_entradas_vacias():
    vacio_i = np.array([], dtype=np.int64)
    bars = np.array([0, STEP], dtype=np.int64)

    assert np.all(np.isnan(reindex_to_bars(vacio_i, np.array([]), bars)))
    assert reindex_to_bars(np.array([0]), np.array([1.0]), vacio_i).shape == (0,)


# --------------------------------------------------------------------------- #
# Invariantes
# --------------------------------------------------------------------------- #


def _escalar_serie(series: OHLCVSeries, k: float) -> OHLCVSeries:
    """Mismo mercado, k veces mas grande: precios iguales, volumenes x k."""
    return OHLCVSeries(
        symbol=series.symbol,
        timeframe=series.timeframe,
        open_time=series.open_time,
        open=series.open,
        high=series.high,
        low=series.low,
        close=series.close,
        volume=series.volume * k,
        quote_volume=series.quote_volume * k,
        taker_buy_volume=series.taker_buy_volume * k,
        n_trades=series.n_trades,
    )


def test_adimensionalidad_del_mercado_completo():
    """Un mercado 1000 veces mas grande da EXACTAMENTE la misma matriz.

    Es la invariante que hace al motor agnostico del simbolo y del nivel de
    precio: el modelo no ve notionales, solo proporciones. Se escalan a la vez
    el libro y los volumenes de la vela, porque las columnas que los cruzan
    (cover ratio, presion taker contra libro) son ratios entre dos magnitudes
    que en un mercado real crecen juntas.
    """
    k = 1000.0
    series = _series(300)

    base = build_microstructure_features(series, _book(300))
    escalado = build_microstructure_features(
        _escalar_serie(series, k), _book(300, escala=k)
    )

    np.testing.assert_allclose(base.X, escalado.X, rtol=1e-9, atol=1e-9, equal_nan=True)


def test_ratios_de_libro_no_dependen_del_tamano_del_libro():
    """Escalar SOLO el libro deja intactos los features de pura forma.

    Desbalances y pendientes son ratios entre lados o entre niveles del propio
    libro: no pueden mover un decimal. Las columnas que cruzan libro con
    volumen quedan deliberadamente fuera — que esas SI cambien cuando solo se
    escala un lado del cociente es la prueba de que miden lo que dicen medir.
    """
    series = _series(300)
    cruzadas = {
        "book_cover_ratio_log",
        "taker_vs_ask_1",
        "taker_vs_bid_1",
        "taker_vs_ask_02",
        "taker_vs_bid_02",
    }

    base = build_microstructure_features(series, _book(300))
    escalado = build_microstructure_features(series, _book(300, escala=1000.0))

    solo_forma = [
        i for i, n in enumerate(base.names) if n.startswith("book_") and n not in cruzadas
    ]
    assert len(solo_forma) >= 10
    np.testing.assert_allclose(
        base.X[:, solo_forma], escalado.X[:, solo_forma], rtol=1e-9, atol=1e-9, equal_nan=True
    )

    # Y el control negativo: la columna que cruza magnitudes se mueve por log(k).
    cover = base.names.index("book_cover_ratio_log")
    delta = escalado.X[:, cover] - base.X[:, cover]
    np.testing.assert_allclose(delta[np.isfinite(delta)], np.log(1000.0))


def test_sin_lookahead():
    """Destruir el libro futuro no puede alterar el pasado."""
    n, corte = 400, 250
    series = _series(n)
    book = _book(n)

    completo = build_microstructure_features(series, book)

    bid_02 = book.bid_02.copy()
    bid_02[corte:] *= 50.0
    mutado = build_microstructure_features(
        series,
        BookDepthSeries(
            symbol=book.symbol,
            timeframe=book.timeframe,
            open_time=book.open_time,
            bid_02=bid_02,
            ask_02=book.ask_02,
            bid_1=book.bid_1,
            ask_1=book.ask_1,
            bid_5=book.bid_5,
            ask_5=book.ask_5,
            n_snapshots=book.n_snapshots,
        ),
    )

    np.testing.assert_allclose(
        completo.X[:corte], mutado.X[:corte], rtol=0, atol=0, equal_nan=True
    )


# --------------------------------------------------------------------------- #
# Semántica de los features
# --------------------------------------------------------------------------- #


def test_imbalance_acotado_y_con_el_signo_correcto():
    n = 50
    series = _series(n)
    book = _book(n)
    # Bid mucho más grueso que ask: presión compradora.
    book = BookDepthSeries(
        symbol=book.symbol,
        timeframe=book.timeframe,
        open_time=book.open_time,
        bid_02=np.full(n, 9e6),
        ask_02=np.full(n, 1e6),
        bid_1=np.full(n, 9e6),
        ask_1=np.full(n, 1e6),
        bid_5=np.full(n, 9e6),
        ask_5=np.full(n, 1e6),
        n_snapshots=book.n_snapshots,
    )

    feats = build_microstructure_features(series, book)
    imb = feats.X[:, feats.names.index("book_imbalance_02")]

    np.testing.assert_allclose(imb, 0.8)  # (9-1)/(9+1)
    assert np.all(np.abs(imb) <= 1.0)


def test_barra_con_pocos_snapshots_no_se_usa():
    """Un promedio de 2 fotos es ruido con cara de dato."""
    n = 50
    series = _series(n)
    book = _book(n, n_snapshots=MIN_SNAPSHOTS - 1)

    feats = build_microstructure_features(series, book)

    assert not np.any(feats.available)
    imb = feats.X[:, feats.names.index("book_imbalance_02")]
    assert np.all(np.isnan(imb))


def test_umbral_de_snapshots_es_inclusivo():
    n = 50
    feats = build_microstructure_features(_series(n), _book(n, n_snapshots=MIN_SNAPSHOTS))
    assert np.all(feats.available)


def test_libro_vacio_da_nan_y_available_en_false():
    series = _series(100)

    feats = build_microstructure_features(series, _empty_book())

    assert feats.X.shape == (100, len(feats.names))
    assert np.all(np.isnan(feats.X))
    assert not np.any(feats.available)


def test_pendiente_del_libro_es_positiva_con_libro_acumulado():
    """5% siempre contiene a 0,2%: la pendiente log no puede salir negativa."""
    n = 60
    feats = build_microstructure_features(_series(n), _book(n))

    for nombre in ("book_slope_bid", "book_slope_ask"):
        pendiente = feats.X[:, feats.names.index(nombre)]
        finitos = pendiente[np.isfinite(pendiente)]
        assert finitos.size > 0
        assert np.all(finitos > 0.0)


def test_forma_y_nombres():
    series = _series(200)
    feats = build_microstructure_features(series, _book(200))

    assert feats.X.shape == (len(series), len(feats.names))
    assert len(set(feats.names)) == len(feats.names)
    for esperado in ("book_imbalance_1", "book_slope_asym", "taker_vs_ask_02"):
        assert esperado in feats.names
    np.testing.assert_array_equal(feats.open_time, series.open_time)


@pytest.mark.parametrize("timeframe", ["5m", "15m", "1h"])
def test_funciona_en_todos_los_timeframes(timeframe):
    step = INTERVAL_MS[timeframe]
    n = 200
    close = np.full(n, 3000.0)
    series = OHLCVSeries(
        symbol="ETHUSDT",
        timeframe=timeframe,
        open_time=np.arange(n, dtype=np.int64) * step,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=np.full(n, 100.0),
        quote_volume=np.full(n, 300_000.0),
        taker_buy_volume=np.full(n, 55.0),
        n_trades=np.full(n, 500, dtype=np.int64),
    )
    book = BookDepthSeries(
        symbol="ETHUSDT",
        timeframe=timeframe,
        open_time=np.arange(n, dtype=np.int64) * step,
        bid_02=np.full(n, 5e6),
        ask_02=np.full(n, 5e6),
        bid_1=np.full(n, 4.5e7),
        ask_1=np.full(n, 4.5e7),
        bid_5=np.full(n, 2e8),
        ask_5=np.full(n, 2e8),
        n_snapshots=np.full(n, 30, dtype=np.int64),
    )

    feats = build_microstructure_features(series, book)
    assert feats.X.shape == (n, len(feats.names))


def _book_sin_near(n: int) -> BookDepthSeries:
    """Libro como lo trae el archivo anterior a ~2026-01-15: sin el 0,2%."""
    base = _book(n)
    return BookDepthSeries(
        symbol=base.symbol,
        timeframe=base.timeframe,
        open_time=base.open_time,
        bid_02=np.full(n, np.nan),
        ask_02=np.full(n, np.nan),
        bid_1=base.bid_1,
        ask_1=base.ask_1,
        bid_5=base.bid_5,
        ask_5=base.ask_5,
        n_snapshots=base.n_snapshots,
    )


def test_sin_near_touch_el_nucleo_sigue_sirviendo():
    """El tramo viejo del archivo no se pierde: 1% y 5% cubren toda la historia.

    n = 800 barras de 15m son 200 horas: mas que la ventana de contexto de 168h,
    asi que los z-scores del nucleo tambien alcanzan a salir del warm-up.
    """
    n = 800
    feats = build_microstructure_features(_series(n), _book_sin_near(n))

    assert np.all(feats.available)
    assert not np.any(feats.near_available)

    # TODAS las columnas del nucleo tienen dato, incluida la pendiente.
    for nombre in feats.core_names():
        col = feats.X[:, feats.names.index(nombre)]
        assert np.isfinite(col).any(), nombre
    assert "book_slope_bid" in feats.core_names()

    # ...y las del near-touch salen NaN, no cero.
    for nombre in feats.near_names:
        col = feats.X[:, feats.names.index(nombre)]
        assert np.all(np.isnan(col)), nombre


def test_near_names_identifica_la_subfamilia():
    feats = build_microstructure_features(_series(50), _book(50))

    assert "book_imbalance_02" in feats.near_names
    assert "taker_vs_ask_02" in feats.near_names
    assert "book_near_share" in feats.near_names
    assert "book_imbalance_1" not in feats.near_names
    # La pendiente se mide sobre el nucleo: NO depende del near-touch.
    assert "book_slope_bid" not in feats.near_names
    assert set(feats.names) == set(feats.near_names) | set(feats.core_names())
    assert np.all(feats.near_available)


def test_near_available_es_subconjunto_de_available():
    n = 100
    feats = build_microstructure_features(
        _series(n), _book(n, n_snapshots=MIN_SNAPSHOTS - 1)
    )
    assert not np.any(feats.available)
    assert not np.any(feats.near_available)
