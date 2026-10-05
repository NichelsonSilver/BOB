"""Detector técnico y simulador de reglas: causalidad y convenciones."""

from __future__ import annotations

import numpy as np
import pytest

from bob.models.technical_rules import (
    RuleConfig,
    Trade,
    expectancy_interval,
    order_block_trades,
    placebo_trades,
    simulate,
    sweep_trades,
    trade_stats,
)
from bob.signals.technical import (
    LONG,
    SHORT,
    Level,
    TechnicalParams,
    cluster_levels,
    scan,
    swing_points,
)


def _random_walk(n: int, seed: int = 0, vol: float = 0.004) -> tuple[np.ndarray, ...]:
    rng = np.random.default_rng(seed)
    c = 100.0 * np.exp(np.cumsum(rng.normal(0.0, vol, n)))
    o = np.concatenate([[100.0], c[:-1]])
    wick = np.abs(rng.normal(0.0, vol / 2, (2, n)))
    h = np.maximum(o, c) * (1 + wick[0])
    lo = np.minimum(o, c) * (1 - wick[1])
    return o, h, lo, c


def _bars(rows: list[tuple[float, float, float, float]]) -> tuple[np.ndarray, ...]:
    a = np.array(rows, dtype=np.float64)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


# --------------------------------------------------------------------------- #
# Pivotes
# --------------------------------------------------------------------------- #
def test_swing_points_marca_el_extremo_y_no_los_bordes() -> None:
    high = np.array([1, 2, 3, 9, 3, 2, 1, 2, 3], dtype=float)
    low = high - 0.5
    is_h, is_l = swing_points(high, low, 3)
    assert np.flatnonzero(is_h).tolist() == [3]
    # Los primeros y últimos k no pueden ser pivote: falta una ventana.
    assert not is_h[:3].any() and not is_h[-3:].any()
    assert np.flatnonzero(is_l).tolist() == []  # el 1 de índice 6 está a 2 del borde


def test_swing_points_meseta_cuenta_una_vez() -> None:
    high = np.array([1, 2, 3, 5, 5, 3, 2, 1, 0], dtype=float)
    is_h, _ = swing_points(high, high - 1, 3)
    assert np.flatnonzero(is_h).tolist() == [3]


# --------------------------------------------------------------------------- #
# Causalidad
# --------------------------------------------------------------------------- #
def test_scan_sin_lookahead_mutar_el_futuro_no_cambia_el_pasado() -> None:
    o, h, lo, c = _random_walk(1500, seed=3)
    m = 900
    full = scan(o, h, lo, c)
    rng = np.random.default_rng(99)
    o2, h2, lo2, c2 = (x.copy() for x in (o, h, lo, c))
    noise = rng.normal(0, 5.0, c.size - m)
    for arr in (o2, h2, lo2, c2):
        arr[m:] += noise
    h2[m:] = np.maximum.reduce([h2[m:], o2[m:], c2[m:]])
    lo2[m:] = np.minimum.reduce([lo2[m:], o2[m:], c2[m:]])
    mut = scan(o2, h2, lo2, c2)
    assert [e for e in full.sweeps if e.idx < m] == [e for e in mut.sweeps if e.idx < m]
    assert [e for e in full.ob_touches if e.idx < m] == [e for e in mut.ob_touches if e.idx < m]
    assert len([e for e in full.sweeps if e.idx < m]) > 10  # el test mide algo


def test_scan_truncado_coincide_con_el_completo_hasta_el_corte() -> None:
    o, h, lo, c = _random_walk(1200, seed=5)
    m = 700
    full = scan(o, h, lo, c)
    part = scan(o[:m], h[:m], lo[:m], c[:m])
    assert part.sweeps == [e for e in full.sweeps if e.idx < m]
    assert part.ob_touches == [e for e in full.ob_touches if e.idx < m]
    # Un pivote confirmado en la barra m-1 tiene su extremo en m-1-k: nunca más tarde.
    k = TechnicalParams().pivot_k
    assert all(pv.pivot_idx <= m - 1 - k for pv in part.pivots)
    assert all(pv.confirmed_idx == pv.pivot_idx + k for pv in part.pivots)


def test_un_pivote_no_se_usa_antes_de_confirmarse() -> None:
    # Pivote alto en 5 (precio 10); se confirma en 8. Una barra 7 que lo
    # perfora y cierra abajo NO es barrido: el nivel todavía no existía.
    rows = [(5, 6, 4, 5)] * 2 + [
        (5, 7, 5, 6),
        (6, 8, 6, 7),
        (7, 9, 7, 8),
        (8, 10, 8, 9),
        (9, 9.5, 8, 8.5),
        (8.5, 10.5, 8, 8.2),
        (8.2, 9, 7.5, 8),
        (8, 8.5, 7, 7.5),
    ]
    o, h, lo, c = _bars(rows)
    res = scan(o, h, lo, c, TechnicalParams(pivot_k=2))
    assert all(e.idx != 7 for e in res.sweeps)


# --------------------------------------------------------------------------- #
# Barridos
# --------------------------------------------------------------------------- #
def _sweep_fixture() -> tuple[np.ndarray, ...]:
    # Sube a un máximo en 4 (12), baja, y en 10 perfora 12 y cierra abajo.
    rows = [
        (8, 9, 7.5, 8.5),
        (8.5, 10, 8, 9.5),
        (9.5, 11, 9, 10.5),
        (10.5, 11.5, 10, 11),
        (11, 12, 10.5, 11.2),
        (11.2, 11.5, 10, 10.2),
        (10.2, 10.8, 9.5, 9.8),
        (9.8, 10.5, 9.2, 10),
        (10, 10.9, 9.6, 10.6),
        (10.6, 11.6, 10.2, 11.4),
        (11.4, 12.8, 11, 11.6),  # barrido de 12, cierra 11.6
        (11.6, 11.8, 10.5, 10.7),
        (10.7, 11, 9.8, 10),
        (10, 10.2, 9, 9.2),
    ]
    return _bars(rows)


def test_barrido_de_maximos_da_short_con_su_nivel_y_mecha() -> None:
    o, h, lo, c = _sweep_fixture()
    res = scan(o, h, lo, c, TechnicalParams(pivot_k=2))
    ev = [e for e in res.sweeps if e.idx == 10]
    assert len(ev) == 1
    assert ev[0].direction == SHORT and ev[0].level == 12.0 and ev[0].extreme == 12.8
    # El nivel tomado deja de ser liquidez.
    assert all(lv.price != 12.0 for lv in res.levels)


def test_ruptura_con_cierre_afuera_no_es_barrido_pero_consume_el_nivel() -> None:
    o, h, lo, c = _sweep_fixture()
    c = c.copy()
    c[10] = 12.5
    res = scan(o, h, lo, c, TechnicalParams(pivot_k=2))
    assert all(e.idx != 10 for e in res.sweeps)
    assert all(lv.price != 12.0 for lv in res.levels)


def test_sweep_trade_entra_al_open_siguiente_con_stop_tras_la_mecha() -> None:
    o, h, lo, c = _sweep_fixture()
    res = scan(o, h, lo, c, TechnicalParams(pivot_k=2))
    cfg = RuleConfig(r_multiple=1.0, horizon=3, cost_pct=0.0, min_risk_pct=0.0, stop_buffer_atr=0.0)
    trades = sweep_trades([e for e in res.sweeps if e.idx == 10], o, h, lo, c, cfg)
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_idx == 11 and t.entry == 11.6 and t.stop == 12.8
    # objetivo = 11.6 − 1.2 = 10.4; la barra 11 llega a 10.5, la 12 a 9.8.
    assert t.exit_kind == "target" and t.exit_idx == 12
    assert t.net_r == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Order blocks
# --------------------------------------------------------------------------- #
def _ob_fixture() -> tuple[np.ndarray, ...]:
    rows = [
        (10, 10.5, 9.5, 10.2),
        (10.2, 10.6, 9.8, 10.0),
        (10.0, 11.0, 9.9, 10.8),
        (10.8, 11.2, 10.4, 10.6),
        (10.6, 10.9, 10.1, 10.3),
        (10.3, 10.6, 9.9, 10.1),
        (10.1, 10.5, 9.8, 10.0),
        (10.0, 10.2, 9.7, 9.8),  # 7: última vela bajista → OB [9.7, 10.2]
        (9.8, 12.5, 9.8, 12.4),  # 8: desplazamiento, rompe el máximo de 11.2
        (12.4, 12.8, 12.0, 12.6),
        (12.6, 12.7, 11.5, 11.7),
        (11.7, 11.8, 10.6, 10.8),
        (10.8, 10.9, 10.0, 10.5),  # 12: entra a la zona (low 10.0 <= 10.2)
        (10.5, 11.5, 10.3, 11.3),
        (11.3, 12.0, 11.1, 11.9),
        (11.9, 12.2, 11.6, 12.0),
    ]
    return _bars(rows)


def test_order_block_alcista_y_su_primer_toque() -> None:
    o, h, lo, c = _ob_fixture()
    res = scan(o, h, lo, c, TechnicalParams(pivot_k=2, impulse_atr=1.0))
    touches = [t for t in res.ob_touches if t.direction == LONG]
    assert len(touches) == 1
    tc = touches[0]
    assert tc.idx == 12 and tc.created_idx == 8 and (tc.bottom, tc.top) == (9.7, 10.2)


def test_order_block_entra_en_el_borde_y_en_la_vela_del_toque_solo_mira_el_stop() -> None:
    o, h, lo, c = _ob_fixture()
    res = scan(o, h, lo, c, TechnicalParams(pivot_k=2, impulse_atr=1.0))
    cfg = RuleConfig(r_multiple=2.0, horizon=4, cost_pct=0.0, min_risk_pct=0.0, stop_buffer_atr=0.0)
    (t,) = order_block_trades(res.ob_touches, o, h, lo, c, cfg)
    assert t.entry == 10.2 and t.stop == 9.7 and t.target == pytest.approx(11.2)
    assert t.exit_kind == "target" and t.exit_idx == 13


# --------------------------------------------------------------------------- #
# Simulador: convenciones conservadoras
# --------------------------------------------------------------------------- #
def test_si_la_vela_toca_stop_y_objetivo_cuenta_el_stop() -> None:
    o = np.array([10.0, 10.0])
    h = np.array([10.1, 12.0])
    lo = np.array([9.9, 8.0])
    c = np.array([10.0, 10.0])
    res = simulate(LONG, 1, 10.0, 9.0, 11.0, o, h, lo, c, 1, first_bar_stop_only=False)
    assert res == (1, 9.0, "stop")


def test_gap_a_traves_del_stop_sale_al_open() -> None:
    o = np.array([10.0, 10.0, 8.5])
    h = np.array([10.1, 10.2, 8.7])
    lo = np.array([9.9, 9.8, 8.4])
    c = np.array([10.0, 10.0, 8.6])
    res = simulate(LONG, 1, 10.0, 9.0, 11.0, o, h, lo, c, 2, first_bar_stop_only=False)
    assert res == (2, 8.5, "stop")


def test_horizonte_incompleto_no_produce_trade() -> None:
    o = h = lo = c = np.ones(5)
    assert simulate(LONG, 3, 1.0, 0.5, 2.0, o, h, lo, c, 5, first_bar_stop_only=False) is None


def test_stop_demasiado_cerca_no_es_operable() -> None:
    o, h, lo, c = _sweep_fixture()
    res = scan(o, h, lo, c, TechnicalParams(pivot_k=2))
    cfg = RuleConfig(horizon=3, min_risk_pct=0.5)  # 50%: ningún stop es tan ancho
    assert sweep_trades(res.sweeps, o, h, lo, c, cfg) == []


# --------------------------------------------------------------------------- #
# Medición
# --------------------------------------------------------------------------- #
def _trade(idx: int, net_r: float) -> Trade:
    return Trade(
        idx, idx + 1, LONG, 100.0, 99.0, 102.0, idx + 2, 100.0, "time", 0.01, 1.0, net_r * 0.01
    )


def test_expectancy_interval_cubre_la_media_y_excluye_cero_con_senal() -> None:
    rng = np.random.default_rng(1)
    trades = [
        _trade(i, float(x))
        for i, x in zip(range(0, 4000, 4), rng.normal(0.3, 1.0, 1000), strict=True)
    ]
    lo, hi = expectancy_interval(trades, 4100, 16, level=0.95, n_boot=2000)
    mean = np.mean([t.net_r for t in trades])
    assert lo < mean < hi and lo > 0


def test_expectancy_interval_sin_senal_contiene_cero() -> None:
    rng = np.random.default_rng(2)
    trades = [
        _trade(i, float(x))
        for i, x in zip(range(0, 4000, 4), rng.normal(0.0, 1.0, 1000), strict=True)
    ]
    lo, hi = expectancy_interval(trades, 4100, 16, level=0.95, n_boot=2000)
    assert lo < 0 < hi


def test_placebo_en_paseo_aleatorio_pierde_el_costo() -> None:
    """Control de plomería: sin estructura, la expectancy es ≈ −costo/riesgo."""
    o, h, lo, c = _random_walk(20_000, seed=11, vol=0.004)
    res = scan(o, h, lo, c)
    cfg = RuleConfig(horizon=48)
    base = sweep_trades(res.sweeps, o, h, lo, c, cfg)
    pl = placebo_trades(base, o, h, lo, c, res.atr, cfg, seed=0)
    st = trade_stats(pl, c.size, cfg, level=0.95, n_boot=1000)
    assert st.n > 200
    expected = -np.mean([cfg.cost_pct / t.risk_pct for t in pl])
    assert st.ci_low < expected < st.ci_high
    assert not st.passes


def test_reglas_en_paseo_aleatorio_no_pasan() -> None:
    """Control negativo: en un paseo sin memoria ninguna regla puede pasar."""
    o, h, lo, c = _random_walk(30_000, seed=21, vol=0.004)
    res = scan(o, h, lo, c)
    cfg = RuleConfig(horizon=48)
    for trades in (
        sweep_trades(res.sweeps, o, h, lo, c, cfg),
        order_block_trades(res.ob_touches, o, h, lo, c, cfg),
    ):
        st = trade_stats(trades, c.size, cfg, level=0.9875, n_boot=1000)
        assert st.n > 50
        assert not st.passes


def test_trade_stats_contabilidad() -> None:
    trades = [_trade(0, 2.0), _trade(10, -1.0), _trade(20, -1.0), _trade(30, 2.0)]
    st = trade_stats(trades, 50, RuleConfig(horizon=4), level=0.95, n_boot=200)
    assert st.n == 4 and st.mean_r == pytest.approx(0.5)
    assert st.win_rate == 0.5 and st.profit_factor == pytest.approx(2.0)
    assert st.max_drawdown_r == pytest.approx(2.0) and st.worst_streak == 2


def test_cluster_levels_agrupa_por_cercania() -> None:
    pv = [Level(100.0, 1, 4, "high"), Level(100.2, 5, 8, "high"), Level(95.0, 9, 12, "low")]
    out = cluster_levels(pv, tol=0.5, price_now=98.0)
    assert [(round(x.price, 1), x.touches, x.kind) for x in out] == [
        (95.0, 1, "support"),
        (100.1, 2, "resistance"),
    ]


def test_resample_a_1h_descarta_horas_incompletas() -> None:
    from bob.backtest.technical_runner import resample
    from bob.data.store import OHLCVSeries

    t = np.array([0, 1, 2, 3, 4, 5, 6, 8, 9, 10, 11], dtype=np.int64) * 900_000  # falta la 7
    x = np.arange(t.size, dtype=np.float64) + 10
    s = OHLCVSeries("X", "15m", t, x, x + 1, x - 1, x + 0.5, x, x, x, np.zeros(t.size, np.int64))
    out = resample(s, "1h")
    assert out["open_time"].tolist() == [0, 8 * 900_000]
    assert out["open"].tolist() == [10.0, 17.0]
    assert out["high"].tolist() == [14.0, 21.0]
    assert out["low"].tolist() == [9.0, 16.0]
    assert out["close"].tolist() == [13.5, 20.5]


def test_technical_map_usa_el_mismo_scan_y_funde_maximos_iguales() -> None:
    from bob.signals.technical import technical_map

    o, h, lo, c = _random_walk(1200, seed=7)
    t = np.arange(c.size, dtype=np.int64) * 900_000
    m = technical_map(t, o, h, lo, c, n_show=200)
    full = scan(o, h, lo, c)
    # Los barridos del mapa son los del backtest, recortados a la ventana.
    assert [s["time"] for s in m["sweeps"]] == [  # type: ignore[index, union-attr]
        int(t[e.idx]) for e in full.sweeps if e.idx >= c.size - 200
    ]
    pools = m["pools"]
    assert isinstance(pools, list)
    # Ninguna zona del mismo lado queda a menos de la tolerancia de otra.
    tol = TechnicalParams().level_tol_atr * float(m["atr"])  # type: ignore[arg-type]
    for side in ("high", "low"):
        prices = sorted(p["price"] for p in pools if p["side"] == side)
        assert all(b - a > tol for a, b in zip(prices, prices[1:], strict=False))
    assert sum(p["equal"] for p in pools) == len(full.levels)
    assert len(m["series"]["time"]) == 200  # type: ignore[index]


def test_cluster_levels_no_encadena_una_escalera_de_pivotes() -> None:
    # Diez pivotes a 0,9·tol uno de otro: comparando con el último se
    # funden en un "nivel" de 10 toques y 8·tol de ancho.
    pv = [Level(100.0 + 0.9 * i, i, i + 3, "high") for i in range(10)]
    out = cluster_levels(pv, tol=1.0, price_now=90.0)
    assert max(x.touches for x in out) <= 2
    assert len(out) == 5
