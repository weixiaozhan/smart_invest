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
│   ├── data/               # 数据加载
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

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `SMART_INVEST_DATA_DIR` | `data` | 数据目录 |
| `SMART_INVEST_INITIAL_CASH` | `100000` | 初始资金 |
| `SMART_INVEST_COMMISSION` | `0.0003` | 单边手续费率 |

## 新增策略

继承 `smart_invest.strategy.Strategy`，实现 `generate_signals(prices) -> pd.Series`（1=持仓，0=空仓）即可传给 `run_backtest`。
