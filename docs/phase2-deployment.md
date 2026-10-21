# Phase 2 本地接管与私有 VPS 部署

用户已授权本轮常规部署、初始化和受控测试；真实 API Key 必须由用户通过受保护界面输入，QQ 扫码由账号持有人完成。实际状态见 [phase2-validation.md](phase2-validation.md)，不能把以下操作说明当作验收通过。原 Phase 1 提交、框架、插件和锁文件保持不变。

## 当前运行边界

- 目标为 Ubuntu 24.04 x86_64、2 vCPU、约 4 GB RAM、60 GB SSD 的腾讯云 Lighthouse；4 GB 仅按低并发单账号试运行，不加载本地 embedding/大模型。
- 本地环境为 Windows + WSL2；SSH/SCP 使用 Windows OpenSSH，身份由既有 Windows PEM 提供，私钥不进入 WSL、仓库或服务器。ACL 限制读取，禁止输出内容。
- SSH 别名为 `qq-companion-vps`，强制 `StrictHostKeyChecking=yes`、仅 publickey、不转发 agent。用户从腾讯云控制台核对 ED25519 指纹后才完成首次信任。
- 服务器原有 SSH 端口和认证方式不改；涉及重启、可能失联、重要数据删除、额外购买和扩大账号权限的操作仍单独请求用户。
- 普通私聊白名单仅在运行配置中保存。群聊、主动消息、自动日程、联网搜索、计算机/文件操作、流式和分段回复暂关闭。

## 固定版本与来源

| 组件 | 固定来源 |
| --- | --- |
| 项目 Phase 1 | `0b6bbf147a83f3e17c598c7a4d49386b6553c823` |
| AstrBot | 4.29.1 / `dffe922773725e9c178392c423676ad65044748c` |
| AstrBot 官方 amd64 基础 | `soulter/astrbot@sha256:0c3b8ef7485dbd15176637133c23dfbdb80675d174545a997648b9c1db7d53c1` |
| Message Recorder | 0.0.4 / `8cb184e2890dee7d5031d3a8ef6b753209dc7d11` |
| MemoryCompanion | 2.3.1 / `e40a83005b0a2e0817e5680c7d14398c9ab49790` |
| PrivateCompanion | 6.7.0 / `da953559f34d28a43d8a2d44c1064b293229c1b8` |
| NapCat | 4.18.38 / 原始官方 Shell ZIP SHA256 `735f2e633247df3ffd6e8ee8b84d7039c315b2655f5807e5f0029f93d2c30016` |
| NapCat 官方容器基础 | 4.18.37 / `mlikiowa/napcat-docker@sha256:132448a544ed5cfa948dff8ae19e80bf4e17896e8ab2ba1c6cbcd86f8ad3e56e` |
| QQ Linux | 官方基础镜像内 `3.2.34-53644`，通过 dpkg-query 实测 |
| Python / Python 依赖 | 3.12.15；原 `lockfiles/astrbot-requirements.txt`、`build-tools.txt` 共 152 个去重运行/构建包及原始哈希 |

没有可用的官方 NapCat 4.18.38 Docker tag，因此在固定 4.18.37 容器基础中替换为未经改动的 4.18.38 官方 ZIP。该镜像是本服务器私有派生镜像，不宣称上游发布过这个 tag，不公开发布第三方插件源码或派生镜像。插件仍按原上游依赖安装，许可限制继续见 [dependencies.md](dependencies.md)。

AstrBot 官方 v4.29.1 基础镜像的 512 个 Python 文件与固定源码一致，但其 Python 依赖不完全等于 Phase 1 锁。生产 Dockerfile 使用原锁和哈希校正，再执行 pip check、152 包版本核验和源码比对。它运行官方 `main.py`，不使用 Phase 1 离线 harness。

## 主机初始化

`scripts/phase2-provision.sh` 针对经检查的现有 Ubuntu 主机执行普通软件更新，使用现有签名 Ubuntu APT 源安装 Git、docker.io、Compose/Buildx 插件，设置 Shanghai 时区、部署目录和 Docker 日志轮转。没有运行来源不明的远程安装脚本。

执行升级前保留第二条 SSH 管理连接，并在升级后检查 ssh.service、sshd -t 和新连接。本次 OpenSSH 升级曾导致服务停止及 `/run/sshd` 缺失，通过保留连接启动 ssh.service 后恢复。脚本已补恢复检查。系统仍提示需要重启，保留的内核/固件包与运行中内核更新尚未完成；不要自动重启或把“APT 已运行”写作全部安全更新生效。

腾讯云安全组尚无授权接口可自动核对。容器仅绑定 loopback，不能据此声称完整云侧规则已经审计。

## 制品运输与可复现构建

VPS 的 Docker Hub registry/auth 超时，Docker CE 下载端点重置，PyPI 下载慢。因此由本地访问官方来源，校验后通过已验证的 SSH 传输：

1. `scripts/phase2-fetch-images.py` 验证 manifest、config、每个压缩层 SHA256 与解压 diffID；保存来源和校验记录。
2. Python 多平台源 index 需要额外元数据/attestation blob，保持原始 index digest 后通过 `docker load --platform linux/amd64` 导入。NapCat 使用排除临时下载文件的清洁 OCI 归档。
3. 固定上游 Git checkout 和插件 source_tree SHA 双重校验。网络失败留下的部分 checkout 保留，不能覆盖未知现有数据。
4. Python 构件只从官方 PyPI 获取，选择 Linux amd64 / Python 3.12 兼容文件；每个文件必须出现在原锁的 SHA256 集合中。生产构建使用完整 wheelhouse，关闭安装索引和构建网络。
5. `scripts/phase2-build-images.sh --apply --dependency-network none` 校验原始镜像身份，再给同一镜像建立唯一的本地构建标签，避免 BuildKit 因 canonical digest 再请求不可达 registry。它在构建前后检查固定来源、层链和最终镜像，不将标签视为新的版本锁。

当前服务器目录：

```text
/opt/qq-ai-companion/repo/             原 Phase 1 checkout，保持原提交
/opt/qq-ai-companion/phase2-src/       本次已审核部署源文件快照
/opt/qq-ai-companion/deployment/
  compose.yml                        deploy/phase2/compose.yml 的部署副本
  .env                               仅最终不可变 image ID，不放 Secret
  build/                             最小构建上下文和 provenance
  artifacts/                         已校验公开依赖归档
  state/data/                        AstrBot 数据，UID/GID 10001
  state/secrets/                     600 文件，运维用户拥有
  napcat/config/                     NapCat 配置
  ntqq/                              QQ 登录态
  backups/                           受保护备份
```

构建脚本每次在 `build/runs/` 新建上下文，保存输入哈希、原始镜像引用、BuildKit metadata 和最终 image ID。生产 `.env` 只选用通过验证的结果。固定源码和插件不加入公开镜像，镜像不推送仓库。

## 生产配置与启动门禁

先运行 `scripts/phase2-configure.py init`，传入专用 `--state`、已核验 `--sources` 和用户授权的 `--admin-id`。此命令拒绝覆盖已有 state，生成 Dashboard 密码、OneBot token 和 NapCat WebUI token，不输出值。接着运行 `scripts/phase2-napcat-configure.py` 生成原生配置，同样拒绝覆盖登录态。

初始化完成后仅 `state/data` 递归转给 UID/GID 10001；Secret 保留运维用户 600，不能把整个 state 一起改 owner。创建 data/home 和 data/cache。密码、Key 和账号 ID 均不写入仓库。

Compose 使用独立 Docker bridge、重启策略、内存/CPU/pid 限制。AstrBot UID10001、只读根文件系统、全部 capability 删除；NapCat 只授予入口 chown/gosu 所需权限，不开 privileged/SYS_ADMIN，不挂 Docker socket。限制是否兼容须以真实启动检查为准。

- AstrBot：`127.0.0.1:6185`；NapCat：`127.0.0.1:6099`。
- OneBot Reverse WebSocket：`ws://astrbot:6199/ws`，仅容器内部，不发布主机端口。
- NapCat 放在 `qq` profile，普通 `compose up` 不会自动启动它。
- HTTP healthcheck 只表示管理服务响应，不能代替 policy、真实模型、QQ 登录或聊天验收。
- `phase2_policy` 通过官方插件钩子应用 Phase 1 Guard、Recorder ACL 和固定 persona；只有插件版本、白名单及 Key 门禁通过才允许授权私聊。启动前必须检查本次启动生成的 readiness，不能靠旧文件或面板在线灯。

## 密钥与日志

固定 AstrBot 的 429 分支会把 Key 前 12 个字符写到日志。生产镜像通过 `sitecustomize.py` 在官方 main.py 前安装最小脱敏保护，覆盖 stdlib logging、Loguru、异常和 WebUI LogBroker；无法安全读取 UID10001 的 600 配置时启动失败。插件同步运行中配置并保留旧 Key 脱敏集合。无生产配置的离线镜像审计需显式清空 PYTHONPATH，生产启动不得清空。

这不是对第三方任意 print/native 输出的普遍保证；不能据此开放 debug 或导出原始日志。NapCat 上游在配置生效前会输出 WebUI token/二维码，因此该容器使用 `logging.driver=none` 且禁用文件日志。认证 WebUI 的上游内存日志仍可能含 token，禁止抓取/保存原始日志；状态与连接诊断使用允许字段。

DeepSeek Key 由用户在 SSH 隧道保护的 AstrBot Dashboard 中输入，保持提供商和模型禁用直到本地配置检查完成。Windows PowerShell 脚本当前受 ExecutionPolicy 限制，本部署不改变策略、不绕过执行限制，改用已有受保护界面。不要在聊天、命令参数、环境转储、日志或报告粘贴 Key。

SSH 隧道仅监听 Windows 本机：

```text
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:6185:127.0.0.1:6185 -L 127.0.0.1:6099:127.0.0.1:6099 qq-companion-vps
```

随机管理密码通过受 Windows ACL 保护的本地文件交付给账号持有人，不进入工具输出。用户在「模型提供商 → 对话 → deepseek → 设置 → API Key」输入并「保存配置」，不点获取模型或测试。`scripts/phase2-configure.py dashboard-key-status --state <生产 state>` 仅返回就绪状态，不回显 Key。停止 AstrBot 后运行同一脚本的 `accept-dashboard-key --state <生产 state>`，从受保护的 Dashboard 配置接收 Key；Secret 为 `600`，已有不同值时拒绝覆盖，配置原子更新保留 UID/GID 和权限。启用 Flash 并重启后检查本次 readiness。不要在 AstrBot 写配置期间运行此接收操作。

## 真实联调

已核对 [DeepSeek 官方 API 文档](https://api-docs.deepseek.com/)：`https://api.deepseek.com`，主模型 `deepseek-flash`，可选 `deepseek-v4-pro`。当前 Flash 支持图片；Pro 不应配置 image modality。工具调用测试关闭 thinking；Search/Fetch 继续关闭。

`scripts/phase2-test-deepseek.py` 必须显式 `--execute`，最多 8 个请求、每次最多 128 输出 token、无自动重试、每请求 60 秒硬期限；包含中文、上下文、Pro、无副作用工具往返、合成图片和错误响应检查。Key 只从本地受保护文件进入子进程 stdin，报告仅保存状态、HTTP 类别、usage 和断言。本次已观察预期的 HTTP 400；超时保护经过本地测试，未故意制造真实 DeepSeek 超时，远端超时恢复仍为 NOT TESTED。直接 API 的无副作用工具测试不会开启生产会话工具。

NapCat 就绪后由用户本人通过隧道 WebUI 扫码。不要读取密码、验证码、Cookie，不抓取或公开二维码，不反复刷新或绕过风控。真实 QQ 验收需少量授权私聊的接收、模型生成、发送状态、入站/出站归档，以及图片、非白名单拒绝、受控重启和掉线诊断。

本轮已分别完成直接 API 的 8 项受控调用，以及用户确认的 QQ 两轮文本上下文、3 个红色方块图片回复、重启后追加上下文私聊和对应消息归档。最终 Guard 入站 7、sent 7，Recorder 入站 7（图片 1）；另有 1 条空文本在发送前被 Guard 拒绝。真实非授权账号拒绝尚未测试。使用 `scripts/phase2-message-evidence.py` 读取受保护数据库的计数及一致性结果；不要用原始聊天正文或全量数据库转储作为工具输出。具体样本数与结果以 [phase2-validation.md](phase2-validation.md) 为准。

## 原生登录会话保存与重启复测

首次停服后 NapCat 返回 `waiting_qrcode`，原生快速登录候选为空，脚本停止且未发出快速登录请求。用户本人再次扫码成功后，使用辅助脚本保存已经在线的会话：

```bash
sudo python3 /opt/qq-ai-companion/phase2-src/scripts/phase2-enable-quick-login.py \
  --deployment /opt/qq-ai-companion/deployment \
  --save-current-session --apply
```

此模式先认证本机 WebUI，确认 `CheckLoginStatus` 为 `ready`，再用 `GetQQLoginInfo` 确认当前机器人账号在线并取得自身身份，随后再次确认 `ready`，只调用一次 `SetQuickLoginQQ` 保存原生配置。身份来自当前机器人会话，不使用管理员的 QQ 号码，不把账号放进命令参数或 Git。脚本还检查挂载的 `napcat/config/webui.json` 为受保护文件，并确认 `autoLoginAccount` 已落盘；只输出状态与布尔断言，不输出账号、Token、二维码或响应正文。没有 `--apply` 时不会连接或修改。

`--save-current-session` 不调用 `SetQuickLogin`，不尝试登录；未登录、离线、身份不明或配置未落盘时停止。本轮已完成此模式的在线身份核验与配置持久化。脚本另一个模式仅允许唯一的已保存账号发起至多一次原生快速登录，使用有限的只读状态轮询且不重试登录；保存前必须核验当前在线账号与候选一致，缓存与当前身份不一致时停止且不保存。本轮因候选为空未发出该请求，不能标为实测成功。需要扫码、设备验证或其他账号授权时仍由用户完成。

保存成功后须先将新配置纳入私有备份，再通过受控服务重启核验 `isLogin`、`coreReady`、`loginPhase` 与内部 Reverse WebSocket 连接。本轮第二次停服备份已包含新配置，恢复原容器后无新增扫码，`isLogin` 与 `coreReady` 为 true、`loginPhase=ready`，内部 WebSocket 连接恢复；用户随后确认追加私聊上下文正确，归档断言也通过。该结论只覆盖本次重启；登录态过期或账号风控仍需用户本人处理。配置持久化、容器健康与真实聊天分别验收，不使用恢复副本启动 QQ。

## 持久化、备份与待验能力

生产数据、QQ 登录态及备份都含私密信息。`scripts/phase2-backup.py` 默认只检查文件类型，不写文件或停服务。显式 `--apply` 才执行停服备份和隔离恢复验证；要求两项服务已有容器、部署和备份目录为 `700`，每次使用新的运行 ID：

```bash
sudo python3 /opt/qq-ai-companion/phase2-src/scripts/phase2-backup.py \
  --deployment /opt/qq-ai-companion/deployment

sudo python3 /opt/qq-ai-companion/phase2-src/scripts/phase2-backup.py \
  --deployment /opt/qq-ai-companion/deployment \
  --run-id "$(date -u +%Y%m%dT%H%M%SZ)" --apply
```

脚本先记录原运行服务，再停止 AstrBot 与 NapCat，在 `finally` 中只启动原本运行的容器。备份失败不会删除原数据；链接、硬链接和特殊文件会被明确拒绝，不能为通过检查自动删除现有数据。运行目录、归档和恢复目标已存在时拒绝覆盖。不得在检查和快照期间另启会写入同一目录的进程。

每次运行在 `deployment/backups/<运行ID>/` 保存：

- `astrbot-state.zip`：复用原 Phase 1 `scripts/backup.py`，包含 AstrBot state、配置、插件和 Secret，检查文件 SHA-256 与 SQLite 完整性。
- `napcat-opaque.zip`：原样保存 NapCat 配置及 QQ 登录态，校验完整文件清单与哈希；QQ 的加密 `.db` 不作为 SQLite 读取。
- `isolated-restore/`：全新恢复副本，文件 `600`、目录 `700`；验证后不挂载容器、不登录 QQ、不替换生产数据。
- `operation.json`、`result.json`：原服务状态及固定结果字段、计数和摘要，不输出账号文件名、Key 或聊天正文。归档也为 `600`，内部清单可能含私密路径，不得公开。

本轮两次备份均通过 11 个 SQLite 的完整性检查、隔离恢复与文件哈希核验，原服务重启后消息记录保持不变；第二轮包含新原生自动登录配置。两轮归档均为最终追加消息之前的 6 条入站、6 条成功出站快照，不包含之后的第 7 条。该测试证明备份内容可在隔离目录恢复，不代表已切换生产到恢复副本。`service_state_restored` 只检查 Docker 运行状态：备份后必须另查 AstrBot 策略、QQ 登录与 Reverse WebSocket。首次重启出现的 `waiting_qrcode` 经用户再次扫码与保存原生配置处理，第二次重启的登录、连接和追加私聊检查通过；不能把容器 healthy 当作此结论的证据。恢复副本不得与原实例同时使用同一 QQ 登录态。备份外移前须加密并另行授权目的地。

不把以下功能算作生产可用：Recorder 全路径出站覆盖、Guard 之外的流式/富媒体发送、自动记忆质量、PrivateCompanion 主动聊天、可靠提醒重试、自然消息合并和严格处理顺序。Future Tasks 一次性任务失败仍可能删除；Phase 2 不扩展为 Phase 3/4 重写。
