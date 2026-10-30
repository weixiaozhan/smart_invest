"""Portfolio valuation, risk and rebalancing from daily close histories.

Amounts are converted to one base currency (USD or HKD) with a user-supplied,
constant USD/HKD rate. Portfolio risk statistics hold today's weights fixed over
the whole window (daily rebalanced) and ignore currency movements, so they
describe the current mix historically rather than the account's actual path.
"""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Real

import numpy as np
import pandas as pd

from .analytics import TRADING_DAYS_PER_YEAR
from .data import DataError, normalize_symbol

BASE_CURRENCIES = ("USD", "HKD")
MIN_COMMON_DAYS = 30


class PortfolioError(ValueError):
    """Holdings or portfolio inputs are unusable."""


@dataclass(frozen=True)
class Holding:
    symbol: str
    shares: float
    cost: float | None  # average cost per share in the symbol's own currency


def _positive(value, name: str, allow_none: bool = False) -> float | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        if allow_none:
            return None
        raise PortfolioError(f"{name} 不能为空。")
    if isinstance(value, bool) or not isinstance(value, Real) or not np.isfinite(value) or value <= 0:
        raise PortfolioError(f"{name} 必须是大于 0 的有限数字。")
    return float(value)


def parse_holdings(rows) -> list[Holding]:
    """Validate rows of {symbol, shares, cost}; merge duplicates by average cost."""
    merged: dict[str, list] = {}
    for row in rows:
        symbol = row.get("symbol")
        if symbol is None or (isinstance(symbol, float) and np.isnan(symbol)) or not str(symbol).strip():
            continue  # blank spreadsheet rows
        try:
            symbol = normalize_symbol(str(symbol))
        except DataError as exc:
            raise PortfolioError(f"持仓代码无效：{symbol}") from exc
        shares = _positive(row.get("shares"), f"{symbol} 的股数")
        cost = _positive(row.get("cost"), f"{symbol} 的成本价", allow_none=True)
        entry = merged.setdefault(symbol, [0.0, 0.0, True])
        entry[0] += shares
        if cost is None:
            entry[2] = False
        else:
            entry[1] += shares * cost
    if not merged:
        raise PortfolioError("请至少输入一只持仓。")
    return [
        Holding(symbol, shares, total_cost / shares if all_costs else None)
        for symbol, (shares, total_cost, all_costs) in merged.items()
    ]


def fx_to_base(currency: str, base: str, usd_hkd: float) -> float:
    """Multiplier turning one unit of `currency` into `base` using HKD per USD."""
    if base not in BASE_CURRENCIES:
        raise PortfolioError("基准货币仅支持 USD 或 HKD。")
    usd_hkd = _positive(usd_hkd, "美元兑港元汇率")
    if currency not in BASE_CURRENCIES:
        raise PortfolioError(f"暂不支持 {currency} 计价的标的，无法换算为 {base}。")
    if currency == base:
        return 1.0
    return usd_hkd if currency == "USD" else 1.0 / usd_hkd


def value_positions(holdings, histories, base: str = "USD", usd_hkd: float = 7.8) -> pd.DataFrame:
    """One row per holding: latest price, market value, weight and unrealized P&L."""
    rows = []
    for holding in holdings:
        history = histories.get(holding.symbol)
        if history is None or history.empty:
            raise PortfolioError(f"缺少 {holding.symbol} 的行情。")
        currency = history.attrs.get("currency") or ("HKD" if holding.symbol.endswith(".HK") else "USD")
        factor = fx_to_base(currency, base, usd_hkd)
        price = float(history["Close"].iloc[-1])
        value = holding.shares * price * factor
        cost_value = None if holding.cost is None else holding.shares * holding.cost * factor
        rows.append(
            {
                "symbol": holding.symbol,
                "shares": holding.shares,
                "currency": currency,
                "price": price,
                "value": value,
                "cost_value": cost_value,
                "pnl": None if cost_value is None else value - cost_value,
                "pnl_pct": None if cost_value is None else value / cost_value - 1,
            }
        )
    table = pd.DataFrame(rows).set_index("symbol")
    table["weight"] = table["value"] / table["value"].sum()
    return table


def returns_matrix(histories: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Daily returns on dates where every symbol traded (US/HK calendars differ)."""
    closes = pd.DataFrame({symbol: h["Close"] for symbol, h in histories.items()})
    returns = closes.dropna().pct_change(fill_method=None).dropna()
    if len(returns) < MIN_COMMON_DAYS:
        raise PortfolioError(f"各标的共同交易日不足 {MIN_COMMON_DAYS} 天，请延长研究区间或减少标的。")
    return returns


def concentration(weights: pd.Series) -> dict:
    """Top weight, Herfindahl index and the equivalent number of equal positions."""
    hhi = float((weights**2).sum())
    return {"top_symbol": str(weights.idxmax()), "top_weight": float(weights.max()), "hhi": hhi, "effective_n": 1.0 / hhi}


def portfolio_risk(weights: pd.Series, returns: pd.DataFrame) -> dict:
    """Volatility, drawdown, return, risk contributions and correlations."""
    weights = weights.reindex(returns.columns)
    if weights.isna().any():
        raise PortfolioError("权重与行情标的不一致。")
    series = returns.mul(weights, axis=1).sum(axis=1)
    curve = (1.0 + series).cumprod()
    covariance = returns.cov() * TRADING_DAYS_PER_YEAR
    variance = float(weights @ covariance @ weights)
    volatility = float(np.sqrt(variance))
    contribution = weights * (covariance @ weights) / variance if variance > 0 else weights * 0.0
    return {
        "volatility": volatility,
        "total_return": float(curve.iloc[-1] - 1),
        "max_drawdown": float((curve / curve.cummax() - 1).min()),
        "curve": curve,
        "risk_contribution": contribution,
        "correlation": returns.corr(),
        "observations": len(series),
    }


def target_weights(method: str, symbols, returns: pd.DataFrame | None = None, custom: dict | None = None) -> pd.Series:
    """Target weights: equal, inverse volatility, or user-given (normalized)."""
    symbols = list(symbols)
    if method == "等权":
        return pd.Series(1.0 / len(symbols), index=symbols)
    if method == "反向波动率":
        if returns is None:
            raise PortfolioError("反向波动率需要历史收益。")
        inverse = 1.0 / returns[symbols].std(ddof=1).replace(0, np.nan)
        if inverse.isna().any():
            raise PortfolioError("存在零波动或缺失波动的标的，无法按反向波动率配置。")
        return inverse / inverse.sum()
    if method == "自定义":
        raw = pd.Series({s: (custom or {}).get(s, 0.0) for s in symbols}, dtype=float)
        if raw.isna().any() or (raw < 0).any() or raw.sum() <= 0:
            raise PortfolioError("自定义目标权重必须非负且合计大于 0。")
        return raw / raw.sum()
    raise PortfolioError(f"未知的配置方法：{method}")


def rebalance_plan(positions: pd.DataFrame, targets: pd.Series, band: float = 0.05) -> pd.DataFrame:
    """Share changes that bring weights to target; drift within `band` is held.

    Ignores lot sizes, fees, taxes and liquidity. Share counts are fractional.
    """
    if band < 0 or band >= 1:
        raise PortfolioError("再平衡容忍带需在 0 到 1 之间。")
    total = positions["value"].sum()
    plan = pd.DataFrame(index=positions.index)
    plan["current_weight"] = positions["weight"]
    plan["target_weight"] = targets.reindex(positions.index).fillna(0.0)
    plan["drift"] = plan["current_weight"] - plan["target_weight"]
    plan["trade_value"] = -plan["drift"] * total
    plan["trade_shares"] = plan["trade_value"] / (positions["value"] / positions["shares"])
    plan["action"] = np.where(
        plan["drift"].abs() <= band, "持有", np.where(plan["drift"] > 0, "减仓", "加仓")
    )
    plan.loc[plan["action"] == "持有", ["trade_value", "trade_shares"]] = 0.0
    return plan
