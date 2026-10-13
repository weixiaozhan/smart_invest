# Smart Invest

面向美股和港股的 Python 股票研究原型。使用 Streamlit、pandas 和 Plotly，提供公开日线行情、均线与 RSI、收益与回撤分析、均线策略回测和可选 AI 解读。

## 本地开发

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync --frozen
uv run pytest
uv run streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

美股代码例如 `AAPL`、`MSFT`；港股代码例如 `0700.HK`、`9988.HK`。默认读取 Yahoo Finance 公开日线并显示来源、获取时间和价格口径。失败时明确提示，可手动切换到标明为虚构的合成演示数据，不会自动替代真实行情。

## 数据与回测

优先使用调整后收盘价，按相同因子调整 OHLC；缺少调整价格时明确标为未调整。港股通常以 HKD、美股以 USD 计价，不自动换汇。公开源可能延迟、限流、缺失或返回未结束交易日的数据。

均线回测采用滞后一交易日的持仓，按日收盘收益近似执行并收取持仓变动成本；买入持有计入首次买入费用。没有模拟滑点、税费、成交限制、现金利息或期末平仓，用于研究历史规则，不能据此声称实盘收益。

## 可选 AI 解读

配置以下进程环境变量；Streamlit 本地运行不自动加载 `.env`，Docker Compose 可读取 `.env`。

- `INVEST_AI_API_KEY`：在部署环境中安全设置的模型服务密钥。
- `INVEST_AI_BASE_URL`：默认 `https://api.openai.com/v1`；也支持官方通义千问国内/国际兼容接口与 DeepSeek 接口。
- `INVEST_AI_MODEL`：默认 `gpt-4.1-mini`；通义千问可使用 `qwen-plus`，按服务商调整。

没有密钥时不调用模型，规则摘要会明确标注。AI 仅收到标的、价格风险统计和用户问题，不获取财报或新闻。输出需要核实，接口调用可能计费。为避免向错误地址发送密钥，当前仅接受代码中列明的官方 HTTPS 服务端点。

## 阿里云部署

提供非 root Docker 镜像和 Compose 配置，支持 ECS 上使用 Caddy 自动 HTTPS。完整步骤见 [阿里云部署说明](docs/deploy-aliyun.md)。已准备部署文件；创建 ECS、配置域名和实际发布仍需在你的阿里云环境执行。

测试使用固定数据验证算法与解析，不依赖外网或模型。真实行情连通性需在目标网络单独验证。公开数据的授权范围、再分发及商业使用条件应按用途核实。
