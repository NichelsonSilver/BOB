"""Tests for signals.indicators pure helpers (no I/O)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from bob.signals.indicators import (
    Candle,
    atr,
    parse_candle,
    percentile,
    suggest_range,
    volatility_pct,
)


def _c(o: str, h: str, l: str, cl: str) -> Candle:
    return Candle(Decimal(o), Decimal(h), Decimal(l), Decimal(cl))


def test_percentile_basic():
    vals = [Decimal(i) for i in range(1, 11)]  # 1..10 sorted
    assert percentile(vals, Decimal(0)) == Decimal(1)
    assert percentile(vals, Decimal(100)) == Decimal(10)
    # Median of 1..10 with linear interpolation at rank 4.5 → 5.5
    assert percentile(vals, Decimal(50)) == Decimal("5.5")


def test_percentile_empty_raises():
    with pytest.raises(ValueError):
        percentile([], Decimal(50))


def test_atr_handles_short_series():
    assert atr([]) == Decimal(0)
    assert atr([_c("100", "101", "99", "100")]) == Decimal(0)


def test_atr_increases_with_wider_candles():
    narrow = [_c("100", "101", "99", "100") for _ in range(5)]
    wide = [_c("100", "110", "90", "100") for _ in range(5)]
    assert atr(wide) > atr(narrow)


def test_suggest_range_percentile_quantizes_to_tick():
    # Close prices ranging 100..200
    candles = [
        _c(str(p), str(p + 2), str(p - 2), str(p))
        for p in range(100, 201, 5)
    ]
    lo, hi, _atr = suggest_range(candles, mode="percentile", tick_size=Decimal("0.5"))
    # Both values quantized to 0.5 ticks
    assert (lo * 2) % 1 == 0
    assert (hi * 2) % 1 == 0
    assert lo < hi


def test_suggest_range_minmax_captures_extremes():
    candles = [
        _c("100", "120", "80", "100"),
        _c("100", "105", "95", "100"),
        _c("100", "115", "85", "100"),
    ]
    lo, hi, _atr = suggest_range(candles, mode="minmax", tick_size=Decimal("0.1"))
    assert lo == Decimal("80.0")
    assert hi == Decimal("120.0")


def test_suggest_range_atr_mode_widens_with_volatility():
    flat = [_c("100", "101", "99", "100") for _ in range(20)]
    volatile = [
        _c("100", "110", "90", "100") if i % 2 == 0 else _c("100", "115", "85", "100")
        for i in range(20)
    ]
    _, _, atr_flat = suggest_range(flat, mode="atr")
    _, _, atr_vol = suggest_range(volatile, mode="atr")
    assert atr_vol > atr_flat


def test_suggest_range_degenerate_widens():
    candles = [_c("100", "100", "100", "100") for _ in range(5)]
    lo, hi, _ = suggest_range(candles, mode="minmax", tick_size=Decimal("0.1"))
    assert hi > lo


def test_volatility_pct_zero_on_flat_market():
    flat = [_c("100", "100", "100", "100") for _ in range(3)]
    assert volatility_pct(flat) == Decimal(0)


def test_volatility_pct_scales_with_range():
    narrow = [_c("100", "101", "99", "100")]
    wide = [_c("100", "120", "80", "100")]
    assert volatility_pct(wide) > volatility_pct(narrow)


def test_parse_candle_accepts_valid_dict():
    c = parse_candle({"open": "100", "high": "110", "low": "90", "close": "105"})
    assert c is not None
    assert c.close == Decimal(105)


def test_parse_candle_rejects_malformed():
    assert parse_candle({"open": "bad"}) is None
    assert parse_candle({}) is None
