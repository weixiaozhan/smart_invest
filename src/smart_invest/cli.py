"""命令行入口。

smart-invest fetch 600519 --start 20200101 --end 20241231
smart-invest backtest --symbol 600519 --start 20200101
smart-invest backtest data/prices.csv
"""

from __future__ import annotations

import argparse

from smart_invest.backtest import run_backtest
from smart_invest.data import fetch_a_share, load_prices_csv
from smart_invest.strategy import MovingAverageCross


def _add_market_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--start", help="开始日期，如 20200101")
    p.add_argument("--end", help="结束日期，默认今天")
    p.add_argument("--period", default="daily", choices=["daily", "weekly", "monthly"])
    p.add_argument(
        "--adjust", default="qfq", choices=["qfq", "hfq", "none"], help="复权方式，默认前复权"
    )
    p.add_argument("--no-cache", action="store_true", help="忽略本地缓存，重新下载")


def _fetch(args: argparse.Namespace):
    return fetch_a_share(
        args.symbol,
        start=args.start,
        end=args.end,
        period=args.period,
        adjust="" if args.adjust == "none" else args.adjust,
        use_cache=not args.no_cache,
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="smart-invest")
    sub = parser.add_subparsers(dest="command", required=True)

    fp = sub.add_parser("fetch", help="通过 akshare 下载 A 股行情")
    fp.add_argument("symbol", help="6 位股票代码，如 600519")
    fp.add_argument("-o", "--output", help="另存为 CSV 文件路径")
    _add_market_args(fp)

    bt = sub.add_parser("backtest", help="运行双均线回测")
    src = bt.add_mutually_exclusive_group(required=True)
    src.add_argument("csv", nargs="?", help="本地行情 CSV（含 date、close 列）")
    src.add_argument("--symbol", help="直接用 akshare 拉取该股票行情")
    bt.add_argument("--short", type=int, default=5, help="短期均线窗口")
    bt.add_argument("--long", type=int, default=20, help="长期均线窗口")
    _add_market_args(bt)

    args = parser.parse_args(argv)
    if args.command == "fetch":
        df = _fetch(args)
        print(f"{args.symbol}: {len(df)} 条，{df.index[0].date()} ~ {df.index[-1].date()}")
        print(df.tail())
        if args.output:
            df.to_csv(args.output)
            print(f"已保存到 {args.output}")
    elif args.command == "backtest":
        prices = _fetch(args) if args.symbol else load_prices_csv(args.csv)
        result = run_backtest(prices, MovingAverageCross(args.short, args.long))
        print(result.summary())


if __name__ == "__main__":
    main()
