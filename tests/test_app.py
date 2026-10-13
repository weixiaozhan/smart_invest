"""Exercise real Streamlit interactions without internet or model credentials."""

from pathlib import Path
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from smart_invest.data import DataError, load_demo_history

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def test_unavailable_data_shows_error_without_synthetic_substitution(monkeypatch):
    monkeypatch.delenv("INVEST_AI_API_KEY", raising=False)
    st.cache_data.clear()
    with patch("smart_invest.data.fetch_history", side_effect=DataError("行情暂时不可用")):
        app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    assert not app.exception
    assert app.error[0].value == "行情暂时不可用"
    assert not app.metric
    assert any("合成演示数据" in item.value for item in app.info)


def test_demo_mode_renders_analysis_and_rejects_invalid_backtest_windows(monkeypatch):
    monkeypatch.delenv("INVEST_AI_API_KEY", raising=False)
    st.cache_data.clear()
    with patch("smart_invest.data.fetch_history", side_effect=DataError("行情暂时不可用")) as fetch:
        app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
        app.radio[0].set_value("合成演示数据").run()
        assert not app.exception
        assert len(app.metric) == 6
        assert any("虚构" in item.value for item in app.warning)
        app.selectbox[0].set_value("港股").run()
        assert not app.exception
        assert "HKD" in app.metric[0].label
        assert any("合成演示数据" in item.value for item in app.caption)
        app.number_input[0].set_value(70).run()
        assert not app.exception
        assert any("短均线周期" in item.value for item in app.error)
        assert fetch.call_count == 1


def test_live_view_keeps_currency_and_marks_missing_ai_configuration(monkeypatch):
    monkeypatch.delenv("INVEST_AI_API_KEY", raising=False)
    st.cache_data.clear()

    def public_history(symbol, period):
        frame = load_demo_history(symbol, period)
        frame.attrs.update(source="Yahoo Finance", is_demo=False)
        return frame

    with patch("smart_invest.data.fetch_history", side_effect=public_history):
        app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
        assert not app.exception
        assert len(app.metric) == 6
        assert "USD" in app.metric[0].label
        assert any("AI 服务尚未配置" in item.value for item in app.info)
        app.selectbox[0].set_value("港股").run()
        assert not app.exception
        assert "HKD" in app.metric[0].label
        assert any("0700.HK" in item.value for item in app.subheader)


def test_ai_report_survives_backtest_rerun_and_is_scoped_to_market_data(monkeypatch):
    monkeypatch.setenv("INVEST_AI_API_KEY", "test-key")
    st.cache_data.clear()

    def public_history(symbol, period):
        frame = load_demo_history(symbol, period)
        frame.attrs.update(source="Yahoo Finance", is_demo=False)
        return frame

    with patch("smart_invest.data.fetch_history", side_effect=public_history), patch("smart_invest.ai.generate_ai_analysis", return_value="已生成的研究报告") as ai:
        app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
        next(button for button in app.button if button.label == "生成 AI 解读").click().run()
        assert not app.exception
        assert ai.call_count == 1
        assert ai.call_args.args[1]["currency"] == "USD"
        assert "start_date" in ai.call_args.args[1]
        assert any(item.value == "已生成的研究报告" for item in app.markdown)
        app.number_input[2].set_value(11.0).run()
        assert ai.call_count == 1
        assert any(item.value == "已生成的研究报告" for item in app.markdown)
        app.selectbox[0].set_value("港股").run()
        assert not any(item.value == "已生成的研究报告" for item in app.markdown)


def test_incomplete_prices_do_not_claim_daily_volatility_or_backtest(monkeypatch):
    monkeypatch.delenv("INVEST_AI_API_KEY", raising=False)
    st.cache_data.clear()
    frame = load_demo_history()
    frame.attrs.update(source="Yahoo Finance", is_demo=False, dropped_rows=2)
    with patch("smart_invest.data.fetch_history", return_value=frame):
        app = AppTest.from_file(str(APP_PATH)).run(timeout=20)
    assert not app.exception
    assert app.metric[2].value == "—"
    assert len(app.metric) == 4
    assert any("缺口" in item.value for item in app.info)
