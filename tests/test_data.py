from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from smart_invest.data import DataError, fetch_history, load_demo_history, normalize_symbol


def chart_payload():
    return {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {
                        "currency": "USD",
                        "exchangeName": "NMS",
                        "exchangeTimezoneName": "America/New_York",
                    },
                    # 00:30 UTC on Jan 3 is still Jan 2 in New York.
                    "timestamp": [1704241800, 1704328200],
                    "indicators": {
                        "quote": [
                            {
                                "open": [98, 100],
                                "high": [102, 112],
                                "low": [96, 99],
                                "close": [100, 110],
                                "volume": [1000, 2000],
                            }
                        ],
                        "adjclose": [{"adjclose": [50, 55]}],
                    },
                }
            ],
        }
    }


def mock_chart(monkeypatch, payload=None, status=200):
    response = Mock(status_code=status)
    response.json.return_value = chart_payload() if payload is None else payload
    get = Mock(return_value=response)
    monkeypatch.setattr("smart_invest.data.requests.get", get)
    return get


@pytest.mark.parametrize(
    ("given", "expected"),
    [("aapl", "AAPL"), (" brk.b ", "BRK-B"), ("700", "0700.HK"), ("0700.hk", "0700.HK"), ("09988.HK", "9988.HK")],
)
def test_normalize_us_and_hong_kong_symbols(given, expected):
    assert normalize_symbol(given) == expected


@pytest.mark.parametrize("symbol", ["", "0000.HK", "../AAPL", "AAPL?token=x", "NVDA.TO", "<script>", None])
def test_invalid_symbol_is_rejected_before_network(monkeypatch, symbol):
    get = mock_chart(monkeypatch)
    with pytest.raises(DataError):
        fetch_history(symbol)
    get.assert_not_called()


def test_adjustments_keep_ohlc_consistent_and_use_exchange_dates(monkeypatch):
    get = mock_chart(monkeypatch)
    frame = fetch_history("aapl", "2y")

    assert frame.index.name == "Date"
    assert frame.index.tz is None
    assert list(frame.index) == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
    assert frame.iloc[0].to_dict() == {"Open": 49, "High": 51, "Low": 48, "Close": 50, "Volume": 1000}
    assert list(frame.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert frame.attrs["source"] == "Yahoo Finance"
    assert frame.attrs["symbol"] == "AAPL"
    assert frame.attrs["currency"] == "USD"
    assert frame.attrs["exchange"] == "NMS"
    assert frame.attrs["timezone"] == "America/New_York"
    assert frame.attrs["price_basis"] == "adjusted"
    assert datetime.fromisoformat(frame.attrs["fetched_at"]).tzinfo == timezone.utc
    assert not frame.attrs["is_demo"]
    args, kwargs = get.call_args
    assert args[0] == "https://query1.finance.yahoo.com/v8/finance/chart/AAPL"
    assert kwargs["params"]["range"] == "2y"
    assert kwargs["timeout"] == (5, 20)
    assert kwargs["allow_redirects"] is False


def test_hong_kong_trading_dates_and_currency(monkeypatch):
    payload = chart_payload()
    meta = payload["chart"]["result"][0]["meta"]
    meta.update(currency="HKD", exchangeName="HKG", exchangeTimezoneName="Asia/Hong_Kong")
    get = mock_chart(monkeypatch, payload)
    frame = fetch_history("700")
    assert str(frame.index[0].date()) == "2024-01-03"
    assert frame.attrs["currency"] == "HKD"
    assert get.call_args.args[0].endswith("/0700.HK")


def test_no_adjusted_series_labels_raw_prices(monkeypatch):
    payload = chart_payload()
    del payload["chart"]["result"][0]["indicators"]["adjclose"]
    mock_chart(monkeypatch, payload)
    frame = fetch_history("AAPL")
    assert frame["Close"].tolist() == [100, 110]
    assert frame.attrs["price_basis"] == "unadjusted"
    assert "未调整" in frame.attrs["price_basis_description"]


def test_partial_adjustments_never_mix_raw_and_adjusted_prices(monkeypatch):
    payload = chart_payload()
    payload["chart"]["result"][0]["indicators"]["adjclose"][0]["adjclose"] = [50, None]
    mock_chart(monkeypatch, payload)
    frame = fetch_history("AAPL")
    assert frame["Close"].tolist() == [50]
    assert frame.attrs["dropped_rows"] == 1
    assert frame.attrs["price_basis"] == "adjusted"


def test_missing_prices_are_removed_without_inventing_rows(monkeypatch):
    payload = chart_payload()
    payload["chart"]["result"][0]["indicators"]["quote"][0]["close"] = [None, 110]
    mock_chart(monkeypatch, payload)
    frame = fetch_history("AAPL")
    assert len(frame) == 1
    assert frame.iloc[0]["Close"] == 55
    assert frame.attrs["dropped_rows"] == 1


@pytest.mark.parametrize("status", [301, 401, 403, 404, 429, 500])
def test_http_failures_raise_without_using_demo(monkeypatch, status):
    mock_chart(monkeypatch, status=status)
    with pytest.raises(DataError):
        fetch_history("AAPL")


@pytest.mark.parametrize("error", [requests.Timeout("timeout"), requests.ConnectionError("failure")])
def test_network_failures_raise_clear_error(monkeypatch, error):
    get = Mock(side_effect=error)
    monkeypatch.setattr("smart_invest.data.requests.get", get)
    with pytest.raises(DataError, match="超时|连接"):
        fetch_history("AAPL")


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"chart": {"error": {"description": "No data"}, "result": None}},
        {"chart": {"error": None, "result": []}},
        {"chart": {"error": None, "result": [{}]}},
    ],
)
def test_invalid_provider_payload_is_rejected(monkeypatch, payload):
    mock_chart(monkeypatch, payload)
    with pytest.raises(DataError):
        fetch_history("AAPL")


def test_mismatched_data_and_invalid_json_are_rejected(monkeypatch):
    payload = deepcopy(chart_payload())
    payload["chart"]["result"][0]["indicators"]["quote"][0]["close"] = [100]
    get = mock_chart(monkeypatch, payload)
    with pytest.raises(DataError):
        fetch_history("AAPL")
    get.return_value.json.side_effect = ValueError("invalid JSON")
    with pytest.raises(DataError, match="解析"):
        fetch_history("AAPL")


def test_invalid_interval_does_not_make_a_request(monkeypatch):
    get = mock_chart(monkeypatch)
    with pytest.raises(DataError):
        fetch_history("AAPL", "100y")
    get.assert_not_called()


def test_demo_is_explicit_deterministic_and_does_not_use_network(monkeypatch):
    get = mock_chart(monkeypatch)
    first = load_demo_history("0700.HK", "6mo")
    second = load_demo_history("0700.HK", "6mo")
    pd.testing.assert_frame_equal(first, second)
    assert len(first) == 126
    assert first.attrs["source"] == "合成演示数据"
    assert first.attrs["is_demo"] is True
    assert first.attrs["price_basis"] == "synthetic"
    assert first.attrs["currency"] == "HKD"
    assert (first["High"] >= first[["Open", "Close"]].max(axis=1)).all()
    assert (first["Low"] <= first[["Open", "Close"]].min(axis=1)).all()
    get.assert_not_called()
