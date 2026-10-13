"""Run with: uv run streamlit run app.py."""

import os

import plotly.graph_objects as go
import streamlit as st

from smart_invest.ai import AIError, generate_ai_analysis
from smart_invest.analytics import add_indicators, backtest_sma, summarize
from smart_invest.data import DataError, fetch_history, load_demo_history, normalize_symbol


st.set_page_config(page_title="Smart Invest · 股票研究", page_icon="📈", layout="wide")


@st.cache_data(ttl=900, show_spinner=False)
def market_history(symbol, period):
    return fetch_history(symbol, period)


def percentage(value):
    return "—" if value is None else f"{value:.2%}"


def chart_style(fig, title, y_title="价格"):
    fig.update_layout(title=title, height=420, margin=dict(l=10, r=10, t=55, b=10), xaxis_title="交易日期", yaxis_title=y_title, template="plotly_white", legend=dict(orientation="h", y=1.1, x=0))
    return fig


def main():
    st.title("Smart Invest")
    st.caption("美股与港股研究工作台 · 公开日线数据 · 指标、风险与策略验证")
    with st.sidebar:
        st.header("研究标的")
        market = st.selectbox("股票市场", ["美股", "港股"])
        examples = {"苹果 · AAPL": "AAPL", "微软 · MSFT": "MSFT", "英伟达 · NVDA": "NVDA"}
        if market == "港股":
            examples = {"腾讯 · 0700.HK": "0700.HK", "阿里巴巴 · 9988.HK": "9988.HK", "汇丰 · 0005.HK": "0005.HK"}
        selection = st.selectbox("常用标的", list(examples) + ["自定义代码"], key=f"selection_{market}")
        symbol = examples.get(selection, "")
        if selection == "自定义代码":
            symbol = st.text_input("股票代码", value="0700.HK" if market == "港股" else "AAPL", key=f"symbol_{market}")
        period_labels = {"近半年": "6mo", "近一年": "1y", "近两年": "2y", "近五年": "5y"}
        period = period_labels[st.selectbox("研究区间", list(period_labels), index=1)]
        mode = st.radio("数据模式", ["公开行情", "合成演示数据"])
        if st.button("刷新行情", use_container_width=True):
            market_history.clear()
        st.caption("港股示例：0700.HK；美股示例：AAPL、BRK-B。公开源可能延迟或限流。")

    is_demo = mode == "合成演示数据"
    try:
        symbol = normalize_symbol(symbol)
        if market == "港股" and not symbol.endswith(".HK"):
            raise DataError("港股代码请使用数字或 .HK 后缀，例如 0700.HK。")
        if market == "美股" and symbol.endswith(".HK"):
            raise DataError("当前选择了美股市场，请切换至港股市场查看此代码。")
        with st.spinner("正在读取日线数据…"):
            if is_demo:
                history = load_demo_history(symbol, period)
            else:
                history = market_history(symbol, period)
    except DataError as error:
        st.error(str(error))
        st.info("可切换至「合成演示数据」体验分析功能。演示数据不代表真实股票表现。")
        return

    if is_demo:
        st.warning("当前使用合成演示数据：价格、收益和回测均为虚构，仅用于体验功能。")
    stats = summarize(history)
    has_gaps = bool(history.attrs.get("dropped_rows", 0))
    if has_gaps:
        stats["annualized_volatility"] = None
    indicators = add_indicators(history)
    currency = history.attrs.get("currency", "")
    display_symbol = "演示标的" if is_demo else symbol
    st.subheader(f"{display_symbol} · 研究概览")
    columns = st.columns(4)
    columns[0].metric(f"最近收盘价（{currency or '计价单位'}）", f"{stats['latest_close']:,.2f}")
    columns[1].metric("区间收益", percentage(stats["total_return"]))
    columns[2].metric("年化波动率", percentage(stats["annualized_volatility"]))
    columns[3].metric("最大回撤", percentage(stats["max_drawdown"]))
    st.caption(f"来源：{history.attrs.get('source', '未知')} · 数据区间：{history.index[0]:%Y-%m-%d} 至 {history.index[-1]:%Y-%m-%d} · 获取时间（UTC）：{history.attrs.get('fetched_at', '—')} · {history.attrs.get('price_basis_description', '日线行情')}")
    st.caption("公开行情可能包含尚未结束交易日的日线。统计和回测采用年化 252 个交易日；不同市场的交易日与交易成本可能不同。")
    if history.attrs.get("dropped_rows", 0):
        st.warning(f"数据源有 {history.attrs['dropped_rows']} 行缺失、无效或重复数据已移除；统计与回测可能受数据缺口影响。")

    price_tab, risk_tab, backtest_tab, ai_tab = st.tabs(["行情与指标", "风险与数据", "策略回测", "研究解读"])
    with price_tab:
        fig = go.Figure()
        fig.add_trace(go.Candlestick(x=indicators.index, open=indicators["Open"], high=indicators["High"], low=indicators["Low"], close=indicators["Close"], name="日线"))
        for column, color in [("SMA20", "#0284c7"), ("SMA60", "#d97706")]:
            fig.add_trace(go.Scatter(x=indicators.index, y=indicators[column], name=column, line=dict(color=color, width=2)))
        fig.update_layout(xaxis_rangeslider_visible=False)
        st.plotly_chart(chart_style(fig, "价格与移动平均线"), use_container_width=True)
        rsi = go.Figure(go.Scatter(x=indicators.index, y=indicators["RSI14"], name="RSI14", line=dict(color="#7c3aed")))
        rsi.add_hline(y=70, line_dash="dot")
        rsi.add_hline(y=30, line_dash="dot")
        rsi.update_yaxes(range=[0, 100])
        st.plotly_chart(chart_style(rsi, "相对强弱指标 RSI14", "RSI"), use_container_width=True)
        st.caption("SMA 是简单移动平均；RSI 使用 Wilder 平滑。阈值描述历史价格状态，不能单独预测未来走势。")
    with risk_tab:
        drawdown = history["Close"] / history["Close"].cummax() - 1
        fig = go.Figure(go.Scatter(x=history.index, y=drawdown, fill="tozeroy", name="回撤", line=dict(color="#e11d48")))
        fig.update_yaxes(tickformat=".0%")
        st.plotly_chart(chart_style(fig, "相对历史高点的回撤", "回撤"), use_container_width=True)
        st.dataframe(indicators.tail(100), use_container_width=True)
        st.download_button("下载完整分析数据 CSV", indicators.to_csv(index_label="Date").encode("utf-8-sig"), file_name=f"{'DEMO' if is_demo else symbol}_analysis.csv", mime="text/csv")
    with backtest_tab:
        st.write("验证一条简单规则：短均线高于长均线时持有，否则持有现金。")
        c1, c2, c3 = st.columns(3)
        fast = c1.number_input("短均线交易日", min_value=2, max_value=120, value=20)
        slow = c2.number_input("长均线交易日", min_value=3, max_value=250, value=60)
        fee = c3.number_input("单边交易成本（基点）", min_value=0.0, max_value=100.0, value=10.0, step=1.0)
        if fast >= slow:
            st.error("短均线周期必须小于长均线周期。")
        elif has_gaps:
            st.info("行情存在缺口，未运行依赖连续日收益的回测。请更换标的或区间，并核实数据源。")
        elif len(history) <= slow:
            st.info("数据长度不足以覆盖长均线预热与下一交易日收益，请延长研究区间。")
        else:
            result = backtest_sma(history, fast=int(fast), slow=int(slow), fee_bps=float(fee))
            fig = go.Figure()
            for column, name in [("BuyHold", "买入持有"), ("Strategy", "均线策略")]:
                fig.add_trace(go.Scatter(x=result.index, y=result[column], name=name))
            st.plotly_chart(chart_style(fig, "相同起点的累计净值", "净值倍数"), use_container_width=True)
            c1, c2 = st.columns(2)
            c1.metric("策略累计收益", percentage(float(result["Strategy"].iloc[-1] - 1)))
            c2.metric("买入持有收益", percentage(float(result["BuyHold"].iloc[-1] - 1)))
            st.caption("收盘生成信号，持仓滞后一交易日；每日收盘收益近似执行，持仓变化收取单边费用，买入持有也计入首次买入费用。未模拟滑点、税费、成交限制、现金利息或最后平仓。仅验证历史规则，不代表可实现的实盘收益。")
    with ai_tab:
        st.markdown("**价格与风险摘要（规则生成）**")
        st.write(f"本区间共 {stats['observations']} 个交易日，收益 {percentage(stats['total_return'])}，年化波动 {percentage(stats['annualized_volatility'])}，最大回撤 {percentage(stats['max_drawdown'])}。")
        st.write("此摘要只基于价格数据。估值、财报、新闻、汇率与市场制度需要另行研究。")
        st.divider()
        st.markdown("**AI 辅助解读**")
        if is_demo:
            st.info("演示数据仅用于验证界面；请切换真实公开行情后使用 AI 解读。")
        elif not os.getenv("INVEST_AI_API_KEY"):
            st.info("AI 服务尚未配置。行情、指标与回测可以独立使用；部署时可接入模型服务。")
        else:
            report_scope = (symbol, period, str(history.index[-1]), float(history["Close"].iloc[-1]), history.attrs.get("fetched_at"))
            with st.form("ai_research"):
                question = st.text_area("研究问题", value="请分析这段价格走势的风险，并列出进一步研究需要核实的信息。", max_chars=2000)
                submitted = st.form_submit_button("生成 AI 解读")
            if submitted:
                try:
                    with st.spinner("正在生成研究解读…"):
                        context = {
                            **stats,
                            "source": history.attrs.get("source"),
                            "currency": currency,
                            "price_basis": history.attrs.get("price_basis_description"),
                            "start_date": str(history.index[0].date()),
                            "end_date": str(history.index[-1].date()),
                            "fetched_at": history.attrs.get("fetched_at"),
                        }
                        report = generate_ai_analysis(symbol, context, question)
                    st.session_state["ai_report"] = {"scope": report_scope, "question": question, "text": report}
                except AIError as error:
                    st.error(str(error))
            saved_report = st.session_state.get("ai_report")
            if saved_report and saved_report["scope"] == report_scope:
                st.caption(f"研究问题：{saved_report['question']}")
                st.markdown(saved_report["text"])
            st.caption("提交后会向配置的模型服务发送股票代码、统计摘要和研究问题；调用可能产生模型服务费用。AI 输出需要核实。")
    st.caption("研究工具原型，未接入交易下单。公开数据的准确性、授权范围与时效性需要按用途核实。")


if __name__ == "__main__":
    main()
