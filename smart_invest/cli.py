"""命令行入口：python -m smart_invest <命令> ...

  strategies   列出内置策略及参数
  backtest     运行回测并生成报告
  optimize     参数网格搜索（可做样本外检验）
  fetch        下载行情数据保存为 CSV
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from .analysis import grid_search, save_report, summary_text, walk_forward_split
from .data import make_source, normalize_symbol
from .engine import BacktestConfig, run_backtest
from .market import HKFeeModel
from .strategy import STRATEGIES, get_strategy

SYNTHETIC_NOTE = "注意：本报告使用模拟行情（synthetic）生成，仅用于演示系统功能，不代表任何真实股票的表现。"


def _kv(items: list[str] | None, what: str) -> dict[str, str]:
    out = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"{what} 格式应为 key=value，收到: {item}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--symbols", "-s", nargs="+", required=True, help="港股代码，如 0700 9988 00005 0700.HK")
    p.add_argument("--start", default="2019-01-01", help="回测开始日期 YYYY-MM-DD")
    p.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"), help="回测结束日期")
    p.add_argument("--source", default="synthetic", choices=["synthetic", "csv", "yahoo", "akshare"],
                   help="数据源（默认 synthetic 模拟数据）")
    p.add_argument("--data-dir", help="csv 数据源的目录")
    p.add_argument("--no-cache", action="store_true", help="不使用本地数据缓存")
    p.add_argument("--seed", type=int, default=42, help="synthetic 数据随机种子")
    p.add_argument("--warmup-days", type=int, default=400, help="开始日期前额外加载的自然日数，用于指标预热")
    p.add_argument("--benchmark", "-b", default="02800", help="基准代码（默认 02800 盈富基金），none 表示不用")
    p.add_argument("--cash", type=float, default=1_000_000, help="初始资金（港元）")
    p.add_argument("--commission", type=float, default=0.0003, help="佣金率，默认 0.0003 (0.03%%)")
    p.add_argument("--min-commission", type=float, default=3.0, help="最低佣金（港元）")
    p.add_argument("--platform-fee", type=float, default=0.0, help="每笔平台费（港元）")
    p.add_argument("--no-fees", action="store_true", help="不计任何交易费用")
    p.add_argument("--slippage-ticks", type=int, default=1, help="滑点（价位数）")
    p.add_argument("--max-volume-pct", type=float, help="单笔成交不超过当日成交量的比例，如 0.1")
    p.add_argument("--lot", nargs="*", help="自定义每手股数，如 00700=100 01810=200")


def _load(args):
    symbols = [normalize_symbol(s) for s in args.symbols]
    source = make_source(args.source, data_dir=args.data_dir, cache=not args.no_cache, seed=args.seed)
    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    if start >= end:
        raise SystemExit("开始日期必须早于结束日期")
    load_start = start - pd.Timedelta(days=args.warmup_days)
    data = source.load(symbols, load_start, end)
    bench = None
    if args.benchmark and args.benchmark.lower() != "none":
        bcode = normalize_symbol(args.benchmark)
        bench = data[bcode]["close"] if bcode in data else source.load([bcode], load_start, end)[bcode]["close"]
    return data, bench, start, end


def _config(args) -> BacktestConfig:
    if args.no_fees:
        fee = HKFeeModel.zero()
    else:
        fee = HKFeeModel(commission_rate=args.commission, min_commission=args.min_commission,
                         platform_fee=args.platform_fee)
    lots = {normalize_symbol(k): int(v) for k, v in _kv(args.lot, "--lot").items()}
    return BacktestConfig(initial_cash=args.cash, fee_model=fee, lot_sizes=lots,
                          slippage_ticks=args.slippage_ticks, max_volume_pct=args.max_volume_pct)


def cmd_strategies(args) -> int:
    for name, cls in sorted(STRATEGIES.items()):
        params = ", ".join(f"{k}={v}" for k, v in cls.params.items()) or "无参数"
        print(f"{name:<12} {cls.description}\n{'':<12} 参数: {params}\n")
    return 0


def cmd_backtest(args) -> int:
    strategy = get_strategy(args.strategy, **_kv(args.param, "--param"))
    data, bench, start, end = _load(args)
    result = run_backtest(data, strategy, _config(args), start=start, end=end, benchmark=bench)
    metrics = result.metrics()
    print(summary_text(result, metrics))
    note = SYNTHETIC_NOTE if args.source == "synthetic" else ""
    out = Path(args.out) if args.out else Path("reports") / f"{strategy.name}_{pd.Timestamp.now():%Y%m%d_%H%M%S}"
    path = save_report(result, out, metrics, data_note=note)
    print(f"\n报告已生成: {path}")
    if args.source == "synthetic":
        print(SYNTHETIC_NOTE)
    return 0


def _parse_grid(items: list[str]) -> dict[str, list[str]]:
    grid = {}
    for k, v in _kv(items, "--grid").items():
        grid[k] = [x.strip() for x in v.split(",") if x.strip()]
    if not grid:
        raise SystemExit("请用 --grid 指定参数网格，如 --grid fast=10,20 slow=60,120")
    return grid


def cmd_optimize(args) -> int:
    grid = _parse_grid(args.grid)
    data, bench, start, end = _load(args)
    config = _config(args)
    pd.set_option("display.width", 200)
    fmt = {c: "{:.2%}".format for c in ["total_return", "cagr", "max_drawdown", "win_rate", "excess_cagr"]}

    def show(df):
        print(df.head(args.top).to_string(formatters={k: v for k, v in fmt.items() if k in df}, index=False))

    if args.holdout:
        split = walk_forward_split(start, end, 1 - args.holdout)
        print(f"样本内: {start:%Y-%m-%d} ~ {split:%Y-%m-%d}   样本外: {split:%Y-%m-%d} ~ {end:%Y-%m-%d}\n")
        ins = grid_search(data, args.strategy, grid, config, start, split, bench, args.sort_by)
        print(f"【样本内】按 {args.sort_by} 排序前 {args.top} 名:")
        show(ins)
        best = {k: ins.iloc[0][k] for k in grid}
        best = get_strategy(args.strategy, **best).p
        oos = grid_search(data, args.strategy, {k: [v] for k, v in best.items()}, config,
                          split, end, bench, args.sort_by)
        print(f"\n【样本外】最优参数 {best} 的表现:")
        show(oos)
        table = ins
    else:
        table = grid_search(data, args.strategy, grid, config, start, end, bench, args.sort_by)
        print(f"按 {args.sort_by} 排序前 {args.top} 名:")
        show(table)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(args.out, index=False)
        print(f"\n完整结果已保存: {args.out}")
    if args.source == "synthetic":
        print("\n" + SYNTHETIC_NOTE)
    return 0


def cmd_fetch(args) -> int:
    source = make_source(args.source, data_dir=None, cache=False, seed=args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    data = source.load(args.symbols, args.start, args.end)
    for code, df in data.items():
        path = out / f"{code}.csv"
        df.reset_index().to_csv(path, index=False)
        print(f"{code}: {len(df)} 条 {df.index[0]:%Y-%m-%d} ~ {df.index[-1]:%Y-%m-%d} -> {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="smart_invest", description="港股量化策略回测系统")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("strategies", help="列出内置策略").set_defaults(func=cmd_strategies)

    bt = sub.add_parser("backtest", help="运行回测")
    bt.add_argument("--strategy", "-S", required=True, help="策略名，见 strategies 命令")
    bt.add_argument("--param", "-p", nargs="*", help="策略参数，如 fast=10 slow=50")
    bt.add_argument("--out", "-o", help="报告输出目录")
    _add_common(bt)
    bt.set_defaults(func=cmd_backtest)

    op = sub.add_parser("optimize", help="参数网格搜索")
    op.add_argument("--strategy", "-S", required=True)
    op.add_argument("--grid", "-g", nargs="+", required=True, help="参数网格，如 fast=5,10,20 slow=60,120")
    op.add_argument("--sort-by", default="sharpe", help="排序指标（sharpe/cagr/calmar/total_return...）")
    op.add_argument("--holdout", type=float, default=0.3,
                    help="样本外比例（默认 0.3，即后 30%% 时间做检验；0 表示不做）")
    op.add_argument("--top", type=int, default=10)
    op.add_argument("--out", "-o", help="把结果表保存为 CSV")
    _add_common(op)
    op.set_defaults(func=cmd_optimize)

    fe = sub.add_parser("fetch", help="下载行情为 CSV")
    fe.add_argument("--symbols", "-s", nargs="+", required=True)
    fe.add_argument("--start", default="2015-01-01")
    fe.add_argument("--end", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    fe.add_argument("--source", default="akshare", choices=["synthetic", "yahoo", "akshare"])
    fe.add_argument("--seed", type=int, default=42)
    fe.add_argument("--out", "-o", default="data")
    fe.set_defaults(func=cmd_fetch)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    try:
        return args.func(args)
    except (ValueError, KeyError, FileNotFoundError, ImportError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
