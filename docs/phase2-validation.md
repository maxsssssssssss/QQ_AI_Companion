# Phase 2 基础上线交付与验收记录

本记录描述 2026-10-20 至 2026-10-21 在用户授权的 VPS 上完成的基础上线交付。Phase 2 总状态为 **PARTIAL**。真实 DeepSeek API、用户确认的 QQ 文本上下文与图片回复、消息归档、两轮停服备份及隔离恢复已通过。NapCat 首次重启等待扫码；用户再次扫码并保存原生自动登录配置后，第二次受控重启无需再次扫码，QQ 登录、内部协议连接及追加上下文私聊均恢复。保留 PARTIAL 的原因是：真实非授权账号拒绝、长期稳定性、远端模型超时恢复、云安全组完整核对及内核维护尚未完成。历史 Phase 1 P0 20/20 PASS 仍属于原交付证据，不能替代本次在线验收。

## 接管与主机

| 检查 | 状态 | 实测结果 |
| --- | --- | --- |
| 原项目接管 | PASS | 原仓库 main，Phase 1 commit `0b6bbf147a83f3e17c598c7a4d49386b6553c823`；七份交接文档及依赖锁已读；未推送或改写提交 |
| 本地环境 | PASS | Windows 上的 WSL2；使用 Windows OpenSSH 和 Windows ACL 保护的既有 PEM，未输出或上传私钥 |
| SSH 主机身份 | PASS | 用户通过腾讯云控制台提供 ED25519 `SHA256:CIjkZTebFvdK93gOeOJqT8G/lXWExEazfi7AnKafemY`；网络取得公钥与此严格匹配后写入 known_hosts |
| SSH 登录 / sudo | PASS | `qq-companion-vps`，ubuntu，publickey，StrictHostKeyChecking=yes；sudo -n 可用；多次新连接成功 |
| OS / 架构 | PASS | 更新后 Ubuntu 24.04.5 LTS（初始 24.04.4），x86_64；当前运行内核 6.8.0-138-generic |
| CPU / RAM / swap | PASS | 2 vCPU；RAM 3,904,782,336 bytes；swap 2,084,564,992 bytes |
| 磁盘 | PASS | 60 GB 块设备；根文件系统 63,290,032,128 bytes；初始化前可用约 51 GiB，构建中持续检查 |
| 时间 | PASS | Asia/Shanghai，NTP 已同步 |
| Git / 软件源 | PASS | Git 2.43；现有腾讯云 Ubuntu APT 镜像，保留 Ubuntu 签名校验 |
| Docker | PASS | Ubuntu 官方签名包 docker.io 29.1.3；Compose 2.40.3；Buildx 0.30.1；daemon active；日志轮转 10m × 3 |
| 软件安全更新 | PARTIAL | 已安装当前普通升级包；内核/固件相关 5 项保留；主机提示 restart-required，尚未重启，不能声称运行中内核已完成更新 |
| SSH 连续管理 | PASS | 更新时 ssh.service 停止且 /run/sshd 缺失；预先保留的管理连接用于恢复 ssh.service，sshd -t 与新连接随后通过；未改 SSH 认证配置 |
| 主机防火墙 | PASS（现状检查） | 初始 ufw inactive；初始公网监听仅 SSH 22；未开放全部端口或修改 SSH 管理方式 |
| 腾讯云安全组 | NOT TESTED | 没有可用的控制台授权接口，不能从主机侧推断完整云侧规则 |

## 网络与制品

| 检查 | 状态 | 实测结果 |
| --- | --- | --- |
| GitHub / DeepSeek | PASS（基本可达） | GitHub HTTPS 200；DeepSeek 文档 200、API 无凭据请求 401；保留 TLS 校验，401 不代表真实模型调用通过 |
| Docker Hub / Docker CE 下载源 | FAIL（VPS 直连） | registry/auth 超时，download.docker.com 重置；改用签名 Ubuntu Docker 包与本地校验制品经 SSH 传输 |
| 插件源代码 | PASS | 三个原始上游固定 commit 和 source_tree SHA 校验通过；VPS 直接 fetch Memory 曾 early EOF，原下载目录已保留，使用本地已校验副本 |
| Python 基础镜像 | PASS | 原始 index digest `34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258` 离线导入成功；无网络运行 Python 3.12.15 |
| AstrBot 上游源码 | PASS | 官方 v4.29.1 amd64 manifest `0c3b8ef7485dbd15176637133c23dfbdb80675d174545a997648b9c1db7d53c1`；512 个根目录及 astrbot Python 文件与固定 commit 完全相同 |
| AstrBot 生产依赖 | PASS（校正后） | 基础镜像原有 18 项差异/缺失；最终 152 个锁定运行/构建包全部匹配，pip check 通过；移除冲突的非运行发布工具 commitizen，原锁未改 |
| NapCat 镜像构建 | PASS | 固定 4.18.37 官方容器基础 + 原始 4.18.38 Shell ZIP 的私有派生镜像构建、基础层链和 ZIP 哈希复验通过；已用于本轮真实 QQ 私聊 |
| 生产配置生成 | PASS（静态） | 唯一私聊白名单、群聊关闭、主动发送关闭、随机 Token、600 Secret 文件；真实账号标识仅存运行配置 |
| AstrBot 生产启动 | PASS（应用启动） | 官方 main.py、UID10001、只读根文件系统；HTTP200、healthy；三个固定插件与 Guard 加载成功；安全注入 Key 后启用 Flash，本次运行策略 PASS |
| 生产日志脱敏 | PASS（已测范围） | 实际最终镜像内 13 项日志保护测试通过，含真实上游 stdout/LogBroker；不覆盖第三方任意 print/native 输出 |
| WebUI 本机隧道 | PASS | Windows 127.0.0.1:6185 经 SSH 返回 HTTP200；VPS 仅 loopback 绑定；随机登录密码以 Windows ACL 保护文件交付，不回显 |
| 最终凭据与日志抽查 | PASS（有限范围） | 6 个受保护文件均为普通文件且权限 600；近期 AstrBot 容器日志及常规 data 日志文件共 2 项中，未检出实际凭据或 Key 前缀；不包含 NapCat 原始日志，也不保证未来所有输出 |

公开上游制品摘要及层验证记录保存在被 Git 排除的 `.runtime/phase2-images/`。最终脱敏状态、消息、资源、原生会话配置和备份摘要在本地 `evidence/phase2/` 与 VPS `/opt/qq-ai-companion/deployment/evidence/` 对应保存；构建证据在 `deployment/build/runs/`，私有备份结果在 `deployment/backups/`。VPS 原 `repo/evidence/phase2/` 仅保留早期初始化证据，不作为最终在线状态入口。凭据、聊天正文和 QQ 登录态不得写入本报告。

## 在线验收

| 项目 | 状态 | 实测结果与边界 |
| --- | --- | --- |
| DeepSeek 安全凭据注入 | PASS | 用户本人通过 SSH 隧道 Dashboard 掩码字段保存；停服后安全接收为 600 Secret 并原子启用主模型；没有输出 Key |
| 真实中文 / 多轮 / 工具 / 图片调用 | PASS（直接 API） | 官方 Flash/Pro 共 8 次受控请求：中文、上下文 nonce、工具往返、合成图片真值、400 错误全部通过；总 793 tokens；不代替 AstrBot/QQ 链路 |
| 超时保护 / 远端超时恢复 | PASS（本地保护） / NOT TESTED（远端场景） | 已配置每请求 60 秒硬期限、有限调用次数、无自动重试，本地超时处理单元测试通过；未故意制造真实 DeepSeek 超时，不能声称远端超时恢复已通过 |
| QQ 首次登录 | PASS | 用户本人备用 QQ 扫码；认证状态接口 isLogin=true、coreReady=true、loginPhase=ready；不读取二维码或 QQ 资料 |
| 真实私聊收发与上下文 | PASS（本轮样本） | 授权主 QQ → NapCat → AstrBot → DeepSeek → QQ 回复；用户确认两轮文本及 nonce 上下文正确，并确认重启后追加上下文回复；最终共 7 条入站、7 条成功出站 |
| QQ 图片输入 | PASS（本轮样本） | 用户发送含 3 个红色方块的图片，机器人回答 3 个；用户确认结果正确，Recorder 识别到 1 条图片消息；不代表所有图像理解场景通过 |
| 非授权用户拒绝 | NOT TESTED（在线） | 配置与合成门禁测试通过不等同真实账号验收 |
| 入站 / 出站归档 | PASS（已测文本发送路径） | Guard：入站 7，出站状态总数 8，其中 sent=7、failed=1、pending=0、unknown=0；failed 为 EmptyText 在 QQ 发送前拦截。Recorder：7 条入站，含图片 1 条；最新授权请求的最终 sent 回复断言匹配，缺失/未完成计数为 0 |
| AstrBot 重启与数据保留 | PASS（本轮） | 停服备份后原服务重新运行，备份时 Guard 入站与成功出站仍为 6/6，Recorder 入站仍为 6；之后追加消息使最终计数增至 7/7。恢复目录未挂载至生产容器 |
| 原生自动登录配置持久化 | PASS（配置与当前会话） | 用户再次扫码后，核对当前在线机器人身份，调用一次 SetQuickLoginQQ 保存；600 的 webui.json 落盘值匹配，current_session_verified=true、auto_login_persisted=true；未调用 SetQuickLogin |
| NapCat 重启后 QQ 登录与协议恢复 | PASS（修正后一次受控重启） | 首次停服后为 waiting_qrcode，快速登录候选为 0；用户再次扫码并保存原生配置。第二轮停服备份后无新增扫码，isLogin=true、coreReady=true、loginPhase=ready，内部 6199 端口 ESTABLISHED 连接 1 条 |
| 重启后追加聊天上下文 | PASS（本轮样本） | 用户确认追加 nonce 私聊回复正确；最终入站及对应 sent 回复已持久化，outbound_match=true，无缺失或未完成回复 |
| QQ 离线状态可观测性 | PASS（已观察状态） | 登录状态接口能区分首次 ready 与重启后 waiting_qrcode；不代表自动恢复或所有离线原因诊断通过 |
| 生产 SQLite / 备份恢复 | PASS（两轮隔离验证） | 两容器停止后备份，两轮各 11 个 SQLite 完整性检查、隔离恢复及完整文件哈希比对通过；第二轮包含新原生自动登录配置。NapCat 配置和 QQ 登录态另作原样字节备份，不作 SQLite 检查 |
| 长期稳定性 | NOT TESTED | 已有短时运行与真实消息样本；尚无长时间在线、持续负载或服务器重启后的结论 |

原 Phase 1 已知限制继续有效：Recorder 出站不完整；Guard 仅覆盖受限非流式文本；自动记忆质量、主动发送未验收；Future Tasks 一次性任务失败仍可能删除；自然消息合并与严格顺序未完成。当前不会把这些能力标记为生产可用。

## 当前生产构建与资源快照

成功构建记录：`deployment/build/runs/20261020T155246Z-18340431/build-provenance.json`。

- AstrBot image ID：`sha256:a0bf74942fc2ba504932897f7f6ba8d8bd01c1084ea6448c399b8df579bc77af`。
- NapCat image ID：`sha256:2f1dd81bdd62f27b3431ac9254000602ed7ed6dfbf0a38bb6e735fd16bc4ee87`。
- 2026-10-21 00:30:59 +08:00，重启后真实聊天完成，两个容器在线：AstrBot 348.1 MiB / 1.367 GiB 限额、14 PIDs、CPU 0.03%；NapCat 180.8 MiB / 1.562 GiB 限额、107 PIDs、CPU 0.53%。主机可用内存 2,641,903,616 bytes（约 2.46 GiB）、swap 已用 536,576 bytes、磁盘可用 44,283,613,184 bytes（约 41.2 GiB），1/5/15 分钟负载约 0.05/0.13/0.14。这是短时快照，不是长期负载结论。
- 首启发现 `request_max_retries=0` 被上游内存规范化为 1；源码确认此字段含首次请求，修正为 1 并通过真实上游规范化回归测试。传输最多一次，但上游另有内容兼容修复循环，不能声称整个框架没有其他重试路径。

## 归档与备份证据

`scripts/phase2-message-evidence.py` 只汇总允许的记录数、发送状态与一致性断言，不输出聊天正文、账号标识或配置秘密。最终统计中的 1 条 `failed` 是空文本在发送前被 Guard 拒绝，不是已向 QQ 提交的有效回复发送失败；没有 pending 或 unknown。最终 7 条成功出站的证据只覆盖已测试的非流式单段文本路径，不能据此外推主动发送、工具发送或富媒体出站覆盖。

停服备份通过 `scripts/phase2-backup.py --apply` 执行，服务器结果为 `deployment/backups/validation-20261021-01/result.json`，本地脱敏摘要为 `evidence/phase2/backup-restore-01.json`。AstrBot state 复用 Phase 1 `backup.py`，纳入 1,515 个文件；NapCat 配置及 QQ 登录态按不解释内容的原始文件备份，合计 2,197 个文件。备份前类型检查无符号链接、硬链接或特殊文件，11 个 SQLite 检查与全文件哈希核对通过。

| 归档 | SHA-256 |
| --- | --- |
| `astrbot-state.zip` | `ca939ff884ae920571365e55be9c96b183b183846eb40a07dd864899d145041c` |
| `napcat-opaque.zip` | `cf15b84da5249a9f3212a2df856de02f327bbbc074ad0822c96f04cd5a39e848` |

归档及隔离恢复副本保持私有，不包含在 Git 中。隔离副本从未启动，不等于完成了切换到恢复副本的灾难恢复演练。重启后只读记录摘要保存在 `evidence/phase2/message-evidence-after-restart.json`；在线摘要按各数据库独立快照统计，SQLite 完整性结论来自停服检查。

脚本在 `finally` 中恢复原本运行的容器，未启动原本停止的服务。`service_state_restored` 仅表示 Docker 运行状态恢复；首次备份后发现 QQ 等待扫码。用户再次扫码后，使用 `scripts/phase2-enable-quick-login.py --save-current-session --apply` 核验并保存原生自动登录配置，证据为 `evidence/phase2/native-autologin-config.json`。该模式不调用 `SetQuickLogin`，不代替用户扫码。

第二轮备份包含新原生自动登录配置，AstrBot 文件 1,515 个、NapCat 配置及 QQ 登录态文件 2,198 个，各文件校验及 11 个 SQLite 完整性检查通过，隔离恢复副本未启动。证据为 `evidence/phase2/backup-restore-02.json`，归档摘要如下：

| 第二轮归档 | SHA-256 |
| --- | --- |
| `astrbot-state.zip` | `9fa556619707195b5cec23d9c1a0fcc3e21da81b0752e51ee9c5ea5e463f1931` |
| `napcat-opaque.zip` | `24771390392b14455cf335d82e61cc11a07c37e2f0ff1c58c066945e3285d83f` |

两轮备份都发生在最终追加私聊之前，只包含当时的 6 条入站与 6 条成功出站；不宣称这两份归档含最终第 7 条消息。第二次服务恢复后未再次扫码，QQ 登录状态与内部 WebSocket 连接通过独立检查，资源和日志抽查为 `evidence/phase2/resource-security-final.json`。随后用户确认重启后追加回复正确，最终 `evidence/phase2/message-evidence-final.json` 显示入站 7、sent 7、Recorder 7，最新授权入站的最终成功回复断言 `outbound_match=true`、缺失/未完成计数 0。

备份脚本本身仍报告 `qq_login_or_chat_verified=false`，表示其职责不包含登录/聊天验收；不能将独立检查结果误写成备份脚本验证了在线链路。最终 `evidence/phase2/final-status.json` 显示两容器均 healthy、无 OOM，AstrBot 策略 PASS，QQ 为 ready 且 isLogin/coreReady 均为 true。

## 追加验收证据

- DeepSeek直接API报告：VPS `deployment/evidence/deepseek-api-01.json`，本地 `evidence/phase2/deepseek-api-01.json`（均不跟踪Git）。8/8测试PASS，7个正常响应200、1个预期400；无重试、无原始响应正文。
- 上线交付时，本地 Phase 2 专项单元/组件测试 135 项全部通过、无跳过（原 93 项，加消息证据 13 项、备份边界 11 项、原生登录配置 18 项）；原 Phase 1 备份测试另有 12 项通过。重跑只把已锁定的 Loguru 0.7.3 wheel 加入临时 PYTHONPATH，未安装系统依赖；此前缺少系统 Loguru 导致的一项跳过已在最终重跑消除。13 项日志保护测试也在最终生产镜像内实跑通过。它们不替代真实账号测试。
- 2026-10-21 发布准备复核修复了快捷登录默认模式的缓存账号/当前在线身份不一致问题：保存前必须核验在线身份与候选一致，失败时停止，不保存或追加登录。新增 4 项回归后共 139 项 Phase 2 测试全部通过、无跳过；线上使用的 `--save-current-session` 模式行为未变，本次发布准备未执行服务器登录、重启或生产配置变更。
- OneBot内部端点无Token的WebSocket握手返回401；没有发送测试QQ消息。
- NapCat 管理端口为 127.0.0.1:6099，原始 Docker 日志禁用。AstrBot 当前策略 PASS；报告中的 real_api_tested=false/real_qq_tested=false 是策略插件声明其自身不承担在线验收，在线结果以本报告和独立证据为准。NapCat 健康检查不检查 QQ 登录状态。

## Phase 3 前置结论

单账号、白名单、普通文本回复与图片输入的基础通信已有真实样本，消息归档与私有备份可验证；原生配置后的服务重启登录、协议恢复及追加上下文私聊已通过一次。仍需完成真实非授权账号负例、长期观察及远端模型超时恢复验收；内核重启与保留包、云安全组核对仍未完成。自动记忆质量、主动消息、可靠提醒和消息顺序不属于本轮通过范围，不因基础链路成功而自动放开。
