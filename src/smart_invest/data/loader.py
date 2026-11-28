"""行情数据加载。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = {"date", "close"}


def load_prices_csv(path: str | Path) -> pd.DataFrame:
    """从 CSV 读取行情，至少包含 date、close 两列，返回以日期为索引、按时间升序的 DataFrame。"""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"CSV 缺少必需列: {sorted(missing)}")
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()
