"""Daily public market data with explicit provenance and an offline demo."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests


class DataError(RuntimeError):
    """A requested quote cannot be retrieved or interpreted safely."""


SUPPORTED_PERIODS = {"6mo": 126, "1y": 252, "2y": 504, "5y": 1260}
PRICE_COLUMNS = ["Open", "High", "Low", "Close"]
COLUMNS = PRICE_COLUMNS + ["Volume"]
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"


def normalize_symbol(symbol: str) -> str:
    """Normalize US symbols and Hong Kong codes to Yahoo's notation."""
    if not isinstance(symbol, str):
        raise DataError("请输入美股代码（如 AAPL）或港股代码（如 0700.HK）。")
    symbol = symbol.strip().upper()
    hk_match = re.fullmatch(r"(\d{1,5})(?:\.HK)?", symbol)
    if hk_match:
        code = int(hk_match.group(1))
        if code == 0:
            raise DataError("港股代码不能为 0。")
        return f"{code:04d}.HK"
    # Yahoo uses a hyphen for US share classes, e.g. BRK-B.
    if re.fullmatch(r"[A-Z][A-Z0-9]{0,10}\.[AB]", symbol):
        symbol = symbol.replace(".", "-")
    if not re.fullmatch(r"[A-Z][A-Z0-9-]{0,14}", symbol):
        raise DataError("代码格式无效：支持美股 AAPL / BRK-B 和港股 0700.HK / 9988.HK。")
    return symbol


def _period(period: str) -> str:
    if period not in SUPPORTED_PERIODS:
        raise DataError("历史区间仅支持 6mo、1y、2y、5y。")
    return period


def fetch_history(symbol: str, period: str = "1y") -> pd.DataFrame:
    """Fetch daily Yahoo data; network failure never substitutes demo prices.

    Prices use Adj Close and the same adjustment factor for O/H/L when
    available. Volume is the provider's original volume, without adjustment.
    Dates are trading dates in the exchange timezone, then made timezone-naive.
    """
    symbol = normalize_symbol(symbol)
    period = _period(period)
    try:
        response = requests.get(
            CHART_URL + quote(symbol, safe=""),
            params={
                "range": period,
                "interval": "1d",
                "includeAdjustedClose": "true",
                "events": "div,splits",
            },
            headers={"User-Agent": "SmartInvest/0.1", "Accept": "application/json"},
            timeout=(5, 20),
            allow_redirects=False,
        )
    except requests.Timeout as exc:
        raise DataError("行情请求超时，请稍后重试或检查 Yahoo Finance 的网络访问。") from exc
    except requests.RequestException as exc:
        raise DataError("无法连接 Yahoo Finance，请检查网络、证书及域名访问配置。") from exc
    if response.status_code == 429:
        raise DataError("Yahoo Finance 请求过于频繁，请稍后重试。")
    if response.status_code in (401, 403):
        raise DataError("Yahoo Finance 拒绝访问；公开接口可能受限，请稍后重试并检查网络。")
    if response.status_code != 200:
        raise DataError(f"Yahoo Finance 返回 HTTP {response.status_code}，未获取行情。")
    try:
        payload = response.json()
    except ValueError as exc:
        raise DataError("Yahoo Finance 返回了无法解析的行情响应。") from exc
    return _parse_history(payload, symbol)


def _parse_history(payload: object, symbol: str) -> pd.DataFrame:
    try:
        chart = payload["chart"]
        if not isinstance(chart, dict):
            raise TypeError
        if chart.get("error"):
            raise DataError(f"Yahoo Finance 没有返回 {symbol} 的行情，请确认代码和交易所。")
        results = chart["result"]
        if not isinstance(results, list) or not results:
            raise DataError(f"未找到 {symbol} 的历史行情。")
        result = results[0]
        meta = result["meta"]
        timestamps = result["timestamp"]
        quote_data = result["indicators"]["quote"][0]
        if not isinstance(meta, dict) or not isinstance(timestamps, list) or not timestamps:
            raise TypeError
        if not isinstance(quote_data, dict):
            raise TypeError
        if any(
            not isinstance(quote_data.get(column.lower()), list)
            or len(quote_data[column.lower()]) != len(timestamps)
            for column in COLUMNS
        ):
            raise DataError("Yahoo Finance 的行情字段长度不一致，未使用该数据。")
        exchange_timezone = meta.get("exchangeTimezoneName") or "UTC"
        dates = pd.to_datetime(timestamps, unit="s", utc=True, errors="coerce")
        dates = dates.tz_convert(exchange_timezone).normalize().tz_localize(None)
        raw = pd.DataFrame(
            {column: quote_data[column.lower()] for column in COLUMNS},
            index=pd.DatetimeIndex(dates, name="Date"),
        )
        for column in COLUMNS:
            raw[column] = pd.to_numeric(raw[column], errors="coerce")
        raw = raw.replace([np.inf, -np.inf], np.nan)
        valid = raw.index.notna() & raw[PRICE_COLUMNS].notna().all(axis=1)
        valid &= raw[PRICE_COLUMNS].gt(0).all(axis=1)
        valid &= raw["High"].ge(raw[["Open", "Close", "Low"]].max(axis=1))
        valid &= raw["Low"].le(raw[["Open", "Close", "High"]].min(axis=1))
        raw.loc[raw["Volume"].lt(0), "Volume"] = np.nan
        adjusted_series = result["indicators"].get("adjclose", [])
        adj_values = adjusted_series[0].get("adjclose") if adjusted_series else None
        adjusted = None
        if adj_values is not None:
            if not isinstance(adj_values, list) or len(adj_values) != len(timestamps):
                raise DataError("Yahoo Finance 的调整价格字段长度不一致，未使用该数据。")
            adjusted = pd.to_numeric(pd.Series(adj_values, index=raw.index), errors="coerce")
            adjusted = adjusted.replace([np.inf, -np.inf], np.nan)
        # A partial adjustment series must not mix adjusted and raw prices.
        use_adjusted = adjusted is not None and bool((adjusted.gt(0) & valid).any())
        if use_adjusted:
            valid &= adjusted.notna() & adjusted.gt(0)
            factor = adjusted / raw["Close"]
            raw[PRICE_COLUMNS] = raw[PRICE_COLUMNS].mul(factor, axis=0)
            raw["Close"] = adjusted
        frame = raw.loc[valid].copy()
        frame = frame.loc[~frame.index.duplicated(keep="last")].sort_index()
    except DataError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, OverflowError) as exc:
        raise DataError("Yahoo Finance 的行情字段不完整或格式异常，未使用该数据。") from exc
    if frame.empty:
        raise DataError(f"{symbol} 在所选区间内没有可用的日行情。")
    frame.attrs.update(
        source="Yahoo Finance",
        symbol=symbol,
        currency=meta.get("currency") or "未知",
        exchange=meta.get("fullExchangeName") or meta.get("exchangeName") or "未知",
        timezone=exchange_timezone,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        price_basis="adjusted" if use_adjusted else "unadjusted",
        price_basis_description=(
            "Yahoo Adj Close 调整口径（拆股与分红），OHLC 使用相同调整因子；成交量未调整。"
            if use_adjusted
            else "未调整价格：数据源未提供可用 Adj Close，收益未包含分红调整。"
        ),
        dropped_rows=len(timestamps) - len(frame),
        is_demo=False,
    )
    return frame


def load_demo_history(symbol: str = "AAPL", period: str = "1y") -> pd.DataFrame:
    """Explicit, reproducible synthetic prices, never a real market-data fallback."""
    symbol = normalize_symbol(symbol)
    count = SUPPORTED_PERIODS[_period(period)]
    seed = int.from_bytes(hashlib.sha256(symbol.encode("utf-8")).digest()[:4], "big")
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end="2025-12-31", periods=count, name="Date")
    close = 100 * np.exp(np.cumsum(rng.normal(0.00025, 0.013, count)))
    opening = np.r_[100, close[:-1]] * np.exp(rng.normal(0, 0.004, count))
    spread = rng.uniform(0.001, 0.014, count)
    frame = pd.DataFrame(
        {
            "Open": opening,
            "High": np.maximum(opening, close) * (1 + spread),
            "Low": np.minimum(opening, close) * (1 - spread),
            "Close": close,
            "Volume": rng.integers(800_000, 6_000_000, count),
        },
        index=dates,
    )
    frame.attrs.update(
        source="合成演示数据",
        symbol=symbol,
        currency="HKD" if symbol.endswith(".HK") else "USD",
        exchange="模拟市场",
        timezone="UTC",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        price_basis="synthetic",
        price_basis_description="人工合成数据，仅供演示，不代表该股票的历史行情或实际收益。",
        dropped_rows=0,
        is_demo=True,
    )
    return frame
