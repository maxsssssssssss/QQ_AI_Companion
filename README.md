# QQ AI Companion

2026-10-27：主动频率已调整为**90分钟最短冷却、每日96次模型评估机会、12轮发送尝试**，09:00–23:00不变。22:47:10 +08:00仅重启AstrBot生效，健康核验PASS；空闲配置60分钟、原生普通有效值45分钟保持。原生日内节奏等限制继续保留，额度不是发送目标。[参数、验证与回滚](docs/phase3-proactive-frequency.md)。

2026-10-26：当前人格为用户提供的V0.4，12:24:27 +08:00重启AstrBot后生效，QQ/OneBot核验通过。[发布记录](docs/phase3-persona-v04-release.md)。

2026-10-26早前发布（已由V0.4取代）：人格更新为用户提供的V0.3 Candidate，11:53:50 +08:00重启AstrBot后生效，QQ/OneBot核验通过。[发布记录](docs/phase3-persona-candidate-release.md)。

2026-10-26早前联合发布：[V4多气泡＋V0.3人格已联合上线](docs/phase3-bubbles-v4-persona-release.md)，00:56:45 +08:00启动核验PASS，仅重启AstrBot；QQ在线，NapCat、既有聊天/记忆和配置保持。

2026-10-26发布前开发记录：V4积极分气泡兜底完成本地实现与历史回放；当时仅本地验证，随后已按上述联合发布上线，V3.1为旧版。结果与边界见[专项验证](docs/phase3-multibubble-v4-validation.md)。

2026-10-25 20:15 +08:00：[公开搜索误拦修复](docs/phase3-search-relaxation.md)已上线。允许公开关联词、翻译、近轮话题承接及正常网页参数；保留3/60额度、凭据/隐私和URL安全检查。177项自动回归通过，原失败公开查询的真实DeepSeek Search返回3条结果；仅重启AstrBot，QQ/OneBot在线。

2026-10-25 18:35 +08:00：[主动参数与多气泡清理联合发布](docs/phase3-tuning-bubbles-release.md)已生效。80条近期消息/16000字符、8条主动摘要/普通24小时、每日48次评估/8轮尝试/2小时冷却、北京时间09:00–23:00；同时修复原生行尾空白清理导致的兜底漏分泡。仅重启AstrBot，QQ与OneBot已恢复，聊天、记忆、NapCat及当天计数保持。

2026-10-24最新维护：[主动空计划恢复](docs/phase3-proactive-plan-recovery.md)。修复长期主动启用后候选仍为空的启动缺口；沿用原生调度、当前模型及Guard，主动次数和时段保持。发布只更新一个桥接源码并重启AstrBot，保留NapCat、现有聊天和记忆；验证与上线结果见报告。

基于 AstrBot 的独立集成项目。**Phase 1 的离线集成交付已保留；Phase 2 已完成 VPS 部署、真实 DeepSeek 与 QQ 文本/图片私聊、归档、隔离备份恢复及一次 QQ 重启登录/协议恢复，当前总状态为 PARTIAL。** 真实非授权账号负例和长期稳定性仍需验收。Phase 1 使用合成 OneBot v11 事件和 Mock LLM，在断网容器内运行真实 AstrBot、原生适配器、模型 provider、数据库和所选插件。

最终 P0 判定、逐项证据与剩余限制见 [Phase 1 验收报告](docs/phase1-validation.md)和[最终交付报告](docs/delivery-report.md)。启动成功、doctor 通过或单个测试返回零，都不能代替全部 P0 验收结论。

完整任务书的执行索引见 [Phase 1 范围](docs/phase1-scope.md)：只有 T01–T20 全部为 PASS 才能标记 Phase 1 PASS；P1 的已验证、受限和未运行项目另行记录。

## Phase 3 当前上线与验收

2026-10-24基础发布记录（人格与分段版本已由上述后续发布更新）：[清洁开始与统一r4发布](docs/phase3-fresh-start-release.md)。已保留小鲸V0.2、获准L1 3组/L2 12组、登录态与配置，清理旧AI聊天、聊天派生记忆及旧聊天备份；以后新聊天正常记录。r4的13个代码文件＋5份配置已安装，包括C/80轮压缩、长期主动、新气泡提示词、V3.1与通用公开搜索。固定镜像容器已重新创建，**05:10:10 +08:00启动健康与QQ/OneBot连接核对PASS**。未增加真实API或QQ实验。

当前长期主动已更新为北京时间09:00–23:00、每日最多12轮发送尝试和96次模型评估机会、间隔至少90分钟，无72小时截止；10-24的5/5及3小时为历史配置。保留公开搜索3/60额度与隐私校验；QQ客户端加密消息库因登录态保留，不能称所有QQ端历史都已删除。旧备份不可用于恢复已清除聊天，代码配置回滚及新的清洁基线见发布记录。

### 本次清理之前的历史发布与验收记录

下面保留每次开发和当时状态；其中“待发布/关闭/旧原文保持”等不是上述新安装的当前状态，逐项验收层级与用户豁免不因此改变。

2026-10-24 [V3.1 多气泡清理兼容](docs/phase3-multibubble-v31-validation.md)：100字符内、2–4完整句、短句合并，最终仅切分原生清理后的正文；不恢复删除的句号或被拦截内容。本地验证完成后用户已授权同步到统一待发布 r4，结果以[同步回执](docs/phase3-pending-sync.md)为准；保持不应用、不重启。

2026-10-24[通用公开搜索候选](docs/phase3-public-search-validation.md)取消三个主题与域名白名单，保留每轮3次/每天60次、私料保护与URL/DNS校验；当前运行实例仍是旧范围，新候选按要求只同步、不重启。

最新同步流程：C/80轮、长期主动、新多气泡提示词及通用公开搜索合入VPS统一待发布候选，未来本地更新同样同步，等待用户通知后统一生效。同步不修改运行文件或重启，详见[统一候选与同步约定](docs/phase3-pending-sync.md)。

2026-10-24多气泡V3增量见[专项记录](docs/phase3-multibubble-v3-validation.md)：148项组件/发布测试及23项完整隔离联调通过；核心人格为V0.2，提示词保持原样。Guard增量已安装，但此次AstrBot重启违反用户“先保存、不重启”的要求，已停止后续发布；V3真实QQ自然度尚未验收。后续配置仅保存，等待明确通知统一生效。

2026-10-24待生效调整：[Token摘要压缩及失败回退80轮](docs/phase3-context-compression.md)、[长期主动规则](docs/phase3-proactive-continuous.md)均已单独保存。主动候选为北京时间09:00–23:59、每天5次尝试、至少3小时、每天5次模型评估（跳过/延后也计数），取消72小时截止；按要求不重启，等后续统一生效。

Phase 3 已在用户批准范围内完成首轮受限上线：V0.1 人格、自然文本、自动记忆、多气泡和三个审核主题的 Search/Fetch 已启用。**整体仍为 PARTIAL**。真实 QQ 普通回复、两个自然气泡、搜索前回应→Search/Fetch→最终回答已有发送和归档证据；旧22条获准历史已自动摘要且原文指纹保持。本人已批准的L1 3组、L2 12组资料完成窄导入和来源核对，此前真实资料抽样多次失败；V2 r2及后续原生上下文修复、完整自动回归后，最新真实资料验收已本人确认通过；L3未导入，首次主动窗口尚未开启，后台搜索关闭。

多气泡复用 PrivateCompanion 原协议，单次主模型生成，Guard 逐条发送和记账。用户确认实际两条回复“自然，没有重复内容”。连续输入自测反馈保留，但本轮实际审计没有三个来源归组的证据，重复测试按用户要求跳过；新版图片完成原生/真实Flash合成验证，真实QQ单图及追问已获本人确认；小字/两图实测由用户豁免。资料准确性、完整真实联合和主动验收仍各自有门槛。

2026-10-24资料上下文修复已上线：只更新3个Runtime源码，AstrBot新停写备份和16库隔离恢复通过，NapCat保持登录。本人已确认资料原题、关系区分、新会话召回、普通聊天、技术长答、单图和图片追问正确。**本轮按用户决定收尾：31/36限定层级PASS、5项用户豁免，Search26/26限定层级PASS；整体技术状态仍PARTIAL。** 主动功能按用户最新要求保持OFF，72小时及其他剩余测试全部跳过，无自动启用窗口。历史失败和未测范围保留。详见[四项体验V2](docs/phase3-four-capabilities-validation.md)。

逐项结果见 [36 项 P0 验收](docs/phase3-validation.md)、[26 项自然搜索验收](docs/natural-agent-validation.md)、[多气泡专项](docs/phase3-multibubble-validation.md)、[资料召回修复](docs/phase3-profile-recall-validation.md)。实现和复现见 [Phase 3 实施](docs/phase3-implementation.md)，生产切换及恢复见 [Phase 3 部署](docs/phase3-deployment.md)，资料范围见 [私人资料预览](docs/private-profile-import.md)。历史失败及旧报告保留。GitHub 发布仅包含工程源码、合成测试及脱敏报告；私有运行证据、访谈、凭据、登录态和第三方依赖源码不随仓库发布，不改写 Phase 1 历史。

## Phase 2 当前状态

截至本次接管记录，已通过用户独立核对的主机指纹恢复 SSH 连接；在随后明确授权的范围内完成 Ubuntu 系统更新，安装 Ubuntu 仓库提供的 Docker `29.1.3` 和 Compose `2.40.3`。SSH 保留现有配置。系统提示需要重启，另有内核/固件相关包保留；尚未执行重启，不能声称运行中内核已完成更新。

生产接线沿用 AstrBot `4.29.1`、NapCat `4.18.38` 和 Phase 1 锁定的三个插件，不重新开发 Phase 1。生产配置、镜像构建入口及补充的启动策略位于 [deploy/phase2](deploy/phase2/) 和 [integrations/phase2_policy](integrations/phase2_policy/)；启动策略复用既有 Guard、命令 ACL 和人格 API，默认关闭主动消息及自动工具调用。

AstrBot 生产镜像已通过 152 个锁定包、512 个 Python 源文件检查并健康启动，三个插件已实际加载。NapCat 私有派生镜像已运行，用户本人已用备用 QQ 扫码登录。真实 DeepSeek 8 项受控 API 测试通过；用户确认 QQ 两轮文本上下文、图片中 3 个红色方块的识别，以及重启后追加上下文回复均正确。最终 Guard 记录 7 条入站、7 条成功出站和 1 条发送前被拦截的空文本，Recorder 记录 7 条入站（含 1 条图片）。两轮停服备份、各 11 个 SQLite 完整性检查、隔离恢复及文件哈希校验通过；备份时的 6 条入站与 6 条成功出站重启后保持不变，最终追加的第 7 条不在这两份备份内。NapCat 首次重启返回等待扫码状态；用户再次扫码并保存原生自动登录配置后，第二次受控重启无需再次扫码，登录状态、内部 Reverse WebSocket 与真实私聊恢复。发布前回归的 139 项 Phase 2 专项测试全部通过；一次恢复不代表登录态长期有效。

NapCat 配置使用 `logging.driver=none`，避免上游启动输出的 WebUI token 或二维码进入 Docker 日志；上游认证后可访问的内存日志仍可能保留 token。控制台可访问、容器健康或启动策略通过都不等同完整生产验收通过。具体证据及剩余问题见 [Phase 2 实测记录](docs/phase2-validation.md)、[部署说明](docs/phase2-deployment.md)和[已知问题](docs/known-issues.md)。

## 固定版本与复用边界

- AstrBot：`v4.29.1`，提交 `dffe922773725e9c178392c423676ad65044748c`，原始源码保持不变。
- 运行环境：Python 3.12，`linux/amd64`；基础镜像以 digest 固定，Python 依赖以版本和制品 SHA-256 固定。
- 第三方候选：Message Recorder、MemoryCompanion、PrivateCompanion；仓库、固定提交、兼容范围和源码摘要见 [plugins-lock.json](config/plugins-lock.json)。
- Phase 1 补充配置、启动/验收脚本、备份工具和轻量 `phase1_guard` 插件。Guard 通过 AstrBot 公开 API 补足私聊去重、双向原文归档及发送结果记录；Phase 2 补充生产入口接线与日志脱敏，不修改锁定的上游源文件。

第三方插件仅下载到忽略跟踪的 `.runtime/upstream/` 做本地原样评估，不进入本项目 Git、构建镜像或发布包。Message Recorder 的许可证声明存在冲突；MemoryCompanion 与 PrivateCompanion 未找到项目级许可证。详细核查与使用边界见 [依赖与许可证](docs/dependencies.md)、[兼容性](docs/compatibility.md)和[插件说明](docs/plugins.md)。

普通 QQ 的 Phase 2 协议接入选用锁定的 NapCat，QQ 扫码由用户本人完成。已核查 DeepSeek [官方型号说明](https://api-docs.deepseek.com/quick_start/pricing/)与[图片输入文档](https://api-docs.deepseek.com/guides/vision/)：`deepseek-flash` 支持图片输入，`deepseek-v4-pro` 不支持。本次真实 API 与 QQ 图片测试证据单独记录在 Phase 2 报告；Phase 1 离线测试使用 `phase1-mock`，不能充当真实 DeepSeek 调用证明。

## 复现离线验收

宿主需要 Linux、Bash、Git、Python 3.12 和可访问 `/var/run/docker.sock` 的 Docker/BuildKit。使用普通非 root 用户执行；测试容器使用调用者 UID/GID，验收会检查非 root。首次构建和下载源码需要访问 GitHub、Python 包索引及基础镜像仓库；构建保留 TLS、依赖哈希和源码摘要校验。

在仓库根目录执行：

```bash
bash scripts/bootstrap.sh
python3 scripts/fetch_plugins.py
bash scripts/doctor.sh
python3 scripts/check-repro.py
bash scripts/test-phase1.sh
```

`bootstrap.sh` 获取锁定的 AstrBot 提交并构建 `qq-ai-companion:phase1`。已有上游 checkout 若版本不符或存在本地改动，脚本会拒绝覆盖。构建时只使用最小临时上下文；第三方插件、运行数据、Git 元数据和凭据不加入镜像。

`fetch_plugins.py` 获取并校验三个候选插件的原始固定提交。下载不代表获得再分发授权；缺少所需网络、源码或许可条件时，应保留明确的阻塞状态。

`doctor.sh` 是只读预检，检查配置、版本锁、路径、时区、Docker 镜像、公开端口和常见凭据误跟踪迹象；它不启动服务、不修复配置，也不代替功能验收。

`test-phase1.sh` 是总验收入口。真实消息链路由 `scripts/test-offline.sh` 在 `--network none` 容器中执行；运行器启动真实 AstrBot 子进程和 loopback 模拟对端，完成检查后关闭服务。它不是持续在线的 QQ 服务，不需要真实 QQ 登录或 LLM 密钥。每次集成运行使用独立状态目录，既有运行数据保留。

可单独定位功能：

```bash
python3 scripts/configuration.py config/offline.json
python3 -m unittest discover -s tests -p test_configuration.py -v
python3 -m unittest discover -s tests -p test_guard.py -v
python3 -m unittest discover -s tests -p test_backup.py -v
bash scripts/test-offline.sh
bash scripts/test-future-tasks.sh
```

新克隆不会包含本地验收证据，必须先运行 `python3 scripts/check-repro.py` 生成 T01 所需的重建记录，再运行总验收。修改执行代码或配置后，先保存本地提交，再重新生成复现证据，最后运行受影响的验收。

原生调度器测试依赖镜像中的 AstrBot；运行它应使用 `test-future-tasks.sh`。其余以上单元测试使用宿主 Python 标准库。不要把存储或配置单元测试当成真实协议集成验收。

## 配置、人格与数据

2026-10-24：[会话Token摘要压缩](docs/phase3-context-compression.md)已随统一r4安装。取消固定20轮限制，摘要失败回退最近最多80轮；启动加载核对见[本次发布记录](docs/phase3-fresh-start-release.md)。

主要配置为 [config/offline.json](config/offline.json)。启动前进行字段检查，离线 profile 只接受 loopback OneBot/LLM 地址、明确的测试凭据、合成身份白名单和 `Asia/Shanghai` 时区。`.env.example` 不包含真实密钥；脚本不会自动加载其中变量。

当前线上核心已按用户全文更新为 [V0.4 人格](persona/phase3-v0.4.md)，原生记录及渲染正文已核验；[V0.3 Candidate](persona/phase3-v0.3-candidate.md)、[V0.3](persona/phase3-v0.3.md)与[V0.2](persona/phase3-v0.2.md)保留为历史模板，接入与回退见[人格说明](docs/persona-integration.md)。以下旧模板入口仅描述 Phase 1：模板位于 [persona/core_persona.md](persona/core_persona.md) 和 [persona/relationship.md](persona/relationship.md)，读取 `<ASTRBOT_ROOT>/persona.md` 后通过 AstrBot persona API 更新 system prompt。资料示例 [user_profile.example.md](persona/user_profile.example.md) 是合成说明，不会自动成为用户事实。

集成验收有意使用带 `PERSONA_ALPHA` / `PERSONA_BETA` 标记的外置合成人格，以真实模型请求验证修改和重启生效。Mock 回复只能验证请求链路，不能评价人格自然度、亲密关系表现或模型能力。

| 路径 | 用途 |
| --- | --- |
| `config/`、`lockfiles/` | 可审阅配置、固定上游版本和依赖哈希 |
| `integrations/phase1_guard/` | Phase 1 轻量公开 API 插件 |
| `deploy/phase2/`、`integrations/phase2_policy/` | Phase 2 生产配置、镜像入口、启动策略和日志保护 |
| `.runtime/upstream/` | 忽略跟踪的原始上游 checkout |
| `.runtime/runs/<run-id>/` | 每次运行的真实数据、配置及插件状态 |
| `evidence/runs/<run-id>/` | 本次命令输出、协议记录、启动信息、结果 JSON 和摘要 |
| `.runtime/latest-evidence` | 最近一次消息集成验收的证据目录 |
| `evidence/future-tasks/` | 原生任务调度行为的独立检查证据 |
| `evidence/phase1/latest/` | 最终交付选择的合成证据与机器可读验收汇总 |
| `backups/` | 本地备份；忽略跟踪 |

状态目录中的 `data/data_v4.db` 属于真实 AstrBot；`data/plugin_data/` 保存各插件自己的 SQLite 或 JSON；`data/config/` 保存本次插件配置。Git 只保留源码、配置、依赖锁、测试和必要文档；整个 `evidence/`、运行数据、日志、备份及第三方源码均不上传。验收证据由上述命令在本地生成；历史报告中的证据路径指向原开发工作区，克隆仓库后需自行重跑。报告采用哪次运行的证据，以验收报告列出的路径和摘要为准，不能混用旧运行结果。

## 停机备份和恢复

Phase 2 生产部署使用 [scripts/phase2-backup.py](scripts/phase2-backup.py)。默认仅检查文件类型；显式 `--apply` 后记录原运行服务、停止两个容器，分别备份 AstrBot state 与 NapCat 配置/QQ 登录态，并恢复到全新的隔离目录验证。结束时只恢复原本运行的服务，不启动恢复副本。目录为 `700`、归档及恢复文件为 `600`，拒绝覆盖和链接/特殊文件。QQ 登录态按原始字节及哈希处理，不把可能加密的 `.db` 当 SQLite；完成后仍需单独确认 QQ 登录及协议重连。操作入口和验收边界见[部署说明](docs/phase2-deployment.md)。

以下为 Phase 1 离线状态的备份方法。先停止 AstrBot 和所有会写入同一状态目录的进程，再备份整个 `ASTRBOT_ROOT`。集成运行器返回后已关闭其启动的服务；独立手动进程需要调用者自行停止。以下 `<run-id>` 替换为实际运行 ID：

```bash
state_path=".runtime/runs/<run-id>/base"
python3 scripts/backup.py create "$state_path" backups/phase1-state.zip
python3 scripts/backup.py restore backups/phase1-state.zip .runtime/restored-phase1
```

格式是 ZIP，包含 `manifest.json`、逐文件 SHA-256、配置、完整数据与插件状态。备份与恢复都会检查 SQLite 完整性，并处理已提交的 WAL 数据。归档必须位于状态目录之外且尚不存在；恢复目标必须完全不存在，包括不能预先创建空目录。

恢复会拒绝校验和不符、路径穿越、链接、缺失/多余文件和损坏 SQLite。SHA-256 证明完整性，不证明归档发布者身份；仅恢复可信来源的本地备份。备份不是内容脱敏工具，第三方插件源码与数据仍受原许可和本地评估边界约束，不应随备份公开发布。

停机后的合成聊天数据也可以单独导出为 JSON：

```bash
python3 scripts/export_data.py "$state_path" backups/phase1-synthetic-export.json
```

导出只选择已知聊天数据表和字段，保留原始时间语义，省略配置和命名秘密字段；输出文件不得已存在。聊天文本保留原文，工具不保证扫描掉文本中的秘密，因此只用于本轮合成状态。JSON 导出不包含完整可恢复状态，不能代替 ZIP 备份。

## Phase 1 历史验收范围

真实基础链路、持久化、进程重启、备份恢复、配置和隔离通过实际测试取证；三个插件按真实加载、命令、数据和上下文注入分别记录。P1 兼容性或授权问题单独列明，不由替身插件冒充完成。

Phase 1 对 AstrBot 上游 WebUI 仅作来源与能力核查，离线入口是 headless，仪表盘关闭，没有实际 UI 验收。原生未来任务的调度、落盘和重启行为有独立检查；其中一次性定时任务失败后会被上游删除，不能称为可靠重试或生产补发。

Phase 1 未登录真实 QQ、未部署 VPS、未使用真实用户隐私，也未发布授权不明的第三方源码。后续 Phase 2 的执行记录单独保存，不回写为 Phase 1 的验证成果。更多设计和边界见 [架构](docs/architecture.md)及[安全说明](docs/security.md)。
