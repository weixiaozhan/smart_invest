"""示例：用 Python API 编写自定义策略并回测。

运行: python examples/custom_strategy.py
"""
import pandas as pd

from smart_invest import BacktestConfig, HKFeeModel, Strategy, make_source, run_backtest, save_report
from smart_invest.analysis import summary_text


class Breakout(Strategy):
    """唐奇安通道突破：收盘创 N 日新高买入，跌破 M 日新低卖出，按波动率倒数分配仓位。"""

    name = "breakout"
    params = {"entry": 55, "exit": 20, "vol_window": 20, "max_weight": 0.4}

    def init(self, data):
        self.hi, self.lo, self.vol = {}, {}, {}
        for s, df in data.items():
            c = df["close"]
            # shift(1)：与"昨日为止"的高低点比较，避免用到当天自身
            self.hi[s] = c.rolling(self.p["entry"]).max().shift(1)
            self.lo[s] = c.rolling(self.p["exit"]).min().shift(1)
            self.vol[s] = c.pct_change().rolling(self.p["vol_window"]).std()

    def on_bar(self, ctx):
        for s in ctx.available:
            if not ctx.has_bar(s):
                continue
            px, d = ctx.price(s), ctx.date
            hi, lo, vol = self.hi[s].get(d), self.lo[s].get(d), self.vol[s].get(d)
            if pd.isna(hi) or pd.isna(vol) or vol <= 0:
                continue
            if ctx.position(s) == 0 and px > hi:
                # 目标日波动 0.5%：波动越大仓位越小
                w = min(0.005 / vol, self.p["max_weight"])
                ctx.order_target_weight(s, w, reason="突破新高")
            elif ctx.position(s) > 0 and px < lo:
                ctx.order_target_shares(s, 0, reason="跌破通道")


if __name__ == "__main__":
    # 换成 make_source("akshare") 或 make_source("yahoo") 即可使用真实行情
    source = make_source("synthetic")
    symbols = ["00700", "09988", "03690", "01299", "00005", "00388"]
    data = source.load(symbols, "2017-01-01", "2024-12-31")
    bench = source.load(["02800"], "2017-01-01", "2024-12-31")["02800"]["close"]

    config = BacktestConfig(
        initial_cash=2_000_000,
        fee_model=HKFeeModel(commission_rate=0.0003, min_commission=3, platform_fee=15),
        slippage_ticks=1,
    )
    result = run_backtest(data, Breakout(entry=55, exit=20), config, start="2018-01-01", benchmark=bench)
    metrics = result.metrics()
    print(summary_text(result, metrics))
    path = save_report(result, "reports/breakout", metrics, data_note="示例使用模拟行情，不代表真实表现。")
    print(f"\n报告: {path}")
