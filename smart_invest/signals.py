"""Explainable rule-based price signals; descriptive, never a buy or sell order.

Each factor votes -1, 0 or +1 from price history alone. The total is mapped to a
coarse label so a user can see why a holding is flagged. Factors that lack
enough history are reported as unavailable and do not vote.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .analytics import TRADING_DAYS_PER_YEAR, add_indicators

MOMENTUM_DAYS = 63
LABELS = {2: "偏强", 1: "略偏强", 0: "中性", -1: "略偏弱", -2: "偏弱"}


def _vote(name: str, value: float | None, vote: int | None, text: str) -> dict:
    return {"factor": name, "value": value, "vote": vote, "reason": text}


def score_signal(history: pd.DataFrame) -> dict:
    """Return factor votes, a net score and a label for one price history."""
    data = add_indicators(history)
    close = data["Close"]
    latest = float(close.iloc[-1])
    factors = []

    sma60 = data["SMA60"].iloc[-1]
    if pd.isna(sma60):
        factors.append(_vote("趋势", None, None, "历史不足 60 个交易日，无法判断长期趋势"))
    else:
        gap = latest / float(sma60) - 1
        vote = 1 if gap > 0.02 else -1 if gap < -0.02 else 0
        factors.append(_vote("趋势", gap, vote, f"收盘价相对 60 日均线 {gap:+.1%}"))

    if len(close) <= MOMENTUM_DAYS:
        factors.append(_vote("动量", None, None, f"历史不足 {MOMENTUM_DAYS} 个交易日，无法计算动量"))
    else:
        momentum = latest / float(close.iloc[-1 - MOMENTUM_DAYS]) - 1
        vote = 1 if momentum > 0.05 else -1 if momentum < -0.05 else 0
        factors.append(_vote("动量", momentum, vote, f"近 {MOMENTUM_DAYS} 个交易日涨跌 {momentum:+.1%}"))

    rsi = data["RSI14"].iloc[-1]
    if pd.isna(rsi):
        factors.append(_vote("RSI", None, None, "历史不足，无法计算 RSI14"))
    else:
        rsi = float(rsi)
        # Overbought lowers the vote: stretched prices carry mean-reversion risk.
        vote = -1 if rsi >= 70 else 1 if rsi <= 30 else 0
        state = "超买区" if rsi >= 70 else "超卖区" if rsi <= 30 else "中性区"
        factors.append(_vote("RSI", rsi, vote, f"RSI14 为 {rsi:.0f}，处于{state}"))

    drawdown = float((close / close.cummax() - 1).iloc[-1])
    vote = -1 if drawdown <= -0.20 else 0
    factors.append(_vote("回撤", drawdown, vote, f"较区间高点回撤 {drawdown:.1%}"))

    returns = close.pct_change(fill_method=None).dropna()
    volatility = None
    if len(returns) >= 2:
        volatility = float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))
    if volatility is None:
        factors.append(_vote("波动率", None, None, "历史不足，无法计算波动率"))
    else:
        vote = -1 if volatility >= 0.50 else 0
        factors.append(_vote("波动率", volatility, vote, f"年化波动率 {volatility:.1%}"))

    votes = [f["vote"] for f in factors if f["vote"] is not None]
    net = int(sum(votes))
    clipped = max(-2, min(2, net))
    return {
        "score": net,
        "label": LABELS[clipped],
        "available_factors": len(votes),
        "factors": factors,
        "volatility": volatility,
        "latest_close": latest,
    }
