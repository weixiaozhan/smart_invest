# smart_invest

量化投资研究与回测工具。

## 目录结构

```
smart_invest/
├── pyproject.toml          # 项目配置与依赖
├── data/                   # 本地行情数据（CSV，不纳入版本管理）
├── src/smart_invest/
│   ├── config.py           # 全局配置（支持环境变量覆盖）
│   ├── cli.py              # 命令行入口
│   ├── data/               # 数据加载（本地 CSV / akshare）
│   ├── strategy/           # 策略（基类 + 双均线示例）
│   └── backtest/           # 回测引擎与绩效指标
└── tests/                  # 单元测试
```

## 快速开始

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                                   # 运行测试
smart-invest backtest data/xxx.csv       # 运行回测（CSV 需包含 date、close 列）
```

## 获取真实行情（akshare）

```bash
pip install -e ".[akshare]"

# 下载贵州茅台前复权日线，并另存为 CSV
smart-invest fetch 600519 --start 20200101 --end 20241231 -o data/600519.csv

# 直接拉取行情并回测
smart-invest backtest --symbol 600519 --start 20200101 --short 5 --long 20
```

- 数据来自东方财富（`akshare.stock_zh_a_hist_em`），需要能访问 `push2his.eastmoney.com`。
- 默认前复权（`--adjust qfq`），可选 `hfq` 后复权、`none` 不复权；`--period` 支持 daily / weekly / monthly。
- 下载结果会缓存在 `data/akshare/`，相同参数再次调用直接读缓存；加 `--no-cache` 强制重新下载。
- 列名统一为英文：`open high low close volume(手) amount(元) amplitude pct_change change turnover`。

Python 中使用：

```python
from smart_invest.data import fetch_a_share

df = fetch_a_share("600519", start="2020-01-01", end="2024-12-31", adjust="qfq")
```

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `SMART_INVEST_DATA_DIR` | `data` | 数据目录 |
| `SMART_INVEST_INITIAL_CASH` | `100000` | 初始资金 |
| `SMART_INVEST_COMMISSION` | `0.0003` | 单边手续费率 |

## 新增策略

继承 `smart_invest.strategy.Strategy`，实现 `generate_signals(prices) -> pd.Series`（1=持仓，0=空仓）即可传给 `run_backtest`。
