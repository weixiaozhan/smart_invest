"""akshare 数据源测试：用假的 akshare 模块替代真实网络请求。"""

import sys
import types

import pandas as pd
import pytest

from smart_invest.config import Settings
from smart_invest.data import akshare_source


def _fake_raw() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "日期": ["2024-01-03", "2024-01-02"],  # 故意乱序
            "股票代码": ["600519", "600519"],
            "开盘": [1700.0, 1690.0],
            "收盘": [1710.0, 1695.0],
            "最高": [1720.0, 1700.0],
            "最低": [1695.0, 1680.0],
            "成交量": [30000, 28000],
            "成交额": [5.1e9, 4.7e9],
            "振幅": [1.5, 1.2],
            "涨跌幅": [0.88, -0.5],
            "涨跌额": [15.0, -8.5],
            "换手率": [0.24, 0.22],
        }
    )


@pytest.fixture
def fake_ak(monkeypatch, tmp_path):
    calls = []

    def stock_zh_a_hist_em(**kwargs):
        calls.append(kwargs)
        return _fake_raw()

    module = types.SimpleNamespace(stock_zh_a_hist_em=stock_zh_a_hist_em)
    monkeypatch.setitem(sys.modules, "akshare", module)
    monkeypatch.setattr(akshare_source, "settings", Settings(data_dir=tmp_path))
    return calls


def test_fetch_normalizes_columns(fake_ak):
    df = akshare_source.fetch_a_share("600519", start="2024-01-01", end="20240131")
    assert list(df.columns[:5]) == ["open", "close", "high", "low", "volume"]
    assert "股票代码" not in df.columns
    assert df.index.is_monotonic_increasing
    assert fake_ak[0] == {
        "symbol": "600519",
        "period": "daily",
        "start_date": "20240101",
        "end_date": "20240131",
        "adjust": "qfq",
    }


def test_fetch_uses_cache(fake_ak):
    first = akshare_source.fetch_a_share("600519", start="20240101", end="20240131")
    second = akshare_source.fetch_a_share("600519", start="20240101", end="20240131")
    assert len(fake_ak) == 1
    pd.testing.assert_frame_equal(first, second, check_freq=False)


def test_fetch_no_cache(fake_ak):
    for _ in range(2):
        akshare_source.fetch_a_share("600519", start="20240101", end="20240131", use_cache=False)
    assert len(fake_ak) == 2


def test_invalid_symbol(fake_ak):
    with pytest.raises(ValueError):
        akshare_source.fetch_a_share("sh600519")


def test_empty_result(monkeypatch, tmp_path):
    module = types.SimpleNamespace(stock_zh_a_hist_em=lambda **_: pd.DataFrame())
    monkeypatch.setitem(sys.modules, "akshare", module)
    monkeypatch.setattr(akshare_source, "settings", Settings(data_dir=tmp_path))
    with pytest.raises(ValueError, match="未获取到"):
        akshare_source.fetch_a_share("600519", start="20240101", end="20240131")


def test_cli_backtest_with_symbol(fake_ak, capsys):
    from smart_invest.cli import main

    main(
        [
            "backtest",
            "--symbol",
            "600519",
            "--start",
            "20240101",
            "--end",
            "20240131",
            "--short",
            "1",
            "--long",
            "2",
        ]
    )
    assert "总收益率" in capsys.readouterr().out
