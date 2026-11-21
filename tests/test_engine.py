import numpy as np
import pandas as pd
import pytest

from smart_invest import BacktestConfig, HKFeeModel, Strategy, get_strategy, run_backtest
from smart_invest.analysis import compute_metrics, max_drawdown, save_report
from smart_invest.data import SyntheticSource, normalize_symbol, standardize, to_yahoo_symbol
from smart_invest.strategy.builtin import rsi


def make_df(closes, opens=None, volume=1e6, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=len(closes), name="date")
    opens = closes if opens is None else opens
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes), "low": np.minimum(opens, closes),
                         "close": closes, "volume": volume}, index=idx).astype(float)


class Scripted(Strategy):
    """按日序号执行预设动作，用于测试引擎。"""
    name = "scripted"
    params = {}

    def __init__(self, actions):
        super().__init__()
        self.actions = actions
        self.seen = []

    def on_bar(self, ctx):
        self.seen.append((ctx.date, ctx.history("00700").iloc[-1]))
        act = self.actions.get(ctx.bar_index)
        if act:
            act(ctx)


NO_COST = dict(fee_model=HKFeeModel.zero(), slippage_ticks=0)


def test_symbol_normalization():
    assert normalize_symbol("700") == "00700"
    assert normalize_symbol("0700.HK") == "00700"
    assert normalize_symbol("HK.09988") == "09988"
    assert to_yahoo_symbol("00700") == "0700.HK"
    with pytest.raises(ValueError):
        normalize_symbol("AAPL")


def test_orders_fill_at_next_open_no_lookahead():
    closes = [10.0, 11.0, 12.0, 13.0]
    opens = [10.0, 10.5, 11.5, 12.5]
    data = {"00700": make_df(closes, opens)}
    strat = Scripted({0: lambda c: c.order_shares("00700", 1000)})
    res = run_backtest(data, strat, BacktestConfig(initial_cash=100_000, **NO_COST))
    t = res.trades.iloc[0]
    assert t["date"] == data["00700"].index[1]
    assert t["price"] == 10.5
    # 策略在第 i 天只能看到第 i 天收盘
    assert [v for _, v in strat.seen] == closes[:-1]
    assert res.equity["equity"].iloc[-1] == pytest.approx(100_000 + 1000 * (13.0 - 10.5))


def test_lot_rounding_and_cash_limit():
    data = {"00700": make_df([100.0] * 5)}   # 每手 100 股，一手 1 万
    strat = Scripted({0: lambda c: c.order_shares("00700", 250)})
    res = run_backtest(data, strat, BacktestConfig(initial_cash=1e6, **NO_COST))
    assert res.trades.iloc[0]["quantity"] == 200   # 不足一手的部分舍去

    strat = Scripted({0: lambda c: c.order_shares("00700", 10_000)})
    res = run_backtest(data, strat, BacktestConfig(initial_cash=35_000))
    assert res.trades.iloc[0]["quantity"] == 300   # 资金只够 3 手（含费用）
    assert res.equity["cash"].min() >= 0


def test_no_short_by_default_and_fees_reduce_equity():
    data = {"00700": make_df([100.0] * 6)}
    strat = Scripted({0: lambda c: c.order_shares("00700", 1000),
                      2: lambda c: c.order_shares("00700", -5000)})
    res = run_backtest(data, strat, BacktestConfig(initial_cash=1e6, slippage_ticks=0))
    sell = res.trades[res.trades["side"] == "SELL"].iloc[0]
    assert sell["quantity"] == 1000                 # 卖出不超过持仓
    assert res.positions.iloc[-1].sum() == 0
    total_fees = res.trades["fees"].sum()
    assert res.equity["equity"].iloc[-1] == pytest.approx(1e6 - total_fees)
    assert sell["realized_pnl"] == pytest.approx(-total_fees)


def test_slippage_uses_ticks():
    data = {"00700": make_df([388.4] * 4, opens=[388.4] * 4)}
    data["00700"]["high"] = 400.0
    data["00700"]["low"] = 380.0
    strat = Scripted({0: lambda c: c.order_shares("00700", 100),
                      1: lambda c: c.order_shares("00700", -100)})
    res = run_backtest(data, strat, BacktestConfig(fee_model=HKFeeModel.zero(), slippage_ticks=2))
    assert list(res.trades["price"]) == [388.8, 388.0]


def test_suspended_day_cancels_order():
    df = make_df([10.0] * 5)
    df.loc[df.index[1], "volume"] = 0  # 第 2 天停牌
    strat = Scripted({0: lambda c: c.order_shares("00700", 1000)})
    res = run_backtest({"00700": df}, strat, BacktestConfig(**NO_COST))
    assert res.trades.empty
    assert res.cancelled_orders == 1


def test_target_weight():
    data = {"00700": make_df([50.0] * 5)}
    strat = Scripted({0: lambda c: c.order_target_weight("00700", 0.5)})
    res = run_backtest(data, strat, BacktestConfig(initial_cash=1e6, cash_buffer=0, **NO_COST))
    assert res.trades.iloc[0]["quantity"] == 10_000


def test_warmup_period_excluded():
    df = make_df(list(np.linspace(10, 20, 30)))
    start = df.index[10]
    res = run_backtest({"00700": df}, get_strategy("buy_hold"), BacktestConfig(**NO_COST), start=start)
    assert res.equity.index[0] == start
    assert res.trades.iloc[0]["date"] == df.index[11]


def test_max_drawdown():
    eq = pd.Series([100, 120, 90, 110, 130, 65], index=pd.bdate_range("2024-01-01", periods=6))
    mdd, _ = max_drawdown(eq)
    assert mdd == pytest.approx(-0.5)


def test_rsi_bounds():
    s = pd.Series(np.random.default_rng(0).normal(0, 1, 300).cumsum() + 100)
    r = rsi(s).dropna()
    assert r.between(0, 100).all()
    assert rsi(pd.Series(np.arange(1.0, 40.0))).iloc[-1] == 100


def test_synthetic_data_deterministic_and_consistent():
    src = SyntheticSource(seed=1)
    a = src.fetch("00700", "2020-01-01", "2020-12-31")
    b = src.fetch("00700", "2019-01-01", "2021-12-31").loc["2020-01-01":"2020-12-31"]
    pd.testing.assert_frame_equal(a, b)
    df = standardize(a.reset_index())
    assert (df["high"] >= df[["open", "close"]].max(axis=1)).all()
    assert (df["low"] <= df[["open", "close"]].min(axis=1)).all()


@pytest.mark.parametrize("name", ["sma_cross", "bollinger", "momentum", "rsi", "buy_hold"])
def test_builtin_strategies_end_to_end(name, tmp_path):
    src = SyntheticSource()
    data = src.load(["00700", "09988", "00005"], "2018-01-01", "2022-12-31")
    bench = src.load(["02800"], "2018-01-01", "2022-12-31")["02800"]["close"]
    res = run_backtest(data, get_strategy(name), BacktestConfig(), start="2019-01-01", benchmark=bench)
    m = compute_metrics(res)
    assert len(res.trades) > 0
    assert res.equity["cash"].min() >= -1e-6
    assert np.isfinite(m["sharpe"]) and -1 <= m["max_drawdown"] <= 0
    assert "beta" in m
    path = save_report(res, tmp_path, m)
    assert path.exists() and (tmp_path / "trades.csv").exists()


def test_strategy_param_validation():
    with pytest.raises(ValueError):
        get_strategy("sma_cross", fast=60, slow=20)
    with pytest.raises(ValueError):
        get_strategy("sma_cross", nope=1)
    assert get_strategy("sma_cross", fast="10", slow=50.0).p == {"fast": 10, "slow": 50}
