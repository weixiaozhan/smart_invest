"""命令行入口：smart-invest backtest <csv>"""

from __future__ import annotations

import argparse

from smart_invest.backtest import run_backtest
from smart_invest.data import load_prices_csv
from smart_invest.strategy import MovingAverageCross


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="smart-invest")
    sub = parser.add_subparsers(dest="command", required=True)

    bt = sub.add_parser("backtest", help="对 CSV 行情运行双均线回测")
    bt.add_argument("csv", help="包含 date、close 列的行情文件")
    bt.add_argument("--short", type=int, default=5, help="短期均线窗口")
    bt.add_argument("--long", type=int, default=20, help="长期均线窗口")

    args = parser.parse_args(argv)
    if args.command == "backtest":
        prices = load_prices_csv(args.csv)
        result = run_backtest(prices, MovingAverageCross(args.short, args.long))
        print(result.summary())


if __name__ == "__main__":
    main()
