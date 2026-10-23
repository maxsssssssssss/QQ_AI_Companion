# Phase 3 资料召回空回复排障

2026-10-22。**工程修复已上线，后续QQ已有回复，但资料准确性仍失败，总体 Phase 3 PARTIAL。** 用户反馈“12通过 3没有回答”：`/new` 和更正后代号召回通过；游戏资料问题失败。原失败记录保留，不补写成功、不自动重放该轮。

## 实际证据

- T4：23:25:54 创建新会话，23:26:10 的更正代号问题得到确认发送及用户正确评价；旧会话完整保留且内容不变。纠正与原表述在同一摘要批次，不能据此宣称已实测两个独立长期断言版本的更正链。
- 23:26:23 的游戏问题调用一次 `memory_companion_sources`，没有 Search/Fetch。最终生成关联为空哈希；Guard 记录空正文 failed、实际适配器调用 0 次。原始 API 响应没有留存，不能仅凭这一现象断言模型供应商或某个清理器是根因。
- 生产记忆的一致副本、固定 MemoryCompanion 原生检索：完整游戏问题及短问法都可命中已批准 L2 游戏资料；“兴趣爱好”问法没有结果。资料并未丢失，但查询覆盖仍有限。此检查断网、0 模型调用，不等于真实问答成功。

证据：`memory-profile-qq-audit-20261022-r1.json`、`memory-profile-owner-feedback-20261022.json`、`profile-empty-reply-diagnostic-20261022-r2/r3.json`、`profile-game-query-native-20261022.json`（均在私有 `evidence/phase3/`）。首个日志诊断使用错误容器名，rc=1，保留但不作为无异常证据。

## 最小处理与限制

在现有 Guard 的公开 Agent/LLM 钩子中，仅当最终模型回复在任何清理器之前已无正文、没有工具调用或媒体、当前轮仍有效、未发生 normal/final 发送时，允许一次补答。使用同一已配置聊天 Provider 的 SDK 和原 Agent 的人格、当前记忆、工具结果；不创建新 Agent，不再执行工具，不逐气泡请求模型。

补答禁用工具和思考模式、最多 768 输出 token、12 秒墙钟截止、SDK 不重试。每轮最多一次，上海日最多 4 次，尝试记录保存在原 Guard 数据库的 `response_recoveries` 表；失败、取消和重启均占额度，pending 重启变 unknown，不恢复执行。普通非空回复不增加调用；被原清理器取消的非空内容不复活，任何既有 final/normal 发送包括 unknown 都不补发。

用户插话、supersede、停机均可终止补答。失败、再次空正文或额度用尽时，只给明确失败说明，不捏造资料答案。正常的原生清理器、语义气泡、Guard 发送和确认历史投影继续执行。模型补答 recovered 不等于 QQ sent，费用由审计工具单列，不混入原生 Agent 运行数。

开关 `phase3_empty_recovery_enabled` 默认 false，本次生产明确启用。只支持文字/工具上下文；超大上下文和媒体降级为失败说明。一次补答仍可能回答不完整或错误，需要本人验收；既有游戏问题未被自动重放。

## 分层验证

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

## 发布与回退

发布目录 `phase3-20261022-empty-response-r1`；包 SHA256 `555452e90924261088680d1dbfe105c12d4c7755e08313b3ddfe9f197a45cbdb`。先停止原两个写入服务，备份 `phase3-empty-response-preflight-20261022-r1` 并验证隔离恢复，再窄安装 Guard main/recovery/schema、Guard 开关及原样 cmd_config，最后恢复原服务。镜像、第三方插件、原聊天与现有资料保持。

23:49:40 +08:00：启动策略 PASS，两个原容器 healthy，QQ isLogin/coreReady=true、isOffline=false；安装文件哈希及开关吻合。原空正文失败记录仍在，恢复尝试 0（尚无新测试）。健康脚本最初未兼容配置 BOM，后续只读脚本修正编码后完成检查。

回退优先关闭 `phase3_empty_recovery_enabled` 并正常重启 AstrBot；或使用该发布 installation-journal 恢复旧 Guard 文件。保留新增表和所有新聊天。无须回滚生产数据库；若以后需要完整数据回滚，先保存切换后数据并说明影响。

后续事实：此前service.search命中检查未验证实际slot注入。2026-10-23排障证实最新真实请求的四条注入均不含游戏资料；扩大到8条仍不足。新增资料召回修复单独发布并保留本次原始失败，见[新报告](phase3-profile-recall-validation.md)。
