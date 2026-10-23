# Phase 3 增量实施与发布边界

当前阶段：现有Phase 3候选已受限上线，本轮验收按本人“跳过全部”剩余项目的要求收尾。**整体PARTIAL；P0 31/36限定层级PASS、5 WAIVED（未实测），Search 26/26限定层级PASS。** 已有原资料题、关系区分、新会话召回、自然聊天、技术长答、单图及追问的真实确认；未测范围不外推通过。主动始终OFF，首次窗口启用准备已取消，不存在生效窗口，不再等待或自动继续UAT。已部署能力、锁定依赖、获准15组L1/L2和原始数据保持；历史失败与修复证据保留。完整矩阵见[36项验收](phase3-validation.md)和[26项搜索验收](natural-agent-validation.md)。

## 依据与基线

以用户提供的 Phase3 Master Implementation v1.0 为执行规范，其余 Natural Agent 附件、Persona V0.1 和完整访谈为设计依据。实际收到的 Persona 文件名没有 `(1)` 后缀，内容是 V0.1。任务书和访谈原件在本地 Downloads，未复制进 Git。

开发起点为 `b6dfc82abbf388d93e0012fd91a55965e66fc4fe`；Phase 1 提交 `0b6bbf147a83f3e17c598c7a4d49386b6553c823` 保留。没有 reset、覆盖旧进度、推送或升级依赖。Phase 2 原有 139 项测试先作为基线运行。只读 SSH 检查确认生产两容器健康、QQ 已登录、原策略通过，管理映射仍为 `127.0.0.1:6185/6099`。这不是 Phase 3 QQ 验收。

AstrBot 4.29.1、MemoryCompanion 2.3.1、PrivateCompanion 6.7.0、Message Recorder 0.0.4、NapCat 4.18.38 继续锁定，提交及摘要沿用 `config/plugins-lock.json` 和 Phase 2 清单。原上游源码未修改、未收录或打包公开；测试镜像只有锁定 Python 依赖，测试时以只读卷挂入上游。

## 接线与职责

- `phase2_policy` 继续负责白名单、必需插件、唯一 Persona 和启动阻断。仅在显式 Phase 3 配置下开放选定工具；先验证凭据及组件，`prepare` 只接线，待人格持久化后才 `activate` 后台任务。任何启动失败都撤销新任务；已请求 Phase 3 但插件加载失败时，不悄悄降级放行。
- `phase1_guard` 保留旧表和默认路径，增加显式开关下的 turn、source、delivery、progress 状态。所有允许的文本输出必须有独立状态，超时与崩溃视为 unknown，不能盲目重发。
- `phase3_runtime` 绑定单一 V0.1 人格、原生 MemoryCompanion、原生 PrivateCompanion 和既有 Guard，不创建第二个 Agent、记忆主库或聊天平台。
- `phase3_memory` 是原生 MemoryService 的来源、权限、批次、事件状态适配。正文仍由原生库保存；原始消息以 Guard/Recorder 档案为证。持久游标从原文档案增量恢复，首次启用跳过旧 Phase 2 档案；原生库已有的未摘要历史则另以精确清单、内容指纹及模型使用授权核验，缺少授权就阻断自动摘要。
- `phase3_search` 包装当前请求中原生 Search/Fetch 的真实调用边界，完成权限、参数最小化、一次进度、预算和回执检查。沿用原生工具结果与 tool_call_id，不另起搜索 Agent。普通闲聊没有额外搜索分类模型。
- `PrivateCompanion` 仍产生候选与正文，薄层仅负责批准窗口、暂停、生命周期、模型预算和最终受控发送。未批准的动作、群发送、虚构生活、后台联网均关闭。

模块细节：[人格](persona-integration.md)、[资料预览](private-profile-import.md)、[归组与发送](phase3-delivery.md)、[记忆](phase3-memory-validation.md)、[搜索](natural-agent-validation.md)、[主动](phase3-proactive-validation.md)。

## 四项体验V2接续

本轮沿用同一Guard、MemoryService、Private候选/调度器和主Agent，没有另一套调度器、记忆正文库或默认分段/视觉模型。新增范围及历史失败见[四项体验V2报告](phase3-four-capabilities-validation.md)。

- 已导入3条L1通过原生接口同ID提升为core_memory，保留精确正文、来源、批次及撤销关系；12组L2保留，按问题在主生成前有界读取实际正文。不是再次导入15组，也不扩展L3或用户事实。候选core预算1600，通过真实原生注入核验3条全文；检索4页、每页32条、12000字符、1.5秒上限，部分覆盖仍回答可靠部分，错误不伪装成无资料。
- 原生摘要调用前固定证据/修订快照，事务提交时再核验；纠错和撤销与短提交串行，模型调用不持有聊天锁或长事务。陈旧摘要不覆盖新纠错，不提前消费成功水位；取消时等待已开始的本地写入落定。该适配可以卸载，不修改固定上游核心。
- 主动一次主模型调用决定send/defer/skip和全部气泡；同一Guard独立记录每个气泡和整轮尝试。新话题可在未回复后再次考虑，同话题冷却，忙碌/暂停/关闭话题仍约束。失败和unknown耗费尝试，不自动重发；原生候选保存defer/到期/退役状态。**候选已实现不等于生产启用，真实主动始终OFF**；本人后来跳过全部剩余验收，首次窗口启用准备已取消，拟定72小时时间仅为历史计划，不存在生效窗口。
- 多气泡沿用Private分隔协议、最多4条、首条无附加等待、后续0.6–1.8秒，格式异常保留可读单条。图片沿用现有Provider和入站组件，保留合并顺序与历史图像载荷；含图上下文补答仍使用同一Provider，不静默切换第二视觉模型。

r2当时的完整固定框架回归为 `evidence/phase3/20261023T143959Z-12559/result.json`，**20/20 PASS，合成HTTP/OneBot，非真实QQ/DeepSeek**。I18后来发现断言会从旧历史取答案，旧结果保留但不再当成充分证明；I19图文/两图顺序、I20含图历史空正文补答和I11原生主动send/defer/skip等原证据保留。原生资料10项、摘要一致性9项、主动26项及发布恢复9项分别保留；发布恢复使用合成服务状态，不当作VPS已安装。真实Flash另用合成资料7例、主动3例、图像3例，沿用原累计预算。最新增强I18及完整20项回归见下节。

r2真实安装另有 `evidence/phase3/four-capabilities-20261023/production-install-r2.json`：新停写备份与隔离恢复PASS，L1原位3条且完整注入3条、L2保留12组，无重复导入或模型调用；48个安装目标，镜像/端口不变。安装回执本身只证明安装完成；后来23:05健康和QQ恢复，23:07资料验收失败，不能由容器健康推断体验通过。

`phase3-deploy-experience.py` 默认PLAN，复用新停写备份/隔离恢复和原生产镜像的断网worker：先ProductionWindow验证，再窄安装原生DB/既有ledger，最后first-party overlay，统一恢复原两服务。覆盖失败可在恢复服务前按journal恢复；若数据在上线后有新增则拒绝旧journal覆盖，先保护新增聊天并核对影响。不覆盖整个data、不改变SSH/端口/镜像，不推送GitHub。该候选通过生产发布前置条件后，才按用户指定顺序逐项邀请真实验收；T4/T6未过和72小时未完成的部分继续PARTIAL/BLOCKED/RUNNING。

## Gate 0–6 的实际含义

| Gate | 本地可自主执行 | 生产放行条件 |
|---|---|---|
| 0 | 基线核对、回归、合成停写备份/恢复及旧表迁移测试 | 批准维护窗口后制作新的生产一致快照，恢复到隔离目录验证 |
| 1 / 3A | 单一人格、真实合成模型场景、私人分层预览 | 确认人格体验；真实资料另按条目/用途授权 |
| 2 / 3B | 真框架合并、取消、语义气泡、逐条回执、迁移 | 白名单下小样本真实 QQ 回归 |
| 3 / 3C | 原生服务抽取、来源、修订、召回、重启与停用测试 | 批准新记忆捕获；初始资料导入独立处理 |
| 4 / 3E | 原生工具安全与合成对端、真实模型选择 | 已批准复用服务器Key的独立搜索配置；限定公开主题、每轮3/每日60尝试及10元估算验收预算 |
| 5 / 3D | 原生候选、暂停与发送边界 | 已批准：被动真实验收后下一个09:00起72小时、每天2次及4小时冷却；按真实通过时间生成绝对窗口 |
| 6 | 隔离联合、恢复、报告 | 已批准配置下 QQ+DeepSeek 联测及生产资源观察 |

并行开发与离线测试不等于跳过上线 Gate。后台定期搜索继续 `NOT_ENABLED`；Phase 4 可靠提醒、Todo、失败补发和长周期成长不在本轮扩大实现。

## 复现与证据

`python3 scripts/phase3-build-test-image.py` 校验既有离线基础镜像和 wheel 锁后构建依赖镜像；`bash scripts/test-phase3-isolated.sh` 在 `--network none`、只读根、无额外权限的容器里跑完整 AstrBot，挂入的状态全部为新建合成状态。无需挂载访谈、生产凭据或 QQ 登录态。

原生组件测试需要 `.runtime/phase3-venv`、`PYTHONPATH=.runtime/upstream/AstrBot:.` 和独立 `ASTRBOT_ROOT`。Windows/WSL 的沙箱无法运行某些本地套接字/线程时，使用 Codex 正常审批运行同一隔离测试，不关闭审批或沙箱策略。

证据目录 `evidence/phase3/` 与 `.runtime/` 被 Git 忽略；最终文档保留测试数量、层级、路径与摘要，不收录原始私聊、Key、token、二维码或访谈。真实人格输出仅保留在 owner-only 本地文件，模型内容检查与用户主观评分分开。

## 多气泡与后续增量

专项复用 Private 6.7.0 的分隔标记/解析器，通过同一次主模型生成选择1–4个语义气泡。Guard新增持久计划/逐气泡状态，首条不额外等待，后续0.6–1.8秒，期间重检插话、取消、supersede和停机。原生历史仅投影已确认发送的正文，修复Private接管后的`_no_save`释放；主动和搜索最终回答复用相同发送循环。

按用户真实QQ反馈增加可独立关闭的`phase3_plain_text_enabled`：纯文本发送前清理常见Markdown装饰，保护代码和URL，32,768字符上限、异常/空结果回退，不增加LLM调用。混合组件保持原结构，仍受既有文本出站范围约束。详细层级、计数和生产状态统一见[专项报告](phase3-multibubble-validation.md)，历史结果不覆盖。

资料生产入口拆为本机默认dry-run、隔离原生worker和只安装两份数据库的维护控制器。真实生产需实际停写和一致快照验证，不能由JSON中的“已备份”代替；候选WAL归一化，保留原usage文件，安装/恢复保留服务UID/GID。该入口本身不授予许可；本人后来明确批准的15组L1/L2已通过精确manifest执行，L3和其余访谈仍不导入。

### 23:05 +08:00 登录恢复与历史READY状态

本人扫码后已核验当前在线备用QQ身份，原生自动登录设置重新保存并验证落盘；没有额外重启或再次登录请求。23:05:16实际isLogin/coreReady=true、loginPhase=ready，内部OneBot ESTABLISHED=1，策略PASS、47源文件摘要匹配。原先waiting_qrcode及一次快捷登录未恢复的证据保留为本次发布历史。

23:05历史时点曾为 **CANDIDATE_READY_FOR_UAT**，仅表示候选自动验证及发布前置条件满足；23:07本人反馈资料失败后转为 **UAT_FAILED_INVESTIGATING**。P08真实FAIL尚未关闭，总体PARTIAL，主动OFF。仅邀请过此前失败的资料问题，未发整组作业。证据 `evidence/phase3/four-capabilities-20261023/candidate-readiness-r2.json`、`production-health-after-login-r2.json`、`napcat-save-current-session-r2.json`。

## 2026-10-24 统一原生资料发布候选的实现与验证

最新实际失败为本地found452字、模型44字/清理43字并Guard确认发送，无空正文或补答；模型否认已有资料。隔离生产副本已证明最终请求包含完整L2和3L1，进一步对照不能再只检查数据库命中。仅compact来源元数据的真实合成长历史场景仍失败；相同14169输入tokens的system对照否认，而放进同一当前原生记忆面后正确区分合成事实与关系未知范围。证据 `four-capabilities-20261023/real-shaped-profile-native-scope-r1.json`，不将有限合成样本当作生产语义全面通过。

`profile_publication.py` 在现有hook内通过 MemoryCompanion `_publish_reply_memory_context` 统一发布L2证据：当前唯一 `<MemoryCompanion-Context>` 内仅替换本适配器的 `<phase3_approved_profile>` 子块，保留原L1/native正文及refs，system不重复放事实。主题目录读取先完成，发布成为该hook最后的await。原生identity将协调器路由绑定到真实当前event，发布前及统计await后核对ACL、撤销、精确revision和连续性；取消/超时/失效只清理本轮无效输入。重复发布退役本适配器新增refs，不删除旧聊天；统计失败须有匹配新native snapshot/ref/digest才承认发布。原生RoleInputSources边界复用，未知可选角色来源仍标不完整，不宣称已单独验证完整Private角色注册生命周期。

该适配不更改MemoryService主库、15组ID/正文/来源/审批或L3边界，也没有硬编码答案、额外分类模型和第二主Agent。资料投影正文置前，保留ID/revision/来源数量与顺序散列；来源原文仍在原库，compact只是请求优化而非独立修复。

候选自动验证：publication11 + evidence12共23原生测试、12严格fixture、8audit、4probe契约PASS；证据 `four-capabilities-20261023/profile-native-publication-validation.json` 及同目录相应summary。增强I18后完整固定框架 `20261023T160122Z-23369/result.json` **20/20 PASS**，system标记0、当前原生块1，保留16条旧失败历史/4个工具对，1次主请求及Guard确认历史；原生记忆、主动、媒体及补答相关回归通过。对端合成，不将分组相加为新全量测试总数，仍有效Phase1/2结果复用。

新隔离生产副本最终捕获 `private-native-final-request-r1.json` 确认完整452字L2和3L1在同一最后user块，8个原生refs/snapshot有效，无空召回提示；不是原失败实时抓包。首次私人模型诊断被自动审批拒绝且未执行；后来本人明确授权后，一次 `private-native-model-r1.json` 重放取得196字非否认实质回答、stop，工具/QQ/生产修改均0。估算0.025070元，原10元预算在该诊断时点累计保守预留5.50元，非账单；完整载荷和回复仅留服务器私有目录。该诊断时点答案完整性及自然度仍待本人验收、P08仍FAIL。

### 三文件实际发布与00:14历史READY

`phase3-20261024-profile-native-r2` 已完成源码窄发布，同名r1只在PLAN阶段失败、未安装。新停写备份 `phase3-profile-native-20261024-r2` 的16库完整性及隔离恢复通过后，仅安装Runtime `main.py`、`profile_recall.py`、`profile_publication.py`；不写数据库或配置、不重新迁移3L1/导入12L2、不覆盖整个data。NapCat未重启，原QQ实例与登录保持，只恢复原AstrBot服务。

00:14:41 +08:00健康、启动策略、49份第一方源码/配置/固定上游锁核验通过，QQ在线、OneBot连接1。34条记忆、90条timeline原字段、48条入站和13条legacy出站均保持，模型调用0。证据 `four-capabilities-20261023/runtime-code-{plan,install,health,preservation,readiness}-r2.json`；回退入口为该release的 `astrbot-code-journal`，只恢复三文件并受控重启AstrBot，不回滚数据或丢失新聊天。

00:14历史时点为 **CANDIDATE_READY_FOR_UAT**，总体PARTIAL，当时P08原FAIL未关闭，仅邀请原资料问题。安装、离线验证、私人模型重放和真实QQ/T6分别记录，不能以任何前一层替代实际体验。

### 首题T4通过后续接关系区分的历史

本人对原资料题明确确认“通过”；`four-capabilities-20261023/uat-profile-native-pass-r1.json` 核对00:16两个相关新轮次均found完整452字、native_published/snapshot有效，原final129/126字→清理128/125字，单条各一次attempt确认sent，历史与实际发送hash一致；无补答、无公开Search。该次真实正确完整性关闭P08在此题范围内的FAIL，历史证据保留，不把所有主题或新会话同时标通过。

首题通过时转为 `UAT_IN_PROGRESS`，当时只邀请“玩得最久”的关系区分题；随后该题及新会话通过，见下节，其他体验仍依原顺序继续。该窗口2条Agent run峰值未缓存估算0.050580元，另1次原生自动摘要估算0.016106元，非账单、Agent run数非确定HTTP数；沿用原预算。主动仍OFF，不因首题通过就伪造完整被动验收时点或提前开启窗口。

### 00:19–00:21 +08:00 关系区分与新会话的独立T4证据

本人确认“玩得最久”的回答符合资料、没有乱猜；`four-capabilities-20261023/uat-profile-relation-pass-r1.json` 记录完整452字原生证据及发布回执，原正文31字、清理30字，一次确认sent，历史一致，补答0、Search0。它没有把常玩或喜欢自动当作时长事实。

本人执行/new后再次确认资料回答正确完整；`uat-profile-new-session-pass-r1.json` 分别记录新会话提示与新问题。新问题的实际模型请求 `history_items=0`，仍found完整452字、native_published/snapshot有效，原正文90字→清理89字，单条一次确认发送并正确保存历史，补答0、Search0。新会话保留旧聊天，不靠清空原记录通过；此项为当前获准主题的真实新上下文召回，不代表所有主题覆盖。

当时仍为 `UAT_IN_PROGRESS`，普通自然聊天当时是唯一邀请项，其余结果不在该时点预填，主动仍OFF。关系题和新会话各记1个Agent run，峰值未缓存估算分别0.028366、0.016702元，非账单、非确定HTTP数。`uat-budget-reservation-r1.json` 已为顺序QQ UAT及原生摘要追加0.50元保守预留，累计由5.50变为 **6.00/原10元**；不是重设预算，不把保守预留当实际花费。

## 最终收尾：剩余项目由本人豁免，主动保持关闭

后续真实QQ自然聊天由本人评价“自然，没有重复或刻意拆句”：`four-capabilities-20261023/uat-natural-chat-pass-r1.json` 记录31字原正文→30字清理发送，一次确认sent及相同历史hash。技术长答本人确认“完整、可读，没有乱拆或截断”：`uat-technical-long-pass-r1.json` 记录989字原正文→957字可读单条，一次确认sent、历史一致，不能据此声称任意超长平台边界均验收。这两轮补答0、Search0，评价来自本人，不是模型自评。

单图与后续追问本人确认正确。`uat-image-pass-interrupt-skipped-r1.json` 与 `uat-image-payload-proof-r1.json` 记录JPEG 960×1446、185352 bytes进入实际Agent准备路径；存入Agent历史的inline载荷与原图hash一致，后两轮均恢复历史图像1、不可用0。实际Provider为deepseek-flash、无caption备用Provider；图像回答及两轮跟问分别36/58/84字，各一次确认发送及历史一致。这是实际Agent准备/历史及固定Provider路径证据，非实时HTTP抓包；不导出像素、图片URL或私人正文。

00:30:28 +08:00曾记录获准受限被动基线通过，纯时间换算得到拟2026-10-24 09:00至2026-10-27 09:00 +08:00的72小时窗口。**后来本人明确“跳过全部”，取消剩余UAT及主动启用准备；上述仅为历史计划，没有写入生效窗口、没有真实主动发送。** 主动始终OFF，不再等72小时结束，不自动续期或恢复测试。未发布的窗口控制器/专用测试草稿已移出交付，取消前本地验证仅留忽略审计，不能计作生产主动通过。

原P0最终为31项限定PASS、5项WAIVED（本人豁免，未实测），Search 26/26限定PASS，整体PARTIAL。真实插话、重复连续输入、小字/两图专项以及其余未测验收按本人要求跳过；已接受的旧自测与T1/T2/T3证据保留，但不补造新的T4。L3生产导入/完整过期控制、通用公开自主搜索和真实主动运行等未完成能力仍明确保留，不由豁免变成已实现或已验证。

累计保守预留 **6.00/原10元**，用于本轮顺序UAT和原生摘要，不重置预算、不是账户账单。各窗口Agent run和原生摘要估算分开，Agent run不冒充准确HTTP调用数。现有私聊与自动记忆仍沿用已批准配置；不为收尾修改生产、清空聊天、重新导入或增加API测试。
