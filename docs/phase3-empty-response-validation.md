# Phase 3 资料召回空回复排障

**2026-10-24当前：资料原题、关系与新会话召回已本人确认真实通过；根因为本轮原生资料上下文接入，详见[召回修复](phase3-profile-recall-validation.md)。这些通过轮次没有触发空正文补答。** 旧空回复和后续失败保留；用户最终跳过全部剩余测试，主动OFF，本轮按豁免收尾、整体PARTIAL。

## 2026-10-23 V2 增量与历史失败状态

当时本人反馈为 **“没有回复或只收到失败提示”**。该资料问题的真实 QQ 功能状态继续为 **FAIL**，不能写成等待首次复测，也不能以发送了失败说明算作答对。V2 自动验证已完成并安装到原 VPS，但本次重启后 NapCat 为 `waiting_qrcode`、快捷登录列表为空，真实复测另受本人扫码阻塞。两个容器 healthy、启动策略 PASS 和源文件核对一致，都不能关闭这项 FAIL。

当前已批准 L1 3 组、L2 12 组及用途继续有效，本次未重复导入 L2、未要求重填访谈，L3 未导入。资料检索、关系/时间语义和 core memory 接入见[资料召回报告](phase3-profile-recall-validation.md)。本报告只记录空回复及发送链的增量，不能把本地 `service.search` 命中当成完整问答通过。

### V2 已实现的有界恢复

- 复用原 `turn_id` 记录阶段状态、长度、摘要哈希和必要配置标识；区分模型未触发、原 final 为空、清理后为空、恢复被限额/上下文拒绝、没有发送尝试及 failed/unknown。不新增另一套追踪或历史主库，公开报告不包含私聊正文或凭据。
- 可选媒体适配/Provider 查询异常局部降级，仍保留原 Agent 上下文供既有恢复器使用，避免辅助钩子异常连带关闭普通回复恢复。关键发送账本异常继续阻止发送，不以捕获异常绕过。
- 恢复器现在保留原 Agent 的文字、工具调用/结果配对及用户图片载荷；不重放工具、不使用思考文本作答、不另选视觉模型。有效图片转为稳定载荷，当前和较近图片优先；失效引用明确标为本次不可读取，不杜撰已看过图片。
- 图片上限为 8 张、每张 8 MiB、合计 16 MiB；文字及工具元数据有 96,000 字符界限，最终序列化请求有 24 MiB 界限。超界如实降级，不把预算不足解释成没有记忆。上下文整理与 SDK 调用共用 12 秒截止。
- 仍只用同一已配置 Provider/model，最多 768 输出 token、SDK 无重试；每轮最多一次、上海日最多 4 次，失败/取消/重启均占额度。已有 final/normal 发送（含 unknown）禁止补发；旧计划 unknown 不锁死后来合法入站。普通非空正文不增加恢复调用。
- 最终发送继续通过原生清理、模型分段与 Guard，历史只记录确认内容。补答非空或 HTTP 200 不等于送达，更不等于资料回答正确。

这些改动解决了可复现的工程缺口；最近真实失败不能仅凭已保存形态证据统一归因为“网络”，仍须按顺序真实复测确认功能。

### V2 验证与发布证据

| 层级 | 当前有效结果 | 范围和边界 |
|---|---|---|
| T2 空正文原生模块 | **12 PASS** | `four-capabilities-20261023/empty-r4/test_phase3_empty_response_native.json`；原上下文、工具配对、限额/超时、取消、unknown 与受控历史 |
| T2 媒体原生模块 | **18 PASS** | `four-capabilities-20261023/components-r2/test_phase3_media_native.json`；含图片历史与恢复兼容，非真实 QQ 图片验收 |
| T1/T2 全部组件 | **382 PASS / 28 模块** | `four-capabilities-20261023/components-aggregate.json`；上述模块已计入，不能重复累加 |
| T2 完整固定框架 | **20/20 PASS** | `20261023T143959Z-12559/result.json`；I16 工具后空正文补答、I18 实际主请求资料注入、I20 含图片历史的同 Provider 补答；真实模型/QQ 均为 false |
| T4 真实资料问题 | **FAIL 保留；当前复测 BLOCKED** | 最近本人反馈未回复/失败说明；本次安装后等待本人扫码，尚无新正确性证据 |
| T5 本次停写备份/隔离恢复 | **PASS** | `four-capabilities-20261023/production-install-r2.json`；不代表真实 QQ 重启自动登录成功 |
| T6 本人评价 | **NOT TESTED（V2）** | 等工程和登录条件满足后按主流程逐项测试，失败提示不作为正向体验样本 |

components-r1/r2 的旧空正文 ERROR 与 empty-r3 的 1 项失败原样保留。r3 的 unknown 夹具把“调用已超时”误当成“请求必已到达”；繁忙机器中 0.5 秒期限可能先于对端收到请求。r4 改为先观察本地 HTTP 实际收到请求，再取消未返回确认的调用，核对 unknown 不触发恢复/重发；另有独立超时用例，未放宽生产保护或增加重试。

22:56:39 +08:00 的 `production-health-r2.json` 核对 47 个源文件无差异、两个容器 healthy，QQ 未登录且 OneBot 连接数为 0。发布为 `phase3-20261023-experience-v2-r2`；优先关闭新能力，必要时使用本发布 `installation-journal` 窄回退文件。保留旧聊天、已批准资料、逐条发送和恢复尝试记录；不得覆盖整个 data，也不自动重放失败问题。整体 Phase 3 仍为 **PARTIAL**。

## 2026-10-22 原始失败、首次修复与发布记录

2026-10-22。**工程修复已上线，后续QQ已有回复，但资料准确性仍失败，总体 Phase 3 PARTIAL。** 用户反馈“12通过 3没有回答”：`/new` 和更正后代号召回通过；游戏资料问题失败。原失败记录保留，不补写成功、不自动重放该轮。

### 实际证据

- T4：23:25:54 创建新会话，23:26:10 的更正代号问题得到确认发送及用户正确评价；旧会话完整保留且内容不变。纠正与原表述在同一摘要批次，不能据此宣称已实测两个独立长期断言版本的更正链。
- 23:26:23 的游戏问题调用一次 `memory_companion_sources`，没有 Search/Fetch。最终生成关联为空哈希；Guard 记录空正文 failed、实际适配器调用 0 次。原始 API 响应没有留存，不能仅凭这一现象断言模型供应商或某个清理器是根因。
- 生产记忆的一致副本、固定 MemoryCompanion 原生检索：完整游戏问题及短问法都可命中已批准 L2 游戏资料；“兴趣爱好”问法没有结果。资料并未丢失，但查询覆盖仍有限。此检查断网、0 模型调用，不等于真实问答成功。

证据：`memory-profile-qq-audit-20261022-r1.json`、`memory-profile-owner-feedback-20261022.json`、`profile-empty-reply-diagnostic-20261022-r2/r3.json`、`profile-game-query-native-20261022.json`（均在私有 `evidence/phase3/`）。首个日志诊断使用错误容器名，rc=1，保留但不作为无异常证据。

### 最小处理与限制

在现有 Guard 的公开 Agent/LLM 钩子中，仅当最终模型回复在任何清理器之前已无正文、没有工具调用或媒体、当前轮仍有效、未发生 normal/final 发送时，允许一次补答。使用同一已配置聊天 Provider 的 SDK 和原 Agent 的人格、当前记忆、工具结果；不创建新 Agent，不再执行工具，不逐气泡请求模型。

补答禁用工具和思考模式、最多 768 输出 token、12 秒墙钟截止、SDK 不重试。每轮最多一次，上海日最多 4 次，尝试记录保存在原 Guard 数据库的 `response_recoveries` 表；失败、取消和重启均占额度，pending 重启变 unknown，不恢复执行。普通非空回复不增加调用；被原清理器取消的非空内容不复活，任何既有 final/normal 发送包括 unknown 都不补发。

用户插话、supersede、停机均可终止补答。失败、再次空正文或额度用尽时，只给明确失败说明，不捏造资料答案。正常的原生清理器、语义气泡、Guard 发送和确认历史投影继续执行。模型补答 recovered 不等于 QQ sent，费用由审计工具单列，不混入原生 Agent 运行数。

开关 `phase3_empty_recovery_enabled` 默认 false，当次生产明确启用。该旧版只支持文字/工具上下文；超大上下文和媒体降级为失败说明。V2 的媒体兼容变更见上文，不回写旧版能力。一次补答仍可能回答不完整或错误，需要本人验收；既有游戏问题未被自动重放。

### 分层验证

| 层级 | 结果 | 范围 |
|---|---|---|
| T2 原生组件 | 10 PASS | 一次补答、原上下文/工具对、正常/主动压制不重试、超时、插话、unknown、日限/重启、媒体保留、同一受控发送和历史 |
| T2 既有回归 | 10 气泡 + 9 发送 PASS | 改动影响的正常路径、复杂组件、失败/unknown 和 Phase2 回退 |
| T2 完整 AstrBot | 16 PASS | 固定 AstrBot/Private/Memory，实际合成 HTTP/OneBot；新增 I16 复现工具→仅思考无正文→一次补答→确认发送 |
| T3 真实 DeepSeek | 1 次 HTTP200、非空且依据正确 | 合成游戏资料，0 工具执行/0 QQ 消息，估算 0.001716 元，非账单；不冒充实际故障的完全复现 |
| T4 真实 QQ | FAIL保留，后续转资料准确性修复 | 23:53复测已确认发送、补答尝试0；本人反馈资料不正确/完整，详见[召回排障](phase3-profile-recall-validation.md) |
| T5 生产备份恢复 | PASS | 新停写快照，16 个 SQLite 完整性与隔离恢复，恢复副本不启动 QQ |
| T6 本人评价 | 待重测 | 不以模型自评替代 |

本次空回复发布时有效组件合计 279 项/20 模块；完整联调证据 `20261022T154223Z-12615/result.json`。新增组件首轮受沙箱回环网络限制失败，经正常审批重跑；unknown 夹具 50ms 在请求到达前已超时，调整为 500ms 后验证真实到达而回执未知，失败记录保留。没有绕过审批。

### 发布与回退

发布目录 `phase3-20261022-empty-response-r1`；包 SHA256 `555452e90924261088680d1dbfe105c12d4c7755e08313b3ddfe9f197a45cbdb`。先停止原两个写入服务，备份 `phase3-empty-response-preflight-20261022-r1` 并验证隔离恢复，再窄安装 Guard main/recovery/schema、Guard 开关及原样 cmd_config，最后恢复原服务。镜像、第三方插件、原聊天与现有资料保持。

23:49:40 +08:00：启动策略 PASS，两个原容器 healthy，QQ isLogin/coreReady=true、isOffline=false；安装文件哈希及开关吻合。原空正文失败记录仍在，恢复尝试 0（尚无新测试）。健康脚本最初未兼容配置 BOM，后续只读脚本修正编码后完成检查。

回退优先关闭 `phase3_empty_recovery_enabled` 并正常重启 AstrBot；或使用该发布 installation-journal 恢复旧 Guard 文件。保留新增表和所有新聊天。无须回滚生产数据库；若以后需要完整数据回滚，先保存切换后数据并说明影响。

后续事实：此前service.search命中检查未验证实际slot注入。2026-10-23排障证实最新真实请求的四条注入均不含游戏资料；扩大到8条仍不足。新增资料召回修复单独发布并保留本次原始失败，见[新报告](phase3-profile-recall-validation.md)。

### 23:05 +08:00 登录恢复与当前状态

本人扫码后已核验当前在线备用QQ身份，原生自动登录设置重新保存并验证落盘；没有额外重启或再次登录请求。23:05:16实际isLogin/coreReady=true、loginPhase=ready，内部OneBot ESTABLISHED=1，策略PASS、47源文件摘要匹配。原先waiting_qrcode及一次快捷登录未恢复的证据保留为本次发布历史。

当前为 **CANDIDATE_READY_FOR_UAT**，仅表示完整候选及自动验证/发布前置条件满足；P08真实FAIL尚未关闭，总体PARTIAL，主动OFF。已按顺序仅邀请此前失败的资料问题，未发整组作业。证据 `evidence/phase3/four-capabilities-20261023/candidate-readiness-r2.json`、`production-health-after-login-r2.json`、`napcat-save-current-session-r2.json`。
