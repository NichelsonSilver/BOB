"""Tests del etiquetado triple-barrier.

Un bug acá no rompe un test: produce una probabilidad falsa que el usuario
usa para entrar apalancado. Por eso los casos se construyen sobre caminos de
precio sintéticos donde la respuesta correcta se conoce a mano, en vez de
comprobar solo que "no explota".
"""

from __future__ import annotations

import numpy as np
import pytest

from bob.models.labeling import (
    BarrierConfig,
    concurrency,
    forward_return,
    forward_volatility,
    target_volatility,
    triple_barrier_labels,
    uniqueness_weights,
)

TF_MS = 900_000  # 15m


def _series_desde_closes(closes: list[float]) -> tuple[np.ndarray, ...]:
    """OHLC degenerado (sin mechas) para controlar exactamente cada toque."""
    c = np.array(closes, dtype=float)
    return c.copy(), c.copy(), c.copy(), c.copy()  # open, high, low, close


def _config(**kwargs) -> BarrierConfig:
    base = dict(
        tp_mult=1.0,
        sl_mult=1.0,
        horizon_bars=5,
        vol_window_bars=2,
        fee_roundtrip_pct=0.0,
        slippage_pct=0.0,
        funding_pct_per_8h=0.0,
    )
    base.update(kwargs)
    return BarrierConfig(**base)  # type: ignore[arg-type]


class TestBarrierConfig:
    def test_costo_suma_fees_y_slippage(self) -> None:
        cfg = BarrierConfig(fee_roundtrip_pct=0.001, slippage_pct=0.0004)
        assert cfg.cost_pct == pytest.approx(0.0014)

    def test_funding_por_barra_en_15m(self) -> None:
        cfg = BarrierConfig(funding_pct_per_8h=0.0001)
        # 8h = 32 barras de 15m
        assert cfg.funding_per_bar(TF_MS) == pytest.approx(0.0001 / 32)

    def test_breakeven_simetrico_sin_costo(self) -> None:
        cfg = BarrierConfig(tp_mult=1.0, sl_mult=1.0, fee_roundtrip_pct=0.0, slippage_pct=0.0)
        assert cfg.breakeven_probability(0.01) == pytest.approx(0.5)

    def test_breakeven_sube_con_el_costo(self) -> None:
        barato = BarrierConfig(tp_mult=1.0, sl_mult=1.0, fee_roundtrip_pct=0.0, slippage_pct=0.0)
        caro = BarrierConfig(tp_mult=1.0, sl_mult=1.0, fee_roundtrip_pct=0.002, slippage_pct=0.0)
        assert caro.breakeven_probability(0.01) > barato.breakeven_probability(0.01)

    def test_breakeven_asimetrico(self) -> None:
        """TP el doble que el SL ⇒ basta acertar ~1 de cada 3 veces."""
        cfg = BarrierConfig(tp_mult=2.0, sl_mult=1.0, fee_roundtrip_pct=0.0, slippage_pct=0.0)
        assert cfg.breakeven_probability(0.01) == pytest.approx(1 / 3)


class TestTargetVolatility:
    def test_escala_con_raiz_del_horizonte(self) -> None:
        rng = np.random.default_rng(0)
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, 500)))
        s1 = target_volatility(close, 96, horizon=1)
        s16 = target_volatility(close, 96, horizon=16)
        np.testing.assert_allclose(s16[200:], s1[200:] * 4.0, rtol=1e-9)

    def test_piso_minimo_en_precio_congelado(self) -> None:
        close = np.full(200, 100.0)
        assert np.all(target_volatility(close, 50, 16) >= 1e-5)

    def test_es_causal(self) -> None:
        rng = np.random.default_rng(1)
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, 400)))
        original = target_volatility(close, 50, 4)
        mutado = close.copy()
        mutado[300:] *= 5.0
        np.testing.assert_allclose(original[:300], target_volatility(mutado, 50, 4)[:300])


class TestTripleBarrier:
    def test_direccion_invalida(self) -> None:
        o, h, low, c = _series_desde_closes([100.0] * 20)
        with pytest.raises(ValueError):
            triple_barrier_labels(h, low, c, o, _config(), TF_MS, "sideways")

    def test_subida_monotona_toca_tp_en_long(self) -> None:
        # Barra 9 es la última plana; la entrada (open[10]) cae ya en la subida.
        closes = [100.0] * 10 + [100 * (1 + 0.01 * k) for k in range(1, 15)]
        o, h, low, c = _series_desde_closes(closes)
        lab = triple_barrier_labels(h, low, c, o, _config(), TF_MS, "long")
        assert lab.label[9] == 1
        assert lab.resolution[9] == 1

    def test_la_misma_subida_toca_sl_en_short(self) -> None:
        closes = [100.0] * 10 + [100 * (1 + 0.01 * k) for k in range(1, 15)]
        o, h, low, c = _series_desde_closes(closes)
        lab = triple_barrier_labels(h, low, c, o, _config(), TF_MS, "short")
        assert lab.label[9] == 0
        assert lab.resolution[9] == 0

    def test_tramo_plano_previo_expira_en_la_vertical(self) -> None:
        """Contrapunto del test anterior: sin movimiento no hay toque."""
        closes = [100.0] * 10 + [100 * (1 + 0.01 * k) for k in range(1, 15)]
        o, h, low, c = _series_desde_closes(closes)
        lab = triple_barrier_labels(h, low, c, o, _config(), TF_MS, "long")
        assert lab.resolution[2] == 2

    def test_precio_plano_expira_en_la_vertical(self) -> None:
        closes = [100.0, 101.0, 100.0, 101.0] * 6 + [100.5] * 12
        o, h, low, c = _series_desde_closes(closes)
        lab = triple_barrier_labels(h, low, c, o, _config(tp_mult=50.0, sl_mult=50.0), TF_MS)
        idx = np.flatnonzero(lab.usable)
        assert np.all(lab.resolution[idx] == 2)
        assert np.all(lab.label[idx] == 0)

    def test_empate_intrabarra_se_resuelve_contra_el_trader(self) -> None:
        """Si el high toca TP y el low toca SL en la misma vela, gana el SL.

        El OHLC no dice el orden; suponer lo contrario regala probabilidad
        que no existe.
        """
        n = 12
        close = np.full(n, 100.0)
        open_ = np.full(n, 100.0)
        high = np.full(n, 100.0)
        low = np.full(n, 100.0)
        # Vela que abarca ambas barreras a la vez.
        high[6] = 130.0
        low[6] = 70.0
        # Volatilidad no nula para que las barreras existan.
        close[:5] = [100.0, 101.0, 100.0, 101.0, 100.0]
        open_[:5] = close[:5]
        high[:5] = close[:5]
        low[:5] = close[:5]

        lab = triple_barrier_labels(
            high, low, close, open_, _config(horizon_bars=4, vol_window_bars=3), TF_MS, "long"
        )
        assert lab.resolution[5] == 0
        assert lab.label[5] == 0

    def test_entrada_es_el_open_siguiente_no_el_close_actual(self) -> None:
        """Lookahead de una barra: el error que infla todo backtest ingenuo."""
        close = np.array([100.0, 101.0] * 10)
        open_ = close + 7.0  # open deliberadamente distinto del close
        high = np.maximum(close, open_)
        low = np.minimum(close, open_)
        lab = triple_barrier_labels(high, low, close, open_, _config(), TF_MS, "long")
        idx = np.flatnonzero(lab.usable)
        for i in idx:
            assert lab.entry_price[i] == pytest.approx(open_[i + 1])

    def test_barreras_estan_a_distancia_de_mercado(self) -> None:
        """TP y SL son los precios que el usuario pone en Binance."""
        rng = np.random.default_rng(2)
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.005, 300)))
        open_ = close.copy()
        high, low = close * 1.001, close * 0.999
        cfg = _config(tp_mult=2.0, sl_mult=1.0, horizon_bars=10, vol_window_bars=50)
        lab = triple_barrier_labels(high, low, close, open_, cfg, TF_MS, "long")
        sigma = target_volatility(close, 50, 10)
        for i in np.flatnonzero(lab.usable)[:50]:
            entry = lab.entry_price[i]
            assert lab.tp_price[i] == pytest.approx(entry * (1 + 2.0 * sigma[i]))
            assert lab.sl_price[i] == pytest.approx(entry * (1 - 1.0 * sigma[i]))

    def test_setup_imposible_de_ganar_se_descarta(self) -> None:
        """Si el TP completo no paga la fricción, no se etiqueta."""
        rng = np.random.default_rng(3)
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.0001, 300)))
        open_ = close.copy()
        high, low = close.copy(), close.copy()
        cfg = BarrierConfig(
            tp_mult=0.5,
            sl_mult=0.5,
            horizon_bars=10,
            vol_window_bars=50,
            fee_roundtrip_pct=0.05,  # 5%: absurdo a propósito
            slippage_pct=0.0,
            funding_pct_per_8h=0.0,
        )
        lab = triple_barrier_labels(high, low, close, open_, cfg, TF_MS, "long")
        assert lab.usable.sum() == 0

    def test_costos_reducen_el_retorno_neto(self) -> None:
        closes = [100.0] * 10 + [100 * (1 + 0.01 * k) for k in range(1, 15)]
        o, h, low, c = _series_desde_closes(closes)
        sin_costo = triple_barrier_labels(h, low, c, o, _config(), TF_MS, "long")
        con_costo = triple_barrier_labels(
            h, low, c, o, _config(fee_roundtrip_pct=0.001, slippage_pct=0.0005), TF_MS, "long"
        )
        idx = np.flatnonzero(sin_costo.usable & con_costo.usable)
        assert np.all(con_costo.net_return[idx] < sin_costo.net_return[idx])

    def test_funding_penaliza_mantener_mas_tiempo(self) -> None:
        closes = [100.0, 101.0] * 5 + [100.5] * 20
        o, h, low, c = _series_desde_closes(closes)
        sin_f = triple_barrier_labels(h, low, c, o, _config(tp_mult=20.0, sl_mult=20.0), TF_MS)
        con_f = triple_barrier_labels(
            h, low, c, o, _config(tp_mult=20.0, sl_mult=20.0, funding_pct_per_8h=0.01), TF_MS
        )
        idx = np.flatnonzero(sin_f.usable & con_f.usable)
        assert np.all(con_f.net_return[idx] < sin_f.net_return[idx])

    def test_sin_label_al_final_de_la_serie(self) -> None:
        rng = np.random.default_rng(4)
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.005, 200)))
        lab = triple_barrier_labels(
            close, close, close, close, _config(horizon_bars=8, vol_window_bars=20), TF_MS
        )
        assert np.all(lab.label[-8:] == -1)

    def test_resolution_mix_suma_uno(self) -> None:
        rng = np.random.default_rng(5)
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.005, 800)))
        high, low = close * 1.002, close * 0.998
        lab = triple_barrier_labels(high, low, close, close, _config(vol_window_bars=50), TF_MS)
        mix = lab.resolution_mix()
        assert mix["tp"] + mix["sl"] + mix["vertical"] == pytest.approx(1.0)

    def test_label_es_uno_solo_si_la_resolucion_fue_tp(self) -> None:
        rng = np.random.default_rng(6)
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.005, 800)))
        high, low = close * 1.002, close * 0.998
        lab = triple_barrier_labels(high, low, close, close, _config(vol_window_bars=50), TF_MS)
        u = lab.usable
        np.testing.assert_array_equal(lab.label[u] == 1, lab.resolution[u] == 1)

    def test_es_causal_respecto_del_horizonte(self) -> None:
        """Mutar barras más allá de i+H no puede cambiar el label de i."""
        rng = np.random.default_rng(7)
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.005, 400)))
        high, low = close * 1.002, close * 0.998
        cfg = _config(horizon_bars=6, vol_window_bars=30)

        original = triple_barrier_labels(high, low, close, close, cfg, TF_MS, "long")
        cut = 300
        c2, h2, l2 = close.copy(), high.copy(), low.copy()
        c2[cut:] *= 4.0
        h2[cut:] *= 4.0
        l2[cut:] *= 4.0
        mutado = triple_barrier_labels(h2, l2, c2, c2, cfg, TF_MS, "long")

        # Los labels cuyo horizonte completo cae antes del corte no cambian.
        safe = cut - cfg.horizon_bars - 1
        np.testing.assert_array_equal(original.label[:safe], mutado.label[:safe])


class TestForwardTargets:
    def test_forward_return_valores_conocidos(self) -> None:
        close = np.array([100.0, 110.0, 121.0, 133.1])
        out = forward_return(close, 1)
        assert out[0] == pytest.approx(np.log(1.1))
        assert np.isnan(out[-1])

    def test_forward_return_mira_al_futuro_por_diseno(self) -> None:
        """Es un target, no un feature: debe mirar adelante. Se documenta acá."""
        close = np.array([100.0, 200.0, 400.0])
        assert forward_return(close, 2)[0] == pytest.approx(np.log(4.0))

    def test_forward_volatility_no_negativa(self) -> None:
        rng = np.random.default_rng(8)
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))
        out = forward_volatility(close, 10)
        valid = out[~np.isnan(out)]
        assert np.all(valid >= 0)

    def test_forward_volatility_detecta_el_tramo_agitado(self) -> None:
        rng = np.random.default_rng(9)
        calmo = rng.normal(0, 0.001, 200)
        agitado = rng.normal(0, 0.02, 200)
        close = 100 * np.exp(np.cumsum(np.concatenate([calmo, agitado])))
        out = forward_volatility(close, 20)
        assert np.nanmean(out[220:380]) > 5 * np.nanmean(out[20:150])

    def test_forward_volatility_nan_al_final(self) -> None:
        close = np.linspace(100, 110, 50)
        assert np.all(np.isnan(forward_volatility(close, 10)[-10:]))


class TestUniqueness:
    def test_concurrencia_de_labels_disjuntos_es_uno(self) -> None:
        n = 10
        span = np.array([1, -1, 3, -1, 5, -1, 7, -1, 9, -1])
        conc = concurrency(span, n)
        assert conc[1] == 1.0

    def test_labels_solapados_elevan_la_concurrencia(self) -> None:
        n = 10
        span = np.full(n, 8)  # todos cubren hasta la barra 8
        span[9] = -1
        conc = concurrency(span, n)
        assert conc[5] > 3.0

    def test_peso_de_label_solitario_es_uno(self) -> None:
        n = 10
        span = np.full(n, -1)
        span[0] = 3  # único label vivo, cubre barras 1..3
        w = uniqueness_weights(span, n)
        assert w[0] == pytest.approx(1.0)

    def test_solapamiento_reduce_el_peso(self) -> None:
        n = 20
        solapados = np.full(n, -1)
        for i in range(10):
            solapados[i] = 12  # diez labels compartiendo casi todo el futuro
        w_sol = uniqueness_weights(solapados, n)

        disjuntos = np.full(n, -1)
        disjuntos[0] = 2
        disjuntos[5] = 7
        w_dis = uniqueness_weights(disjuntos, n)

        assert w_sol[0] < 0.5
        assert w_dis[0] > 0.9

    def test_pesos_en_rango_valido(self) -> None:
        rng = np.random.default_rng(10)
        n = 500
        span = np.arange(n) + rng.integers(1, 15, n)
        span = np.minimum(span, n - 1)
        w = uniqueness_weights(span, n)
        assert np.all(w >= 0.0) and np.all(w <= 1.0 + 1e-9)

    def test_labels_sin_resolver_pesan_cero(self) -> None:
        n = 10
        span = np.full(n, -1)
        assert np.all(uniqueness_weights(span, n) == 0.0)
