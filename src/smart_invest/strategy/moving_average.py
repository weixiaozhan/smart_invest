"""双均线交叉策略（示例）。"""

from __future__ import annotations

import pandas as pd

from smart_invest.strategy.base import Strategy


class MovingAverageCross(Strategy):
    name = "ma_cross"

    def __init__(self, short_window: int = 5, long_window: int = 20) -> None:
        if short_window >= long_window:
            raise ValueError("short_window 必须小于 long_window")
        self.short_window = short_window
        self.long_window = long_window

    def generate_signals(self, prices: pd.DataFrame) -> pd.Series:
        close = prices["close"]
        short_ma = close.rolling(self.short_window).mean()
        long_ma = close.rolling(self.long_window).mean()
        return (short_ma > long_ma).astype(int).rename("position")
