"""策略基类与注册表。

自定义策略示例::

    from smart_invest.strategy import Strategy, register

    @register
    class MyStrategy(Strategy):
        name = "my_strategy"
        params = {"window": 20}

        def init(self, data):
            # 可选：一次性向量化计算指标（必须只用历史数据，如 rolling）
            self.ma = {s: df["close"].rolling(self.p["window"]).mean() for s, df in data.items()}

        def on_bar(self, ctx):
            for s in ctx.available:
                ...
                ctx.order_target_weight(s, 0.5)
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

STRATEGIES: dict[str, type["Strategy"]] = {}


class Strategy:
    name: str = "base"
    description: str = ""
    params: dict[str, Any] = {}

    def __init__(self, **kwargs):
        unknown = set(kwargs) - set(self.params)
        if unknown:
            raise ValueError(f"策略 {self.name} 不支持参数: {sorted(unknown)}，可用参数: {sorted(self.params)}")
        self.p: dict[str, Any] = dict(self.params)
        for k, v in kwargs.items():
            self.p[k] = _cast(v, self.params[k])
        self.validate()

    def validate(self) -> None:
        """子类可覆盖以校验参数。"""

    def init(self, data: dict[str, pd.DataFrame]) -> None:
        """回测开始前调用一次，可在此预计算指标。"""

    def on_bar(self, ctx) -> None:
        raise NotImplementedError

    def __repr__(self) -> str:
        args = ", ".join(f"{k}={v}" for k, v in self.p.items())
        return f"{self.name}({args})"


def _cast(value: Any, default: Any) -> Any:
    """把字符串参数（如命令行输入）转换为默认值的类型。"""
    if isinstance(default, str):
        return str(value)
    if not isinstance(value, str):
        # 数值统一为默认值的类型（例如结果表中读出的 5.0 -> 5）
        if isinstance(default, bool):
            return bool(value)
        if isinstance(default, int) and isinstance(value, (int, float, np.number)):
            if float(value) != int(value):
                raise ValueError(f"需要整数参数，收到: {value}")
            return int(value)
        if isinstance(default, float) and isinstance(value, (int, float, np.number)):
            return float(value)
        return value
    if isinstance(default, bool):
        if value.lower() in ("1", "true", "yes", "y", "on"):
            return True
        if value.lower() in ("0", "false", "no", "n", "off"):
            return False
        raise ValueError(f"无法解析布尔值: {value}")
    if isinstance(default, int):
        return int(float(value))
    if isinstance(default, float):
        return float(value)
    return value


def register(cls: type[Strategy]) -> type[Strategy]:
    if cls.name in STRATEGIES:
        raise ValueError(f"策略名重复: {cls.name}")
    STRATEGIES[cls.name] = cls
    return cls


def get_strategy(name: str, **params) -> Strategy:
    if name not in STRATEGIES:
        raise KeyError(f"未知策略 {name!r}，可选: {', '.join(sorted(STRATEGIES))}")
    return STRATEGIES[name](**params)
