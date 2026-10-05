# ruff: noqa: E501 — lineas de formato del reporte/fixtures
"""Gate del ranking macro (`marea.ranking`): plomería, causalidad y controles."""

from __future__ import annotations

import numpy as np
import pytest

from marea import ranking
from marea.ranking import RankingConfig

RNG = np.random.default_rng(11)
BLOCKS = ["acciones"] * 4 + ["renta_fija"] * 4 + ["commodities"] * 4
N_WEEKS, N_ASSETS = 420, 12
FAST = {"n_boot": 500}


def _closes(rng: np.random.Generator) -> np.ndarray:
    w = rng.normal(0.001, 0.02, (N_WEEKS, N_ASSETS))
    return np.exp(np.cumsum(w, axis=0))


def test_indice_y_retornos_con_rezago_de_entrada() -> None:
    daily = np.array([[0.01], [0.02], [-0.01], [0.03], [0.0], [0.01]])
    idx = ranking.price_index(daily)
    ends = [0, 2, 4]
    f1 = ranking.forward_returns(idx, ends, 1, entry_lag=1)
    assert f1[0, 0] == pytest.approx(idx[3, 0] / idx[1, 0] - 1)  # entra en 1, sale en 3
    f0 = ranking.forward_returns(idx, ends, 1, entry_lag=0)
    assert f0[0, 0] == pytest.approx(idx[2, 0] / idx[0, 0] - 1)
    assert np.isnan(f1[2]).all()  # la salida todavía no ocurrió
    assert np.isnan(ranking.forward_returns(idx, ends, 2, entry_lag=2)[0]).all()


def test_features_no_miran_el_futuro_en_ningun_eje() -> None:
    c = _closes(RNG)
    macro = RNG.normal(size=(N_WEEKS, 5))
    cfg = RankingConfig()
    base, names = ranking.assemble(c, macro, BLOCKS, 0, cfg)
    assert len(names) == 28 and base.shape == (N_WEEKS, N_ASSETS, 28)

    cut = 300
    fut = c.copy()
    fut[cut + 1 :] *= 3.0  # muta el futuro de todos
    mut, _ = ranking.assemble(fut, macro, BLOCKS, 0, cfg)
    np.testing.assert_array_equal(base[: cut + 1], mut[: cut + 1])

    other = c.copy()
    other[cut + 1 :, 5] *= 0.2  # muta el futuro de UN activo
    mut2, _ = ranking.assemble(other, macro, BLOCKS, 0, cfg)
    np.testing.assert_array_equal(base[: cut + 1], mut2[: cut + 1])


def test_bloques_y_onehot() -> None:
    c = _closes(RNG)
    x, names = ranking.assemble(
        c,
        RNG.normal(size=(N_WEEKS, 5)),
        BLOCKS,
        0,
        RankingConfig(use_macro=False, use_cross_section=False),
    )
    assert len(names) == 8 + 1 + 6 and not any(n.startswith(("liq_", "xs_")) for n in names)
    acc = names.index("bloque_acciones")
    assert x[100, :4, acc].tolist() == [1.0] * 4 and x[100, 4:, acc].sum() == 0.0


def test_beta_recupera_la_carga() -> None:
    ref = RNG.normal(0, 0.02, N_WEEKS)
    w = np.column_stack([ref, 2.0 * ref + RNG.normal(0, 0.001, N_WEEKS)])
    beta = ranking.beta_to(np.exp(np.cumsum(w, axis=0)), 0)
    assert beta[-1, 0] == pytest.approx(1.0) and beta[-1, 1] == pytest.approx(2.0, rel=0.02)


def _synthetic(
    signal: float, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Retorno futuro con la interacción liquidez × clase de activo inyectada."""
    c = _closes(rng)
    macro = rng.normal(size=(N_WEEKS, 5))
    tilt = np.array(
        [1.0 if b == "acciones" else -1.0 if b == "renta_fija" else 0.0 for b in BLOCKS]
    )
    fwd = signal * macro[:, [0]] * tilt[None, :] + rng.normal(0, 1.0, (N_WEEKS, N_ASSETS))
    fwd[-4:] = np.nan
    return c, macro, fwd


def test_control_positivo_detecta_la_interaccion_y_el_placebo_la_mata() -> None:
    c, macro, fwd = _synthetic(0.8, np.random.default_rng(5))
    cfg = RankingConfig(**FAST)
    res = ranking.run(c, fwd, macro, BLOCKS, 0, cfg)
    v = ranking.verdict(res, "momentum_12_1")
    assert v["c1_ordena"] and v["c1_ic_mean"] > 0.2
    assert v["c2_supera_trivial"] and v["viable"]

    sin_liq = ranking.run(c, fwd, macro, BLOCKS, 0, RankingConfig(use_macro=False, **FAST))
    assert np.nanmean(sin_liq.model.ic) < 0.05  # sin el bloque C no hay forma de verla

    pla = ranking.run(c, fwd, macro, BLOCKS, 0, RankingConfig(placebo=True, **FAST))
    assert abs(np.nanmean(pla.model.ic)) < 0.05


def test_ruido_no_ordena() -> None:
    c, macro, fwd = _synthetic(0.0, np.random.default_rng(9))
    res = ranking.run(c, fwd, macro, BLOCKS, 0, RankingConfig(**FAST))
    v = ranking.verdict(res, "momentum_12_1")
    assert not v["viable"]
    assert v["reading"] in (
        "no hay orden detectable a esta potencia",
        "el ranking trivial captura lo que hay",
    )
    assert res.n_test_rows < res.n_rows and res.fold_sizes


def test_spread_rotacion_y_paired() -> None:
    score = np.array([3.0, 2.0, 1.0, 1.0, 2.0, 3.0])
    fwd = np.array([0.03, 0.02, 0.01, 0.01, 0.02, 0.03])
    d = np.array([0, 0, 0, 1, 1, 1])
    sp, tops = ranking.spread_series(score, fwd, d, np.array([0, 1]), 1 / 3)
    assert sp.tolist() == pytest.approx([0.02, 0.02])
    assert ranking.turnover(tops, np.array([0, 1, 2, 0, 1, 2])) == 1.0
    ic = ranking.ic_series(score, fwd, d, np.array([0, 1]))
    assert ic.tolist() == pytest.approx([1.0, 1.0])
    p = ranking.paired(np.full(40, 0.1), np.zeros(40), 4, RankingConfig(**FAST))
    assert p["excludes_zero"] and p["diff_mean"] == pytest.approx(0.1)


def test_cross_rank_exige_la_fila_completa() -> None:
    r = ranking.cross_rank(np.array([[1.0, 3.0, 2.0], [1.0, np.nan, 2.0]]))
    assert r[0].tolist() == [0.0, 1.0, 0.5] and np.isnan(r[1]).all()


def test_drop_macro_saca_solo_esa_columna() -> None:
    c = _closes(RNG)
    _, names = ranking.assemble(
        c, RNG.normal(size=(N_WEEKS, 5)), BLOCKS, 0, RankingConfig(drop_macro=("fed_net",))
    )
    liq = [n for n in names if n.startswith("liq_")]
    assert liq == ["liq_m2", "liq_dolar", "liq_tasa_real", "liq_nfci"]
