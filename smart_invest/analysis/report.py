"""回测报告：HTML（内嵌图表）+ CSV/JSON 明细。"""
from __future__ import annotations

import base64
import html
import io
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .metrics import METRIC_LABELS, drawdown_series, format_metric, monthly_returns

logger = logging.getLogger(__name__)

_SUMMARY_KEYS = ["total_return", "cagr", "sharpe", "max_drawdown", "benchmark_cagr", "excess_cagr"]
_SECTIONS = {
    "收益": ["start", "end", "initial_equity", "final_equity", "total_return", "cagr", "best_day", "worst_day"],
    "风险": ["annual_volatility", "sharpe", "sortino", "max_drawdown", "max_drawdown_days", "calmar", "exposure"],
    "交易": ["num_trades", "num_round_trips", "win_rate", "profit_factor", "avg_win", "avg_loss",
           "total_fees", "fees_pct", "annual_turnover"],
    "相对基准": ["benchmark_total_return", "benchmark_cagr", "benchmark_max_drawdown", "excess_cagr",
             "alpha", "beta", "information_ratio"],
}


def _chart(result) -> str | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("未安装 matplotlib，报告中不包含图表（pip install matplotlib）")
        return None
    eq = result.equity["equity"]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1.2]})
    ax1.plot(eq.index, eq / eq.iloc[0], color="#1f5fbf", lw=1.6, label="Strategy")
    if result.benchmark is not None:
        b = result.benchmark
        ax1.plot(b.index, b / b.iloc[0], color="#8a8f98", lw=1.2, label="Benchmark")
    ax1.axhline(1, color="#cccccc", lw=0.8)
    ax1.set_ylabel("Net value")
    ax1.legend(loc="upper left", frameon=False)
    ax1.grid(alpha=0.25)
    for side, color, marker in (("BUY", "#d9480f", "^"), ("SELL", "#2b8a3e", "v")):
        t = result.trades[result.trades["side"] == side] if len(result.trades) else None
        if t is not None and len(t) and len(t) <= 400:
            ys = (eq / eq.iloc[0]).reindex(t["date"]).values
            ax1.scatter(t["date"], ys, marker=marker, color=color, s=14, alpha=0.6, zorder=3)
    dd = drawdown_series(eq)
    ax2.fill_between(dd.index, dd.values * 100, 0, color="#c92a2a", alpha=0.35, lw=0)
    if result.benchmark is not None:
        ax2.plot(dd.index, drawdown_series(result.benchmark).values * 100, color="#8a8f98", lw=0.8)
    ax2.set_ylabel("Drawdown %")
    ax2.grid(alpha=0.25)
    for ax in (ax1, ax2):
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _cell_color(v: float) -> str:
    if not np.isfinite(v):
        return ""
    # 港股习惯：红涨绿跌
    a = min(abs(v) / 0.10, 1.0) * 0.55 + 0.08
    return f"background: rgba(201,42,42,{a:.2f})" if v > 0 else f"background: rgba(43,138,62,{a:.2f})"


def _monthly_table(eq: pd.Series) -> str:
    t = monthly_returns(eq)
    head = "".join(f"<th>{c}月</th>" if isinstance(c, (int, np.integer)) else f"<th>{c}</th>" for c in t.columns)
    rows = []
    for year, row in t.iterrows():
        cells = "".join(
            f'<td style="{_cell_color(v)}">{"" if pd.isna(v) else f"{v * 100:.1f}"}</td>' for v in row.values
        )
        rows.append(f"<tr><th>{year}</th>{cells}</tr>")
    return f'<table class="monthly"><tr><th>年份</th>{head}</tr>{"".join(rows)}</table>'


def _trades_table(trades: pd.DataFrame, limit: int = 300) -> str:
    if trades.empty:
        return "<p>无成交记录</p>"
    t = trades.tail(limit)
    rows = []
    for r in t.itertuples():
        cls = "buy" if r.side == "BUY" else "sell"
        pnl = "" if r.side == "BUY" else f"{r.realized_pnl:,.2f}"
        rows.append(
            f'<tr><td>{r.date:%Y-%m-%d}</td><td>{r.symbol}</td><td class="{cls}">{"买入" if r.side == "BUY" else "卖出"}</td>'
            f"<td>{r.quantity:,}</td><td>{r.price:,.3f}</td><td>{r.value:,.2f}</td><td>{r.fees:,.2f}</td>"
            f"<td>{pnl}</td><td>{html.escape(str(r.reason))}</td></tr>"
        )
    note = f"<p class='muted'>仅显示最近 {limit} 笔，完整记录见 trades.csv</p>" if len(trades) > limit else ""
    return ("<table><tr><th>日期</th><th>代码</th><th>方向</th><th>股数</th><th>成交价</th><th>金额</th>"
            f"<th>费用</th><th>实现盈亏</th><th>原因</th></tr>{''.join(rows)}</table>{note}")


def render_html(result, metrics: dict, title: str | None = None, data_note: str = "") -> str:
    title = title or f"港股回测报告 - {result.strategy_name}"
    chart = _chart(result)
    params = ", ".join(f"{k}={v}" for k, v in result.strategy_params.items()) or "默认"
    symbols = ", ".join(result.symbols) or "-"

    cards = "".join(
        f'<div class="card"><div class="label">{METRIC_LABELS[k][0]}</div>'
        f'<div class="value {"pos" if (metrics.get(k) or 0) > 0 and METRIC_LABELS[k][1] == "pct" else ""}">'
        f"{format_metric(k, metrics.get(k))}</div></div>"
        for k in _SUMMARY_KEYS if k in metrics
    )
    sections = ""
    for name, keys in _SECTIONS.items():
        present = [k for k in keys if k in metrics]
        if not present:
            continue
        rows = "".join(f"<tr><th>{METRIC_LABELS[k][0]}</th><td>{format_metric(k, metrics[k])}</td></tr>"
                       for k in present)
        sections += f'<div class="metric-group"><h3>{name}</h3><table>{rows}</table></div>'

    fm = result.config.fee_model
    cfg_rows = [
        ("策略", f"{result.strategy_name}（{params}）"),
        ("股票池", symbols),
        ("初始资金", f"HKD {result.config.initial_cash:,.0f}"),
        ("成交规则", f"信号次日开盘价成交，滑点 {result.config.slippage_ticks} 个价位，整手交易"),
        ("费用", f"佣金 {fm.commission_rate * 100:.3f}%（最低 {fm.min_commission:g}），印花税 {fm.stamp_duty_rate * 100:.2f}%，"
               f"交易费/征费/交收费按港交所标准"),
        ("作废订单", f"{result.cancelled_orders} 笔（停牌、资金不足或无持仓可卖）"),
    ]
    cfg = "".join(f"<tr><th>{k}</th><td>{html.escape(v)}</td></tr>" for k, v in cfg_rows)
    chart_html = f'<img src="data:image/png;base64,{chart}" alt="净值与回撤">' if chart else ""
    note_html = f'<div class="warn">{html.escape(data_note)}</div>' if data_note else ""

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ --bg:#f7f8fa; --panel:#fff; --text:#1d2433; --muted:#6b7280; --line:#e5e7eb; --up:#c92a2a; --down:#2b8a3e; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#14171c; --panel:#1c2028; --text:#e6e8eb; --muted:#9aa1ab; --line:#2c323c; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--text);
       font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif; }}
main {{ max-width:1100px; margin:0 auto; }}
h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:17px; margin:28px 0 10px; }} h3 {{ font-size:14px; margin:0 0 6px; color:var(--muted); }}
.muted {{ color:var(--muted); font-size:12px; }}
.warn {{ background:#fff4e5; color:#8a4b00; border:1px solid #ffd8a8; padding:8px 12px; border-radius:6px; margin:12px 0; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin:16px 0; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px 14px; }}
.card .label {{ color:var(--muted); font-size:12px; }} .card .value {{ font-size:20px; font-weight:600; font-variant-numeric:tabular-nums; }}
.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; overflow-x:auto; }}
img {{ width:100%; height:auto; background:#fff; border-radius:4px; }}
.metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:12px; }}
table {{ border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }}
th, td {{ padding:5px 8px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }}
th {{ text-align:left; font-weight:500; color:var(--muted); }}
.metric-group table th {{ width:55%; }}
table.monthly td {{ text-align:center; font-size:12px; }}
summary {{ cursor:pointer; color:var(--muted); }}
td.buy {{ color:var(--up); }} td.sell {{ color:var(--down); }}
</style></head><body><main>
<h1>{html.escape(title)}</h1>
<div class="muted">{metrics["start"]} ~ {metrics["end"]}</div>
{note_html}
<div class="cards">{cards}</div>
<h2>净值曲线与回撤</h2><div class="panel">{chart_html or "<p>（未安装 matplotlib，无图表）</p>"}</div>
<h2>绩效指标</h2><div class="metrics">{sections}</div>
<h2>月度收益（%）</h2><div class="panel">{_monthly_table(result.equity["equity"])}</div>
<h2>回测设置</h2><div class="panel"><table>{cfg}</table></div>
<h2>成交记录</h2><div class="panel"><details><summary>共 {len(result.trades)} 笔成交，点击展开</summary>{_trades_table(result.trades)}</details></div>
<p class="muted">回测结果仅供研究参考，不构成投资建议。历史表现不代表未来收益。</p>
</main></body></html>"""


def save_report(result, out_dir: str | Path, metrics: dict | None = None, data_note: str = "") -> Path:
    """保存 report.html、metrics.json、equity.csv、trades.csv，返回 HTML 路径。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    metrics = metrics if metrics is not None else result.metrics()
    eq = result.equity.copy()
    if result.benchmark is not None:
        eq["benchmark"] = result.benchmark
    eq.to_csv(out / "equity.csv")
    result.trades.to_csv(out / "trades.csv", index=False)
    clean = {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in metrics.items()}
    payload = {"strategy": result.strategy_name, "params": result.strategy_params, "metrics": clean}
    (out / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    path = out / "report.html"
    path.write_text(render_html(result, metrics, data_note=data_note), encoding="utf-8")
    return path


def summary_text(result, metrics: dict) -> str:
    keys = ["start", "end", "final_equity", "total_return", "cagr", "annual_volatility", "sharpe",
            "max_drawdown", "calmar", "win_rate", "num_trades", "total_fees",
            "benchmark_cagr", "excess_cagr", "beta"]
    width = max(len(METRIC_LABELS[k][0]) for k in keys)
    lines = [f"策略: {result.strategy_name} {result.strategy_params}"]
    for k in keys:
        if k in metrics:
            label = METRIC_LABELS[k][0]
            lines.append(f"  {label}{'　' * (width - len(label))}  {format_metric(k, metrics[k])}")
    return "\n".join(lines)
