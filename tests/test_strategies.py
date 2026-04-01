"""
Tests for delay strategies.
"""

import pytest

from fastapi_gradual_throttle.strategies.base import BaseDelayStrategy
from fastapi_gradual_throttle.strategies.exponential import ExponentialDelayStrategy
from fastapi_gradual_throttle.strategies.linear import LinearDelayStrategy
from fastapi_gradual_throttle.strategies.none import NoDelayStrategy


class TestLinearStrategy:
    def test_zero_excess(self):
        s = LinearDelayStrategy(base_delay=0.2)
        assert s.calculate_delay(0) == 0.0

    def test_negative_excess(self):
        s = LinearDelayStrategy(base_delay=0.2)
        assert s.calculate_delay(-5) == 0.0

    def test_linear_calculation(self):
        s = LinearDelayStrategy(base_delay=0.2)
        assert s.calculate_delay(1) == pytest.approx(0.2)
        assert s.calculate_delay(5) == pytest.approx(1.0)

    def test_clamping(self):
        s = LinearDelayStrategy(base_delay=0.2, max_delay=0.5)
        assert s.calculate_delay(10) == pytest.approx(0.5)

    def test_base_delay_zero(self):
        s = LinearDelayStrategy(base_delay=0.0)
        assert s.calculate_delay(100) == 0.0


class TestExponentialStrategy:
    def test_zero_excess(self):
        s = ExponentialDelayStrategy(base_delay=0.2)
        assert s.calculate_delay(0) == 0.0

    def test_negative_excess(self):
        s = ExponentialDelayStrategy(base_delay=0.2)
        assert s.calculate_delay(-1) == 0.0

    def test_first_excess(self):
        s = ExponentialDelayStrategy(base_delay=0.2, multiplier=2.0)
        # 0.2 * 2^0 = 0.2
        assert s.calculate_delay(1) == pytest.approx(0.2)

    def test_exponential_growth(self):
        s = ExponentialDelayStrategy(base_delay=0.2, multiplier=2.0)
        # 0.2 * 2^1 = 0.4, 0.2 * 2^2 = 0.8, 0.2 * 2^3 = 1.6
        assert s.calculate_delay(2) == pytest.approx(0.4)
        assert s.calculate_delay(3) == pytest.approx(0.8)
        assert s.calculate_delay(4) == pytest.approx(1.6)

    def test_clamping(self):
        s = ExponentialDelayStrategy(base_delay=0.2, max_delay=1.0, multiplier=2.0)
        assert s.calculate_delay(10) == pytest.approx(1.0)

    def test_custom_multiplier(self):
        s = ExponentialDelayStrategy(base_delay=0.1, multiplier=3.0)
        # 0.1 * 3^1 = 0.3
        assert s.calculate_delay(2) == pytest.approx(0.3)


class TestNoDelayStrategy:
    def test_always_zero(self):
        s = NoDelayStrategy()
        assert s.calculate_delay(0) == 0.0
        assert s.calculate_delay(1) == 0.0
        assert s.calculate_delay(100) == 0.0


class TestBaseStrategyAbstract:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            BaseDelayStrategy()

    def test_clamp_delay(self):
        class TestStrategy(BaseDelayStrategy):
            def calculate_delay(self, excess_requests: int) -> float:
                return self._clamp_delay(100.0)

        s = TestStrategy(max_delay=5.0)
        assert s.calculate_delay(1) == 5.0
