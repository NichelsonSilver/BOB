"""Tests for grid/spacing.py — level generation and quantization."""

from decimal import Decimal

import pytest

from bob.grid.spacing import (
    calc_grid_step,
    calc_profit_per_grid,
    calc_profit_range,
    calc_qty_per_grid,
    generate_levels,
    quantize,
)


# ────────────────── quantize ──────────────────


class TestQuantize:
    def test_basic(self):
        assert quantize(Decimal("100.123"), Decimal("0.1")) == Decimal("100.1")

    def test_round_up(self):
        assert quantize(Decimal("100.15"), Decimal("0.1")) == Decimal("100.2")

    def test_exact(self):
        assert quantize(Decimal("100.0"), Decimal("0.5")) == Decimal("100.0")

    def test_large_step(self):
        assert quantize(Decimal("123.4"), Decimal("10")) == Decimal("120")

    def test_tiny_step(self):
        assert quantize(Decimal("0.123456"), Decimal("0.0001")) == Decimal("0.1235")

    def test_zero_step_raises(self):
        with pytest.raises(ValueError, match="positive"):
            quantize(Decimal("100"), Decimal("0"))

    def test_negative_step_raises(self):
        with pytest.raises(ValueError, match="positive"):
            quantize(Decimal("100"), Decimal("-1"))


# ────────────────── generate_levels ──────────────────


class TestGenerateLevels:
    def test_arithmetic_basic(self):
        levels = generate_levels(
            Decimal("100"), Decimal("200"), 5, "arithmetic", Decimal("0.1")
        )
        assert len(levels) == 6  # n_grids + 1
        assert levels[0] == Decimal("100.0")
        assert levels[-1] == Decimal("200.0")
        # Uniform spacing
        step = levels[1] - levels[0]
        for i in range(1, len(levels) - 1):
            assert abs(levels[i + 1] - levels[i] - step) < Decimal("0.2")

    def test_geometric_basic(self):
        levels = generate_levels(
            Decimal("100"), Decimal("200"), 4, "geometric", Decimal("0.01")
        )
        assert len(levels) == 5
        assert levels[0] == Decimal("100.00")
        assert levels[-1] == Decimal("200.00")
        # Geometric: ratio between consecutive levels should be ~equal
        ratios = [levels[i + 1] / levels[i] for i in range(len(levels) - 1)]
        for r in ratios:
            assert abs(r - ratios[0]) < Decimal("0.01")

    def test_n_grids_2_minimum(self):
        levels = generate_levels(
            Decimal("50"), Decimal("60"), 2, "arithmetic", Decimal("0.1")
        )
        assert len(levels) == 3
        assert levels == [Decimal("50.0"), Decimal("55.0"), Decimal("60.0")]

    def test_n_grids_500_maximum(self):
        levels = generate_levels(
            Decimal("1000"), Decimal("2000"), 500, "arithmetic", Decimal("0.1")
        )
        assert len(levels) <= 501
        assert levels[0] == Decimal("1000.0")
        assert levels[-1] == Decimal("2000.0")

    def test_narrow_range(self):
        """Very narrow range — some levels may collapse after quantization."""
        levels = generate_levels(
            Decimal("100.0"), Decimal("100.5"), 5, "arithmetic", Decimal("0.1")
        )
        # With step=0.1 and tick_size=0.1, all levels should survive
        assert len(levels) == 6

    def test_narrow_range_collapse(self):
        """Range too narrow for the tick size — levels collapse."""
        levels = generate_levels(
            Decimal("100.0"), Decimal("100.2"), 5, "arithmetic", Decimal("0.1")
        )
        # Step = 0.04, quantized to 0.1 -> many levels collapse
        assert len(levels) < 6
        assert len(levels) >= 2  # At least low and high

    def test_wide_range(self):
        levels = generate_levels(
            Decimal("100"), Decimal("100000"), 10, "arithmetic", Decimal("1")
        )
        assert len(levels) == 11
        assert levels[0] == Decimal("100")
        assert levels[-1] == Decimal("100000")

    def test_invalid_low_gte_high(self):
        with pytest.raises(ValueError, match="price_low"):
            generate_levels(Decimal("200"), Decimal("100"), 5, "arithmetic", Decimal("0.1"))

    def test_invalid_equal(self):
        with pytest.raises(ValueError):
            generate_levels(Decimal("100"), Decimal("100"), 5, "arithmetic", Decimal("0.1"))

    def test_invalid_n_grids_too_low(self):
        with pytest.raises(ValueError, match="n_grids"):
            generate_levels(Decimal("100"), Decimal("200"), 1, "arithmetic", Decimal("0.1"))

    def test_invalid_n_grids_too_high(self):
        with pytest.raises(ValueError, match="n_grids"):
            generate_levels(Decimal("100"), Decimal("200"), 501, "arithmetic", Decimal("0.1"))

    def test_invalid_spacing(self):
        with pytest.raises(ValueError, match="spacing"):
            generate_levels(Decimal("100"), Decimal("200"), 5, "linear", Decimal("0.1"))

    def test_invalid_tick_size(self):
        with pytest.raises(ValueError, match="tick_size"):
            generate_levels(Decimal("100"), Decimal("200"), 5, "arithmetic", Decimal("0"))

    def test_geometric_tight_range(self):
        """Geometric on a tight range."""
        levels = generate_levels(
            Decimal("99"), Decimal("101"), 4, "geometric", Decimal("0.01")
        )
        assert levels[0] == Decimal("99.00")
        assert levels[-1] == Decimal("101.00")
        assert len(levels) == 5


# ────────────────── calc_qty_per_grid ──────────────────


class TestCalcQtyPerGrid:
    def test_basic(self):
        levels = [Decimal("100"), Decimal("110"), Decimal("120")]
        qty = calc_qty_per_grid(
            Decimal("1000"), 3, 2, levels, Decimal("0.001")
        )
        # avg_price = 110, qty = (1000 * 3) / (2 * 110) = 13.636...
        assert qty == Decimal("13.636")

    def test_quantized_down(self):
        levels = [Decimal("100"), Decimal("200")]
        qty = calc_qty_per_grid(
            Decimal("100"), 1, 2, levels, Decimal("0.01")
        )
        # avg = 150, qty = 100 / (2 * 150) = 0.3333... -> rounded down to 0.33
        assert qty == Decimal("0.33")

    def test_empty_levels_raises(self):
        with pytest.raises(ValueError):
            calc_qty_per_grid(Decimal("1000"), 1, 5, [], Decimal("0.01"))


# ────────────────── calc_grid_step ──────────────────


class TestCalcGridStep:
    def test_basic(self):
        levels = [Decimal("100"), Decimal("110"), Decimal("120")]
        assert calc_grid_step(levels, 0) == Decimal("10")
        assert calc_grid_step(levels, 1) == Decimal("10")

    def test_out_of_bounds(self):
        levels = [Decimal("100"), Decimal("110")]
        with pytest.raises(IndexError):
            calc_grid_step(levels, 1)
        with pytest.raises(IndexError):
            calc_grid_step(levels, -1)


# ────────────────── calc_profit_per_grid ──────────────────


class TestCalcProfitPerGrid:
    def test_basic(self):
        profit = calc_profit_per_grid(
            Decimal("10"), Decimal("105"), Decimal("0.0002")
        )
        # (10 / 105) - 2 * 0.0002 = 0.09524 - 0.0004 = 0.09484
        expected = Decimal("10") / Decimal("105") - 2 * Decimal("0.0002")
        assert profit == expected

    def test_negative_profit(self):
        """Very small step with high fees -> negative profit."""
        profit = calc_profit_per_grid(
            Decimal("0.01"), Decimal("100"), Decimal("0.01")
        )
        assert profit < 0


class TestCalcProfitRange:
    def test_arithmetic(self):
        levels = [Decimal("100"), Decimal("110"), Decimal("120")]
        min_p, max_p = calc_profit_range(levels, Decimal("0.0002"))
        # All steps equal in arithmetic -> min == max (approximately)
        assert min_p > 0
        assert max_p > 0

    def test_geometric_varying(self):
        levels = generate_levels(
            Decimal("100"), Decimal("200"), 4, "geometric", Decimal("0.01")
        )
        min_p, max_p = calc_profit_range(levels, Decimal("0.0002"))
        assert min_p > 0
        # Geometric has equal ratio, so profit per grid should be approximately equal
        assert abs(max_p - min_p) < Decimal("0.001")

    def test_single_level_raises(self):
        with pytest.raises(ValueError):
            calc_profit_range([Decimal("100")], Decimal("0.0002"))
