import numpy as np
import pandas as pd
import pytest

from smart_invest.analytics import add_indicators, backtest_sma, summarize


def prices(values):
    return pd.DataFrame(
        {"Close": values, "Volume": np.arange(len(values)) + 100},
        index=pd.date_range("2024-01-01", periods=len(values), freq="B"),
    )


def test_indicators_preserve_input_and_use_full_windows():
    history = prices(np.arange(1, 81, dtype=float))
    original = history.copy(deep=True)
    result = add_indicators(history)
    pd.testing.assert_frame_equal(history, original)
    pd.testing.assert_frame_equal(result[history.columns], original)
    assert result["SMA20"].iloc[:19].isna().all()
    assert result["SMA20"].iloc[19] == pytest.approx(10.5)
    assert result["SMA60"].iloc[:59].isna().all()
    assert result["SMA60"].iloc[59] == pytest.approx(30.5)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (list(range(1, 21)), 100.0),
        (list(range(21, 1, -1)), 0.0),
        ([100.0] * 20, 50.0),
    ],
)
def test_rsi_boundaries(values, expected):
    result = add_indicators(prices(values))["RSI14"]
    assert result.iloc[:14].isna().all()
    np.testing.assert_allclose(result.iloc[14:], expected)


def test_rsi_matches_wilder_initialization_and_smoothing():
    history = prices(
        [
            44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
            46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03,
        ]
    )
    result = add_indicators(history)["RSI14"]
    np.testing.assert_allclose(
        result.iloc[14:], [70.464135, 66.249619, 66.480942], atol=1e-6
    )


def test_summary_return_drawdown_and_annualized_volatility():
    history = prices([100, 120, 90, 110])
    original = history.copy(deep=True)
    result = summarize(history)
    expected_returns = np.array([0.2, -0.25, 110 / 90 - 1])
    assert result["latest_close"] == 110
    assert result["total_return"] == pytest.approx(0.1)
    assert result["max_drawdown"] == pytest.approx(-0.25)
    assert result["annualized_volatility"] == pytest.approx(
        expected_returns.std(ddof=1) * np.sqrt(252)
    )
    assert result["observations"] == 4
    pd.testing.assert_frame_equal(history, original)


def test_empty_and_short_histories_have_defined_results():
    empty = pd.DataFrame()
    assert add_indicators(empty).empty
    assert list(add_indicators(empty).columns) == ["SMA20", "SMA60", "RSI14"]
    assert summarize(empty) == {
        "latest_close": None,
        "total_return": None,
        "annualized_volatility": None,
        "max_drawdown": None,
        "observations": 0,
    }
    assert backtest_sma(empty).empty
    one = prices([100.0])
    assert summarize(one) == {
        "latest_close": 100.0,
        "total_return": 0.0,
        "annualized_volatility": None,
        "max_drawdown": 0.0,
        "observations": 1,
    }
    result = backtest_sma(one)
    assert result["BuyHold"].iloc[0] == 1
    assert result["Strategy"].iloc[0] == 1
    assert result["Position"].iloc[0] == 0
    assert add_indicators(one)[["SMA20", "SMA60", "RSI14"]].isna().all().all()


def test_backtest_delays_signal_and_charges_both_entry_and_exit():
    history = prices([100, 110, 120, 130, 80, 70])
    original = history.copy(deep=True)
    result = backtest_sma(history, fast=1, slow=2, fee_bps=100)
    assert result["Position"].tolist() == [0, 0, 1, 1, 1, 0]
    expected = np.array([0, 0, 120 / 110 * 0.99 - 1, 130 / 120 - 1,
                         80 / 130 - 1, -0.01])
    np.testing.assert_allclose(result["StrategyReturn"], expected)
    np.testing.assert_allclose(result["Strategy"], np.cumprod(1 + expected))
    expected_buy_hold = history["Close"].to_numpy(dtype=float) / 100
    expected_buy_hold[1:] *= 0.99
    np.testing.assert_allclose(result["BuyHold"], expected_buy_hold)
    pd.testing.assert_frame_equal(history, original)


def test_backtest_large_price_drop_with_fee_keeps_positive_equity():
    result = backtest_sma(prices([100, 110, 1]), fast=1, slow=2, fee_bps=100)
    assert result["Strategy"].iloc[-1] == pytest.approx(1 / 110 * 0.99)
    assert (result[["Strategy", "BuyHold"]] > 0).all().all()


def test_backtest_future_prices_do_not_change_past_results():
    history = prices([100, 110, 120, 130, 80, 70])
    changed = history.copy(deep=True)
    changed.iloc[-1, changed.columns.get_loc("Close")] = 1000
    first = backtest_sma(history, fast=1, slow=2)
    second = backtest_sma(changed, fast=1, slow=2)
    pd.testing.assert_frame_equal(first.iloc[:-1], second.iloc[:-1])
    assert first["Position"].iloc[-1] == second["Position"].iloc[-1]


@pytest.mark.parametrize("value", [0, -1, np.inf, np.nan, "invalid"])
@pytest.mark.parametrize("operation", [add_indicators, summarize, backtest_sma])
def test_invalid_close_prices_are_rejected(value, operation):
    with pytest.raises(ValueError, match="Close prices"):
        operation(prices([100, value]))


@pytest.mark.parametrize("operation", [add_indicators, summarize, backtest_sma])
def test_nonempty_history_requires_close_column(operation):
    with pytest.raises(ValueError, match="Close column"):
        operation(pd.DataFrame(index=[0, 1]))


@pytest.mark.parametrize(
    "parameters",
    [
        {"fast": 0}, {"fast": 1.5}, {"fast": True}, {"slow": -1},
        {"fast": 20, "slow": 20}, {"fast": 21, "slow": 20},
        {"fee_bps": -1}, {"fee_bps": np.inf}, {"fee_bps": np.nan},
        {"fee_bps": 10000}, {"fee_bps": True},
    ],
)
def test_invalid_backtest_parameters_are_rejected(parameters):
    with pytest.raises(ValueError):
        backtest_sma(prices([100, 110]), **parameters)
