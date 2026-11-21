"""绩效指标。"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 指标显示名与格式：pct 百分比，num 数值，int 整数
METRIC_LABELS: dict[str, tuple[str, str]] = {
    "start": ("开始日期", "str"),
    "end": ("结束日期", "str"),
    "initial_equity": ("初始资金", "money"),
    "final_equity": ("期末权益", "money"),
    "total_return": ("总收益率", "pct"),
    "cagr": ("年化收益率", "pct"),
    "annual_volatility": ("年化波动率", "pct"),
    "sharpe": ("夏普比率", "num"),
    "sortino": ("索提诺比率", "num"),
    "max_drawdown": ("最大回撤", "pct"),
    "max_drawdown_days": ("最长回撤持续(天)", "int"),
    "calmar": ("卡玛比率", "num"),
    "best_day": ("单日最大涨幅", "pct"),
    "worst_day": ("单日最大跌幅", "pct"),
    "exposure": ("平均仓位", "pct"),
    "num_trades": ("成交笔数", "int"),
    "num_round_trips": ("平仓次数", "int"),
    "win_rate": ("胜率(按平仓)", "pct"),
    "profit_factor": ("盈亏比(总盈/总亏)", "num"),
    "avg_win": ("平均盈利(HKD)", "money"),
    "avg_loss": ("平均亏损(HKD)", "money"),
    "total_fees": ("总交易费用", "money"),
    "fees_pct": ("费用占初始资金", "pct"),
    "annual_turnover": ("年化换手率(倍)", "num"),
    "benchmark_total_return": ("基准总收益率", "pct"),
    "benchmark_cagr": ("基准年化收益率", "pct"),
    "benchmark_max_drawdown": ("基准最大回撤", "pct"),
    "excess_cagr": ("超额年化收益", "pct"),
    "alpha": ("Alpha(年化)", "pct"),
    "beta": ("Beta", "num"),
    "information_ratio": ("信息比率", "num"),
}


def max_drawdown(equity: pd.Series) -> tuple[float, int]:
    """返回 (最大回撤(负数), 最长回撤持续的自然日数)。"""
    peak = equity.cummax()
    dd = equity / peak - 1
    mdd = float(dd.min()) if len(dd) else 0.0
    longest, start = 0, None
    for date, value in dd.items():
        if value < 0 and start is None:
            start = date
        elif value >= 0 and start is not None:
            longest = max(longest, (date - start).days)
            start = None
    if start is not None:
        longest = max(longest, (dd.index[-1] - start).days)
    return mdd, longest


def drawdown_series(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1


def _cagr(equity: pd.Series) -> float:
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0 or equity.iloc[0] <= 0 or equity.iloc[-1] <= 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1)


def _safe_div(a: float, b: float) -> float:
    return float(a / b) if b and np.isfinite(b) and b != 0 else float("nan")


def compute_metrics(result, periods_per_year: int = 252, risk_free: float = 0.0) -> dict:
    eq = result.equity["equity"]
    rets = eq.pct_change().dropna()
    rf_daily = (1 + risk_free) ** (1 / periods_per_year) - 1
    excess = rets - rf_daily

    vol = float(rets.std(ddof=1) * np.sqrt(periods_per_year)) if len(rets) > 1 else 0.0
    sharpe = _safe_div(excess.mean() * periods_per_year, rets.std(ddof=1) * np.sqrt(periods_per_year))
    downside = rets[rets < 0]
    downside_dev = np.sqrt((np.minimum(excess, 0) ** 2).mean()) * np.sqrt(periods_per_year) if len(rets) else 0
    sortino = _safe_div(excess.mean() * periods_per_year, downside_dev) if len(downside) else float("nan")
    mdd, mdd_days = max_drawdown(eq)
    cagr = _cagr(eq)

    trades = result.trades
    sells = trades[trades["side"] == "SELL"] if len(trades) else trades
    wins = sells[sells["realized_pnl"] > 0]["realized_pnl"] if len(sells) else pd.Series(dtype=float)
    losses = sells[sells["realized_pnl"] <= 0]["realized_pnl"] if len(sells) else pd.Series(dtype=float)
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    traded_value = float(trades["value"].sum()) if len(trades) else 0.0
    total_fees = float(trades["fees"].sum()) if len(trades) else 0.0
    initial = float(eq.iloc[0]) if len(eq) else result.config.initial_cash

    m = {
        "start": eq.index[0].strftime("%Y-%m-%d"),
        "end": eq.index[-1].strftime("%Y-%m-%d"),
        "initial_equity": result.config.initial_cash,
        "final_equity": float(eq.iloc[-1]),
        "total_return": float(eq.iloc[-1] / result.config.initial_cash - 1),
        "cagr": cagr,
        "annual_volatility": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": mdd,
        "max_drawdown_days": mdd_days,
        "calmar": _safe_div(cagr, abs(mdd)),
        "best_day": float(rets.max()) if len(rets) else 0.0,
        "worst_day": float(rets.min()) if len(rets) else 0.0,
        "exposure": float((result.equity["market_value"] / result.equity["equity"]).mean()),
        "num_trades": int(len(trades)),
        "num_round_trips": int(len(sells)),
        "win_rate": _safe_div(len(wins), len(sells)) if len(sells) else float("nan"),
        "profit_factor": _safe_div(wins.sum(), abs(losses.sum())) if len(losses) else float("nan"),
        "avg_win": float(wins.mean()) if len(wins) else float("nan"),
        "avg_loss": float(losses.mean()) if len(losses) else float("nan"),
        "total_fees": total_fees,
        "fees_pct": total_fees / initial if initial else 0.0,
        "annual_turnover": traded_value / 2 / float(eq.mean()) / years,
    }

    if result.benchmark is not None:
        b = result.benchmark
        b_rets = b.pct_change().dropna()
        aligned = pd.concat([rets, b_rets], axis=1, join="inner").dropna()
        aligned.columns = ["s", "b"]
        bmdd, _ = max_drawdown(b)
        b_cagr = _cagr(b)
        var_b = aligned["b"].var(ddof=1)
        beta = _safe_div(aligned["s"].cov(aligned["b"]), var_b)
        diff = aligned["s"] - aligned["b"]
        ann_alpha = float("nan")
        if np.isfinite(beta):
            ann_alpha = float(((aligned["s"] - rf_daily) - beta * (aligned["b"] - rf_daily)).mean()
                              * periods_per_year)
        m.update({
            "benchmark_total_return": float(b.iloc[-1] / b.iloc[0] - 1),
            "benchmark_cagr": b_cagr,
            "benchmark_max_drawdown": bmdd,
            "excess_cagr": cagr - b_cagr,
            "alpha": ann_alpha,
            "beta": beta,
            "information_ratio": _safe_div(diff.mean() * periods_per_year,
                                           diff.std(ddof=1) * np.sqrt(periods_per_year)),
        })
    return m


def format_metric(key: str, value) -> str:
    kind = METRIC_LABELS.get(key, (key, "num"))[1]
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "-"
    if kind == "pct":
        return f"{value * 100:.2f}%"
    if kind == "money":
        return f"{value:,.2f}"
    if kind == "int":
        return f"{int(value):d}"
    if kind == "num":
        return f"{value:.2f}"
    return str(value)


def monthly_returns(equity: pd.Series) -> pd.DataFrame:
    """按年 x 月的收益率表，最后一列为全年收益。"""
    month_end = equity.resample("ME").last()
    first = pd.Series([equity.iloc[0]], index=[equity.index[0] - pd.Timedelta(days=1)])
    m = pd.concat([first, month_end]).pct_change().dropna()
    table = pd.DataFrame({"year": m.index.year, "month": m.index.month, "ret": m.values})
    pivot = table.pivot(index="year", columns="month", values="ret").reindex(columns=range(1, 13))
    year_end = equity.resample("YE").last()
    yearly = pd.concat([first, year_end]).pct_change().dropna()
    pivot["全年"] = yearly.values
    return pivot
