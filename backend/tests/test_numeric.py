"""Tests de las primitivas vectorizadas.

El foco está en dos propiedades que, si se rompen, corrompen todo lo de
arriba en silencio: **causalidad** (nada mira al futuro) y **manejo de NaN**
(un NaN no debe contaminar el resto de la serie ni disfrazarse de dato).
"""

from __future__ import annotations

import numpy as np
import pytest

from bob.signals import numeric as nm


@pytest.fixture
def ramp() -> np.ndarray:
    return np.arange(1.0, 21.0)


class TestRollingSum:
    def test_valores_conocidos(self, ramp: np.ndarray) -> None:
        out = nm.rolling_sum(ramp, 3)
        assert np.isnan(out[0]) and np.isnan(out[1])
        assert out[2] == pytest.approx(1 + 2 + 3)
        assert out[19] == pytest.approx(18 + 19 + 20)

    def test_ventana_de_uno_es_identidad(self, ramp: np.ndarray) -> None:
        assert np.allclose(nm.rolling_sum(ramp, 1), ramp)

    def test_serie_mas_corta_que_ventana(self) -> None:
        out = nm.rolling_sum(np.array([1.0, 2.0]), 5)
        assert np.all(np.isnan(out))

    def test_nan_no_se_propaga_mas_alla_de_su_ventana(self) -> None:
        """El bug clásico: cumsum ingenuo convierte un NaN en NaN para siempre."""
        x = np.array([np.nan, 1.0, 2.0, 3.0, 4.0, 5.0])
        out = nm.rolling_sum(x, 2)
        assert np.isnan(out[1])  # la ventana incluye el NaN
        assert out[2] == pytest.approx(3.0)  # ya no
        assert out[5] == pytest.approx(9.0)
        assert np.isfinite(out[2:]).all()

    def test_ventana_invalida(self) -> None:
        with pytest.raises(ValueError):
            nm.rolling_sum(np.arange(5.0), 0)


class TestRollingStats:
    def test_media_y_std_contra_numpy(self, ramp: np.ndarray) -> None:
        w = 5
        mean = nm.rolling_mean(ramp, w)
        std = nm.rolling_std(ramp, w)
        for i in range(w - 1, ramp.size):
            window = ramp[i - w + 1 : i + 1]
            assert mean[i] == pytest.approx(window.mean())
            assert std[i] == pytest.approx(window.std(), abs=1e-9)

    def test_std_de_serie_constante_es_cero(self) -> None:
        out = nm.rolling_std(np.full(10, 7.0), 3)
        assert np.allclose(out[2:], 0.0, atol=1e-9)

    def test_max_y_min(self, ramp: np.ndarray) -> None:
        assert nm.rolling_max(ramp, 4)[10] == pytest.approx(11.0)
        assert nm.rolling_min(ramp, 4)[10] == pytest.approx(8.0)

    def test_rank_en_rango_unitario(self) -> None:
        rng = np.random.default_rng(0)
        x = rng.normal(size=500)
        out = nm.rolling_rank(x, 50)
        valid = out[~np.isnan(out)]
        assert valid.min() >= 0.0
        assert valid.max() <= 1.0

    def test_rank_del_maximo_es_uno(self) -> None:
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        assert nm.rolling_rank(x, 5)[4] == pytest.approx(1.0)

    def test_rank_invalida_ventana_con_nan(self) -> None:
        x = np.array([1.0, np.nan, 3.0, 4.0, 5.0])
        out = nm.rolling_rank(x, 3)
        assert np.isnan(out[2])  # ventana contiene el NaN
        assert np.isfinite(out[4])

    def test_bloques_dan_el_mismo_resultado_que_una_pasada(self) -> None:
        """El chunking es una optimización de memoria: no puede cambiar valores."""
        rng = np.random.default_rng(7)
        x = rng.normal(size=3000)
        original = nm._WINDOW_CHUNK_CELLS
        try:
            nm._WINDOW_CHUNK_CELLS = 10_000_000
            full = nm.rolling_rank(x, 100).copy()
            full_max = nm.rolling_max(x, 100).copy()
            nm._WINDOW_CHUNK_CELLS = 500  # fuerza muchos bloques chicos
            chunked = nm.rolling_rank(x, 100)
            chunked_max = nm.rolling_max(x, 100)
        finally:
            nm._WINDOW_CHUNK_CELLS = original
        np.testing.assert_allclose(full, chunked, equal_nan=True)
        np.testing.assert_allclose(full_max, chunked_max, equal_nan=True)


class TestIndicadores:
    def test_ewma_converge_a_constante(self) -> None:
        out = nm.ewma(np.full(200, 5.0), span=10)
        assert out[-1] == pytest.approx(5.0)

    def test_ewma_pondera_mas_lo_reciente(self) -> None:
        x = np.array([0.0] * 50 + [10.0] * 50)
        fast = nm.ewma(x, span=5)
        slow = nm.ewma(x, span=50)
        assert fast[55] > slow[55]

    def test_log_returns(self) -> None:
        close = np.array([100.0, 110.0, 99.0])
        out = nm.log_returns(close)
        assert np.isnan(out[0])
        assert out[1] == pytest.approx(np.log(1.1))
        assert out[2] == pytest.approx(np.log(99 / 110))

    def test_true_range_usa_cierre_previo(self) -> None:
        high = np.array([10.0, 12.0])
        low = np.array([9.0, 11.5])
        close = np.array([9.5, 12.0])
        tr = nm.true_range(high, low, close)
        assert tr[0] == pytest.approx(1.0)
        # El gap contra el cierre previo (9.5) manda sobre el rango intrabarra.
        assert tr[1] == pytest.approx(2.5)

    def test_atr_positivo(self) -> None:
        rng = np.random.default_rng(1)
        close = 100 + np.cumsum(rng.normal(0, 1, 200))
        high, low = close + 1.0, close - 1.0
        assert np.all(nm.atr(high, low, close, 14) > 0)

    def test_rsi_extremos(self) -> None:
        subida = np.linspace(100, 200, 100)
        bajada = np.linspace(200, 100, 100)
        assert nm.rsi(subida, 14)[-1] > 99.0
        assert nm.rsi(bajada, 14)[-1] < 1.0

    def test_rsi_en_rango(self) -> None:
        rng = np.random.default_rng(2)
        close = 100 + np.cumsum(rng.normal(0, 1, 300))
        out = nm.rsi(close, 14)
        valid = out[~np.isnan(out)]
        assert valid.min() >= 0.0 and valid.max() <= 100.0

    def test_vwap_con_volumen_uniforme_es_media_del_tipico(self) -> None:
        n = 20
        close = np.linspace(100, 120, n)
        high, low = close + 1, close - 1
        vol = np.ones(n)
        out = nm.rolling_vwap(close, high, low, vol, 5)
        typical = (high + low + close) / 3
        assert out[10] == pytest.approx(typical[6:11].mean())

    def test_estimadores_de_volatilidad_son_positivos(self) -> None:
        rng = np.random.default_rng(3)
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 500)))
        high = close * 1.005
        low = close * 0.995
        open_ = np.roll(close, 1)
        open_[0] = close[0]
        for out in (
            nm.realized_vol(nm.log_returns(close), 20),
            nm.parkinson_vol(high, low, 20),
            nm.garman_klass_vol(open_, high, low, close, 20),
        ):
            valid = out[~np.isnan(out)]
            assert valid.size > 0
            assert np.all(valid >= 0)

    def test_realized_vol_escala_con_la_volatilidad(self) -> None:
        rng = np.random.default_rng(4)
        calmo = nm.realized_vol(rng.normal(0, 0.001, 1000), 50)
        agitado = nm.realized_vol(rng.normal(0, 0.010, 1000), 50)
        assert np.nanmean(agitado) > 5 * np.nanmean(calmo)

    def test_safe_div_evita_division_por_cero(self) -> None:
        out = nm.safe_div(np.array([1.0, 2.0]), np.array([0.0, 4.0]), fill=-1.0)
        assert out[0] == -1.0
        assert out[1] == pytest.approx(0.5)

    def test_zscore_de_constante_es_cero(self) -> None:
        """Ventana plana con datos validos: el punto ES su media, z = 0."""
        out = nm.zscore(np.full(50, 3.0), 10)
        assert np.allclose(out[9:], 0.0)

    def test_zscore_sin_datos_es_nan_no_cero(self) -> None:
        """El warm-up no puede salir como "exactamente el promedio".

        Un 0 ahi es una observacion inventada: la fila pasa el filtro de
        finitud y entra al entrenamiento como si el dato existiera. Es la
        distincion entre "no se" y "es promedio", y solo la segunda es un dato.
        """
        out = nm.zscore(np.arange(50, dtype=float), 10)
        assert np.all(np.isnan(out[:9]))
        assert np.all(np.isfinite(out[9:]))

    def test_zscore_con_hueco_en_la_ventana_es_nan(self) -> None:
        """Un NaN a mitad de serie invalida su ventana, no la promedia."""
        x = np.arange(50, dtype=float)
        x[20] = np.nan
        out = nm.zscore(x, 10)
        # Las ventanas que contienen el indice 20 (o sea 20..29) salen NaN.
        assert np.all(np.isnan(out[20:30]))
        assert np.isfinite(out[30])


class TestCausalidad:
    """Regla 5 de CLAUDE.md: ninguna función puede mirar al futuro."""

    @pytest.mark.parametrize(
        "fn",
        [
            lambda x: nm.rolling_sum(x, 10),
            lambda x: nm.rolling_mean(x, 10),
            lambda x: nm.rolling_std(x, 10),
            lambda x: nm.rolling_max(x, 10),
            lambda x: nm.rolling_min(x, 10),
            lambda x: nm.rolling_rank(x, 10),
            lambda x: nm.ewma(x, 10),
            lambda x: nm.wilder_ema(x, 10),
            lambda x: nm.rsi(x, 10),
            lambda x: nm.zscore(x, 10),
        ],
    )
    def test_mutar_el_futuro_no_cambia_el_pasado(self, fn) -> None:
        rng = np.random.default_rng(11)
        x = 100 + np.cumsum(rng.normal(0, 1, 300))
        cut = 200

        original = fn(x)
        mutado = x.copy()
        mutado[cut:] = mutado[cut:] * 3.0 + 500.0  # destroza todo el futuro
        recomputado = fn(mutado)

        np.testing.assert_allclose(
            original[:cut], recomputado[:cut], equal_nan=True, rtol=1e-12
        )
