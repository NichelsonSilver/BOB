"""Tests de los perfiles de venue — la independencia de la plataforma.

Lo que se verifica acá no es aritmética de exchange: es que el motor
probabilístico no dependa de dónde entra el usuario. El mismo setup, en dos
venues distintos, tiene que dar la misma probabilidad y distinto costo.
"""

from __future__ import annotations

import numpy as np
import pytest

from bob.models.labeling import BarrierConfig
from bob.models.projection import liquidation_price
from bob.venues import (
    BINANCE_USDM,
    BYBIT_LINEAR,
    DEFAULT_VENUE,
    VENUES,
    MarginTier,
    VenueProfile,
    get_venue,
)


def test_get_venue_sin_clave_devuelve_el_default():
    assert get_venue().key == DEFAULT_VENUE
    assert get_venue(None) is get_venue()


def test_get_venue_desconocido_dice_cuales_hay():
    with pytest.raises(ValueError, match="venue desconocido"):
        get_venue("kraken_futures")


def test_todos_los_perfiles_estan_indexados_por_su_clave():
    for key, profile in VENUES.items():
        assert profile.key == key


def test_roundtrip_taker_es_el_caso_conservador():
    """El default debe ser el más caro: subestimar el fee infla el EV."""
    taker = BINANCE_USDM.roundtrip_fee()
    maker = BINANCE_USDM.roundtrip_fee(maker_entry=True, maker_exit=True)
    mixto = BINANCE_USDM.roundtrip_fee(maker_entry=True)

    assert taker == pytest.approx(2 * BINANCE_USDM.taker_fee)
    assert maker == pytest.approx(2 * BINANCE_USDM.maker_fee)
    assert maker < mixto < taker


def test_tier_for_elige_el_escalon_correcto():
    tiers = (
        MarginTier(notional_cap=100.0, maintenance_margin_rate=0.005),
        MarginTier(notional_cap=1000.0, maintenance_margin_rate=0.01, maintenance_amount=5.0),
    )
    venue = VenueProfile(
        key="t",
        name="t",
        maker_fee=0.0,
        taker_fee=0.0,
        slippage_pct=0.0,
        funding_interval_hours=8.0,
        margin_tiers=tiers,
        max_leverage=10.0,
    )

    assert venue.tier_for(50.0) is tiers[0]
    assert venue.tier_for(100.0) is tiers[0]  # el cap es inclusivo
    assert venue.tier_for(100.01) is tiers[1]
    assert venue.tier_for(999_999.0) is tiers[1]  # arriba del último: el más exigente


def test_tier_for_arriba_del_ultimo_no_afloja_el_margen():
    """Equivocarse hacia el lado exigente acerca la liquidación, no la aleja."""
    ultimo = BINANCE_USDM.margin_tiers[-1]
    enorme = BINANCE_USDM.tier_for(10_000_000.0)

    assert enorme is ultimo
    assert enorme.maintenance_margin_rate == max(
        t.maintenance_margin_rate for t in BINANCE_USDM.margin_tiers
    )


def test_barrier_config_inyecta_los_costos_del_venue():
    base = BarrierConfig()
    config = BINANCE_USDM.barrier_config(base)

    assert config.fee_roundtrip_pct == pytest.approx(2 * BINANCE_USDM.taker_fee)
    assert config.slippage_pct == pytest.approx(2 * BINANCE_USDM.slippage_pct)
    # Lo que no es del venue no se toca: las barreras siguen siendo las mismas.
    assert config.tp_mult == base.tp_mult
    assert config.horizon_bars == base.horizon_bars


def test_barrier_config_no_muta_la_base():
    base = BarrierConfig()
    fee_original = base.fee_roundtrip_pct

    BYBIT_LINEAR.barrier_config(base)

    assert base.fee_roundtrip_pct == fee_original


def test_venue_mas_caro_sube_la_probabilidad_de_equilibrio():
    """Es el punto entero del módulo: el venue cambia el umbral, no el modelo.

    Bybit cobra taker más caro que Binance; con las mismas barreras, el mismo
    KPI de Seguridad tiene que rendir más para valer la pena.
    """
    barato = BINANCE_USDM.barrier_config()
    caro = BYBIT_LINEAR.barrier_config()
    sigma = 0.01

    assert BYBIT_LINEAR.taker_fee > BINANCE_USDM.taker_fee
    assert caro.breakeven_probability(sigma) > barato.breakeven_probability(sigma)


def test_funding_se_reescala_al_intervalo_del_venue():
    """Un venue que cobra cada 4h cobra el doble en las mismas 8 horas."""
    cada_4h = VenueProfile(
        key="t",
        name="t",
        maker_fee=0.0,
        taker_fee=0.0,
        slippage_pct=0.0,
        funding_interval_hours=4.0,
        margin_tiers=(MarginTier(notional_cap=1e9, maintenance_margin_rate=0.005),),
        max_leverage=10.0,
    )

    config = cada_4h.barrier_config(funding_pct_per_8h=0.0001)
    assert config.funding_pct_per_8h == pytest.approx(0.0002)


def test_funding_sin_argumento_conserva_el_de_la_base():
    base = BarrierConfig()
    config = BINANCE_USDM.barrier_config(base)
    assert config.funding_pct_per_8h == base.funding_pct_per_8h


def test_leverage_profile_traduce_el_maintenance_amount_a_unidad():
    """`projection.py` lo consume por unidad; el bracket lo publica absoluto."""
    entry = 3000.0
    quantity = 100.0  # notional 300.000 -> segundo tier de Binance
    notional = entry * quantity

    profile = BINANCE_USDM.leverage_profile(10.0, notional, quantity)
    tier = BINANCE_USDM.tier_for(notional)

    assert profile.leverage == 10.0
    assert profile.maintenance_margin_rate == tier.maintenance_margin_rate
    assert profile.maintenance_amount_per_unit == pytest.approx(
        tier.maintenance_amount / quantity
    )


def test_leverage_profile_con_cantidad_cero_no_divide_por_cero():
    profile = BINANCE_USDM.leverage_profile(5.0, 0.0, 0.0)
    assert profile.maintenance_amount_per_unit == 0.0


def test_el_venue_no_toca_la_probabilidad_solo_la_liquidacion():
    """Misma entrada y mismo leverage, distinto venue: cambia dónde liquida.

    El modelo probabilístico no aparece en este cálculo — que es exactamente lo
    que significa "el asistente es independiente de la plataforma".
    """
    entry, quantity = 3000.0, 1.0
    notional = entry * quantity

    binance = BINANCE_USDM.leverage_profile(10.0, notional, quantity)
    bybit = BYBIT_LINEAR.leverage_profile(10.0, notional, quantity)

    liq_binance = liquidation_price(entry, "long", binance)
    liq_bybit = liquidation_price(entry, "long", bybit)

    assert np.isfinite(liq_binance) and np.isfinite(liq_bybit)
    assert liq_binance < entry and liq_bybit < entry


def test_los_tiers_estan_ordenados_por_cap():
    """`tier_for` recorre en orden: un bracket desordenado daría el tier errado."""
    for profile in VENUES.values():
        caps = [t.notional_cap for t in profile.margin_tiers]
        assert caps == sorted(caps)
        assert len(caps) == len(set(caps))


def test_los_mmr_crecen_con_el_notional():
    for profile in VENUES.values():
        mmrs = [t.maintenance_margin_rate for t in profile.margin_tiers]
        assert mmrs == sorted(mmrs)


def test_binance_es_el_default_y_dice_que_tambien_es_la_fuente():
    """La ambigüedad de los dos roles de Binance tiene que quedar escrita."""
    assert DEFAULT_VENUE == BINANCE_USDM.key
    assert "fuente de datos" in BINANCE_USDM.notes.lower()
    for otro in (BYBIT_LINEAR,):
        assert "binance" in otro.notes.lower()
