# Phase 2 部署交接草案（未执行）

本文仅交接未来部署步骤。Phase 1 没有购买/部署 VPS、启动真实 QQ 协议端、扫码登录、调用真实 DeepSeek 或接触真实聊天记录。本文件不是生产验收报告；开始以下操作前须有用户进入 Phase 2 的明确指令，并解决 [`dependencies.md`](dependencies.md) 中与实际使用方式相关的许可和联网阻塞。

## 建议资源与预算

| 方案 | 建议用途 | 资源 | 预算规划 |
| --- | --- | --- | --- |
| 最低试运行 | 单账号、低并发、纯文本，不开本地向量/图片模型 | 2 vCPU、4 GiB RAM、40–60 GiB SSD、可用的 HTTPS 出站 | 约 30–80 元/月，通常依赖年付或活动价 |
| 推荐 | AstrBot、一个 QQ 协议端、三个插件、日志与备份余量 | 2–4 vCPU、8 GiB RAM、60–100 GiB SSD | 约 80–150 元/月 |

这是资源与预算估算，不是实时商家报价；DeepSeek API、备份存储和额外流量另计。使用 x86_64 Linux（Ubuntu 24.04 LTS 或 Debian 12），以匹配本项目已验证的 linux/amd64 构建。4 GiB 方案先观察实际 RSS 和 swap；不要同时加载本地 embedding/大模型。地区按 QQ、DeepSeek 的真实网络可达性与账号使用要求选择，不根据离线测试推定可达。

## Phase 2 开始前

1. 用户明确授权真实账号、服务器与 API 调用范围，指定备用测试 QQ 与授权的主账号私聊身份；本项目不要求群聊。
2. 选择 NapCat 或 SnowLuma 中一个协议端，复核当前版本、QQ 客户端兼容范围、授权条件与发行包校验。不同时启用两个进程竞争同一账号。
3. 放行必要的官方目的域名，至少核对 `api-docs.deepseek.com`、`api.deepseek.com` 和实际发行包下载域名；保留 TLS 验证。
4. 从 [DeepSeek 官方文档](https://api-docs.deepseek.com/) 再确认 `deepseek-flash`、`deepseek-v4-pro` 的当日有效名称、API base 和计费；本轮未完成该核实，不自行换成近似 ID。
5. 通过服务器安全配置供应密钥，不把 key、QQ 登录态、二维码或 OneBot token 提交到 Git、聊天或测试证据。

## 先复验本项目离线基线

在已安装 Docker Engine / Compose 插件、Git 和 Python 3.12 的服务器工作目录中执行。项目仓库使用独立 checkout；不要将生产数据目录指向测试 `.runtime/runs/`。

```bash
git clone https://github.com/maxsssssssssss/QQ_AI_Companion.git
cd QQ_AI_Companion
# 切换到已审核并合并的 Phase 1 交付 commit，禁止无记录地跟随 main。
bash scripts/bootstrap.sh
bash scripts/test-offline.sh
bash scripts/test-future-tasks.sh
```

若 Phase 1 交付尚未推送，先通过审核后的 Git commit 或补丁交付，不能假定 GitHub 上已有本地工作区文件。`bootstrap.sh` 是当前云环境经过验证的构建入口，其他主机的 Docker proxy、CA、工具路径需按其环境核对。离线测试镜像 `qq-ai-companion:phase1` 使用专门 harness，**不能直接作为已验证的生产启动方案**。

## 待实际验证的 Compose 示例

此示例采用独立上游发行镜像，与 Phase 1 harness 分开。AstrBot 官方 [`compose.yml`](https://github.com/AstrBotDevs/AstrBot/blob/dffe922773725e9c178392c423676ad65044748c/compose.yml) 使用 `soulter/astrbot`；其文档链接到 [NapCat-Docker 的 AstrBot 示例](https://github.com/NapNeko/NapCat-Docker/blob/42e4f9e8373c739f4d2ee599980ab8f87cff39b6/compose/astrbot.yml)，后者使用 `mlikiowa/napcat-docker` 与 `/app/.config/QQ` 持久化目录。本次只读了该示例，没有下载、启动这些发行镜像，也未核实某个具体 image tag/digest 的对应内容。

将已核对 release 版本且记录 digest 的镜像引用写入本机 `.env`：`ASTRBOT_IMAGE=soulter/astrbot@sha256:<核实后的digest>`、`NAPCAT_IMAGE=mlikiowa/napcat-docker@sha256:<核实后的digest>`。尖括号是待填写项；不能使用字面占位符或 `latest` 部署。密钥不放进 `.env`。检查 `.env` 和下述 `data/`、`napcat/`、`ntqq/`、`backups/` 都在 Git 排除范围内。

把下面内容保存到独立部署目录的 `compose.phase2.yml`，与该目录的 `.env` 放一起：

```yaml
services:
  astrbot:
    image: ${ASTRBOT_IMAGE:?Set an audited immutable AstrBot image}
    restart: unless-stopped
    init: true
    security_opt: [no-new-privileges:true]
    cap_drop: [ALL]
    environment:
      TZ: Asia/Shanghai
    ports:
      - "127.0.0.1:6185:6185"
    volumes:
      - ./data:/AstrBot/data
    networks: [companion]
    logging:
      driver: json-file
      options: {max-size: "10m", max-file: "3"}
  napcat:
    image: ${NAPCAT_IMAGE:?Set an audited immutable NapCat image}
    restart: unless-stopped
    environment:
      NAPCAT_UID: ${NAPCAT_UID:-1000}
      NAPCAT_GID: ${NAPCAT_GID:-1000}
    ports:
      - "127.0.0.1:6099:6099"
    volumes:
      - ./napcat/config:/app/napcat/config
      - ./ntqq:/app/.config/QQ
    networks: [companion]
    logging:
      driver: json-file
      options: {max-size: "10m", max-file: "3"}
networks:
  companion:
    driver: bridge
```

两个管理面板仅映射 loopback；6199 不发布到公网；不挂载 Docker socket，不授予 privileged。协议端镜像的运行用户、启动行为和必要权限需在正式版本上实测，不能因为上面 YAML 能解析就声称隔离完整。插件按锁定 commit 原样安装在 `data/plugins/`，完成真实加载核验，尚未取得分发授权的源码不推到公开仓库/镜像。

```bash
umask 077
mkdir -p data napcat/config ntqq backups
docker compose -f compose.phase2.yml config --quiet
docker compose -f compose.phase2.yml pull
docker compose -f compose.phase2.yml up -d astrbot
docker compose -f compose.phase2.yml ps
# 完成 AstrBot 初始密码和访问白名单配置后，才启动 QQ 协议端。
docker compose -f compose.phase2.yml up -d napcat
```

镜像版本、进程权限、卷所有权和 Dashboard 初始密码机制以选定发行包实测为准。不要用 `chmod 777` 或关闭 TLS 解决启动失败。管理入口从本机 SSH 隧道访问：

```bash
ssh -L 6185:127.0.0.1:6185 -L 6099:127.0.0.1:6099 operator@VPS_HOST
```

## NapCat / OneBot 配置和首次扫码（仅步骤）

1. 通过隧道打开 AstrBot Dashboard；按发行版初始密码流程登录后立即设置独立密码。配置 `timezone=Asia/Shanghai`、唯一会话、管理员和受控账号/群白名单。初次关闭主动消息、任意命令执行、远程文件操作和网页工具。
2. AstrBot 新增 `aiocqhttp` / OneBot v11 reverse WebSocket，内部监听 `0.0.0.0:6199`，设置随机强 token。容器内绑定 0.0.0.0 是为 bridge 上的协议端访问；主机不发布该端口。
3. NapCat WebUI 选择 WebSocket 客户端，目标 `ws://astrbot:6199/ws`，填同一个 token，消息格式 `array`，自消息上报关闭，重连间隔 5000 ms，心跳 30000 ms。URL 路径 `/ws` 必须按选定 AstrBot/aiocqhttp 实例检查；不要将 forward WS server 配置误作 reverse client。
4. 仅在上述限制就绪且用户授权后，用户本人用手机 QQ 扫描协议端提供的二维码并确认登录。二维码不截图提交、不进入自动化日志或仓库。若出现设备验证/风控，停止自动重试，交由账号持有人按平台流程处理。
5. 观察 OneBot WebSocket 已连接、心跳和 `get_login_info`，用一个受控测试号发送最小文本。先验证接收和明确的静态响应，再启用真实 LLM；不能只看面板在线灯。

NapCat 配置结构的对应项是 `network.websocketClients`，字段为 `enable`、`url`、`token`、`messagePostFormat`、`reconnectInterval`、`heartInterval`。端点在同机私有 bridge 时使用明文 WS；跨机器必须另行配置受验证 TLS 或受控专网，不关闭证书验证。

若改选 SnowLuma，用其 `networks.wsClients` / `accessToken` 配置，不直接套用 NapCat JSON；持久化 `config/` 与 `data/`，复核原生 QQ 进程接入方式。SnowLuma Node ≥24 且含专有 native addon，本项目没有做真实启动证明。

## 真实 DeepSeek 配置

确认官方模型名称后，在 AstrBot Dashboard 中新建 DeepSeek provider：`type=openai_chat_completion`、`api_base=https://api.deepseek.com/v1`、provider key 从现有安全绑定/本机秘密文件供应。设置主模型为经核实的 `deepseek-flash`，复杂任务模型为经核实的 `deepseek-v4-pro`；不要宣称宿主会自动按复杂度路由。Phase 1 仅准备配置，复杂任务通过显式模型选择或经过另行验收的路由开启。

Search/Fetch 原生配置为 `provider_settings.web_search=true`、`websearch_provider=deepseek`、`websearch_deepseek_key`。该字段可使用**同一个已授权 DeepSeek key**，无需为了搜索新申请第二个 key；但它与 chat provider 的 key 字段分离，不能假设自动继承。先保持关闭，完成一次公开主题搜索与网页抓取及引用核对后再开放给受控用户。不要将真实隐私发到 Search/Fetch。

首次调用设置较小输出上限和费用预算，分别记录成功/401/429/超时等结果但不记录 Authorization。验证复杂模型失败能清楚报错或按明确策略退回主模型；离线 Mock LLM 的回复质量不构成真实 DeepSeek 质量验收。

## 掉线、监控和失败处理

- 容器状态、OOM、磁盘使用率、SQLite 错误、OneBot 最近心跳、最近成功 LLM 与发送时间分别监控。`restart: unless-stopped` 只能重启进程，不能恢复需要用户确认的 QQ 登录态。
- OneBot 连接中断先检查网络与 token，然后确认 QQ 是否在线；避免不断扫码或暴力登录。记录离线持续时长，并向运维通道发受控告警，不能向未经允许的联系人群发。
- 按白名单限制入站、主动发送和群聊范围；验证关闭群白名单后陌生群消息无 LLM 调用、无出站回复。保留 per-session 数据隔离和消息发送审计。
- AstrBot v4.29.1 一次 Future Task 即使失败也删除任务行，见 [`dependencies.md`](dependencies.md)。上线提醒前要确认接受此限制，或另行实现/验收外部失败日志告警和人工补发；不能标注“自动重试可靠投递”。停止超过 300 秒的任务补发也需要单独验证。
- 更新前记录全部 commit/image digest，先备份，再在新数据副本上离线验证；通过后切换。不要运行未审计的远程 `curl | sh` 更新脚本。

## 数据卷与备份恢复

AstrBot 的完整 `data/` 包含主库、配置、插件数据、会话与可能的凭据；NapCat 的 `napcat/config/` 和 `ntqq/` 包含协议配置与 QQ 登录态，均按秘密处理。若启用媒体归档，还需确认具体媒体路径和保留规则。SnowLuma 内置配置导出不能替代全部 SQLite 与账号数据备份。

推荐先用最容易验证的一致性策略：**停服务后备份整个已确认的持久化目录**，备份结束恢复服务；长期运行再单独评估应用一致性快照。以下命令不上传内容：

```bash
umask 077
backup_path="backups/companion-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
docker compose -f compose.phase2.yml stop
tar -czf "$backup_path" data napcat/config ntqq
sha256sum "$backup_path" > "$backup_path.sha256"
docker compose -f compose.phase2.yml up -d
```

若 tar 失败，先修复备份路径/权限并明确恢复服务；不要覆盖已有备份。生产备份外移前由受控备份工具加密，保留密钥在独立安全位置；不得把明文归档提交到 Git、聊天或公开对象存储。建议每日一份、保留 7 日和每周一份保留 4 周，预算/磁盘按媒体量调整。

恢复到**新的空目录**先校验 SHA256，检查归档仅含预期目录，再解包。隔离网络启动并核对主库/插件库完整性、会话和配置；不要在同一 QQ 账号仍在线时同时启用恢复副本登录。记录恢复时间与具体恢复点，核对 OneBot token、管理员、白名单和模型 key 的安全注入后才替换原实例。

## 首次真实联调验收清单

1. 记录实际 VPS、CPU 架构、OS、QQ/协议端/AstrBot/插件版本和镜像 digest；核对许可证处理结果。
2. 用户扫码与会话恢复成功；重启协议端与 AstrBot 后重新连接；断网恢复、token 错误、QQ 离线各有明确状态。
3. 测试账号私聊与受控群各完成“接收 → DeepSeek → 回复 → SQLite记录”，证据脱敏。
4. 非白名单用户/群不触发 LLM；两个用户会话、记忆、权限互不串联；管理员能力不泄露。
5. Message Recorder 实际存储，MemoryCompanion 写入/召回/重启保持，PrivateCompanion 状态更新；不从插件加载成功推定这些行为成功。
6. 主模型与复杂模型各最小调用；真实 Search/Fetch 公开页面、有引用且错误清晰；限流、费用和超时检查。
7. 一次提醒时区、重启、执行和故意失败场景按已记录原生限制验收；需要可靠重试时单独设阻塞，不以日志存在代替成功送达。
8. 备份恢复到隔离副本并校验数据；资源、磁盘、日志和掉线告警可用。用户确认后才开放所需范围。

所有条目当前均等待 Phase 2 授权后实施；本文件不会把它们计入 Phase 1 已完成的真实 QQ/真实模型验证。
