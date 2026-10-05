"""Medición de potencia (`marea.power`): la plomería mide lo que dice medir."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from marea import power

RNG = np.random.default_rng(3)


def test_grilla_semanal_toma_el_ultimo_habil_de_cada_semana() -> None:
    days = [date(2026, 9, 28) + timedelta(days=i) for i in range(12) if (i % 7) < 5]
    ends = power.weekly_grid(days)
    assert [days[i] for i in ends] == [date(2026, 10, 2), date(2026, 10, 9)]


def test_compound_es_el_producto_de_los_diarios() -> None:
    daily = np.array([[0.01, 0.0], [0.02, -0.01], [-0.01, 0.03], [0.0, 0.0]])
    w = power.compound(daily, [0, 2, 3])
    assert w[0] == pytest.approx([1.02 * 0.99 - 1, 0.99 * 1.03 - 1])
    assert w[1] == pytest.approx([0.0, 0.0])


def test_forward_y_senales_no_miran_el_futuro() -> None:
    w = RNG.normal(0, 0.02, (120, 5))
    mutated = w.copy()
    mutated[80:] = 0.5
    for f in (power.momentum, power.reversal):
        np.testing.assert_array_equal(f(w)[:80], f(mutated)[:80])
    fwd = power.forward_returns(w, 4)
    assert fwd[10] == pytest.approx(np.prod(1 + w[11:15], axis=0) - 1)
    assert np.isnan(fwd[-4:]).all()


def test_momentum_12_1_excluye_el_ultimo_mes() -> None:
    w = np.zeros((60, 2))
    w[57, 0] = 0.5  # dentro de las últimas 4 semanas respecto de t=59
    assert power.momentum(w)[59, 0] == 0.0
    w[50, 0] = 0.5
    assert power.momentum(w)[59, 0] == pytest.approx(np.log(1.5))


def test_nulo_senal_al_azar_dispersa_como_permutacion() -> None:
    n_assets, t = 23, 4000
    sig = RNG.normal(size=(t, n_assets))
    fwd = RNG.normal(size=(t, n_assets))
    ic = power.ic_by_date(sig, fwd)
    assert np.nanstd(ic) == pytest.approx(1 / np.sqrt(n_assets - 1), rel=0.05)
    assert abs(np.nanmean(ic)) < 3 * power.block_se(ic, 1)


def test_control_positivo_una_senal_inyectada_se_detecta() -> None:
    n_assets, t, effect = 23, 600, 0.15
    sig = RNG.normal(size=(t, n_assets))
    fwd = effect * sig + RNG.normal(size=(t, n_assets))
    ic = power.ic_by_date(sig, fwd)
    mde = power.MDE_FACTOR * power.block_se(ic, 1)
    assert np.nanmean(ic) > mde


def test_ic_omite_filas_incompletas_o_constantes() -> None:
    sig = np.array([[1.0, 2.0, 3.0], [1.0, np.nan, 3.0], [1.0, 1.0, 1.0]])
    fwd = np.array([[3.0, 2.0, 1.0], [1.0, 2.0, 3.0], [1.0, 2.0, 3.0]])
    ic = power.ic_by_date(sig, fwd)
    assert ic[0] == pytest.approx(-1.0) and np.isnan(ic[1:]).all()


def test_block_se_crece_con_la_dependencia() -> None:
    iid = RNG.normal(size=800)
    dep = np.repeat(RNG.normal(size=200), 4)  # cada valor repetido 4 veces
    assert power.block_se(dep, 4) > 1.5 * power.block_se(iid, 4)
    assert np.isnan(power.block_se(np.array([1.0]), 4))


def test_effective_n_factor_comun_vs_independientes() -> None:
    f = RNG.normal(size=(2000, 1))
    one_factor = f + 0.1 * RNG.normal(size=(2000, 10))
    indep = RNG.normal(size=(2000, 10))
    a, b = power.effective_n(one_factor), power.effective_n(indep)
    assert a.participation_ratio < 1.2 and a.pc1_share > 0.9 and a.n80 == 1
    assert b.participation_ratio > 9.0
    dm = power.demean_rows(one_factor)
    assert np.allclose(dm.mean(axis=1), 0.0)


def test_veredicto_por_umbral() -> None:
    assert power.verdict(0.04) == "potencia holgada"
    assert power.verdict(0.05) == "potencia holgada"
    assert power.verdict(0.08) == "potencia solo para un efecto grande"
    assert power.verdict(0.11) == "sin potencia"
    assert power.verdict(float("nan")) == "sin dato"
