# 阿里云 ECS 部署

该原型分析美股与港股，使用公开行情，不连接券商或执行交易。仓库提供部署文件；下面的步骤需要在你自己的 ECS 执行，当前并未创建云资源或部署到阿里云。

下文 `git clone` 假设本次新增文件已经提交并推送至仓库；当前工作区的文件不会自动出现在远端。首次试部署也可以安全复制当前代码目录到 ECS，但排除 `.git`、`.venv`、`.env`、缓存与本地凭据。

## 1. 准备 ECS 与网络

- 建议从 **2 vCPU / 2 GiB 内存**、Ubuntu 24.04 LTS、至少 20 GiB 系统盘开始，按访问量调整。公网 IP、流量及 AI 调用可能产生费用。
- 安全组的 TCP 22 只允许自己的固定 IP 或可信管理网络。先通过 SSH 隧道使用时，无需开放其他入站端口。
- 使用域名发布时，将域名的 A 记录指向 ECS 公网 IPv4；仅在已配置公网 IPv6 时设置 AAAA。放行 TCP 80、443，并确认服务器防火墙也允许它们。**不用开放 8501**。
- 中国内地公网网站按阿里云要求办理域名备案。区域选择还应符合数据源、AI 服务的服务地区与访问条款。

安装 Docker Engine 与 Compose 插件请按 [Docker 官方 Ubuntu 安装说明](https://docs.docker.com/engine/install/ubuntu/)；使用 Compose **v2.24 或以上**（本配置使用可选 `env_file`）。验证安装：

```bash
sudo docker version
sudo docker compose version
```

以下命令假设 SSH 登录用户可运行 Docker；没有该权限时给 `docker` 命令加 `sudo`。Docker 用户组具有主机管理权限，只授予可信用户。

## 2. 获取代码与启动

在 ECS 的用户主目录内执行：

```bash
git clone https://github.com/weixiaozhan/smart_invest.git
cd smart_invest
test -f .env || cp .env.example .env
chmod 600 .env
docker compose up -d --build --wait --wait-timeout 120 app
docker compose ps
curl --fail http://127.0.0.1:8501/_stcore/health
```

健康检查预期返回 `ok`。构建使用 Python 3.12、固定版本 uv 和 `uv.lock`，应用以非 root 用户运行。默认仅向 ECS 本机发布 8501，缺少 `.env` 也能启动行情分析与示例模式。配置文件中的 AI API Key 可为空；需要 AI 解读时在 ECS 的 `.env` 填入 `INVEST_AI_API_KEY`，并设置可用的 `INVEST_AI_BASE_URL` 与模型。凭据不要提交到 Git。

本机 8501 已被其他服务占用时，可以将 `.env` 中的 `INVEST_HOST_PORT` 改为其他空闲端口（如 8502），同步调整本机健康检查与 SSH 隧道的目标端口。容器内部和 Caddy 反代仍使用 8501。

没有域名时，在**自己的电脑**建立 SSH 隧道，然后通过浏览器打开本机 8501：

```bash
ssh -N -L 8501:127.0.0.1:8501 YOUR_USER@YOUR_ECS_IP
```

## 3. 域名与 HTTPS

编辑 ECS 上的 `.env`，将 `SITE_DOMAIN` 改为实际域名（如 `invest.your-domain.com`，不要包含 `https://` 或路径），确认 DNS 已生效，再执行：

```bash
docker compose --profile https up -d --build --wait --wait-timeout 120
docker compose --profile https logs --tail=100 caddy
curl --fail --retry 12 --retry-all-errors --retry-delay 5 --retry-max-time 90 \
  https://YOUR_DOMAIN/_stcore/health
```

Caddy 自动申请和续期 HTTPS 证书，并将 HTTP 请求重定向至 HTTPS。80、443 需要能被证书颁发机构访问；不要使用示例域名申请证书。证书状态保存在 Docker 卷中。反向代理支持 Streamlit 的 WebSocket，无需关闭 Streamlit 的 CORS 或 XSRF 检查。

原型尚无账户权限系统。个人研究优先使用 SSH 隧道；如对公网开放，先在网关添加访问控制，再配置付费 AI 密钥，以控制访问与调用成本。

## 4. 在 ECS 检查外部服务

行情通过 `query1.finance.yahoo.com` 获取，构建还需要访问 `ghcr.io`、Docker Hub、`pypi.org` 与 `files.pythonhosted.org`。HTTPS 证书需要能访问 Caddy 使用的 ACME 服务。可选 AI 默认为 `api.openai.com`；另支持官方 DashScope 和 DeepSeek 接口，不能填入任意第三方域名。

阿里云通义千问可配置 `INVEST_AI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1` 与 `INVEST_AI_MODEL=qwen-plus`，密钥须属于对应服务；国际站使用 `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` 及对应账户的密钥。不同服务的密钥不能混用。

在 **ECS 实际检查**所选区域的连通性：

```bash
curl --fail --show-error --max-time 20 \
  'https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=1mo&interval=1d'
curl --fail --show-error --max-time 20 \
  'https://query1.finance.yahoo.com/v8/finance/chart/0700.HK?range=1mo&interval=1d'
curl --silent --show-error --max-time 20 --output /dev/null \
  --write-out '%{http_code}\n' https://api.openai.com/v1/models
```

最后一项未附认证时通常返回 `401`，只能说明服务端有响应，不能证明密钥、模型或地区授权可用；在页面发起一次 AI 解读才能验证完整调用。不要将密钥放在命令行参数或粘贴到日志。若请求超时、`403` 或 `429`，检查服务地区、出站网络、服务授权和限流；不要关闭 TLS 验证或绕过访问限制。

公开可访问不代表可自由商用或再分发。Yahoo 行情接口不承诺可用性，可能延迟、限流或变化；商用前确认数据使用条款并按需要购买授权。页面上的示例数据只用于体验，不作为真实市场数据。

## 5. 运维、更新与备份

```bash
# 查看状态和近期日志（不要公开可能包含个人研究信息的日志）
docker compose --profile https ps
docker compose --profile https logs --tail=100 app caddy

# 先保留当前镜像，确认工作区干净后再更新与重建
docker image tag smart-invest:local smart-invest:previous
git status --short
git pull --ff-only
docker compose --profile https up -d --build --wait --wait-timeout 120
curl --fail http://127.0.0.1:8501/_stcore/health

# 重启或停止；down 默认保留证书卷
docker compose --profile https restart
docker compose --profile https down
```

无域名部署可省略 `--profile https`。如更新后的健康或页面验证失败，可执行 `docker image tag smart-invest:previous smart-invest:local`，再用 `docker compose --profile https up -d --no-build --force-recreate --wait --wait-timeout 120 app` 恢复上一个应用镜像，并保留对应 Git 提交以便排查。

保留 `.env` 的安全备份、当前 Git 提交 ID 与 `smart-invest_caddy_data` / `smart-invest_caddy_config` 卷。卷内含证书私钥，备份按秘密文件处理；不要提交到仓库。首次启用 HTTPS 后，可这样备份（停止服务期间暂不可访问）：

```bash
backup_dir="$HOME/smart-invest-backup/$(date +%Y%m%d-%H%M%S)"
install -d -m 700 "$backup_dir"
cp .env "$backup_dir/app.env"
chmod 600 "$backup_dir/app.env"
git rev-parse HEAD > "$backup_dir/commit.txt"
docker compose --profile https stop
docker run --rm \
  --mount source=smart-invest_caddy_data,target=/data,readonly \
  --mount "type=bind,src=$backup_dir,dst=/backup" \
  alpine:3.22 tar -czf /backup/caddy-data.tar.gz -C /data .
docker run --rm \
  --mount source=smart-invest_caddy_config,target=/data,readonly \
  --mount "type=bind,src=$backup_dir,dst=/backup" \
  alpine:3.22 tar -czf /backup/caddy-config.tar.gz -C /data .
docker compose --profile https start
```

恢复到新主机时，先获取相同 Git 提交的代码，将 `app.env` 安全复制为 `.env` 并设为 `600` 权限，用 `docker volume create smart-invest_caddy_data` 和 `docker volume create smart-invest_caddy_config` 创建同名卷。按上面的卷挂载路径运行 `tar -xzf /backup/caddy-data.tar.gz -C /data`（恢复时移除 `readonly`）；配置卷同理，然后执行 `docker compose --profile https up -d --build`。归档文件须来自自己可信的备份。**不要使用 `docker compose down -v`**，它会删除证书卷。当前原型不保存研究结果到数据库；下载的报告与数据需要自行备份。

## Codex 云环境构建时的 CA

这一节只用于受管理的 Codex 构建环境；普通 ECS 构建无需此参数。环境若通过带自有 CA 的代理出站，在读取其运行时网络说明后，将当前受信任的 CA bundle 作为临时 BuildKit secret 提供：

```bash
docker build \
  --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt \
  -t smart-invest:local .
```

secret 仅在依赖安装步骤挂载，不会保存到镜像。该环境运行容器访问 HTTPS 时也需要按其运行时说明挂载受信任的 CA bundle，并设置 `REQUESTS_CA_BUNDLE`；不要把会话代理地址或证书写入阿里云 `.env` 或正式镜像。
