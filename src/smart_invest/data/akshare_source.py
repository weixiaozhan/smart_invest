"""通过 akshare 获取 A 股真实行情（数据来自东方财富）。

akshare 是可选依赖，安装方式：pip install -e ".[akshare]"
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd

from smart_invest.config import settings

Period = Literal["daily", "weekly", "monthly"]
Adjust = Literal["", "qfq", "hfq"]

# akshare 返回的中文列名 -> 项目内统一的英文列名
COLUMN_MAP = {
    "日期": "date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",  # 单位：手
    "成交额": "amount",  # 单位：元
    "振幅": "amplitude",  # 单位：%
    "涨跌幅": "pct_change",  # 单位：%
    "涨跌额": "change",
    "换手率": "turnover",  # 单位：%
}


def _import_akshare():
    try:
        import akshare as ak
    except ImportError as e:  # pragma: no cover - 取决于运行环境
        raise ImportError('未安装 akshare，请运行: pip install -e ".[akshare]"') from e
    return ak


def _normalize_date(value: str | date | None, default: str) -> str:
    if value is None:
        return default
    return pd.Timestamp(value).strftime("%Y%m%d")


def cache_path(symbol: str, period: str, adjust: str, start: str, end: str) -> Path:
    adj = adjust or "none"
    return settings.data_dir / "akshare" / f"{symbol}_{period}_{adj}_{start}_{end}.csv"


def fetch_a_share(
    symbol: str,
    start: str | date | None = None,
    end: str | date | None = None,
    period: Period = "daily",
    adjust: Adjust = "qfq",
    use_cache: bool = True,
) -> pd.DataFrame:
    """获取单只 A 股历史行情。

    Args:
        symbol: 6 位股票代码，不带市场前缀，如 "600519"。
        start / end: 起止日期，支持 "20240101"、"2024-01-01" 或 date；默认取全部历史。
        period: daily / weekly / monthly。
        adjust: "" 不复权，"qfq" 前复权（默认，适合回测），"hfq" 后复权。
        use_cache: 是否读写本地 CSV 缓存（位于 data_dir/akshare/）。

    Returns:
        以 date 为索引、按时间升序的 DataFrame，列为 open/high/low/close/volume 等英文名。
    """
    symbol = symbol.strip()
    if not (len(symbol) == 6 and symbol.isdigit()):
        raise ValueError(f"股票代码应为 6 位数字，例如 600519，收到: {symbol!r}")
    start_s = _normalize_date(start, "19700101")
    end_s = _normalize_date(end, pd.Timestamp.today().strftime("%Y%m%d"))

    path = cache_path(symbol, period, adjust, start_s, end_s)
    if use_cache and path.exists():
        return pd.read_csv(path, index_col="date", parse_dates=["date"])

    ak = _import_akshare()
    raw = ak.stock_zh_a_hist_em(
        symbol=symbol, period=period, start_date=start_s, end_date=end_s, adjust=adjust
    )
    if raw is None or raw.empty:
        raise ValueError(f"未获取到 {symbol} 在 {start_s}~{end_s} 的行情数据，请检查代码或日期范围")

    df = raw.rename(columns=COLUMN_MAP)
    df = df[[c for c in COLUMN_MAP.values() if c in df.columns]]
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    if use_cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path)
    return df
