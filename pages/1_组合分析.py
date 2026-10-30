"""Portfolio workbench: valuation, risk, rule signals, rebalancing and AI review."""

import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from smart_invest.ai import AIError, generate_portfolio_review
from smart_invest.data import DataError, fetch_history, load_demo_history
from smart_invest.portfolio import (
    BASE_CURRENCIES,
    PortfolioError,
    concentration,
    parse_holdings,
    portfolio_risk,
    rebalance_plan,
    returns_matrix,
    target_weights,
    value_positions,
)
from smart_invest.signals import score_signal

st.set_page_config(page_title="Smart Invest · 组合分析", page_icon="🧭", layout="wide")

SAMPLE = pd.DataFrame(
    {
        "symbol": ["AAPL", "MSFT", "NVDA", "0700.HK", "9988.HK"],
        "shares": [50.0, 20.0, 30.0, 200.0, 300.0],
        "cost": [170.0, 300.0, 90.0, 320.0, 85.0],
    }
)
PERIODS = {"近一年": "1y", "近两年": "2y", "近五年": "5y"}


@st.cache_data(ttl=900, show_spinner=False)
def market_history(symbol, period):
    return fetch_history(symbol, period)


def pct(value):
    return "—" if value is None or pd.isna(value) else f"{value:.1%}"


def main():
    st.title("组合分析与 AI 顾问")
    st.caption("持仓估值 · 组合风险 · 规则信号 · 再平衡测算 · AI 点评｜研究工具，不构成投资建议，不接入下单")
    with st.sidebar:
        st.header("组合设置")
        base = st.selectbox("基准货币", BASE_CURRENCIES)
        usd_hkd = st.number_input("美元兑港元汇率（HKD/USD）", min_value=1.0, max_value=20.0, value=7.8, step=0.01, help="手动输入的固定汇率，不自动更新，也不反映汇率波动。")
        period = PERIODS[st.selectbox("风险统计区间", list(PERIODS))]
        mode = st.radio("数据模式", ["公开行情", "合成演示数据"])
        if st.button("刷新行情", use_container_width=True):
            market_history.clear()

    st.markdown("**持仓**（成本价为该标的自身货币下的每股均价，可留空；可增删行）")
    edited = st.data_editor(
        SAMPLE,
        num_rows="dynamic",
        use_container_width=True,
        key="holdings",
        column_config={
            "symbol": st.column_config.TextColumn("代码"),
            "shares": st.column_config.NumberColumn("股数", min_value=0.0),
            "cost": st.column_config.NumberColumn("成本价", min_value=0.0),
        },
    )
    st.caption("以上为示例持仓，请替换为你的真实持仓；数据只在当前会话中处理，不会保存。")

    is_demo = mode == "合成演示数据"
    try:
        holdings = parse_holdings(edited.to_dict("records"))
        if len(holdings) < 2:
            raise PortfolioError("组合风险统计至少需要两只持仓。")
        histories = {}
        with st.spinner("正在读取行情…"):
            for holding in holdings:
                histories[holding.symbol] = (
                    load_demo_history(holding.symbol, period) if is_demo else market_history(holding.symbol, period)
                )
        positions = value_positions(holdings, histories, base, usd_hkd)
        returns = returns_matrix(histories)
        risk = portfolio_risk(positions["weight"], returns)
    except (PortfolioError, DataError) as error:
        st.error(str(error))
        if not is_demo:
            st.info("行情不可用时可切换到「合成演示数据」体验功能；演示数据为虚构。")
        return

    if is_demo:
        st.warning("当前使用合成演示数据：价格、盈亏和风险均为虚构，仅用于体验功能。")
    dropped = sum(h.attrs.get("dropped_rows", 0) for h in histories.values())
    if dropped:
        st.warning(f"行情中有 {dropped} 行缺失或无效数据已移除，统计可能受影响。")

    total = positions["value"].sum()
    cost_known = positions["cost_value"].notna().all()
    pnl = positions["pnl"].sum() if cost_known else None
    conc = concentration(positions["weight"])
    cols = st.columns(5)
    cols[0].metric(f"组合市值（{base}）", f"{total:,.0f}")
    cols[1].metric("浮动盈亏", "—" if pnl is None else f"{pnl:+,.0f}", None if pnl is None else pct(pnl / (total - pnl)))
    cols[2].metric("年化波动率", pct(risk["volatility"]))
    cols[3].metric("最大回撤", pct(risk["max_drawdown"]))
    cols[4].metric("有效持仓数", f"{conc['effective_n']:.1f}")
    st.caption(
        f"风险统计基于 {risk['observations']} 个各标的共同交易日，假设当前权重固定、忽略汇率波动与分红再投资差异；"
        f"价格来源：{'合成演示数据' if is_demo else 'Yahoo Finance 公开日线'}。"
    )
    if conc["top_weight"] > 0.4:
        st.warning(f"集中度提示：{conc['top_symbol']} 占组合 {pct(conc['top_weight'])}，单一持仓占比较高。")
    top_risk = risk["risk_contribution"].idxmax()
    top_share = risk["risk_contribution"].max()
    if top_share > 0.4:
        st.warning(f"风险集中提示：{top_risk} 贡献了组合 {pct(top_share)} 的波动（持仓权重 {pct(positions.loc[top_risk, 'weight'])}）。")

    signals = {symbol: score_signal(history) for symbol, history in histories.items()}
    tab_hold, tab_risk, tab_signal, tab_rebalance, tab_ai = st.tabs(["持仓概览", "组合风险", "规则信号", "再平衡测算", "AI 点评"])

    with tab_hold:
        table = positions.rename(
            columns={"shares": "股数", "currency": "计价货币", "price": "最新价", "value": f"市值（{base}）", "cost_value": f"成本（{base}）", "pnl": f"盈亏（{base}）", "pnl_pct": "盈亏率", "weight": "权重"}
        )
        st.dataframe(table.style.format({"股数": "{:,.2f}", "最新价": "{:,.2f}", f"市值（{base}）": "{:,.0f}", f"成本（{base}）": "{:,.0f}", f"盈亏（{base}）": "{:+,.0f}", "盈亏率": "{:+.1%}", "权重": "{:.1%}"}, na_rep="—"), use_container_width=True)
        pie = go.Figure(go.Pie(labels=list(positions.index), values=positions["value"], hole=0.5))
        pie.update_layout(title="持仓权重", height=360, margin=dict(l=10, r=10, t=55, b=10))
        st.plotly_chart(pie, use_container_width=True)

    with tab_risk:
        curve = go.Figure(go.Scatter(x=risk["curve"].index, y=risk["curve"], name="组合净值"))
        curve.update_layout(title="当前权重的历史净值（起点 = 1）", height=360, template="plotly_white", margin=dict(l=10, r=10, t=55, b=10))
        st.plotly_chart(curve, use_container_width=True)
        left, right = st.columns(2)
        compare = pd.DataFrame({"持仓权重": positions["weight"], "风险贡献": risk["risk_contribution"]})
        bar = go.Figure([go.Bar(x=compare.index, y=compare[c], name=c) for c in compare.columns])
        bar.update_layout(title="权重 vs 风险贡献", barmode="group", height=360, template="plotly_white", margin=dict(l=10, r=10, t=55, b=10), yaxis_tickformat=".0%")
        left.plotly_chart(bar, use_container_width=True)
        corr = risk["correlation"]
        heat = go.Figure(go.Heatmap(z=corr.values, x=list(corr.columns), y=list(corr.index), zmin=-1, zmax=1, colorscale="RdBu_r", text=corr.round(2).values, texttemplate="%{text}"))
        heat.update_layout(title="日收益相关系数", height=360, margin=dict(l=10, r=10, t=55, b=10))
        right.plotly_chart(heat, use_container_width=True)
        st.caption("风险贡献 = 权重 × 该资产与组合的协方差 ÷ 组合方差，合计 100%。相关性随市场状态变化，历史相关性在压力期可能上升。")

    with tab_signal:
        rows = [
            {"代码": symbol, "信号": s["label"], "净得分": s["score"], "可用因子": s["available_factors"], **{f["factor"]: f["reason"] for f in s["factors"]}}
            for symbol, s in signals.items()
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption("信号由趋势、动量、RSI、回撤、波动率五项价格规则投票得出，用于描述当前价格状态，不是买卖指令，也不预测未来走势；未考虑估值、基本面与新闻。")

    with tab_rebalance:
        method = st.radio("目标配置", ["等权", "反向波动率", "自定义"], horizontal=True)
        custom = None
        if method == "自定义":
            entered = st.data_editor(pd.DataFrame({"symbol": positions.index, "目标权重(%)": 100.0 / len(positions)}), hide_index=True, disabled=["symbol"], key="targets")
            custom = dict(zip(entered["symbol"], entered["目标权重(%)"]))
        band = st.slider("容忍带（权重偏离在此范围内则持有）", 0.0, 0.20, 0.05, 0.01, format="%.2f")
        try:
            plan = rebalance_plan(positions, target_weights(method, positions.index, returns, custom), band)
        except PortfolioError as error:
            st.error(str(error))
        else:
            shown = plan.rename(columns={"current_weight": "当前权重", "target_weight": "目标权重", "drift": "偏离", "trade_value": f"调整金额（{base}）", "trade_shares": "调整股数", "action": "测算动作"})
            st.dataframe(shown.style.format({"当前权重": "{:.1%}", "目标权重": "{:.1%}", "偏离": "{:+.1%}", f"调整金额（{base}）": "{:+,.0f}", "调整股数": "{:+,.1f}"}), use_container_width=True)
            st.caption("这是把权重拉回你所选目标的算术测算，不是交易建议；未考虑每手股数、佣金、印花税、资本利得税与流动性，股数为小数。")

    with tab_ai:
        st.markdown("**规则摘要**")
        worst = min(signals, key=lambda s: signals[s]["score"])
        best = max(signals, key=lambda s: signals[s]["score"])
        st.write(f"组合年化波动 {pct(risk['volatility'])}，区间最大回撤 {pct(risk['max_drawdown'])}；最大持仓 {conc['top_symbol']} 占 {pct(conc['top_weight'])}。规则信号最强：{best}（{signals[best]['label']}），最弱：{worst}（{signals[worst]['label']}）。")
        st.divider()
        if is_demo:
            st.info("演示数据仅用于验证界面；请切换真实公开行情后使用 AI 点评。")
        elif not os.getenv("INVEST_AI_API_KEY"):
            st.info("AI 服务尚未配置。其余分析可独立使用；部署时设置 INVEST_AI_API_KEY 即可启用。")
        else:
            scope = (base, period, usd_hkd, tuple(zip(positions.index, positions["shares"], positions["price"])))
            with st.form("portfolio_ai"):
                question = st.text_area("你的问题", value="请评估这个组合的集中度与风险来源，并列出做决策前需要自行核实的信息。", max_chars=2000)
                submitted = st.form_submit_button("生成 AI 点评")
            if submitted:
                context = {
                    "base_currency": base,
                    "period": period,
                    "fx_assumption_hkd_per_usd": usd_hkd,
                    "positions": [
                        {
                            "symbol": symbol,
                            "weight": round(float(row.weight), 4),
                            "pnl_pct": None if pd.isna(row.pnl_pct) else round(float(row.pnl_pct), 4),
                            "risk_contribution": round(float(risk["risk_contribution"][symbol]), 4),
                            "signal": signals[symbol]["label"],
                            "signal_score": signals[symbol]["score"],
                            "volatility": None if signals[symbol]["volatility"] is None else round(signals[symbol]["volatility"], 4),
                        }
                        for symbol, row in positions.iterrows()
                    ],
                    "portfolio": {
                        "annualized_volatility": round(risk["volatility"], 4),
                        "max_drawdown": round(risk["max_drawdown"], 4),
                        "total_return_fixed_weights": round(risk["total_return"], 4),
                        "effective_positions": round(conc["effective_n"], 2),
                        "top_weight": round(conc["top_weight"], 4),
                        "observations": risk["observations"],
                    },
                    "correlation": {a: {b: round(float(v), 2) for b, v in row.items()} for a, row in risk["correlation"].iterrows()},
                }
                try:
                    with st.spinner("正在生成组合点评…"):
                        st.session_state["portfolio_report"] = {"scope": scope, "question": question, "text": generate_portfolio_review(context, question)}
                except AIError as error:
                    st.error(str(error))
            report = st.session_state.get("portfolio_report")
            if report and report["scope"] == scope:
                st.caption(f"问题：{report['question']}")
                st.markdown(report["text"])
            st.caption("提交后会向配置的模型服务发送持仓代码、权重、盈亏率与风险统计（不含股数和金额）；调用可能产生费用。AI 输出需要核实，不构成投资建议。")
    st.caption("研究工具原型，未接入交易下单。公开数据的准确性、授权范围与时效性需要按用途核实。")


main()
