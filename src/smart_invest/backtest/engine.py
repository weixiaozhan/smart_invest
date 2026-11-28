"""向量化回测引擎（单标的、全仓/空仓）。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from smart_invest.config import settings
from smart_invest.strategy.base import Strategy

TRADING_DAYS_PER_YEAR = 252


@dataclass
class BacktestResult:
    equity: pd.Series
    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe: float

    def summary(self) -> str:
        return (
            f"总收益率: {self.total_return:.2%}\n"
            f"年化收益率: {self.annual_return:.2%}\n"
            f"最大回撤: {self.max_drawdown:.2%}\n"
            f"夏普比率: {self.sharpe:.2f}"
        )


def run_backtest(
    prices: pd.DataFrame,
    strategy: Strategy,
    initial_cash: float | None = None,
    commission_rate: float | None = None,
) -> BacktestResult:
    initial_cash = settings.initial_cash if initial_cash is None else initial_cash
    commission_rate = settings.commission_rate if commission_rate is None else commission_rate

    returns = prices["close"].pct_change().fillna(0.0)
    # 用前一日信号决定当日持仓，避免未来函数
    position = strategy.generate_signals(prices).shift(1).fillna(0)
    costs = position.diff().abs().fillna(position.abs()) * commission_rate
    strat_returns = position * returns - costs

    equity = initial_cash * (1 + strat_returns).cumprod()
    total_return = equity.iloc[-1] / initial_cash - 1
    years = max(len(equity) / TRADING_DAYS_PER_YEAR, 1 / TRADING_DAYS_PER_YEAR)
    annual_return = (1 + total_return) ** (1 / years) - 1
    max_drawdown = float((equity / equity.cummax() - 1).min())
    std = strat_returns.std()
    sharpe = float(strat_returns.mean() / std * np.sqrt(TRADING_DAYS_PER_YEAR)) if std > 0 else 0.0

    return BacktestResult(
        equity=equity.rename("equity"),
        total_return=float(total_return),
        annual_return=float(annual_return),
        max_drawdown=max_drawdown,
        sharpe=sharpe,
    )
