"""Price-based indicators and a reproducible daily moving-average backtest.

Inputs must be ordered from oldest to newest. Returns and drawdowns use decimal
fractions: a return of 0.1 means 10%, and a drawdown of -0.2 means a 20% loss
from the preceding peak. None represents a statistic with insufficient data.
"""

from __future__ import annotations

from numbers import Integral, Real

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


def _close_prices(history: pd.DataFrame) -> pd.Series:
    """Validate prices without changing the caller's frame."""
    if not isinstance(history, pd.DataFrame):
        raise TypeError("history must be a pandas DataFrame")
    if len(history) == 0:
        return pd.Series(index=history.index, dtype=float, name="Close")
    if "Close" not in history.columns:
        raise ValueError("history must contain a Close column")
    if not isinstance(history["Close"], pd.Series):
        raise ValueError("history must contain exactly one Close column")
    try:
        close = pd.to_numeric(history["Close"], errors="raise").astype(float)
    except (TypeError, ValueError) as exc:
        raise ValueError("Close prices must be numeric") from exc
    if not np.isfinite(close.to_numpy()).all() or (close <= 0).any():
        raise ValueError("Close prices must be finite and greater than zero")
    return close


def _wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Seed Wilder's smoothing with the first period's simple averages."""
    result = pd.Series(np.nan, index=close.index, dtype=float)
    if len(close) <= period:
        return result

    changes = close.diff()
    gains = changes.clip(lower=0).to_numpy()
    losses = (-changes.clip(upper=0)).to_numpy()
    average_gain = float(gains[1 : period + 1].mean())
    average_loss = float(losses[1 : period + 1].mean())

    for position in range(period, len(close)):
        if position > period:
            average_gain = (average_gain * (period - 1) + gains[position]) / period
            average_loss = (average_loss * (period - 1) + losses[position]) / period
        if average_loss == 0:
            result.iloc[position] = 100.0 if average_gain > 0 else 50.0
        elif average_gain == 0:
            result.iloc[position] = 0.0
        else:
            result.iloc[position] = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


def add_indicators(history: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with SMA20, SMA60 and Wilder RSI14.

    Moving averages require their full windows. RSI requires 14 price changes
    (15 observations); an entirely flat window has neutral RSI of 50.
    """
    close = _close_prices(history)
    result = history.copy(deep=True)
    result["SMA20"] = close.rolling(20, min_periods=20).mean()
    result["SMA60"] = close.rolling(60, min_periods=60).mean()
    result["RSI14"] = _wilder_rsi(close)
    return result


def summarize(history: pd.DataFrame) -> dict[str, float | int | None]:
    """Summarize closes, using sample daily volatility annualized over 252 days.

    Maximum drawdown is negative or zero. Volatility requires at least two
    daily returns; other statistics require at least one price observation.
    """
    close = _close_prices(history)
    if close.empty:
        return {
            "latest_close": None,
            "total_return": None,
            "annualized_volatility": None,
            "max_drawdown": None,
            "observations": 0,
        }
    daily_returns = close.pct_change(fill_method=None).dropna()
    volatility = (
        float(daily_returns.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))
        if len(daily_returns) >= 2
        else None
    )
    return {
        "latest_close": float(close.iloc[-1]),
        "total_return": float(close.iloc[-1] / close.iloc[0] - 1),
        "annualized_volatility": volatility,
        "max_drawdown": float((close / close.cummax() - 1).min()),
        "observations": len(close),
    }


def backtest_sma(
    history: pd.DataFrame,
    fast: int = 20,
    slow: int = 60,
    fee_bps: float = 10.0,
) -> pd.DataFrame:
    """Return daily long/cash SMA results with no look-ahead.

    A close-based signal is applied to the following close-to-close return.
    Position is 1 when invested and 0 when in cash; it stays zero until both
    averages are available. Each entry or exit multiplies available capital by
    1 - fee_bps / 10,000. BuyHold deducts one entry fee on its first return day.
    Both curves start at growth factor 1. This simplified model excludes
    dividends, slippage, taxes, market impact and a final liquidation.
    """
    for name, value in (("fast", fast), ("slow", slow)):
        if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if fast >= slow:
        raise ValueError("fast must be less than slow")
    if (
        isinstance(fee_bps, bool)
        or not isinstance(fee_bps, Real)
        or not np.isfinite(fee_bps)
        or fee_bps < 0
        or fee_bps >= 10_000
    ):
        raise ValueError("fee_bps must be finite and satisfy 0 <= fee_bps < 10000")

    close = _close_prices(history)
    result = history.copy(deep=True)
    fast_average = close.rolling(int(fast), min_periods=int(fast)).mean()
    slow_average = close.rolling(int(slow), min_periods=int(slow)).mean()
    signal = fast_average.gt(slow_average).astype(int)
    position = signal.shift(1, fill_value=0)
    daily_returns = close.pct_change(fill_method=None).fillna(0.0)
    turnover = position.diff().fillna(position).abs()
    fee_fraction = fee_bps / 10_000
    strategy_returns = (1.0 + position * daily_returns) * (1.0 - turnover * fee_fraction) - 1.0
    buy_hold_returns = daily_returns.copy()
    if len(buy_hold_returns) > 1:
        buy_hold_returns.iloc[1] = (1.0 + buy_hold_returns.iloc[1]) * (1.0 - fee_fraction) - 1.0
    result["BuyHold"] = (1.0 + buy_hold_returns).cumprod()
    result["Strategy"] = (1.0 + strategy_returns).cumprod()
    result["Position"] = position
    result["StrategyReturn"] = strategy_returns
    return result
