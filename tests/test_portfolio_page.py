"""Portfolio page renders end to end offline using synthetic data."""

from pathlib import Path
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from smart_invest.data import DataError

PAGE = str(Path(__file__).resolve().parents[1] / "pages" / "1_组合分析.py")


def test_demo_mode_renders_all_sections_without_ai_key(monkeypatch):
    monkeypatch.delenv("INVEST_AI_API_KEY", raising=False)
    st.cache_data.clear()
    app = AppTest.from_file(PAGE, default_timeout=30).run()
    app.radio[0].set_value("合成演示数据").run()
    assert not app.exception
    assert len(app.metric) == 5
    assert len(app.tabs) == 5
    assert any("虚构" in w.value for w in app.warning)
    assert any("请切换真实公开行情" in i.value for i in app.info)


def test_unavailable_market_data_errors_instead_of_substituting_demo(monkeypatch):
    monkeypatch.delenv("INVEST_AI_API_KEY", raising=False)
    st.cache_data.clear()
    with patch("smart_invest.data.fetch_history", side_effect=DataError("行情暂时不可用")):
        app = AppTest.from_file(PAGE, default_timeout=30).run()
    assert not app.exception
    assert app.error[0].value == "行情暂时不可用"
    assert not app.metric
