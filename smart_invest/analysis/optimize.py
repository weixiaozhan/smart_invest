"""参数网格搜索 + 样本外检验。

只看样本内最优参数很容易过拟合，建议用 holdout：在前段数据上选参，
再用选出的参数在后段（从未参与选参的）数据上检验。
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import replace

import numpy as np
import pandas as pd

from ..engine.backtest import BacktestConfig, run_backtest
from ..strategy.base import get_strategy

logger = logging.getLogger(__name__)

_COLUMNS = ["total_return", "cagr", "sharpe", "max_drawdown", "calmar", "win_rate", "num_trades",
            "excess_cagr"]


def walk_forward_split(start, end, train_ratio: float) -> pd.Timestamp:
    """按时间比例返回样本内/样本外分界日。"""
    if not 0 < train_ratio < 1:
        raise ValueError("train_ratio 必须在 (0, 1) 之间")
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    return (start + (end - start) * train_ratio).normalize()


def grid_search(data, strategy_name: str, grid: dict[str, list], config: BacktestConfig | None = None,
                start=None, end=None, benchmark: pd.Series | None = None,
                sort_by: str = "sharpe") -> pd.DataFrame:
    """遍历参数组合，返回按 sort_by 降序排列的结果表。非法参数组合会被跳过。"""
    config = config or BacktestConfig()
    keys = list(grid)
    rows = []
    combos = list(itertools.product(*(grid[k] for k in keys)))
    for values in combos:
        params = dict(zip(keys, values))
        try:
            strat = get_strategy(strategy_name, **params)
        except ValueError as e:
            logger.debug("跳过参数 %s: %s", params, e)
            continue
        # 每次回测使用独立的配置副本
        res = run_backtest(data, strat, replace(config), start=start, end=end, benchmark=benchmark)
        m = res.metrics()
        rows.append({**strat.p, **{k: m.get(k, np.nan) for k in _COLUMNS}})
    if not rows:
        raise ValueError("没有合法的参数组合")
    df = pd.DataFrame(rows)
    return df.sort_values(sort_by, ascending=False, na_position="last").reset_index(drop=True)
