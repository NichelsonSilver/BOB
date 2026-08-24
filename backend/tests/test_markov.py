"""Tests for the Markov regime detector — pure, no I/O."""

from __future__ import annotations

from decimal import Decimal

from bob.models.markov import (
    _DEFAULT_TRANSITIONS,
    MarketRegime,
    MarkovRegimeDetector,
    RegimeState,
    expected_regime_duration,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flat_prices(base: str, n: int = 20) -> list[Decimal]:
    """Perfectly flat price series."""
    return [Decimal(base)] * n


def _trend_up(start: str, pct_per_step: float, n: int = 20) -> list[Decimal]:
    """Monotonically rising prices."""
    p = Decimal(start)
    factor = Decimal(str(1 + pct_per_step))
    result = [p]
    for _ in range(n - 1):
        p = p * factor
        result.append(p)
    return result


def _trend_down(start: str, pct_per_step: float, n: int = 20) -> list[Decimal]:
    p = Decimal(start)
    factor = Decimal(str(1 - pct_per_step))
    result = [p]
    for _ in range(n - 1):
        p = p * factor
        result.append(p)
    return result


def _volatile(base: str, amplitude: float, n: int = 20) -> list[Decimal]:
    """Oscillating prices with large swings to trigger high vol."""
    b = Decimal(base)
    amp = Decimal(str(amplitude))
    return [b + amp * (Decimal("1") if i % 2 == 0 else Decimal("-1")) for i in range(n)]


# ---------------------------------------------------------------------------
# classify()
# ---------------------------------------------------------------------------

class TestClassify:
    def test_empty_returns_ranging(self):
        d = MarkovRegimeDetector()
        assert d.classify([]) == MarketRegime.RANGING

    def test_single_price_returns_ranging(self):
        d = MarkovRegimeDetector()
        assert d.classify([Decimal("100")]) == MarketRegime.RANGING

    def test_flat_series_is_ranging(self):
        d = MarkovRegimeDetector()
        assert d.classify(_flat_prices("100", 10)) == MarketRegime.RANGING

    def test_strong_uptrend_classified(self):
        d = MarkovRegimeDetector(trend_threshold=Decimal("0.01"))
        # Each step +1% → 20 steps ≈ +22% net
        prices = _trend_up("100", 0.01, 20)
        assert d.classify(prices) == MarketRegime.TRENDING_UP

    def test_strong_downtrend_classified(self):
        d = MarkovRegimeDetector(trend_threshold=Decimal("0.01"))
        prices = _trend_down("100", 0.01, 20)
        assert d.classify(prices) == MarketRegime.TRENDING_DOWN

    def test_volatile_classified_before_trend(self):
        d = MarkovRegimeDetector(
            trend_threshold=Decimal("0.01"),
            vol_threshold=Decimal("0.005"),
        )
        # Large oscillations: high vol, low net trend
        prices = _volatile("100", 5, 20)
        assert d.classify(prices) == MarketRegime.VOLATILE

    def test_zero_first_price_returns_ranging(self):
        d = MarkovRegimeDetector()
        prices = [Decimal("0")] + [Decimal("100")] * 5
        assert d.classify(prices) == MarketRegime.RANGING


# ---------------------------------------------------------------------------
# estimate_transitions()
# ---------------------------------------------------------------------------

class TestEstimateTransitions:
    def test_returns_default_when_too_short(self):
        d = MarkovRegimeDetector(subwindow=5)
        # 5 prices → only one sub-window → falls back to default
        prices = _trend_up("100", 0.01, 5)
        matrix = d.estimate_transitions(prices)
        assert matrix is _DEFAULT_TRANSITIONS

    def test_rows_sum_to_one(self):
        d = MarkovRegimeDetector(subwindow=3)
        # 30 prices → 7+ sub-windows, enough for estimation
        prices = _trend_up("100", 0.005, 30) + _flat_prices("150", 30)
        matrix = d.estimate_transitions(prices)
        for regime, row in matrix.items():
            total = sum(row.values())
            assert abs(total - Decimal("1")) < Decimal("0.0001"), (
                f"Row for {regime} sums to {total}"
            )

    def test_all_regimes_present_in_matrix(self):
        d = MarkovRegimeDetector(subwindow=3)
        prices = _trend_up("100", 0.005, 30)
        matrix = d.estimate_transitions(prices)
        for regime in MarketRegime:
            assert regime in matrix
            for r2 in MarketRegime:
                assert r2 in matrix[regime]

    def test_laplace_smoothing_prevents_zero_prob(self):
        d = MarkovRegimeDetector(subwindow=3)
        # Purely trending prices — RANGING/VOLATILE transitions should still be > 0
        prices = _trend_up("100", 0.01, 50)
        matrix = d.estimate_transitions(prices)
        for from_r in MarketRegime:
            for to_r in MarketRegime:
                assert matrix[from_r][to_r] > Decimal("0")


# ---------------------------------------------------------------------------
# current_regime()
# ---------------------------------------------------------------------------

class TestCurrentRegime:
    def test_returns_regime_state(self):
        d = MarkovRegimeDetector(subwindow=3)
        prices = _trend_up("100", 0.01, 30)
        result = d.current_regime(prices)
        assert isinstance(result, RegimeState)
        assert isinstance(result.regime, MarketRegime)
        assert Decimal("0") < result.confidence <= Decimal("1")

    def test_confidence_is_self_transition_prob(self):
        d = MarkovRegimeDetector(subwindow=3)
        prices = _trend_up("100", 0.01, 30)
        result = d.current_regime(prices)
        # confidence == transition_probs[regime → regime]
        assert result.confidence == result.transition_probs[result.regime]

    def test_trending_up_detected_with_sufficient_history(self):
        d = MarkovRegimeDetector(
            trend_threshold=Decimal("0.01"),
            vol_threshold=Decimal("0.02"),
            subwindow=5,
        )
        prices = _trend_up("100", 0.005, 50)
        result = d.current_regime(prices)
        assert result.regime == MarketRegime.TRENDING_UP

    def test_ranging_detected_on_flat_history(self):
        d = MarkovRegimeDetector(subwindow=3)
        prices = _flat_prices("100", 30)
        result = d.current_regime(prices)
        assert result.regime == MarketRegime.RANGING

    def test_uses_recent_window_for_classification(self):
        """Long trending history followed by flat prices → should detect RANGING."""
        d = MarkovRegimeDetector(
            trend_threshold=Decimal("0.01"),
            vol_threshold=Decimal("0.02"),
            subwindow=5,
        )
        # 40 trending prices, then 10 flat ones
        trending = _trend_up("100", 0.005, 40)
        flat_end = _flat_prices(str(trending[-1]), 10)
        prices = trending + flat_end
        result = d.current_regime(prices)
        # The classification of the most-recent window should see a flat market
        assert result.regime == MarketRegime.RANGING


# ---------------------------------------------------------------------------
# expected_regime_duration() — KPI 3
# ---------------------------------------------------------------------------

class TestExpectedRegimeDuration:
    def _state(self, p_stay: str) -> RegimeState:
        return RegimeState(
            regime=MarketRegime.RANGING,
            confidence=Decimal(p_stay),
            transition_probs=_DEFAULT_TRANSITIONS[MarketRegime.RANGING],
        )

    def test_geometric_mean(self):
        # p_stay = 0.60 → E[duración] = 1 / 0.4 = 2.5 barras
        assert expected_regime_duration(self._state("0.60")) == Decimal("2.5")

    def test_low_persistence_short_duration(self):
        # p_stay = 0.20 → 1.25 barras
        assert expected_regime_duration(self._state("0.20")) == Decimal("1.25")

    def test_degenerate_matrix_returns_none(self):
        assert expected_regime_duration(self._state("1")) is None

    def test_higher_persistence_longer_duration(self):
        d_low = expected_regime_duration(self._state("0.50"))
        d_high = expected_regime_duration(self._state("0.90"))
        assert d_low is not None and d_high is not None
        assert d_high > d_low


# ---------------------------------------------------------------------------
# Default transitions sanity checks
# ---------------------------------------------------------------------------

class TestDefaultTransitions:
    def test_all_rows_sum_to_one(self):
        for regime, row in _DEFAULT_TRANSITIONS.items():
            total = sum(row.values())
            assert abs(total - Decimal("1")) < Decimal("0.001"), (
                f"Default row for {regime} sums to {total}"
            )

    def test_persistent_regimes_have_highest_self_transition(self):
        """Ranging/trending regimes should prefer to stay in themselves.

        VOLATILE is intentionally an exception: it reverts to RANGING more
        often than it persists (volatile spikes tend to resolve quickly).
        """
        for regime in (
            MarketRegime.RANGING,
            MarketRegime.TRENDING_UP,
            MarketRegime.TRENDING_DOWN,
        ):
            row = _DEFAULT_TRANSITIONS[regime]
            self_prob = row[regime]
            others = [v for r, v in row.items() if r != regime]
            assert all(self_prob >= o for o in others), (
                f"Self-transition for {regime} ({self_prob}) is not the max"
            )

    def test_volatile_reverts_to_ranging(self):
        """Volatile regime should have higher reversion-to-ranging than self-persistence."""
        row = _DEFAULT_TRANSITIONS[MarketRegime.VOLATILE]
        assert row[MarketRegime.RANGING] > row[MarketRegime.VOLATILE]
