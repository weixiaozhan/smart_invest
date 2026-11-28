"""策略基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    """所有策略的基类。

    子类实现 generate_signals，返回与行情索引对齐的目标仓位序列：
    1 表示满仓持有，0 表示空仓。
    """

    name: str = "base"

    @abstractmethod
    def generate_signals(self, prices: pd.DataFrame) -> pd.Series:
        raise NotImplementedError
