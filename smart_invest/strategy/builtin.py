"""内置策略。所有指标都只使用截至当日的数据（rolling / shift），无未来函数。"""
from __future__ import annotations

import pandas as pd

from .base import Strategy, register


@register
class BuyAndHold(Strategy):
    name = "buy_hold"
    description = "买入持有：首个交易日等权买入全部股票，作为基准对照"
    params: dict = {}

    def init(self, data):
        self._done = False

    def on_bar(self, ctx):
        if self._done:
            return
        avail = ctx.available
        if avail:
            ctx.order_target_weights({s: 1 / len(ctx.symbols) for s in avail}, reason="建仓")
            self._done = len(avail) == len(ctx.symbols)


@register
class SmaCross(Strategy):
    name = "sma_cross"
    description = "双均线交叉：快线上穿慢线买入，下穿卖出；多股票时每只分配 1/N 资金"
    params = {"fast": 20, "slow": 60}

    def validate(self):
        if not 0 < self.p["fast"] < self.p["slow"]:
            raise ValueError("要求 0 < fast < slow")

    def init(self, data):
        self.signal = {}
        for s, df in data.items():
            fast = df["close"].rolling(self.p["fast"]).mean()
            slow = df["close"].rolling(self.p["slow"]).mean()
            self.signal[s] = (fast > slow) & slow.notna()

    def on_bar(self, ctx):
        n = len(ctx.symbols)
        for s in ctx.available:
            if not ctx.has_bar(s):
                continue
            long = bool(self.signal[s].get(ctx.date, False))
            holding = ctx.position(s) > 0
            if long and not holding:
                ctx.order_target_weight(s, 1 / n, reason="金叉买入")
            elif not long and holding:
                ctx.order_target_shares(s, 0, reason="死叉卖出")


@register
class BollingerReversion(Strategy):
    name = "bollinger"
    description = "布林带均值回归：收盘跌破下轨买入，回到中轨（或上轨）卖出"
    params = {"window": 20, "k": 2.0, "exit": "mid", "stop_loss": 0.0}

    def validate(self):
        if self.p["exit"] not in ("mid", "upper"):
            raise ValueError("exit 只能为 mid 或 upper")
        if self.p["window"] < 2 or self.p["k"] <= 0:
            raise ValueError("window >= 2 且 k > 0")

    def init(self, data):
        self.bands = {}
        for s, df in data.items():
            mid = df["close"].rolling(self.p["window"]).mean()
            std = df["close"].rolling(self.p["window"]).std()
            self.bands[s] = pd.DataFrame({"mid": mid, "upper": mid + self.p["k"] * std,
                                          "lower": mid - self.p["k"] * std})
        self.entry: dict[str, float] = {}

    def on_bar(self, ctx):
        n = len(ctx.symbols)
        for s in ctx.available:
            if not ctx.has_bar(s) or ctx.date not in self.bands[s].index:
                continue
            b = self.bands[s].loc[ctx.date]
            if pd.isna(b["mid"]):
                continue
            px = ctx.price(s)
            holding = ctx.position(s) > 0
            if not holding:
                if px < b["lower"]:
                    ctx.order_target_weight(s, 1 / n, reason="跌破下轨")
                    self.entry[s] = px
            else:
                sl = self.p["stop_loss"]
                if sl > 0 and s in self.entry and px < self.entry[s] * (1 - sl):
                    ctx.order_target_shares(s, 0, reason="止损")
                elif px > b[self.p["exit"]]:
                    ctx.order_target_shares(s, 0, reason="回归卖出")


@register
class MomentumRotation(Strategy):
    name = "momentum"
    description = "动量轮动：定期选出过去 lookback 日涨幅最高的 top_k 只等权持有，可叠加均线过滤（绝对动量）"
    params = {"lookback": 60, "top_k": 3, "rebalance_days": 20, "ma_filter": 200, "skip_recent": 0,
              "tolerance": 0.02}

    def validate(self):
        if self.p["lookback"] < 1 or self.p["top_k"] < 1 or self.p["rebalance_days"] < 1:
            raise ValueError("lookback、top_k、rebalance_days 必须 >= 1")

    def init(self, data):
        lb, skip = self.p["lookback"], self.p["skip_recent"]
        self.mom, self.trend_ok = {}, {}
        for s, df in data.items():
            c = df["close"]
            self.mom[s] = c.shift(skip) / c.shift(skip + lb) - 1
            if self.p["ma_filter"] > 0:
                self.trend_ok[s] = c > c.rolling(self.p["ma_filter"]).mean()
        self._count = 0

    def on_bar(self, ctx):
        self._count += 1
        if (self._count - 1) % self.p["rebalance_days"]:
            return
        scores = {}
        for s in ctx.available:
            if not ctx.has_bar(s):
                continue
            m = self.mom[s].get(ctx.date)
            if m is None or pd.isna(m) or m <= 0:
                continue
            if self.trend_ok and not bool(self.trend_ok[s].get(ctx.date, False)):
                continue
            scores[s] = m
        picks = sorted(scores, key=scores.get, reverse=True)[: self.p["top_k"]]
        # 停牌的持仓保留，避免产生无法成交的卖单
        weights = {s: 1 / self.p["top_k"] for s in picks}
        for s in ctx.positions:
            if s not in weights and not ctx.has_bar(s):
                weights[s] = ctx.weight(s)
        ctx.order_target_weights(weights, tolerance=self.p["tolerance"], reason="动量调仓")


@register
class RsiReversion(Strategy):
    name = "rsi"
    description = "RSI 超买超卖：RSI 低于 oversold 买入，高于 overbought 卖出"
    params = {"period": 14, "oversold": 30.0, "overbought": 70.0}

    def validate(self):
        if not 0 < self.p["oversold"] < self.p["overbought"] < 100:
            raise ValueError("要求 0 < oversold < overbought < 100")

    def init(self, data):
        self.rsi = {s: rsi(df["close"], self.p["period"]) for s, df in data.items()}

    def on_bar(self, ctx):
        n = len(ctx.symbols)
        for s in ctx.available:
            if not ctx.has_bar(s):
                continue
            r = self.rsi[s].get(ctx.date)
            if r is None or pd.isna(r):
                continue
            holding = ctx.position(s) > 0
            if not holding and r < self.p["oversold"]:
                ctx.order_target_weight(s, 1 / n, reason=f"RSI={r:.0f} 超卖")
            elif holding and r > self.p["overbought"]:
                ctx.order_target_shares(s, 0, reason=f"RSI={r:.0f} 超买")


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI。"""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    return out.where(loss > 0, 100.0).where(gain.notna())
