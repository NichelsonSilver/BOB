"""Tests de la proyección operativa (KPI 2 + distancia a liquidación).

Acá un error no produce una métrica fea en un reporte: produce un precio de
liquidación equivocado en la pantalla de alguien que va a entrar apalancado.
Por eso los números se verifican contra la aritmética a mano, no contra sí
mismos.
"""

from __future__ import annotations

import math

import pytest

from bob.models.labeling import BarrierConfig
from bob.models.projection import (
    DEFAULT_MMR,
    LeverageProfile,
    barrier_prices,
    breakeven_probability,
    funding_cost_pct,
    liquidation_price,
    max_safe_leverage,
    net_expected_value,
    prob_touch,
    project_setup,
)

TF_MS = 900_000  # 15m
ENTRY = 2500.0
SIGMA = 0.01  # 1% para el horizonte completo


def _config(**kwargs: float) -> BarrierConfig:
    return BarrierConfig(**kwargs)  # type: ignore[arg-type]


class TestBarreras:
    def test_long_pone_tp_arriba_y_sl_abajo(self) -> None:
        tp, sl = barrier_prices(ENTRY, SIGMA, _config(), "long")
        assert tp == pytest.approx(ENTRY * (1 + 0.5 * SIGMA))
        assert sl == pytest.approx(ENTRY * (1 - 0.5 * SIGMA))

    def test_short_es_el_espejo(self) -> None:
        tp_l, sl_l = barrier_prices(ENTRY, SIGMA, _config(), "long")
        tp_s, sl_s = barrier_prices(ENTRY, SIGMA, _config(), "short")
        assert tp_s == pytest.approx(2 * ENTRY - tp_l)
        assert sl_s == pytest.approx(2 * ENTRY - sl_l)

    def test_escala_con_la_volatilidad(self) -> None:
        """El punto de dimensionar por sigma: el mismo setup se ensancha
        cuando el mercado se agita."""
        tp_calmo, _ = barrier_prices(ENTRY, 0.005, _config(), "long")
        tp_agitado, _ = barrier_prices(ENTRY, 0.02, _config(), "long")
        assert (tp_agitado - ENTRY) == pytest.approx(4 * (tp_calmo - ENTRY))

    def test_rechaza_entradas_invalidas(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            barrier_prices(ENTRY, SIGMA, _config(), "arriba")
        with pytest.raises(ValueError, match="precio de entrada"):
            barrier_prices(0.0, SIGMA, _config(), "long")
        with pytest.raises(ValueError, match="sigma"):
            barrier_prices(ENTRY, 0.0, _config(), "long")


class TestEconomiaDelSetup:
    def test_ev_es_la_formula_de_siempre(self) -> None:
        ev = net_expected_value(0.6, 0.01, 0.01, 0.0014)
        assert ev == pytest.approx(0.6 * 0.01 - 0.4 * 0.01 - 0.0014)

    def test_el_equilibrio_anula_el_ev(self) -> None:
        """Definición de punto de equilibrio: con esa P exacta, EV = 0."""
        p = breakeven_probability(0.01, 0.01, 0.0014)
        assert net_expected_value(p, 0.01, 0.01, 0.0014) == pytest.approx(0.0, abs=1e-12)

    def test_el_costo_sube_el_equilibrio(self) -> None:
        sin_costo = breakeven_probability(0.01, 0.01, 0.0)
        con_costo = breakeven_probability(0.01, 0.01, 0.0014)
        assert sin_costo == pytest.approx(0.5)
        assert con_costo > sin_costo

    def test_barreras_degeneradas_dan_nan(self) -> None:
        assert math.isnan(breakeven_probability(0.0, 0.0, 0.001))

    def test_probabilidad_fuera_de_rango_falla(self) -> None:
        with pytest.raises(ValueError, match="probabilidad"):
            net_expected_value(1.4, 0.01, 0.01, 0.0)

    def test_el_funding_se_cobra_por_barra(self) -> None:
        config = _config()
        una = funding_cost_pct(config, TF_MS, 1)
        dieciseis = funding_cost_pct(config, TF_MS, 16)
        assert dieciseis == pytest.approx(16 * una)
        # 0.01% cada 8h, en barras de 15m => 32 barras por cobro
        assert una == pytest.approx(0.0001 / 32)

    def test_barras_negativas_no_generan_credito(self) -> None:
        assert funding_cost_pct(_config(), TF_MS, -5) == 0.0


class TestLiquidacion:
    def test_long_a_10x_coincide_con_la_aritmetica(self) -> None:
        profile = LeverageProfile(leverage=10, maintenance_margin_rate=0.005)
        esperado = ENTRY * (1 - 1 / 10) / (1 - 0.005)
        assert liquidation_price(ENTRY, "long", profile) == pytest.approx(esperado)

    def test_short_a_10x_coincide_con_la_aritmetica(self) -> None:
        profile = LeverageProfile(leverage=10, maintenance_margin_rate=0.005)
        esperado = ENTRY * (1 + 1 / 10) / (1 + 0.005)
        assert liquidation_price(ENTRY, "short", profile) == pytest.approx(esperado)

    def test_mas_leverage_acerca_la_liquidacion(self) -> None:
        d = [
            abs(liquidation_price(ENTRY, "long", LeverageProfile(leverage=lev)) - ENTRY) / ENTRY
            for lev in (2, 5, 10, 20)
        ]
        assert d == sorted(d, reverse=True)

    def test_sin_leverage_la_liquidacion_es_practicamente_cero(self) -> None:
        precio = liquidation_price(ENTRY, "long", LeverageProfile(leverage=1))
        assert precio == pytest.approx(0.0, abs=1e-9)

    def test_nunca_devuelve_precio_negativo(self) -> None:
        """Con leverage < 1x la fórmula daría negativo: eso no es un precio."""
        assert liquidation_price(ENTRY, "long", LeverageProfile(leverage=0.5)) == 0.0

    def test_el_maintenance_amount_acerca_la_liquidacion_del_long(self) -> None:
        base = liquidation_price(ENTRY, "long", LeverageProfile(leverage=10))
        con_cum = liquidation_price(
            ENTRY, "long", LeverageProfile(leverage=10, maintenance_amount_per_unit=5.0)
        )
        assert con_cum < base

    def test_leverage_invalido_falla_temprano(self) -> None:
        with pytest.raises(ValueError, match="leverage"):
            LeverageProfile(leverage=0)
        with pytest.raises(ValueError, match="maintenance margin"):
            LeverageProfile(maintenance_margin_rate=1.5)


class TestLeverageSeguro:
    def test_el_maximo_deja_la_liquidacion_detras_del_stop(self) -> None:
        sl = 0.005
        lev = max_safe_leverage(sl, DEFAULT_MMR, 1.5)
        profile = LeverageProfile(leverage=lev, maintenance_margin_rate=DEFAULT_MMR)
        dist = abs(liquidation_price(ENTRY, "short", profile) - ENTRY) / ENTRY
        assert dist == pytest.approx(1.5 * sl, rel=1e-6)

    def test_tambien_protege_al_long(self) -> None:
        """Se usa la variante del short (la más estrecha) para las dos."""
        sl = 0.005
        lev = max_safe_leverage(sl, DEFAULT_MMR, 1.5)
        profile = LeverageProfile(leverage=lev, maintenance_margin_rate=DEFAULT_MMR)
        dist = abs(liquidation_price(ENTRY, "long", profile) - ENTRY) / ENTRY
        assert dist >= 1.5 * sl

    def test_un_stop_mas_ancho_exige_menos_leverage(self) -> None:
        assert max_safe_leverage(0.01) < max_safe_leverage(0.005)

    def test_sin_stop_no_hay_limite(self) -> None:
        assert max_safe_leverage(0.0) == float("inf")


class TestProbabilidadDeToque:
    def test_una_barrera_a_una_sigma(self) -> None:
        """P(tocar 1σ) = erfc(1/√2) ≈ 31.7% en el modelo browniano."""
        assert prob_touch(0.01, 0.01) == pytest.approx(math.erfc(1 / math.sqrt(2)))

    def test_mas_lejos_es_menos_probable(self) -> None:
        assert prob_touch(0.03, 0.01) < prob_touch(0.01, 0.01)

    def test_una_barrera_ya_tocada_es_certeza(self) -> None:
        assert prob_touch(0.0, 0.01) == 1.0

    def test_sin_volatilidad_no_hay_respuesta(self) -> None:
        assert math.isnan(prob_touch(0.01, 0.0))


class TestProyeccionCompleta:
    def test_arma_el_setup_entero(self) -> None:
        p = project_setup(
            entry_price=ENTRY,
            sigma_horizon=SIGMA,
            probability=0.70,
            config=_config(),
            timeframe_ms=TF_MS,
            direction="long",
            profile=LeverageProfile(leverage=5),
        )
        assert p.take_profit > ENTRY > p.stop_loss > p.liquidation_price
        assert p.tp_pct == pytest.approx(0.005)
        assert p.risk_reward == pytest.approx(1.0)
        assert p.horizon_hours == pytest.approx(4.0)  # 16 barras de 15m
        assert p.margin_pct == pytest.approx(0.2)
        assert p.net_ev_pct > 0
        assert p.is_actionable

    def test_el_leverage_multiplica_el_ev_sobre_el_margen(self) -> None:
        kwargs = dict(
            entry_price=ENTRY,
            sigma_horizon=SIGMA,
            probability=0.70,
            config=_config(),
            timeframe_ms=TF_MS,
        )
        sin = project_setup(**kwargs, profile=LeverageProfile(leverage=1))  # type: ignore[arg-type]
        con = project_setup(**kwargs, profile=LeverageProfile(leverage=10))  # type: ignore[arg-type]
        assert con.roe_pct == pytest.approx(10 * sin.roe_pct)
        assert con.net_ev_pct == pytest.approx(sin.net_ev_pct)

    def test_avisa_cuando_la_liquidacion_llega_antes_que_el_stop(self) -> None:
        """El escenario que ya le costó una liquidación al usuario."""
        p = project_setup(
            entry_price=ENTRY,
            sigma_horizon=0.02,
            probability=0.70,
            config=_config(),
            timeframe_ms=TF_MS,
            profile=LeverageProfile(leverage=75),
        )
        # SL a 1% (0.5 x sigma) contra una liquidación a ~0.84%: el stop nunca
        # se ejecuta, la posición muere antes.
        assert p.liq_distance_pct < p.sl_pct
        assert p.liquidation_before_stop is True
        assert p.is_actionable is False
        assert any("liquidación" in w for w in p.warnings)
        assert p.max_safe_leverage < 75

    def test_avisa_cuando_la_liquidacion_esta_apretada(self) -> None:
        p = project_setup(
            entry_price=ENTRY,
            sigma_horizon=SIGMA,
            probability=0.70,
            config=_config(),
            timeframe_ms=TF_MS,
            profile=LeverageProfile(leverage=int(max_safe_leverage(0.005)) + 3),
        )
        assert p.liquidation_before_stop is False
        assert any("mechas" in w for w in p.warnings)

    def test_avisa_cuando_la_probabilidad_no_paga_los_costos(self) -> None:
        p = project_setup(
            entry_price=ENTRY,
            sigma_horizon=SIGMA,
            probability=0.45,
            config=_config(),
            timeframe_ms=TF_MS,
        )
        assert p.net_ev_pct < 0
        assert p.is_actionable is False
        assert any("equilibrio" in w for w in p.warnings)
        assert p.edge_pp < 0

    def test_el_edge_se_mide_contra_el_equilibrio_no_contra_50(self) -> None:
        p = project_setup(
            entry_price=ENTRY,
            sigma_horizon=SIGMA,
            probability=0.62,
            config=_config(),
            timeframe_ms=TF_MS,
        )
        assert p.edge_pp == pytest.approx((0.62 - p.breakeven_probability) * 100)

    def test_cobrar_menos_barras_de_funding_mejora_el_ev(self) -> None:
        """El KPI 3 (duración esperada) entra por acá: si el trade dura menos
        que el horizonte, paga menos funding."""
        kwargs = dict(
            entry_price=ENTRY,
            sigma_horizon=SIGMA,
            probability=0.70,
            config=_config(),
            timeframe_ms=TF_MS,
        )
        completo = project_setup(**kwargs)  # type: ignore[arg-type]
        corto = project_setup(**kwargs, bars_held=4)  # type: ignore[arg-type]
        assert corto.net_ev_pct > completo.net_ev_pct
        assert corto.cost_pct < completo.cost_pct

    def test_el_short_proyecta_simetrico(self) -> None:
        largo = project_setup(
            entry_price=ENTRY,
            sigma_horizon=SIGMA,
            probability=0.70,
            config=_config(),
            timeframe_ms=TF_MS,
            direction="long",
            profile=LeverageProfile(leverage=5),
        )
        corto = project_setup(
            entry_price=ENTRY,
            sigma_horizon=SIGMA,
            probability=0.70,
            config=_config(),
            timeframe_ms=TF_MS,
            direction="short",
            profile=LeverageProfile(leverage=5),
        )
        assert corto.net_ev_pct == pytest.approx(largo.net_ev_pct)
        assert corto.liquidation_price > ENTRY > largo.liquidation_price
        assert corto.take_profit < ENTRY < largo.take_profit

    def test_serializa_todo_para_el_log_de_la_senal(self) -> None:
        p = project_setup(
            entry_price=ENTRY,
            sigma_horizon=SIGMA,
            probability=0.70,
            config=_config(),
            timeframe_ms=TF_MS,
        )
        d = p.as_dict()
        assert d["direction"] == "long"
        assert d["is_actionable"] is True
        assert isinstance(d["warnings"], list)
        assert set(d) >= {"liquidation_price", "net_ev_pct", "breakeven_probability"}

    def test_direccion_invalida_falla(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            project_setup(
                entry_price=ENTRY,
                sigma_horizon=SIGMA,
                probability=0.7,
                config=_config(),
                timeframe_ms=TF_MS,
                direction="lateral",
            )
