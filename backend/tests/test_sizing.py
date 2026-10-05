"""Calculadora de tamaño por riesgo fijo."""

from __future__ import annotations

import pytest

from bob.models.projection import liquidation_price
from bob.models.sizing import cost_fraction, size_position
from bob.venues import BINANCE_USDM


def _codes(s) -> set[str]:  # type: ignore[no-untyped-def]
    return {w.code for w in s.warnings}


def test_caso_de_referencia_500_usd_1pct_stop_1pct() -> None:
    s = size_position(500.0, 0.01, 2500.0, 2475.0, 5.0)
    assert s.direction == "long"
    assert s.cost_pct == pytest.approx(0.0014)
    # 5 USD / (1% + 0,14%) = 438,6 USD → 0,175 ETH redondeado hacia abajo.
    assert s.quantity == pytest.approx(0.175)
    assert s.notional == pytest.approx(437.5)
    assert s.loss_at_stop == pytest.approx(437.5 * 0.0114)
    assert s.loss_at_stop <= s.risk_budget
    assert s.margin == pytest.approx(87.5)
    assert s.liquidation_price == pytest.approx(2500 * 0.8 / 0.995)
    assert s.max_safe_leverage == pytest.approx(1 / (1.5 * 0.01 * 1.005 + 0.005))
    assert _codes(s) == set()


def test_la_perdida_al_stop_nunca_supera_el_presupuesto() -> None:
    for entry, stop in ((2500.0, 2490.0), (2500.0, 2600.0), (3333.3, 3301.7)):
        s = size_position(500.0, 0.015, entry, stop, 10.0)
        assert s.loss_at_stop <= s.risk_budget + 1e-9
        # Y no se queda corto por más de un paso de cantidad.
        step_loss = 0.001 * entry * (s.stop_pct + s.cost_pct)
        assert s.risk_budget - s.loss_at_stop < step_loss + 1e-9


def test_el_leverage_no_cambia_el_tamano_solo_margen_y_liquidacion() -> None:
    a = size_position(500.0, 0.01, 2500.0, 2475.0, 2.0)
    b = size_position(500.0, 0.01, 2500.0, 2475.0, 20.0)
    assert a.quantity == b.quantity and a.loss_at_stop == b.loss_at_stop
    assert b.margin < a.margin
    assert b.liquidation_pct < a.liquidation_pct


def test_short_se_infiere_del_stop_arriba() -> None:
    s = size_position(500.0, 0.01, 2500.0, 2525.0, 5.0, target=2450.0)
    assert s.direction == "short"
    assert s.liquidation_price > s.stop
    assert s.target_pct == pytest.approx(0.02)
    # p = (sl + c) / (tp + sl)
    assert s.breakeven_win_rate == pytest.approx((0.01 + 0.0014) / 0.03)
    assert s.reward_risk_net == pytest.approx((0.02 - 0.0014) / (0.01 + 0.0014))


def test_liquidacion_antes_que_el_stop_es_peligro() -> None:
    # Stop a 5% con 25x: la liquidación está a ~3,5%.
    s = size_position(500.0, 0.01, 2500.0, 2375.0, 25.0)
    assert s.liquidation_to_stop < 1.0
    assert "liq_before_stop" in _codes(s)
    level = next(w.level for w in s.warnings if w.code == "liq_before_stop")
    assert level == "danger"


def test_avisos_de_riesgo_costo_margen_y_minimo() -> None:
    assert "risk_high" in _codes(size_position(500.0, 0.05, 2500.0, 2475.0, 5.0))
    assert "cost_heavy" in _codes(size_position(500.0, 0.01, 2500.0, 2495.0, 50.0))
    # Stop a 0,2% y 1x: el notional (≈1.470 USD) no cabe en 500 USD.
    s = size_position(500.0, 0.01, 2500.0, 2495.0, 1.0)
    assert "margin_exceeds_capital" in _codes(s)
    assert s.required_leverage == pytest.approx(s.notional / 500.0)
    tiny = size_position(50.0, 0.005, 2500.0, 2400.0, 5.0)
    assert {"qty_zero"} <= _codes(tiny) or {"below_min_notional"} <= _codes(tiny)


def test_rachas_de_perdida_con_riesgo_fijo() -> None:
    s = size_position(500.0, 0.02, 2500.0, 2475.0, 5.0)
    assert s.losing_streaks[10] == pytest.approx(1 - 0.98**10)


def test_entrada_maker_no_paga_slippage_de_entrada() -> None:
    assert cost_fraction(BINANCE_USDM, maker_entry=True) == pytest.approx(0.0002 + 0.0005 + 0.0002)


def test_la_liquidacion_es_la_misma_formula_de_projection() -> None:
    s = size_position(500.0, 0.01, 2500.0, 2475.0, 7.0)
    profile = BINANCE_USDM.leverage_profile(7.0, s.notional, s.quantity)
    assert s.liquidation_price == liquidation_price(2500.0, "long", profile)


@pytest.mark.parametrize(
    "args",
    [(0, 0.01, 1, 0.9, 1), (500, 0, 1, 0.9, 1), (500, 0.01, 1, 1, 1), (500, 0.01, 1, 0.9, 0)],
)
def test_entradas_invalidas(args: tuple[float, ...]) -> None:
    with pytest.raises(ValueError):
        size_position(*args)


def test_objetivo_del_lado_equivocado() -> None:
    with pytest.raises(ValueError):
        size_position(500.0, 0.01, 2500.0, 2475.0, 5.0, target=2400.0)


def test_stop_en_sigmas_y_toque_por_ruido() -> None:
    from bob.models.projection import prob_touch

    s = size_position(500.0, 0.01, 2500.0, 2490.0, 5.0, target=2520.0, sigma_horizon=0.01)
    assert s.stop_sigmas == pytest.approx(0.4)
    assert s.p_touch_stop == pytest.approx(prob_touch(0.004, 0.01))
    assert s.p_touch_target == pytest.approx(prob_touch(0.008, 0.01))
    assert "stop_in_noise" in _codes(s)
    wide = size_position(500.0, 0.01, 2500.0, 2450.0, 5.0, sigma_horizon=0.01)
    assert "stop_in_noise" not in _codes(wide)
    bare = size_position(500.0, 0.01, 2500.0, 2450.0, 5.0)
    assert bare.stop_sigmas is None and bare.p_touch_stop is None


def test_regimen_elevado_avisa_y_es_rojo_a_leverage_alto() -> None:
    low = size_position(500.0, 0.01, 2500.0, 2450.0, 5.0, regime_elevated=True)
    high = size_position(500.0, 0.01, 2500.0, 2475.0, 20.0, regime_elevated=True)
    lvl = {w.code: w.level for w in low.warnings}
    assert lvl["regime_elevated"] == "warn"
    assert {w.code: w.level for w in high.warnings}["regime_elevated"] == "danger"
    calm = size_position(500.0, 0.01, 2500.0, 2450.0, 5.0, regime_elevated=False)
    assert "regime_elevated" not in _codes(calm)
