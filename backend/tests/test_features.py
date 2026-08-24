"""Tests del ensamblado de features.

Dos propiedades mandan acá:

1. **Sin lookahead** — el test decisivo muta todo el futuro de la serie y
   exige que ni un solo valor del pasado cambie. Es la regla 5 de CLAUDE.md
   puesta a prueba sobre el pipeline completo, no función por función.
2. **Adimensionalidad** — si un feature escala con el precio, el modelo deja
   de ser agnóstico del símbolo. Se verifica multiplicando toda la serie por
   una constante y comprobando que la matriz no cambia.
"""

from __future__ import annotations

import numpy as np
import pytest

from bob.data.store import OHLCVSeries
from bob.signals.features import (
    bars_per_hour,
    build_features,
    feature_families,
)

TF_MS = 900_000  # 15m


def _serie_sintetica(n: int = 2000, seed: int = 0, escala: float = 1.0) -> OHLCVSeries:
    """Serie geométrica browniana con OHLC y volumen coherentes."""
    rng = np.random.default_rng(seed)
    ret = rng.normal(0, 0.004, n)
    close = 2000.0 * escala * np.exp(np.cumsum(ret))
    spread = np.abs(rng.normal(0, 0.002, n)) + 0.0005
    high = close * (1 + spread)
    low = close * (1 - spread)
    open_ = np.concatenate([[close[0]], close[:-1]])
    volume = np.abs(rng.lognormal(5, 1, n))
    taker = volume * rng.uniform(0.3, 0.7, n)
    return OHLCVSeries(
        symbol="TESTUSDT",
        timeframe="15m",
        open_time=np.arange(n, dtype=np.int64) * TF_MS,
        open=open_,
        high=np.maximum(high, np.maximum(open_, close)),
        low=np.minimum(low, np.minimum(open_, close)),
        close=close,
        volume=volume,
        quote_volume=volume * close,
        taker_buy_volume=taker,
        n_trades=rng.integers(50, 5000, n).astype(np.int64),
    )


@pytest.fixture
def serie() -> OHLCVSeries:
    return _serie_sintetica()


class TestEstructura:
    def test_dimensiones_y_alineacion(self, serie: OHLCVSeries) -> None:
        fs = build_features(serie)
        assert fs.X.shape[0] == len(serie)
        assert fs.X.shape[1] == len(fs.names)
        assert fs.n_features > 30
        np.testing.assert_array_equal(fs.open_time, serie.open_time)

    def test_nombres_unicos(self, serie: OHLCVSeries) -> None:
        fs = build_features(serie)
        assert len(set(fs.names)) == len(fs.names)

    def test_valid_from_marca_el_fin_del_warmup(self, serie: OHLCVSeries) -> None:
        fs = build_features(serie)
        assert 0 < fs.valid_from < len(serie)
        assert np.all(np.isfinite(fs.X[fs.valid_from]))

    def test_todo_finito_despues_del_warmup(self, serie: OHLCVSeries) -> None:
        """Un NaN después del warm-up es un bug, no un dato faltante."""
        fs = build_features(serie)
        bloque = fs.X[fs.valid_from :]
        assert np.isfinite(bloque).all(), [
            fs.names[j] for j in np.flatnonzero(~np.isfinite(bloque).all(axis=0))
        ]

    def test_serie_vacia(self) -> None:
        vacia = OHLCVSeries(
            symbol="X",
            timeframe="15m",
            open_time=np.array([], dtype=np.int64),
            **{
                k: np.array([])
                for k in (
                    "open", "high", "low", "close",
                    "volume", "quote_volume", "taker_buy_volume",
                )
            },
            n_trades=np.array([], dtype=np.int64),
        )
        fs = build_features(vacia)
        assert len(fs) == 0

    def test_acceso_por_nombre(self, serie: OHLCVSeries) -> None:
        fs = build_features(serie)
        col = fs.column("rsi_14")
        assert col.shape[0] == len(serie)
        assert fs.index_of("rsi_14") == fs.names.index("rsi_14")


class TestSinLookahead:
    def test_mutar_el_futuro_no_altera_el_pasado(self, serie: OHLCVSeries) -> None:
        """El test que sostiene la regla 5 sobre el pipeline entero."""
        fs_original = build_features(serie)
        cut = 1500

        rng = np.random.default_rng(99)
        mutada = OHLCVSeries(
            symbol=serie.symbol,
            timeframe=serie.timeframe,
            open_time=serie.open_time,
            open=serie.open.copy(),
            high=serie.high.copy(),
            low=serie.low.copy(),
            close=serie.close.copy(),
            volume=serie.volume.copy(),
            quote_volume=serie.quote_volume.copy(),
            taker_buy_volume=serie.taker_buy_volume.copy(),
            n_trades=serie.n_trades.copy(),
        )
        # Crash del 60% seguido de ruido salvaje: si algo mira adelante, salta.
        for arr in (mutada.open, mutada.high, mutada.low, mutada.close):
            arr[cut:] *= 0.4 * rng.uniform(0.5, 1.5, len(serie) - cut)
        mutada.volume[cut:] *= 50.0
        mutada.quote_volume[cut:] *= 50.0
        mutada.taker_buy_volume[cut:] = mutada.volume[cut:] * 0.95

        fs_mutada = build_features(mutada)
        for j, name in enumerate(fs_original.names):
            np.testing.assert_allclose(
                fs_original.X[:cut, j],
                fs_mutada.X[:cut, j],
                equal_nan=True,
                rtol=1e-10,
                err_msg=f"el feature '{name}' mira al futuro",
            )

    def test_features_de_un_prefijo_coinciden_con_los_de_la_serie_completa(self) -> None:
        """Recomputar sobre un prefijo debe dar lo mismo: no hay estado global."""
        serie = _serie_sintetica(n=1500, seed=5)
        completa = build_features(serie)
        prefijo = build_features(serie.slice(0, 1200))
        for j, name in enumerate(completa.names):
            np.testing.assert_allclose(
                completa.X[:1200, j],
                prefijo.X[:, j],
                equal_nan=True,
                rtol=1e-10,
                err_msg=f"'{name}' depende del largo de la serie",
            )


class TestAdimensionalidad:
    def test_escalar_el_precio_no_cambia_los_features(self) -> None:
        """Lo que hace al motor agnóstico del símbolo (ETH a 2.000 o a 4.000)."""
        base = _serie_sintetica(n=1500, seed=3, escala=1.0)
        escalada = _serie_sintetica(n=1500, seed=3, escala=10.0)
        fs_base = build_features(base)
        fs_esc = build_features(escalada)
        for j, name in enumerate(fs_base.names):
            np.testing.assert_allclose(
                fs_base.X[fs_base.valid_from :, j],
                fs_esc.X[fs_esc.valid_from :, j],
                rtol=1e-6,
                atol=1e-9,
                err_msg=f"'{name}' depende del nivel de precio",
            )

    def test_features_acotados_no_se_desbordan(self, serie: OHLCVSeries) -> None:
        fs = build_features(serie)
        bloque = slice(fs.valid_from, None)
        for name in ("rsi_14", "rsi_48", "taker_buy_ratio"):
            col = fs.column(name)[bloque]
            assert col.min() >= -0.01 and col.max() <= 1.01, name
        for name in ("hour_sin", "hour_cos", "dow_sin", "dow_cos", "volume_delta"):
            col = fs.column(name)[bloque]
            assert col.min() >= -1.01 and col.max() <= 1.01, name


class TestSemantica:
    def test_momentum_positivo_en_tendencia_alcista(self) -> None:
        n = 1500
        close = 100 * np.exp(np.cumsum(np.full(n, 0.001)))
        serie = OHLCVSeries(
            symbol="UP",
            timeframe="15m",
            open_time=np.arange(n, dtype=np.int64) * TF_MS,
            open=close,
            high=close * 1.001,
            low=close * 0.999,
            close=close,
            volume=np.ones(n),
            quote_volume=close,
            taker_buy_volume=np.full(n, 0.5),
            n_trades=np.full(n, 10, dtype=np.int64),
        )
        fs = build_features(serie)
        assert np.all(fs.column("mom_4h")[fs.valid_from :] > 0)
        assert np.mean(fs.column("rsi_14")[fs.valid_from :]) > 0.9

    def test_volatilidad_sube_en_el_tramo_agitado(self) -> None:
        n = 2400
        rng = np.random.default_rng(8)
        ret = np.concatenate([rng.normal(0, 0.0005, 1200), rng.normal(0, 0.01, 1200)])
        close = 100 * np.exp(np.cumsum(ret))
        serie = OHLCVSeries(
            symbol="V",
            timeframe="15m",
            open_time=np.arange(n, dtype=np.int64) * TF_MS,
            open=close,
            high=close * 1.002,
            low=close * 0.998,
            close=close,
            volume=np.ones(n),
            quote_volume=close,
            taker_buy_volume=np.full(n, 0.5),
            n_trades=np.full(n, 10, dtype=np.int64),
        )
        fs = build_features(serie)
        rv = fs.column("rv_4h")
        assert np.nanmean(rv[1400:2300]) > 5 * np.nanmean(rv[800:1150])

    def test_taker_ratio_refleja_presion_compradora(self, serie: OHLCVSeries) -> None:
        fs = build_features(serie)
        ratio = fs.column("taker_buy_ratio")
        esperado = serie.taker_buy_volume / serie.volume
        np.testing.assert_allclose(ratio, esperado, rtol=1e-9)

    def test_estacionalidad_es_ciclica(self, serie: OHLCVSeries) -> None:
        fs = build_features(serie)
        s, c = fs.column("hour_sin"), fs.column("hour_cos")
        np.testing.assert_allclose(s**2 + c**2, 1.0, rtol=1e-9)


class TestUtilidades:
    def test_bars_per_hour(self) -> None:
        assert bars_per_hour(TF_MS) == pytest.approx(4.0)
        assert bars_per_hour(3_600_000) == pytest.approx(1.0)

    def test_familias_cubren_todos_los_features(self, serie: OHLCVSeries) -> None:
        fs = build_features(serie)
        familias = feature_families(fs.names)
        clasificados = [n for miembros in familias.values() for n in miembros]
        assert sorted(clasificados) == sorted(fs.names)

    def test_ninguna_familia_esta_vacia(self, serie: OHLCVSeries) -> None:
        fs = build_features(serie)
        for fam, miembros in feature_families(fs.names).items():
            assert miembros, f"familia sin features: {fam}"
