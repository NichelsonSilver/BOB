"""Pure helpers to derive a grid price range from 1D klines.

No I/O. Takes a list of OHLC candles and returns suggested price_low,
price_high, and n_grids so the caller can autofill the create-bot form.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Sequence

Mode = Literal["percentile", "minmax", "atr"]


@dataclass(frozen=True)
class Candle:
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class RangeSuggestion:
    price_low: Decimal
    price_high: Decimal
    atr: Decimal
    volatility_pct: Decimal
    suggested_n_grids: int
    mode: Mode
    sample_size: int


def percentile(sorted_values: Sequence[Decimal], pct: Decimal) -> Decimal:
    """Linear-interpolation percentile. `pct` in [0,100]."""
    if not sorted_values:
        raise ValueError("empty sample")
    if pct <= 0:
        return sorted_values[0]
    if pct >= 100:
        return sorted_values[-1]
    n = len(sorted_values)
    rank = (pct / Decimal(100)) * Decimal(n - 1)
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    frac = rank - Decimal(lo)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def atr(candles: Sequence[Candle], period: int = 14) -> Decimal:
    """Average True Range over the last `period` candles (Wilder's simple avg).

    TR_t = max(high-low, |high-prev_close|, |low-prev_close|).
    """
    if len(candles) < 2:
        return Decimal(0)
    trs: list[Decimal] = []
    for i in range(1, len(candles)):
        c = candles[i]
        prev_close = candles[i - 1].close
        tr = max(
            c.high - c.low,
            abs(c.high - prev_close),
            abs(c.low - prev_close),
        )
        trs.append(tr)
    window = trs[-period:] if len(trs) >= period else trs
    return sum(window, Decimal(0)) / Decimal(len(window))


def _quantize_to_tick(v: Decimal, tick: Decimal) -> Decimal:
    if tick <= 0:
        return v
    return (v / tick).to_integral_value() * tick


def suggest_range(
    candles: Sequence[Candle],
    mode: Mode = "percentile",
    *,
    percentile_low: Decimal = Decimal(10),
    percentile_high: Decimal = Decimal(90),
    atr_k: Decimal = Decimal(3),
    tick_size: Decimal = Decimal("0.1"),
) -> tuple[Decimal, Decimal, Decimal]:
    """Return (price_low, price_high, atr_value) quantized to tick_size.

    - percentile: use pN/p(100-N) of closes.
    - minmax: min(low) / max(high) over the window.
    - atr: mid ± atr_k * ATR(14).
    """
    if not candles:
        raise ValueError("need at least 1 candle")

    atr_value = atr(candles)

    if mode == "minmax":
        lo = min(c.low for c in candles)
        hi = max(c.high for c in candles)
    elif mode == "percentile":
        closes = sorted(c.close for c in candles)
        lo = percentile(closes, percentile_low)
        hi = percentile(closes, percentile_high)
    elif mode == "atr":
        last_close = candles[-1].close
        lo = last_close - atr_k * atr_value
        hi = last_close + atr_k * atr_value
    else:
        raise ValueError(f"unknown mode: {mode!r}")

    if lo >= hi:
        # Degenerate: flat market or single candle. Widen by 1%.
        mid = (lo + hi) / Decimal(2) if hi > 0 else lo
        lo = mid * Decimal("0.99")
        hi = mid * Decimal("1.01")

    lo = _quantize_to_tick(lo, tick_size)
    hi = _quantize_to_tick(hi, tick_size)
    if hi <= lo:
        hi = lo + tick_size
    return lo, hi, atr_value


def suggest_n_grids(
    price_low: Decimal,
    price_high: Decimal,
    investment_usdt: Decimal,
    leverage: int,
    min_notional: Decimal,
    maker_fee: Decimal = Decimal("0.0002"),
    tick_size: Decimal = Decimal("0.1"),
    *,
    cap: int = 200,
) -> int:
    """Pick n_grids that balances density vs net profit/grid.

    Constraints:
    - Each grid's notional (= investment*leverage / n_grids) must be >= min_notional.
    - step/avg > 2*maker_fee so each completed cycle is net-positive.
    - step must be at least 2 ticks so quantization doesn't collapse adjacent levels.

    Returns the largest n_grids satisfying all constraints, clamped to [2, cap].
    """
    if price_low <= 0 or price_high <= price_low:
        return 2
    if investment_usdt <= 0 or leverage <= 0 or min_notional <= 0:
        return 2

    avg = (price_low + price_high) / Decimal(2)
    span = price_high - price_low

    # Upper bound from min_notional
    n_by_notional = int((investment_usdt * Decimal(leverage)) / min_notional)
    # Upper bound from net-positive cycle: step/avg > 2*fee  =>  span/n/avg > 2*fee
    #   n < span / (avg * 2 * fee)
    if maker_fee > 0:
        n_by_fee = int(span / (avg * Decimal(2) * maker_fee))
    else:
        n_by_fee = cap
    # Upper bound from tick: step >= 2*tick  => n <= span / (2*tick)
    if tick_size > 0:
        n_by_tick = int(span / (Decimal(2) * tick_size))
    else:
        n_by_tick = cap

    n = min(n_by_notional, n_by_fee, n_by_tick, cap)
    return max(2, n)


def volatility_pct(candles: Sequence[Candle]) -> Decimal:
    """Range / mid close, as percent. Rough volatility proxy."""
    if not candles:
        return Decimal(0)
    hi = max(c.high for c in candles)
    lo = min(c.low for c in candles)
    mid = (hi + lo) / Decimal(2)
    if mid <= 0:
        return Decimal(0)
    return ((hi - lo) / mid) * Decimal(100)


def parse_candle(raw: dict) -> Candle | None:
    """Best-effort parser for a GRVT kline dict. Returns None on malformed."""
    try:
        return Candle(
            open=Decimal(str(raw["open"])),
            high=Decimal(str(raw["high"])),
            low=Decimal(str(raw["low"])),
            close=Decimal(str(raw["close"])),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return None
