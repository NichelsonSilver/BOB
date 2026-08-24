"""Markov chain market regime detection.

Pure, no I/O. Takes a price sequence and classifies the current market into
one of four regimes using a discrete first-order Markov chain:

  RANGING       — low trend, moderate volatility
  TRENDING_UP   — strong uptrend
  TRENDING_DOWN — strong downtrend
  VOLATILE      — high volatility, unclear direction

The transition matrix is estimated from the same price history, falling back to
empirical crypto-market defaults when history is too short.

Heredado del build grid (branch legacy/grvt-grid). En el nuevo BOB cumple dos
roles: (1) baseline de comparación y fallback del HMM gaussiano de Fase 3, y
(2) fuente del KPI 3 — duración esperada de régimen = 1 / (1 - p_permanencia),
en unidades del timeframe. El regime actual es además un feature del KPI 1.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class MarketRegime(StrEnum):
    RANGING = "ranging"
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    VOLATILE = "volatile"


@dataclass(frozen=True)
class RegimeState:
    """Current regime with probabilistic context derived from the Markov chain."""

    regime: MarketRegime
    # P(staying in this regime next step) — diagonal entry of the transition matrix.
    confidence: Decimal
    # Full row of the transition matrix for the current regime.
    transition_probs: dict[MarketRegime, Decimal]


# ---------------------------------------------------------------------------
# Empirical default transition matrix for crypto perpetuals.
# Rows = current regime, columns = next regime.
# Key insight: all regimes tend to persist but trending/volatile regimes
# revert to ranging more often than they reverse direction directly.
# ---------------------------------------------------------------------------
_ALL_REGIMES = list(MarketRegime)

_DEFAULT_TRANSITIONS: dict[MarketRegime, dict[MarketRegime, Decimal]] = {
    MarketRegime.RANGING: {
        MarketRegime.RANGING: Decimal("0.60"),
        MarketRegime.TRENDING_UP: Decimal("0.15"),
        MarketRegime.TRENDING_DOWN: Decimal("0.15"),
        MarketRegime.VOLATILE: Decimal("0.10"),
    },
    MarketRegime.TRENDING_UP: {
        MarketRegime.RANGING: Decimal("0.30"),
        MarketRegime.TRENDING_UP: Decimal("0.50"),
        MarketRegime.TRENDING_DOWN: Decimal("0.10"),
        MarketRegime.VOLATILE: Decimal("0.10"),
    },
    MarketRegime.TRENDING_DOWN: {
        MarketRegime.RANGING: Decimal("0.30"),
        MarketRegime.TRENDING_UP: Decimal("0.10"),
        MarketRegime.TRENDING_DOWN: Decimal("0.50"),
        MarketRegime.VOLATILE: Decimal("0.10"),
    },
    MarketRegime.VOLATILE: {
        MarketRegime.RANGING: Decimal("0.40"),
        MarketRegime.TRENDING_UP: Decimal("0.20"),
        MarketRegime.TRENDING_DOWN: Decimal("0.20"),
        MarketRegime.VOLATILE: Decimal("0.20"),
    },
}


class MarkovRegimeDetector:
    """Classifies market regime from a price sequence using a Markov chain.

    Classification algorithm (per sub-window of prices):
      1. net_return  = (last - first) / first   — directional bias
      2. vol         = std(tick returns)         — local volatility

      VOLATILE      if vol > vol_threshold
      TRENDING_UP   if net_return >  trend_threshold  (and not volatile)
      TRENDING_DOWN if net_return < -trend_threshold  (and not volatile)
      RANGING       otherwise

    Transition matrix is estimated from the same price history by splitting
    into sub-windows of length `subwindow`, classifying each, and counting
    transitions with Laplace smoothing (α=1). Falls back to
    `_DEFAULT_TRANSITIONS` when fewer than 2 sub-windows are available.

    Args:
        trend_threshold: Minimum abs net return to classify as trending.
                         Default 1.5% over the sub-window.
        vol_threshold:   Maximum per-tick return std for non-volatile.
                         Default 0.5% per tick.
        subwindow:       Number of prices per sub-window for transition
                         estimation and for single `classify` calls.
    """

    def __init__(
        self,
        trend_threshold: Decimal = Decimal("0.015"),
        vol_threshold: Decimal = Decimal("0.005"),
        subwindow: int = 5,
    ) -> None:
        self.trend_threshold = trend_threshold
        self.vol_threshold = vol_threshold
        self.subwindow = subwindow

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, prices: Sequence[Decimal]) -> MarketRegime:
        """Classify a price sequence into a single regime.

        Returns RANGING if fewer than 2 prices are provided.
        """
        if len(prices) < 2:
            return MarketRegime.RANGING

        pl = list(prices)
        first, last = pl[0], pl[-1]

        if first == 0:
            return MarketRegime.RANGING

        net_return = (last - first) / first
        vol = self._return_std(pl)

        if vol > self.vol_threshold:
            return MarketRegime.VOLATILE
        if net_return > self.trend_threshold:
            return MarketRegime.TRENDING_UP
        if net_return < -self.trend_threshold:
            return MarketRegime.TRENDING_DOWN
        return MarketRegime.RANGING

    def estimate_transitions(
        self, prices: Sequence[Decimal]
    ) -> dict[MarketRegime, dict[MarketRegime, Decimal]]:
        """Estimate the transition matrix from a price series.

        Divides prices into non-overlapping sub-windows of length `subwindow`,
        classifies each window, counts (from → to) transitions, and normalises
        each row with Laplace smoothing (α=1 per entry) so every row sums to 1.

        Returns the default matrix when fewer than 2 windows are available.
        """
        pl = list(prices)
        regimes: list[MarketRegime] = []

        step = self.subwindow
        for i in range(0, len(pl) - step, step):
            window = pl[i : i + step + 1]
            regimes.append(self.classify(window))

        if len(regimes) < 2:
            return _DEFAULT_TRANSITIONS

        alpha = Decimal("1")

        # counts[from][to] starts at alpha (Laplace smoothing)
        counts: dict[MarketRegime, dict[MarketRegime, Decimal]] = {
            r: {s: alpha for s in _ALL_REGIMES} for r in _ALL_REGIMES
        }
        for i in range(len(regimes) - 1):
            counts[regimes[i]][regimes[i + 1]] += Decimal("1")

        matrix: dict[MarketRegime, dict[MarketRegime, Decimal]] = {}
        for from_r in _ALL_REGIMES:
            row_total = sum(counts[from_r].values())
            matrix[from_r] = {
                to_r: counts[from_r][to_r] / row_total for to_r in _ALL_REGIMES
            }
        return matrix

    def current_regime(self, prices: Sequence[Decimal]) -> RegimeState:
        """Return the current regime with transition probabilities.

        Uses the full price history to estimate the transition matrix, then
        classifies the most recent sub-window (`subwindow` last prices).
        """
        pl = list(prices)
        # Classify the most recent window
        recent = pl[-self.subwindow :] if len(pl) >= self.subwindow else pl
        regime = self.classify(recent)

        transitions = self.estimate_transitions(pl)
        row = transitions.get(regime, _DEFAULT_TRANSITIONS[regime])
        confidence = row[regime]

        return RegimeState(
            regime=regime,
            confidence=confidence,
            transition_probs=row,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _return_std(prices: list[Decimal]) -> Decimal:
        """Per-tick return standard deviation (population std)."""
        returns: list[Decimal] = []
        for i in range(1, len(prices)):
            prev = prices[i - 1]
            if prev != 0:
                returns.append((prices[i] - prev) / prev)

        if not returns:
            return Decimal("0")

        n = len(returns)
        mean_r = sum(returns, Decimal("0")) / n
        variance = sum((r - mean_r) ** 2 for r in returns) / n
        return Decimal(str(math.sqrt(float(variance))))


def expected_regime_duration(state: RegimeState) -> Decimal | None:
    """Expected remaining duration of the current regime, in timeframe units.

    Geometric-distribution mean: E[duración] = 1 / (1 - p_permanencia).
    Returns None when p_permanencia >= 1 (degenerate matrix) — the caller
    should display "indefinida" rather than a number.
    """
    p_stay = state.confidence
    if p_stay >= 1:
        return None
    return Decimal(1) / (Decimal(1) - p_stay)
