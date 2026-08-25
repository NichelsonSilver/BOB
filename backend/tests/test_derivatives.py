"""Tests de la familia de derivados — alineación causal y features.

El test que importa de verdad es `test_sin_lookahead`: la alineación entre dos
grillas distintas es exactamente donde un bug de causalidad se esconde bien y
produce un KPI inflado que el backtest no delata.
"""

from __future__ import annotations

import numpy as np
import pytest

from bob.data.binance_rest import INTERVAL_MS
from bob.data.store import DerivativesSeries, OHLCVSeries
from bob.signals.derivatives import (
    DEFAULT_MAX_STALENESS_MS,
    align_to_bars,
    build_derivative_features,
)

TF = "15m"
STEP = INTERVAL_MS[TF]
METRICS_STEP = 300_000  # el archivo viene en grilla de 5m


def _series(n: int, seed: int = 7) -> OHLCVSeries:
    rng = np.random.default_rng(seed)
    close = 3000.0 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    return OHLCVSeries(
        symbol="ETHUSDT",
        timeframe=TF,
        open_time=np.arange(n, dtype=np.int64) * STEP,
        open=close,
        high=close * 1.001,
        low=close * 0.999,
        close=close,
        volume=np.full(n, 100.0),
        quote_volume=close * 100.0,
        taker_buy_volume=np.full(n, 55.0),
        n_trades=np.full(n, 500, dtype=np.int64),
    )


def _derivatives(n_points: int, seed: int = 11) -> DerivativesSeries:
    rng = np.random.default_rng(seed)
    ts = np.arange(n_points, dtype=np.int64) * METRICS_STEP
    oi = 2_000_000.0 * np.exp(np.cumsum(rng.normal(0, 0.001, n_points)))
    return DerivativesSeries(
        symbol="ETHUSDT",
        period="5m",
        timestamp=ts,
        open_interest=oi,
        open_interest_value=oi * 3000.0,
        long_short_ratio=np.exp(rng.normal(0.4, 0.1, n_points)),
        taker_buy_sell_ratio=np.exp(rng.normal(0.0, 0.1, n_points)),
        top_trader_account_ratio=np.exp(rng.normal(0.3, 0.1, n_points)),
        top_trader_position_ratio=np.exp(rng.normal(0.35, 0.1, n_points)),
        funding_rate=np.full(n_points, np.nan),
    )


def _funding(n_points: int) -> DerivativesSeries:
    ts = np.arange(n_points, dtype=np.int64) * 28_800_000
    nan = np.full(n_points, np.nan)
    return DerivativesSeries(
        symbol="ETHUSDT",
        period="funding",
        timestamp=ts,
        open_interest=nan,
        open_interest_value=nan,
        long_short_ratio=nan,
        taker_buy_sell_ratio=nan,
        top_trader_account_ratio=nan,
        top_trader_position_ratio=nan,
        funding_rate=np.full(n_points, 0.0001),
    )


# --------------------------------------------------------------------------- #
# align_to_bars — el corazón causal del módulo
# --------------------------------------------------------------------------- #


def test_align_toma_el_ultimo_punto_observable():
    """La barra [0, 900000) ve los puntos de 5m que ya se publicaron."""
    point_time = np.array([0, 300_000, 600_000, 900_000], dtype=np.int64)
    values = np.array([1.0, 2.0, 3.0, 4.0])
    bars = np.array([0, STEP], dtype=np.int64)

    out = align_to_bars(point_time, values, bars, STEP, publication_lag_ms=0)

    # Barra 0 cierra en 899.999: el último punto observable es el de 600.000.
    assert out[0] == 3.0
    # Barra 1 cierra en 1.799.999: alcanza al de 900.000.
    assert out[1] == 4.0


def test_align_respeta_el_retraso_de_publicacion():
    """Un punto publicado con retraso NO es observable al cierre de su barra."""
    point_time = np.array([600_000], dtype=np.int64)
    values = np.array([42.0])
    bars = np.array([0], dtype=np.int64)

    sin_lag = align_to_bars(point_time, values, bars, STEP, publication_lag_ms=0)
    con_lag = align_to_bars(point_time, values, bars, STEP, publication_lag_ms=300_000)

    assert sin_lag[0] == 42.0
    assert np.isnan(con_lag[0])  # 600.000 + 300.000 = 900.000 > cierre 899.999


def test_align_nan_cuando_el_dato_esta_rancio():
    """Arrastrar un OI de hace horas como si fuera el actual es inventar dato."""
    point_time = np.array([0], dtype=np.int64)
    values = np.array([5.0])
    bars = np.array([0, 100 * STEP], dtype=np.int64)

    out = align_to_bars(
        point_time, values, bars, STEP, publication_lag_ms=0, max_staleness_ms=STEP
    )

    assert out[0] == 5.0
    assert np.isnan(out[1])  # 25 horas después, el punto ya no describe nada


def test_align_nan_antes_del_primer_punto():
    point_time = np.array([10 * STEP], dtype=np.int64)
    values = np.array([1.0])
    bars = np.array([0, STEP], dtype=np.int64)

    out = align_to_bars(point_time, values, bars, STEP, publication_lag_ms=0)
    assert np.all(np.isnan(out))


def test_align_con_entradas_vacias():
    vacio = np.array([], dtype=np.int64)
    bars = np.array([0, STEP], dtype=np.int64)

    assert np.all(np.isnan(align_to_bars(vacio, np.array([]), bars, STEP)))
    assert align_to_bars(np.array([0]), np.array([1.0]), vacio, STEP).shape == (0,)


def test_align_propaga_los_nan_del_origen():
    """Un punto sin dato sigue sin dato después de alinearlo."""
    point_time = np.array([0, 600_000], dtype=np.int64)
    values = np.array([1.0, np.nan])
    bars = np.array([0], dtype=np.int64)

    out = align_to_bars(point_time, values, bars, STEP, publication_lag_ms=0)
    assert np.isnan(out[0])  # el último observable es el NaN, no el 1.0 anterior


# --------------------------------------------------------------------------- #
# Invariantes de la matriz completa
# --------------------------------------------------------------------------- #


def test_sin_lookahead():
    """Mutar el futuro no puede alterar ni una celda del pasado.

    Es la regla 5 de CLAUDE.md aplicada a la familia que mezcla dos grillas:
    si `align_to_bars` mirara el punto siguiente en vez del anterior, este
    test lo caza y ningún backtest lo haría.
    """
    n_bars = 400
    series = _series(n_bars)
    derivs = _derivatives(n_bars * (STEP // METRICS_STEP))
    corte = 250

    completo = build_derivative_features(series, derivs)

    # Se destruye todo lo posterior al corte, en las dos grillas.
    close_mutado = series.close.copy()
    close_mutado[corte:] *= 3.0
    series_mutada = OHLCVSeries(
        symbol=series.symbol,
        timeframe=series.timeframe,
        open_time=series.open_time,
        open=series.open,
        high=series.high,
        low=series.low,
        close=close_mutado,
        volume=series.volume,
        quote_volume=series.quote_volume,
        taker_buy_volume=series.taker_buy_volume,
        n_trades=series.n_trades,
    )
    corte_puntos = corte * (STEP // METRICS_STEP)
    oi_mutado = derivs.open_interest.copy()
    oi_mutado[corte_puntos:] *= 10.0
    ls_mutado = derivs.long_short_ratio.copy()
    ls_mutado[corte_puntos:] *= 10.0
    derivs_mutados = DerivativesSeries(
        symbol=derivs.symbol,
        period=derivs.period,
        timestamp=derivs.timestamp,
        open_interest=oi_mutado,
        open_interest_value=derivs.open_interest_value,
        long_short_ratio=ls_mutado,
        taker_buy_sell_ratio=derivs.taker_buy_sell_ratio,
        top_trader_account_ratio=derivs.top_trader_account_ratio,
        top_trader_position_ratio=derivs.top_trader_position_ratio,
        funding_rate=derivs.funding_rate,
    )

    mutado = build_derivative_features(series_mutada, derivs_mutados)

    np.testing.assert_allclose(
        completo.X[:corte], mutado.X[:corte], rtol=0, atol=0, equal_nan=True
    )


def test_adimensionalidad_del_open_interest():
    """Multiplicar el OI por una constante no puede mover la matriz.

    Si el número de contratos cambiara de escala (otro símbolo, otro tamaño de
    contrato), un feature que dependa del nivel dejaría de significar lo mismo
    y el motor no sería agnóstico del símbolo.
    """
    series = _series(300)
    derivs = _derivatives(300 * (STEP // METRICS_STEP))

    base = build_derivative_features(series, derivs)
    escalado = build_derivative_features(
        series,
        DerivativesSeries(
            symbol=derivs.symbol,
            period=derivs.period,
            timestamp=derivs.timestamp,
            open_interest=derivs.open_interest * 1000.0,
            open_interest_value=derivs.open_interest_value * 1000.0,
            long_short_ratio=derivs.long_short_ratio,
            taker_buy_sell_ratio=derivs.taker_buy_sell_ratio,
            top_trader_account_ratio=derivs.top_trader_account_ratio,
            top_trader_position_ratio=derivs.top_trader_position_ratio,
            funding_rate=derivs.funding_rate,
        ),
    )

    np.testing.assert_allclose(base.X, escalado.X, rtol=1e-9, atol=1e-9, equal_nan=True)


def test_columnas_esperadas_y_forma():
    series = _series(200)
    derivs = _derivatives(200 * (STEP // METRICS_STEP))

    feats = build_derivative_features(series, derivs)

    assert feats.X.shape == (len(series), len(feats.names))
    assert len(set(feats.names)) == len(feats.names)  # sin nombres repetidos
    for esperado in ("oi_chg_4h", "oi_px_agree_4h", "top_vs_crowd", "taker_ratio_log"):
        assert esperado in feats.names
    np.testing.assert_array_equal(feats.open_time, series.open_time)


def test_derivados_vacios_dan_columnas_nan_no_ceros():
    """Sin cobertura, la respuesta honesta es NaN — un 0 diría "neutral"."""
    series = _series(100)
    vacio = _derivatives(0)

    feats = build_derivative_features(series, vacio)

    assert feats.X.shape[1] > 0
    assert np.all(np.isnan(feats.X))


def test_funding_entra_como_escalera_no_como_muestreo():
    """Entre cobros la tasa vigente no cambia: debe sostenerse 8h enteras."""
    n_bars = 96  # 24 horas de 15m
    series = _series(n_bars)
    feats = build_derivative_features(series, _derivatives(n_bars * 3), _funding(3))

    bps = feats.X[:, feats.names.index("funding_bps")]
    observados = bps[np.isfinite(bps)]
    assert observados.size > 0
    np.testing.assert_allclose(observados, 1.0)  # 0.0001 -> 1 bps


def test_sin_funding_la_columna_queda_nan():
    series = _series(100)
    feats = build_derivative_features(series, _derivatives(300), funding=None)

    bps = feats.X[:, feats.names.index("funding_bps")]
    assert np.all(np.isnan(bps))


def test_oi_px_agree_detecta_la_divergencia():
    """Precio arriba con OI abajo debe salir -1: es short covering, no tendencia."""
    n = 200
    series = _series(n)
    # Precio estrictamente creciente.
    subiendo = 3000.0 * np.exp(np.arange(n) * 0.001)
    series = OHLCVSeries(
        symbol=series.symbol,
        timeframe=TF,
        open_time=series.open_time,
        open=subiendo,
        high=subiendo,
        low=subiendo,
        close=subiendo,
        volume=series.volume,
        quote_volume=series.quote_volume,
        taker_buy_volume=series.taker_buy_volume,
        n_trades=series.n_trades,
    )
    # OI estrictamente decreciente en la grilla de 5m.
    n_pts = n * (STEP // METRICS_STEP)
    derivs = _derivatives(n_pts)
    bajando = 2_000_000.0 * np.exp(-np.arange(n_pts) * 0.0005)
    derivs = DerivativesSeries(
        symbol=derivs.symbol,
        period=derivs.period,
        timestamp=derivs.timestamp,
        open_interest=bajando,
        open_interest_value=derivs.open_interest_value,
        long_short_ratio=derivs.long_short_ratio,
        taker_buy_sell_ratio=derivs.taker_buy_sell_ratio,
        top_trader_account_ratio=derivs.top_trader_account_ratio,
        top_trader_position_ratio=derivs.top_trader_position_ratio,
        funding_rate=derivs.funding_rate,
    )

    feats = build_derivative_features(series, derivs)
    agree = feats.X[:, feats.names.index("oi_px_agree_4h")]
    observados = agree[np.isfinite(agree)]

    assert observados.size > 0
    assert np.all(observados == -1.0)


def test_top_vs_crowd_es_cero_cuando_coinciden():
    """La señal está en la brecha: si ballenas y multitud coinciden, no hay."""
    n_pts = 400
    derivs = _derivatives(n_pts)
    mismo = np.full(n_pts, 1.5)
    derivs = DerivativesSeries(
        symbol=derivs.symbol,
        period=derivs.period,
        timestamp=derivs.timestamp,
        open_interest=derivs.open_interest,
        open_interest_value=derivs.open_interest_value,
        long_short_ratio=mismo,
        taker_buy_sell_ratio=derivs.taker_buy_sell_ratio,
        top_trader_account_ratio=mismo,
        top_trader_position_ratio=mismo,
        funding_rate=derivs.funding_rate,
    )
    feats = build_derivative_features(_series(100), derivs)

    brecha = feats.X[:, feats.names.index("top_vs_crowd")]
    observados = brecha[np.isfinite(brecha)]
    assert observados.size > 0
    np.testing.assert_allclose(observados, 0.0, atol=1e-12)


def test_max_staleness_por_defecto_cubre_una_hora():
    assert DEFAULT_MAX_STALENESS_MS == 3_600_000


@pytest.mark.parametrize("timeframe", ["5m", "15m", "1h"])
def test_mismo_set_de_features_en_todos_los_timeframes(timeframe):
    """El motor debe significar lo mismo en 5m, 15m y 1h (window_bars)."""
    step = INTERVAL_MS[timeframe]
    n = 300
    close = np.full(n, 3000.0)
    series = OHLCVSeries(
        symbol="ETHUSDT",
        timeframe=timeframe,
        open_time=np.arange(n, dtype=np.int64) * step,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=np.full(n, 1.0),
        quote_volume=np.full(n, 3000.0),
        taker_buy_volume=np.full(n, 0.5),
        n_trades=np.full(n, 10, dtype=np.int64),
    )
    derivs = _derivatives(n * max(1, step // METRICS_STEP))

    feats = build_derivative_features(series, derivs)
    assert feats.X.shape == (n, len(feats.names))
