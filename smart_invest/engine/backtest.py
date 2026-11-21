"""事件驱动的日线回测引擎。

每个交易日 t 的处理顺序：
  1. 以 t 日开盘价成交 t-1 日收盘后产生的订单（先卖后买），含滑点与费用；
  2. 按 t 日收盘价对持仓估值；
  3. 调用策略 on_bar —— 策略只能看到截至 t 日收盘的数据，新订单在 t+1 开盘成交。

因此不会出现"用当天收盘价算信号、又按当天收盘价成交"的未来函数问题。

港股特有处理：
  - 按每手股数整手交易（不做碎股）；
  - 价格对齐港交所价位表，滑点以"价位"为单位；
  - 买卖双边收取印花税等费用；
  - 停牌（当日无数据或成交量为 0）的订单作废；
  - 默认不允许卖空。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import pandas as pd

from ..market.hk import HKFeeModel, LotSizeTable, shift_ticks

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    initial_cash: float = 1_000_000.0
    fee_model: HKFeeModel = field(default_factory=HKFeeModel)
    lot_sizes: dict[str, int] = field(default_factory=dict)
    slippage_ticks: int = 1            # 每笔成交不利方向滑点（价位数）
    max_volume_pct: float | None = None  # 单笔成交不超过当日成交量的比例，None 不限制
    cash_buffer: float = 0.005         # 按目标权重下单时预留的现金比例（覆盖费用）
    allow_short: bool = False


@dataclass
class Position:
    quantity: int = 0
    avg_cost: float = 0.0  # 含买入费用的平均成本


@dataclass
class Order:
    symbol: str
    quantity: int          # 正数买入，负数卖出（股数）
    created: pd.Timestamp
    reason: str = ""


@dataclass
class Trade:
    date: pd.Timestamp
    symbol: str
    side: str
    quantity: int
    price: float
    value: float
    fees: float
    realized_pnl: float
    reason: str = ""


class Portfolio:
    def __init__(self, cash: float):
        self.cash = float(cash)
        self.positions: dict[str, Position] = {}

    def quantity(self, symbol: str) -> int:
        pos = self.positions.get(symbol)
        return pos.quantity if pos else 0

    def market_value(self, prices: dict[str, float]) -> float:
        return sum(p.quantity * prices[s] for s, p in self.positions.items() if p.quantity)

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.market_value(prices)

    def apply(self, symbol: str, qty: int, price: float, fees: float) -> float:
        """更新持仓与现金，返回本次实现盈亏（仅平仓部分）。"""
        pos = self.positions.setdefault(symbol, Position())
        realized = 0.0
        self.cash -= qty * price + fees
        if qty > 0:
            if pos.quantity >= 0:
                total_cost = pos.quantity * pos.avg_cost + qty * price + fees
                pos.quantity += qty
                pos.avg_cost = total_cost / pos.quantity
            else:  # 回补空头
                cover = min(qty, -pos.quantity)
                realized = (pos.avg_cost - price) * cover - fees
                pos.quantity += qty
                if pos.quantity > 0:
                    pos.avg_cost = price
        else:
            sell = -qty
            if pos.quantity > 0:
                closed = min(sell, pos.quantity)
                realized = (price - pos.avg_cost) * closed - fees
                pos.quantity -= sell
                if pos.quantity < 0:
                    pos.avg_cost = price
            else:  # 开/加空头
                total = -pos.quantity * pos.avg_cost + sell * price
                pos.quantity -= sell
                pos.avg_cost = total / -pos.quantity
        if pos.quantity == 0:
            del self.positions[symbol]
        return realized


class Context:
    """策略在 on_bar 中使用的接口。"""

    def __init__(self, engine: "BacktestEngine"):
        self._e = engine
        self.date: pd.Timestamp | None = None
        self.bar_index: int = -1
        self._orders: dict[str, Order] = {}

    # ---- 数据访问 ----
    @property
    def symbols(self) -> list[str]:
        return list(self._e.data.keys())

    @property
    def available(self) -> list[str]:
        """已上市（截至今日有过行情）的股票，只有它们可以下单。"""
        return [s for s in self._e.data if s in self._e._last_close]

    def has_bar(self, symbol: str) -> bool:
        """当日是否有该股票的行情（未停牌）。"""
        return self._e._bar(symbol, self.date) is not None

    def price(self, symbol: str) -> float:
        """当前（截至今日收盘）的最新价格。"""
        return self._e._last_close[symbol]

    def history(self, symbol: str, field: str = "close", window: int | None = None) -> pd.Series:
        s = self._e.data[symbol][field].loc[: self.date]
        return s.iloc[-window:] if window else s

    # ---- 账户 ----
    @property
    def cash(self) -> float:
        return self._e.portfolio.cash

    @property
    def equity(self) -> float:
        return self._e.portfolio.equity(self._e._last_close)

    def position(self, symbol: str) -> int:
        return self._e.portfolio.quantity(symbol)

    @property
    def positions(self) -> dict[str, int]:
        return {s: p.quantity for s, p in self._e.portfolio.positions.items()}

    def weight(self, symbol: str) -> float:
        eq = self.equity
        return self.position(symbol) * self.price(symbol) / eq if eq > 0 else 0.0

    def lot_size(self, symbol: str) -> int:
        return self._e.lots.get(symbol)

    # ---- 下单（均在下一交易日开盘成交）----
    def order_shares(self, symbol: str, quantity: int, reason: str = "") -> None:
        """买入（正）/卖出（负）指定股数，不足一手的部分会被舍去。"""
        if symbol not in self._e.data:
            raise KeyError(f"未知股票: {symbol}")
        lot = self.lot_size(symbol)
        lots = int(abs(quantity) // lot)
        qty = int(math.copysign(lots * lot, quantity)) if lots else 0
        if qty == 0:
            self._orders.pop(symbol, None)
            return
        self._orders[symbol] = Order(symbol, qty, self.date, reason)

    def order_target_shares(self, symbol: str, target: int, reason: str = "") -> None:
        self.order_shares(symbol, target - self.position(symbol), reason)

    def order_target_weight(self, symbol: str, weight: float, reason: str = "") -> None:
        """调整到目标权重（按今日收盘价估算股数）。"""
        if weight < 0 and not self._e.config.allow_short:
            raise ValueError("未开启卖空 (allow_short=False)")
        if weight == 0:
            self.order_target_shares(symbol, 0, reason)
            return
        target_value = weight * self.equity * (1 - self._e.config.cash_buffer)
        target = target_value / self.price(symbol)
        lot = self.lot_size(symbol)
        target_shares = int(math.copysign(math.floor(abs(target) / lot) * lot, target))
        self.order_target_shares(symbol, target_shares, reason)

    def order_target_weights(self, weights: dict[str, float], tolerance: float = 0.0, reason: str = "") -> None:
        """把组合调整为给定权重，未列出的持仓清仓。

        tolerance: 当前权重与目标权重差异小于该值时不交易，用于减少换手。
        """
        for sym in list(self.positions):
            if sym not in weights:
                self.order_target_shares(sym, 0, reason)
        for sym, w in weights.items():
            if tolerance and abs(self.weight(sym) - w) < tolerance and (w > 0) == (self.position(sym) > 0):
                continue
            self.order_target_weight(sym, w, reason)

    def cancel_all(self) -> None:
        self._orders.clear()


@dataclass
class BacktestResult:
    equity: pd.DataFrame          # columns: cash, market_value, equity
    trades: pd.DataFrame
    positions: pd.DataFrame       # 每日持仓股数
    config: BacktestConfig
    strategy_name: str
    strategy_params: dict
    benchmark: pd.Series | None = None
    cancelled_orders: int = 0
    symbols: list[str] = field(default_factory=list)

    @property
    def returns(self) -> pd.Series:
        return self.equity["equity"].pct_change().fillna(0.0)

    def metrics(self, periods_per_year: int = 252, risk_free: float = 0.0) -> dict:
        from ..analysis.metrics import compute_metrics
        return compute_metrics(self, periods_per_year=periods_per_year, risk_free=risk_free)


class BacktestEngine:
    def __init__(self, data: dict[str, pd.DataFrame], strategy, config: BacktestConfig | None = None):
        if not data:
            raise ValueError("没有行情数据")
        self.data = data
        self.strategy = strategy
        self.config = config or BacktestConfig()
        self.lots = LotSizeTable(self.config.lot_sizes)
        self.portfolio = Portfolio(self.config.initial_cash)
        self._last_close: dict[str, float] = {}
        self._bars = {s: df.to_dict("index") for s, df in data.items()}

    def _bar(self, symbol: str, date) -> dict | None:
        bar = self._bars[symbol].get(date)
        if bar is None or bar["volume"] <= 0:
            return None
        return bar

    def run(self, start=None, end=None, benchmark: pd.Series | None = None) -> BacktestResult:
        """运行回测。start/end 之前的数据只作为指标预热，不参与交易与绩效统计。"""
        calendar = sorted(set().union(*(df.index for df in self.data.values())))
        calendar = pd.DatetimeIndex(calendar)
        trade_days = calendar
        if start is not None:
            trade_days = trade_days[trade_days >= pd.Timestamp(start)]
        if end is not None:
            trade_days = trade_days[trade_days <= pd.Timestamp(end)]
        if len(trade_days) < 2:
            raise ValueError("回测区间内交易日不足")

        # 用回测开始前的最后价格初始化估值价格
        for sym, df in self.data.items():
            before = df["close"].loc[: trade_days[0]]
            if len(before):
                self._last_close[sym] = float(before.iloc[-1])

        ctx = Context(self)
        if hasattr(self.strategy, "init"):
            self.strategy.init(self.data)

        equity_rows, pos_rows, trades = [], [], []
        pending: dict[str, Order] = {}
        cancelled = 0

        for i, date in enumerate(trade_days):
            # 1. 开盘撮合上一交易日的订单（先卖后买，卖出资金可用于买入）
            for order in sorted(pending.values(), key=lambda o: o.quantity):
                trade = self._execute(order, date)
                if trade is None:
                    cancelled += 1
                else:
                    trades.append(trade)
            pending = {}

            # 2. 收盘估值
            for sym in self.data:
                bar = self._bars[sym].get(date)
                if bar is not None:
                    self._last_close[sym] = bar["close"]
            prices = self._last_close
            mv = self.portfolio.market_value(prices)
            equity_rows.append((date, self.portfolio.cash, mv, self.portfolio.cash + mv))
            pos_rows.append({s: p.quantity for s, p in self.portfolio.positions.items()})

            # 3. 策略决策（最后一天产生的订单不会成交，不再调用）
            if i < len(trade_days) - 1:
                ctx.date, ctx.bar_index = date, i
                ctx._orders = {}
                self.strategy.on_bar(ctx)
                pending = ctx._orders

        equity = pd.DataFrame(equity_rows, columns=["date", "cash", "market_value", "equity"]).set_index("date")
        positions = pd.DataFrame(pos_rows, index=equity.index).fillna(0).astype(int)
        trades_df = pd.DataFrame([t.__dict__ for t in trades],
                                 columns=list(Trade.__dataclass_fields__.keys()))
        bench = None
        if benchmark is not None:
            b = benchmark.reindex(equity.index).ffill().bfill()
            bench = b / b.iloc[0] * self.config.initial_cash
        return BacktestResult(
            equity=equity, trades=trades_df, positions=positions, config=self.config,
            strategy_name=getattr(self.strategy, "name", type(self.strategy).__name__),
            strategy_params=dict(getattr(self.strategy, "p", {})),
            benchmark=bench, cancelled_orders=cancelled, symbols=list(self.data),
        )

    def _execute(self, order: Order, date) -> Trade | None:
        sym = order.symbol
        bar = self._bar(sym, date)
        if bar is None:
            logger.debug("%s %s 停牌/无数据，订单作废", date.date(), sym)
            return None
        lot = self.lots.get(sym)
        qty = order.quantity
        cfg = self.config

        # 不允许卖空时，卖出不超过持仓
        held = self.portfolio.quantity(sym)
        if qty < 0 and not cfg.allow_short:
            qty = -min(-qty, max(held, 0))

        # 成交量限制
        if cfg.max_volume_pct is not None:
            cap = int(bar["volume"] * cfg.max_volume_pct // lot * lot)
            qty = int(math.copysign(min(abs(qty), cap), qty))

        # 成交价：开盘价 ± 滑点（按价位），并限制在当日最高/最低价内
        price = bar["open"]
        if cfg.slippage_ticks:
            price = shift_ticks(price, cfg.slippage_ticks if qty > 0 else -cfg.slippage_ticks)
        price = float(min(max(price, bar["low"]), bar["high"]))

        fees_of = cfg.fee_model.total
        if qty > 0:
            # 资金不足时减少手数
            max_lots = int(self.portfolio.cash // (price * lot))
            lots = min(qty // lot, max_lots)
            while lots > 0 and lots * lot * price + fees_of(lots * lot * price, sym) > self.portfolio.cash + 1e-6:
                lots -= 1
            qty = lots * lot
        if qty == 0:
            return None

        value = abs(qty) * price
        fees = fees_of(value, sym)
        realized = self.portfolio.apply(sym, qty, price, fees)
        return Trade(date=date, symbol=sym, side="BUY" if qty > 0 else "SELL", quantity=abs(qty),
                     price=price, value=round(value, 2), fees=fees, realized_pnl=round(realized, 2),
                     reason=order.reason)


def run_backtest(data, strategy, config: BacktestConfig | None = None, start=None, end=None,
                 benchmark: pd.Series | None = None) -> BacktestResult:
    return BacktestEngine(data, strategy, config).run(start=start, end=end, benchmark=benchmark)


__all__ = [
    "BacktestConfig", "BacktestEngine", "BacktestResult", "Context", "Order", "Portfolio",
    "Position", "Trade", "run_backtest",
]
