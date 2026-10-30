import numpy as np
import pandas as pd
import pytest

from smart_invest.portfolio import (
    PortfolioError,
    concentration,
    fx_to_base,
    parse_holdings,
    portfolio_risk,
    rebalance_plan,
    returns_matrix,
    target_weights,
    value_positions,
)


def history(values, currency="USD"):
    frame = pd.DataFrame({"Close": values}, index=pd.date_range("2024-01-01", periods=len(values), freq="B"))
    frame.attrs["currency"] = currency
    return frame


def test_parse_merges_duplicates_with_weighted_cost_and_skips_blank_rows():
    rows = [
        {"symbol": "aapl", "shares": 10, "cost": 100},
        {"symbol": "AAPL", "shares": 30, "cost": 200},
        {"symbol": None, "shares": None, "cost": None},
        {"symbol": "700", "shares": 5, "cost": None},
    ]
    holdings = {h.symbol: h for h in parse_holdings(rows)}
    assert holdings["AAPL"].shares == 40
    assert holdings["AAPL"].cost == pytest.approx(175)
    assert holdings["0700.HK"].cost is None


@pytest.mark.parametrize(
    "rows",
    [[], [{"symbol": "AAPL", "shares": 0, "cost": 1}], [{"symbol": "AAPL", "shares": float("nan"), "cost": 1}], [{"symbol": "AAPL", "shares": 1, "cost": -5}], [{"symbol": "$$$", "shares": 1, "cost": 1}]],
)
def test_parse_rejects_bad_rows(rows):
    with pytest.raises(PortfolioError):
        parse_holdings(rows)


def test_fx_conversion():
    assert fx_to_base("USD", "HKD", 8.0) == 8.0
    assert fx_to_base("HKD", "USD", 8.0) == 0.125
    assert fx_to_base("USD", "USD", 8.0) == 1.0
    with pytest.raises(PortfolioError):
        fx_to_base("EUR", "USD", 7.8)


def test_valuation_converts_currency_and_weights_sum_to_one():
    holdings = parse_holdings([{"symbol": "AAPL", "shares": 10, "cost": 50}, {"symbol": "0700.HK", "shares": 100, "cost": 400}])
    positions = value_positions(holdings, {"AAPL": history([90, 100]), "0700.HK": history([390, 400], "HKD")}, "USD", 8.0)
    assert positions.loc["AAPL", "value"] == pytest.approx(1000)
    assert positions.loc["0700.HK", "value"] == pytest.approx(5000)
    assert positions.loc["0700.HK", "pnl"] == pytest.approx(0)
    assert positions.loc["AAPL", "pnl_pct"] == pytest.approx(1.0)
    assert positions["weight"].sum() == pytest.approx(1)


def test_returns_use_common_dates_and_require_enough_history():
    long = history(np.linspace(100, 140, 60))
    shifted = history(np.linspace(50, 60, 60))
    shifted.index = pd.date_range("2030-01-01", periods=60, freq="B")
    with pytest.raises(PortfolioError):
        returns_matrix({"A": long, "B": shifted})
    assert len(returns_matrix({"A": long, "B": long.copy()})) == 59


def test_risk_two_identical_assets_equals_single_asset_and_splits_contribution():
    rng = np.random.default_rng(1)
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, 120))
    returns = returns_matrix({"A": history(close), "B": history(close)})
    risk = portfolio_risk(pd.Series({"A": 0.5, "B": 0.5}), returns)
    assert risk["volatility"] == pytest.approx(returns["A"].std(ddof=1) * np.sqrt(252))
    assert risk["risk_contribution"].sum() == pytest.approx(1)
    assert risk["risk_contribution"]["A"] == pytest.approx(0.5)
    assert risk["correlation"].loc["A", "B"] == pytest.approx(1)
    assert risk["max_drawdown"] <= 0


def test_risk_contribution_follows_the_riskier_asset():
    rng = np.random.default_rng(2)
    calm = 100 * np.cumprod(1 + rng.normal(0, 0.005, 200))
    wild = 100 * np.cumprod(1 + rng.normal(0, 0.03, 200))
    returns = returns_matrix({"CALM": history(calm), "WILD": history(wild)})
    risk = portfolio_risk(pd.Series({"CALM": 0.5, "WILD": 0.5}), returns)
    assert risk["risk_contribution"]["WILD"] > 0.9


def test_concentration():
    result = concentration(pd.Series({"A": 0.5, "B": 0.5}))
    assert result["effective_n"] == pytest.approx(2)
    assert concentration(pd.Series({"A": 0.9, "B": 0.1}))["top_symbol"] == "A"


def test_target_weight_methods():
    rng = np.random.default_rng(3)
    returns = pd.DataFrame({"A": rng.normal(0, 0.005, 100), "B": rng.normal(0, 0.02, 100)})
    assert target_weights("等权", ["A", "B"]).tolist() == [0.5, 0.5]
    inverse = target_weights("反向波动率", ["A", "B"], returns)
    assert inverse.sum() == pytest.approx(1) and inverse["A"] > inverse["B"]
    custom = target_weights("自定义", ["A", "B"], custom={"A": 30, "B": 10})
    assert custom["A"] == pytest.approx(0.75)
    with pytest.raises(PortfolioError):
        target_weights("自定义", ["A", "B"], custom={"A": 0, "B": 0})
    with pytest.raises(PortfolioError):
        target_weights("魔法", ["A"])


def test_rebalance_trades_restore_targets_and_respect_band():
    holdings = parse_holdings([{"symbol": "A", "shares": 10, "cost": 1}, {"symbol": "B", "shares": 10, "cost": 1}, {"symbol": "C", "shares": 10, "cost": 1}])
    positions = value_positions(holdings, {"A": history([30, 60]), "B": history([30, 20]), "C": history([30, 20])})
    targets = pd.Series(1 / 3, index=positions.index)
    plan = rebalance_plan(positions, targets, band=0.0)
    assert plan.loc["A", "action"] == "减仓" and plan.loc["B", "action"] == "加仓"
    assert plan["trade_value"].sum() == pytest.approx(0)
    after = (positions["value"] + plan["trade_value"]) / positions["value"].sum()
    np.testing.assert_allclose(after, targets)
    assert (rebalance_plan(positions, targets, band=0.5)["action"] == "持有").all()
    with pytest.raises(PortfolioError):
        rebalance_plan(positions, targets, band=1.0)
