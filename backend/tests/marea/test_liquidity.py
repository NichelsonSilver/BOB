"""Monitor de liquidez, núcleo puro (`marea.liquidity`)."""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pytest

from marea.liquidity import (
    H_LONG_DAYS,
    Realtime,
    change,
    combine,
    composite,
    expanding_percentile,
    label,
    product,
    realtime,
    weekly_dates,
)
from marea.pit import OPEN_END, Period

D = date.fromisoformat


def weekly(
    start: str, n: int, value: float = 100.0, step: float = 1.0, lag: int = 2
) -> list[Period]:
    """Serie semanal sin revisiones: cada dato se conoce `lag` días después."""
    s = D(start)
    return [
        Period(s + timedelta(weeks=i), s + timedelta(weeks=i, days=lag), OPEN_END, value + step * i)
        for i in range(n)
    ]


class TestRealtime:
    def test_revision_no_se_ve_antes_de_publicarse(self) -> None:
        obs = D("2020-01-03")
        ps = [
            Period(obs, D("2020-01-05"), D("2020-02-09"), 10.0),
            Period(obs, D("2020-02-10"), OPEN_END, 12.0),
        ]
        rt = realtime(ps, [D("2020-01-10"), D("2020-02-14")], max_age_days=60)
        assert rt.level.tolist() == [10.0, 12.0]

    def test_las_dos_puntas_salen_de_la_misma_vintage(self) -> None:
        # La serie termina el 2020-04-03; su punta pasada (13 semanas antes) es
        # el 2020-01-03, que se revisa el 2020-05-02 de 100 a 50. El mismo
        # cambio leído antes y después de la revisión usa cada versión.
        ps = weekly("2020-01-03", 14)
        old = ps[0]
        ps[0] = Period(old.obs_date, old.realtime_start, D("2020-05-01"), 100.0)
        ps.append(Period(old.obs_date, D("2020-05-02"), OPEN_END, 50.0))
        rt = realtime(ps, [D("2020-04-10"), D("2020-05-03")], max_age_days=60)
        assert rt.obs_date == [D("2020-04-03"), D("2020-04-03")]
        assert rt.past_long.tolist() == [100.0, 50.0]

    def test_dato_vencido_es_nan_no_se_arrastra(self) -> None:
        ps = weekly("2020-01-03", 3)
        rt = realtime(ps, [D("2020-01-20"), D("2020-03-01")], max_age_days=10)
        assert math.isfinite(rt.level[0])
        assert math.isnan(rt.level[1]) and rt.obs_date[1] is None

    def test_nulos_se_saltan_y_lo_no_publicado_no_aparece(self) -> None:
        ps = weekly("2020-01-03", 3)
        ps.append(Period(D("2020-01-24"), D("2020-01-26"), OPEN_END, None))
        rt = realtime(ps, [D("2020-01-27"), D("2020-01-03")], max_age_days=30)
        assert rt.obs_date[0] == D("2020-01-17") and rt.level[0] == 102.0
        assert math.isnan(rt.level[1])  # el primer dato se publicó el 05-01

    def test_sin_lookahead_mutar_el_futuro_no_cambia_el_pasado(self) -> None:
        ps = weekly("2018-01-05", 150, step=0.7)
        dates = weekly_dates(D("2018-06-01"), D("2020-06-01"))
        cut = D("2019-06-01")
        base = realtime(ps, dates, max_age_days=14)
        mutated = [
            p
            if p.realtime_start <= cut
            else Period(p.obs_date, p.realtime_start, p.realtime_end, -1e6)
            for p in ps
        ]
        # y además una revisión futura de un dato viejo
        mutated.append(Period(D("2018-03-02"), cut + timedelta(days=1), OPEN_END, 9e9))
        mut = realtime(mutated, dates, max_age_days=14)
        k = sum(d <= cut for d in dates)
        for a, b in ((base.level, mut.level), (base.past_long, mut.past_long)):
            np.testing.assert_array_equal(a[:k], b[:k])
        assert not np.array_equal(base.level[k:], mut.level[k:])

    def test_respaldo_antes_de_la_primera_vintage_sale_no_exacto(self) -> None:
        fv = D("2011-01-01")
        ps = [
            Period(D("2010-01-01") + timedelta(weeks=i), fv, OPEN_END, float(i)) for i in range(60)
        ]
        rt = realtime(
            ps, [D("2010-06-04"), D("2011-02-04")], 30, first_vintage=fv, fallback_lag_days=7
        )
        assert rt.exact.tolist() == [False, True]
        assert rt.obs_date[0] is not None and rt.obs_date[0] <= D("2010-05-28")


def rt_of(level: list[float], past: list[float], obs: list[date | None] | None = None) -> Realtime:
    n = len(level)
    return Realtime(
        obs if obs is not None else [D("2020-01-01")] * n,
        np.array(level),
        np.array(past),
        np.array(past),
        np.ones(n, dtype=bool),
    )


class TestCombinaciones:
    def test_combine_falta_un_componente_falta_todo(self) -> None:
        a = rt_of([10.0, 10.0], [8.0, 8.0], [D("2020-01-08"), D("2020-01-15")])
        b = rt_of([2.0, math.nan], [1.0, math.nan], [D("2020-01-06"), None])
        c = combine([(a, 1.0), (b, -1.0)])
        assert c.level[0] == 8.0 and c.past_long[0] == 7.0
        assert math.isnan(c.level[1]) and c.obs_date == [D("2020-01-06"), None]

    def test_product_convierte_moneda(self) -> None:
        jpy = rt_of([1000.0], [900.0])
        jpy_per_usd = rt_of([100.0], [90.0])
        usd = product(jpy, jpy_per_usd, -1.0)
        assert usd.level[0] == pytest.approx(10.0) and usd.past_long[0] == pytest.approx(10.0)

    def test_change_log_y_diff(self) -> None:
        rt = rt_of([110.0, 0.0, 2.5], [100.0, 5.0, 3.0])
        lg = change(rt, "log")
        assert lg[0] == pytest.approx(math.log(1.1)) and math.isnan(lg[1])
        assert change(rt, "diff", long=False)[2] == pytest.approx(-0.5)


class TestPercentil:
    def test_pasado_estricto_y_minimo_de_historia(self) -> None:
        x = np.array([1.0, 2.0, 3.0, 0.5, np.nan, 10.0])
        p = expanding_percentile(x, min_history=3)
        assert np.isnan(p[:3]).all()
        assert p[3] == 0.0  # 0.5 contra {1,2,3}
        assert np.isnan(p[4])
        assert p[5] == 1.0  # contra {1,2,3,0.5}; no contra sí mismo

    def test_futuro_no_cambia_el_percentil(self) -> None:
        rng = np.random.default_rng(0)
        x = rng.normal(size=300)
        y = x.copy()
        y[200:] = 1e9
        np.testing.assert_array_equal(
            expanding_percentile(x, 50)[:200], expanding_percentile(y, 50)[:200]
        )

    def test_empates_cuentan_la_mitad_y_ventana(self) -> None:
        assert expanding_percentile(np.array([1.0, 1.0, 1.0]), 2)[2] == 0.5
        p = expanding_percentile(np.array([5.0, 1.0, 2.0, 3.0]), 2, window=2)
        assert p[3] == 1.0  # solo {1, 2}; el 5 quedó fuera de la ventana

    def test_label_terciles(self) -> None:
        assert label(0.9) == "expansivo" and label(2 / 3) == "expansivo"
        assert label(0.5) == "neutro" and label(1 / 3) == "neutro"
        assert label(0.1) == "contractivo" and label(math.nan) is None

    def test_composite_exige_todos(self) -> None:
        c = composite([np.array([0.2, 0.4]), np.array([0.8, np.nan])])
        assert c[0] == pytest.approx(0.5) and math.isnan(c[1])


def test_weekly_dates_viernes_y_hoy() -> None:
    ds = weekly_dates(D("2026-09-01"), D("2026-09-21"))
    assert ds == [D("2026-09-04"), D("2026-09-11"), D("2026-09-18"), D("2026-09-21")]
    assert weekly_dates(D("2026-09-01"), D("2026-09-18"))[-1] == D("2026-09-18")
    assert H_LONG_DAYS == 91
