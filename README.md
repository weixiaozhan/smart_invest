# smart_invest —— 港股量化策略回测系统

一个针对港股市场规则设计的日线级别事件驱动回测框架，内置常用策略、完整费用模型、绩效分析、HTML 报告和参数优化。

## 功能

| 模块 | 说明 |
|---|---|
| **港股规则** | 整手交易（每手股数表，可自定义）、港交所价位表、滑点以价位计、停牌订单作废、默认禁止卖空 |
| **费用模型** | 佣金（含最低收费）、平台费、印花税 0.1%（向上取整，ETF 豁免）、交易费、证监会征费、会财局征费、交收费 |
| **回测引擎** | 信号在 T 日收盘产生、T+1 开盘成交，杜绝未来函数；先卖后买；资金不足自动减手数；支持指标预热期 |
| **数据源** | akshare（东方财富，前复权）、Yahoo Finance、本地 CSV、模拟数据（离线演示），自动本地缓存 |
| **内置策略** | 双均线、布林带均值回归、动量轮动、RSI、买入持有 |
| **绩效分析** | 年化收益、波动率、夏普、索提诺、最大回撤及持续期、卡玛、胜率、盈亏比、换手率、费用，及相对基准的 Alpha / Beta / 信息比率 |
| **报告** | HTML 报告（净值/回撤曲线、月度收益热力表、成交明细），并导出 equity.csv / trades.csv / metrics.json |
| **参数优化** | 网格搜索 + 样本外检验（默认后 30% 时间做 holdout），降低过拟合风险 |

## 安装

```bash
pip install -e .              # 基础功能
pip install -e ".[akshare]"   # 使用 akshare 获取真实港股数据（推荐）
pip install -e ".[yahoo]"     # 使用 Yahoo Finance
pip install -e ".[dev]"       # 运行测试
```

需要 Python 3.10+。

## 快速开始

```bash
# 查看内置策略与参数
smart-invest strategies

# 双均线策略回测腾讯、阿里、汇丰，对比盈富基金(02800)
smart-invest backtest -S sma_cross -s 0700 9988 0005 -p fast=20 slow=60 \
    --start 2019-01-01 --end 2024-12-31 --source akshare

# 动量轮动：每 20 个交易日在股票池中选 60 日涨幅前 2 名
smart-invest backtest -S momentum -s 0700 9988 3690 1299 0005 0388 0941 \
    -p lookback=60 top_k=2 rebalance_days=20 --source akshare

# 参数优化（前 70% 时间选参，后 30% 检验）
smart-invest optimize -S sma_cross -s 0700 9988 0005 \
    -g fast=5,10,20,30 slow=40,60,120 --sort-by sharpe --source akshare
```

报告默认输出到 `reports/<策略名>_<时间>/report.html`，也可用 `-o` 指定目录。也可以用 `python -m smart_invest ...` 代替 `smart-invest`。

> 不指定 `--source` 时默认使用 **模拟数据**（synthetic），无需联网即可体验全部功能，但结果不代表真实行情。

### 常用参数

| 参数 | 说明 | 默认 |
|---|---|---|
| `-s/--symbols` | 股票代码，支持 `700`、`0700`、`00700`、`0700.HK` | 必填 |
| `--source` | `synthetic` / `akshare` / `yahoo` / `csv` | synthetic |
| `--data-dir` | csv 数据源目录（文件名如 `00700.csv`） | |
| `-b/--benchmark` | 基准代码，`none` 表示不用 | 02800 |
| `--cash` | 初始资金（港元） | 1,000,000 |
| `--commission` / `--min-commission` / `--platform-fee` | 券商收费 | 0.03% / 3 / 0 |
| `--no-fees` | 不计任何费用（对比用） | |
| `--slippage-ticks` | 滑点价位数 | 1 |
| `--max-volume-pct` | 单笔成交上限占当日成交量比例 | 不限 |
| `--lot` | 自定义每手股数，如 `--lot 01810=200` | 内置表，未知为 100 |
| `--warmup-days` | 开始日前额外加载的天数，用于均线等指标预热 | 400 |

### 下载数据到本地

```bash
smart-invest fetch -s 0700 9988 0005 --source akshare --start 2015-01-01 -o data/
smart-invest backtest -S rsi -s 0700 --source csv --data-dir data/ -b none
```

## 编写自定义策略

继承 `Strategy`，在 `init` 中向量化计算指标（只能用历史数据，如 `rolling`、`shift`），在 `on_bar` 中下单：

```python
from smart_invest import Strategy, make_source, run_backtest, save_report

class MyStrategy(Strategy):
    name = "my_strategy"
    params = {"window": 20}

    def init(self, data):
        self.ma = {s: df["close"].rolling(self.p["window"]).mean() for s, df in data.items()}

    def on_bar(self, ctx):
        for s in ctx.available:
            above = ctx.price(s) > self.ma[s].get(ctx.date, float("inf"))
            if above and ctx.position(s) == 0:
                ctx.order_target_weight(s, 1 / len(ctx.symbols), reason="站上均线")
            elif not above and ctx.position(s) > 0:
                ctx.order_target_shares(s, 0, reason="跌破均线")

source = make_source("akshare")
data = source.load(["00700", "09988"], "2018-01-01", "2024-12-31")
result = run_backtest(data, MyStrategy(window=30), start="2019-01-01")
print(result.metrics())
save_report(result, "reports/my_strategy")
```

`ctx` 提供的接口：

- 数据：`ctx.date`、`ctx.symbols`、`ctx.available`、`ctx.price(s)`、`ctx.history(s, field, window)`、`ctx.has_bar(s)`
- 账户：`ctx.cash`、`ctx.equity`、`ctx.position(s)`、`ctx.positions`、`ctx.weight(s)`、`ctx.lot_size(s)`
- 下单：`order_shares`、`order_target_shares`、`order_target_weight`、`order_target_weights(weights, tolerance)`、`cancel_all`

用 `@register` 装饰后，策略即可通过命令行 `-S my_strategy` 调用。完整示例见 [`examples/custom_strategy.py`](examples/custom_strategy.py)（波动率加权的通道突破策略）。

## 项目结构

```
smart_invest/
├── market/hk.py          港股规则：价位表、每手股数、费用模型
├── data/sources.py       数据源（akshare / yahoo / csv / synthetic）与缓存
├── engine/backtest.py    回测引擎、组合、订单撮合、策略上下文
├── strategy/             策略基类、注册表、内置策略
├── analysis/             绩效指标、HTML 报告、参数优化
└── cli.py                命令行
tests/                    单元测试与端到端测试（pytest）
examples/                 自定义策略示例
```

## 注意事项

- **复权**：akshare 默认前复权、Yahoo 使用 auto_adjust，均已处理分红拆股，但回测结果会与"不复权价格 + 现金分红"的真实账户略有差异。
- **每手股数**会因公司行动变化，内置表只覆盖常见股票，请以券商/港交所公布为准，必要时用 `--lot` 覆盖。
- **费率**按编写时的公开标准设置，监管调整后可通过 `HKFeeModel` 参数修改。
- **幸存者偏差**：只回测今天仍上市的股票会高估收益。
- 交易日历取所有股票数据日期的并集，模拟数据按工作日生成，不含港股假期。
- 回测结果仅供研究，不构成投资建议。

## 测试

```bash
pytest
```

## License

Apache-2.0
