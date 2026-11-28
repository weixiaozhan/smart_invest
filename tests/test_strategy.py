import pytest

from smart_invest.strategy import MovingAverageCross


def test_signals_are_binary_and_aligned(prices):
    signals = MovingAverageCross(5, 20).generate_signals(prices)
    assert signals.index.equals(prices.index)
    assert set(signals.unique()) <= {0, 1}


def test_invalid_windows():
    with pytest.raises(ValueError):
        MovingAverageCross(20, 5)
