import numpy as np
import pandas as pd

from smart_invest.signals import score_signal


def prices(values):
    return pd.DataFrame({"Close": values}, index=pd.date_range("2024-01-01", periods=len(values), freq="B"))


def by_name(result):
    return {f["factor"]: f for f in result["factors"]}


def test_steady_uptrend_is_strong_but_flags_overbought_rsi():
    result = score_signal(prices(np.linspace(100, 200, 150)))
    factors = by_name(result)
    assert factors["趋势"]["vote"] == 1 and factors["动量"]["vote"] == 1
    assert factors["RSI"]["vote"] == -1  # RSI pinned at 100
    assert factors["回撤"]["vote"] == 0
    assert result["score"] == 1 and result["label"] == "略偏强"


def test_downtrend_with_deep_drawdown_is_weak():
    result = score_signal(prices(np.linspace(200, 100, 150)))
    factors = by_name(result)
    assert factors["趋势"]["vote"] == -1 and factors["回撤"]["vote"] == -1
    assert factors["RSI"]["vote"] == 1  # oversold
    assert result["label"] in {"略偏弱", "偏弱", "中性"}
    assert result["score"] == -2


def test_short_history_skips_unavailable_factors_instead_of_voting():
    result = score_signal(prices(np.linspace(100, 110, 30)))
    factors = by_name(result)
    assert factors["趋势"]["vote"] is None and factors["动量"]["vote"] is None
    assert result["available_factors"] == sum(f["vote"] is not None for f in result["factors"])
