"""Pure functions for grid level generation and quantization.

No I/O in this module — all functions are deterministic and side-effect free.
"""

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Literal


def quantize(value: Decimal, step: Decimal, rounding: str = ROUND_HALF_UP) -> Decimal:
    """Quantize a value to the nearest multiple of step.

    >>> quantize(Decimal("100.123"), Decimal("0.1"))
    Decimal('100.1')
    >>> quantize(Decimal("100.15"), Decimal("0.5"))
    Decimal('100.0')
    """
    if step <= 0:
        raise ValueError(f"step must be positive, got {step}")
    return (value / step).quantize(Decimal("1"), rounding=rounding) * step


def generate_levels(
    price_low: Decimal,
    price_high: Decimal,
    n_grids: int,
    spacing: Literal["arithmetic", "geometric"],
    tick_size: Decimal,
) -> list[Decimal]:
    """Generate grid price levels, quantized to tick_size.

    Returns n_grids + 1 levels (including both boundaries).

    Args:
        price_low: Lower bound of the grid range.
        price_high: Upper bound of the grid range.
        n_grids: Number of grid intervals (levels = n_grids + 1).
        spacing: "arithmetic" or "geometric".
        tick_size: Minimum price increment for the instrument.

    Raises:
        ValueError: If inputs are invalid.
    """
    if price_low >= price_high:
        raise ValueError(f"price_low ({price_low}) must be < price_high ({price_high})")
    if n_grids < 2:
        raise ValueError(f"n_grids must be >= 2, got {n_grids}")
    if n_grids > 500:
        raise ValueError(f"n_grids must be <= 500, got {n_grids}")
    if tick_size <= 0:
        raise ValueError(f"tick_size must be positive, got {tick_size}")

    if spacing == "arithmetic":
        levels = _arithmetic_levels(price_low, price_high, n_grids)
    elif spacing == "geometric":
        levels = _geometric_levels(price_low, price_high, n_grids)
    else:
        raise ValueError(f"Unknown spacing: {spacing!r}")

    # Quantize all levels to tick_size
    levels = [quantize(lvl, tick_size) for lvl in levels]

    # Deduplicate (quantization can collapse close levels)
    seen: set[Decimal] = set()
    unique: list[Decimal] = []
    for lvl in levels:
        if lvl not in seen:
            seen.add(lvl)
            unique.append(lvl)

    return unique


def _arithmetic_levels(
    price_low: Decimal, price_high: Decimal, n_grids: int
) -> list[Decimal]:
    """Arithmetic spacing: equal price distance between levels."""
    step = (price_high - price_low) / n_grids
    return [price_low + i * step for i in range(n_grids + 1)]


def _geometric_levels(
    price_low: Decimal, price_high: Decimal, n_grids: int
) -> list[Decimal]:
    """Geometric spacing: equal ratio between levels.

    Uses Decimal arithmetic to avoid float precision issues.
    ratio = (high/low) ^ (1/n_grids), computed via ln/exp approximation.
    """
    import math

    # Use float for the ratio calculation, then apply with Decimal
    ratio = float(price_high / price_low) ** (1.0 / n_grids)
    ratio_d = Decimal(str(ratio))

    levels: list[Decimal] = [price_low]
    for i in range(1, n_grids):
        levels.append(price_low * Decimal(str(ratio**i)))
    levels.append(price_high)
    return levels


def calc_qty_per_grid(
    investment_usdt: Decimal,
    leverage: int,
    n_grids: int,
    levels: list[Decimal],
    lot_size: Decimal,
) -> Decimal:
    """Calculate the quantity per grid cell.

    qty = (investment * leverage) / (n_grids * avg_price)
    Quantized down to lot_size to avoid over-allocation.
    """
    if not levels:
        raise ValueError("levels must not be empty")

    avg_price = sum(levels) / len(levels)
    raw_qty = (investment_usdt * leverage) / (n_grids * avg_price)
    return quantize(raw_qty, lot_size, rounding=ROUND_DOWN)


def calc_grid_step(levels: list[Decimal], index: int) -> Decimal:
    """Return the price distance between level[index] and level[index+1]."""
    if index < 0 or index >= len(levels) - 1:
        raise IndexError(f"index {index} out of range for {len(levels)} levels")
    return levels[index + 1] - levels[index]


def calc_profit_per_grid(
    step: Decimal,
    avg_price: Decimal,
    maker_fee: Decimal,
) -> Decimal:
    """Calculate gross profit per grid trade (before funding).

    profit = (step / avg_price) - 2 * maker_fee
    Returns as a ratio (e.g. 0.002 = 0.2%).
    """
    return (step / avg_price) - 2 * maker_fee


def calc_profit_range(
    levels: list[Decimal],
    maker_fee: Decimal,
) -> tuple[Decimal, Decimal]:
    """Calculate (min_profit, max_profit) per grid across all levels.

    Returns profit ratios.
    """
    if len(levels) < 2:
        raise ValueError("Need at least 2 levels")

    profits: list[Decimal] = []
    for i in range(len(levels) - 1):
        step = levels[i + 1] - levels[i]
        avg = (levels[i] + levels[i + 1]) / 2
        profits.append(calc_profit_per_grid(step, avg, maker_fee))

    return min(profits), max(profits)
