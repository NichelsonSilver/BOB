"""Probabilidad de recesión (NY Fed) y Risk de ciclo de BTC."""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pytest

from marea.cycle import (
    MIN_HISTORY_DAYS,
    SMA_DAYS,
    cycle_risk,
    divergence,
    settled_prices,
)
from marea.pit import OPEN_END, AsOfPoint, Period
from marea.recession import (
    ESTIMATION_END,
    add_months,
    bond_equivalent,
    month_end,
    monthly_means,
    probability,
    recession_series,
)

D = date.fromisoformat

# --- Recesión ----------------------------------------------------------------------


def test_conversion_a_base_de_bono_reproduce_la_primera_fila_de_la_ny_fed() -> None:
    # allmonth.xls, 1959-01: letra 2,82 % (descuento) -> 2,879694 % (base de bono).
    assert bond_equivalent(2.82) == pytest.approx(2.879694, abs=1e-6)


@pytest.mark.parametrize(
    ("spread", "published"),
    [
        (0.872530, 0.138825),  # spread 2026-08 -> probabilidad para 2027-08
        (1.140306, None),
    ],
)
def test_probit_reproduce_la_probabilidad_publicada(spread: float, published: float | None) -> None:
    p = probability(spread)
    assert 0.0 < p < 1.0
    if published is not None:
        assert p == pytest.approx(published, abs=1e-6)


def test_spread_mas_invertido_da_mas_probabilidad() -> None:
    assert probability(-1.0) > probability(0.0) > probability(1.0)


def test_aritmetica_de_meses() -> None:
    assert month_end(D("2024-02-10")) == D("2024-02-29")
    assert month_end(D("2026-12-03")) == D("2026-12-31")
    assert add_months(D("2026-08-31"), 12) == D("2027-08-31")
    assert add_months(D("2026-01-31"), 1) == D("2026-02-28")


def pts(start: str, days: int, value: float, exact: bool = True) -> list[AsOfPoint]:
    d0 = D(start)
    return [AsOfPoint(d0 + timedelta(days=i), value, exact) for i in range(days)]


def test_promedio_mensual_solo_de_meses_cerrados_y_redondeado() -> None:
    p = [*pts("2026-01-01", 31, 4.004), *pts("2026-02-01", 10, 3.0)]
    p.append(AsOfPoint(D("2026-02-11"), None, True))
    means = monthly_means(p)
    assert means == {D("2026-01-31"): 4.0}  # febrero sigue abierto
    assert monthly_means([]) == {}


def test_serie_de_recesion_marca_inexacto_y_dentro_de_muestra() -> None:
    g10 = [*pts("2005-12-01", 31, 4.5, exact=False), *pts("2006-01-01", 40, 4.5)]
    tb3 = [*pts("2005-12-01", 31, 4.0), *pts("2006-01-01", 40, 4.0)]
    rows = recession_series(g10, tb3)
    assert [r.month for r in rows] == [D("2005-12-31"), D("2006-01-31")]
    dec, jan = rows
    assert not dec.exact  # insumo inexacto y anterior al fin de la estimación
    assert jan.exact and jan.month >= ESTIMATION_END
    assert jan.target == D("2007-01-31")
    assert jan.spread == pytest.approx(4.5 - bond_equivalent(4.0))
    assert jan.probability == pytest.approx(probability(jan.spread))


# --- Risk de ciclo -----------------------------------------------------------------


def series(n: int, seed: int = 0) -> list[tuple[date, float]]:
    rng = np.random.default_rng(seed)
    d0 = D("2014-12-01")
    logp = np.log(400.0) + np.cumsum(rng.normal(0.001, 0.03, n))
    return [(d0 + timedelta(days=i), float(np.exp(v))) for i, v in enumerate(logp)]


def test_precio_asentado_usa_el_valor_del_dia_siguiente() -> None:
    d = D("2025-03-01")
    periods = [
        Period(d, d, d, 86_102.95),  # intradía
        Period(d, d + timedelta(days=1), OPEN_END, 94_265.48),  # cierre corregido
        Period(d + timedelta(days=1), d + timedelta(days=1), OPEN_END, 93_000.0),
        Period(d + timedelta(days=2), d + timedelta(days=5), OPEN_END, None),
    ]
    out = settled_prices(periods)
    assert out == [(d, 94_265.48), (d + timedelta(days=1), 93_000.0)]


def test_sin_historia_suficiente_no_hay_filas() -> None:
    assert cycle_risk(series(SMA_DAYS)) == []


def test_warmup_point_in_time_y_rango() -> None:
    rows = cycle_risk(series(MIN_HISTORY_DAYS + 400))
    first_pit = next(r for r in rows if r.risk_pit is not None)
    assert first_pit.day >= rows[0].day  # el warm-up existe
    assert (first_pit.day - D("2014-12-01")).days >= MIN_HISTORY_DAYS
    assert all(0.0 <= r.risk_full <= 1.0 for r in rows)
    assert all(r.risk_pit is None or 0.0 <= r.risk_pit <= 1.0 for r in rows)


def test_cambiar_el_futuro_no_mueve_el_pasado_point_in_time_pero_si_el_completo() -> None:
    """El test que decide: la versión completa tiene lookahead; la PIT no."""
    base = series(MIN_HISTORY_DAYS + 600, seed=1)
    cut = MIN_HISTORY_DAYS + 300
    shocked = base[:cut] + [(d, v * 5.0) for d, v in base[cut:]]
    a = {r.day: r for r in cycle_risk(base)}
    b = {r.day: r for r in cycle_risk(shocked)}
    past = [d for d, _ in base[:cut] if d in a and a[d].risk_pit is not None]
    assert past
    assert all(a[d].risk_pit == b[d].risk_pit for d in past)
    assert any(not math.isclose(a[d].risk_full, b[d].risk_full) for d in past)


def test_divergencia() -> None:
    rows = cycle_risk(series(MIN_HISTORY_DAYS + 500, seed=2))
    dv = divergence(rows)
    assert dv.n > 0 and -1.0 <= dv.correlation <= 1.0
    assert 0.0 <= dv.mean_abs_diff <= 1.0 and 0.0 <= dv.share_far <= 1.0
    empty = divergence(rows[:5])
    assert empty.n == 0 and math.isnan(empty.correlation)
