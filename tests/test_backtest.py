from smart_invest.backtest import run_backtest
from smart_invest.data import load_prices_csv
from smart_invest.strategy import MovingAverageCross


def test_backtest_runs(prices):
    result = run_backtest(prices, MovingAverageCross(5, 20), initial_cash=10_000)
    assert len(result.equity) == len(prices)
    assert result.max_drawdown <= 0
    assert result.equity.iloc[0] == 10_000


def test_load_prices_csv(tmp_path, prices):
    path = tmp_path / "prices.csv"
    prices.reset_index().to_csv(path, index=False)
    loaded = load_prices_csv(path)
    assert list(loaded.columns) == ["close"]
    assert loaded.index.is_monotonic_increasing
