# Phase 3 交互、发送账本及主动发送桥接

状态：被动多气泡已部署且真实QQ/T6通过；搜索progress/final也有真实发送证据。主动发送仅原生隔离验证，条件窗口尚未开启。具体最新结果见[专项报告](phase3-multibubble-validation.md)。
本文件覆盖 Gate 2 和 Gate 5 的发送边界，不能代替总验收表。

## 固定版本及复用决策

- AstrBot 4.29.1：沿用原 Agent、原生会话锁、`AiocqhttpMessageEvent.send`、`Context.send_message`，使用 `active_event_registry.request_agent_stop_all` 取消失效生成。没有新增第二个 Agent 或业务队列。
- PrivateCompanion 6.7.0：原生语义缓冲 `_note_semantic_message_buffer` 对单条文本截至 260 字并去重相同文本，不能保存本任务要求的完整来源，因此禁用其重复 debounce，薄层按原始入站 ID 保留完整文本。原生后台分段直接 `event.send`，停用该后台模式；动态调用上游 `parse_llm_segment_control`，由统一账本顺序发送。
- 语义气泡提示只加在已授权 Phase 3 原生请求的 system prompt；日常默认一条，必要时 2–4 条，技术长答不按字数切片。搜索进度生成单独受限，不使用分段标记。
- 主动候选、候选 ID、生成正文和候选策略继续来自原生 Private。实例级桥接 `_tick_user`、`_generate_proactive_message_with_llm`、`_send_proactive_message_chain`；卸载后恢复绑定，不修改第三方源码。

## 输入及可证明的并发边界

授权纯文本默认 0.8 秒滑动窗口，最长 3 秒，8 条时唤醒组长。原始消息先各自归档，再使组长请求包含有序完整文本。相同文本不同 ID 不视为重复；同一 ID 不再执行。

输入在 ready／generating 阶段到来时，将未完成输入来源并入后继轮，调用 AstrBot 原生停止接口，并禁止旧轮继续发气泡。已确认发出的内容保留历史，不能撤回已经发生的发送。停止发生在原生工具／发送边界，不能取消已经完成的远程副作用。

窗口和条数是唤醒阈值；事件循环恢复前已经到达的消息保留在同组，不因硬截条数丢消息。上游会话锁仍负责处理串行化。单张图片输入沿用原生处理，不把图片输出纳入本次发送能力。混合多图片与文本的高速竞争、跨平台消息排序**尚未证明严格 FIFO**；不能据纯文本测试宣称所有媒体组合通过。

结束轮的内存索引在 5 分钟后清理；15 分钟没有完成的活跃轮失效关闭，避免异常路径永久占用主动发送 busy 状态。持久账本不随内存清理删除。

## SQLite 与发送语义

保持旧 `inbound`、`outbound` 表及查询接口不变。首次显式启用 Phase 3 时，先用 SQLite backup API 生成权限 600 的 `.pre-phase3.sqlite3` 一致备份并检查完整性，再事务性新增 `guard_schema`、`turns`、`turn_sources`、`deliveries`、`progress_claims`、`delivery_observed`。

每次真实发送尝试使用独立 `delivery_id`，同一轮允许 normal／progress／final／proactive，`part_index` 区分气泡。来源表保留平台、会话和原始入站 ID；主动消息的来源列表为空，绝不伪造入站。进度 claim 持久化，重启不能重复发送已领取的进度。

| 状态 | 含义 |
|---|---|
| pending | 已写尝试记录，等待发送结果 |
| sent | 原生平台发送调用已得到成功回执；不是用户已读 |
| failed | 明确拒绝、空内容、权限/失效轮阻止等确定失败 |
| unknown | 超时、网络不确定异常、取消途中、进程中断或缺少明确回执 |

AstrBot 固定版本的 QQ 适配器等待 OneBot API 后丢弃远端 `message_id` 并返回 `None`。薄层基于已审计的 await 成功构造回执，`platform_message_id` 保持 NULL，不能编造 ID。普通未知回调返回 None 不算成功。重启把 pending 转为 unknown；没有自动重试。回执观察失败不改变已发送事实。

回滚优先停止新插件、关闭开关并恢复旧配置；旧 Guard 忽略新增表，旧表记录仍可读。不要用迁移前数据库覆盖之后发生的真实消息；迁移前备份用于隔离恢复及审计，涉及生产恢复仍须批准。迁移失败事务回滚；备份路径冲突会停下而非覆盖证据。

## 公共接口

- `guard.send_controlled(event, chain, kind, part_index=0, delivery_id=None)`：固定私聊目的地、原生 event 发送；normal/progress/final。
- `guard.send_proactive(umo, text, delivery_id=..., is_current=...)`：独立主动轮，返回含 state 的字典；需要独立主动开关。
- `guard.turn_is_current(event)`、`guard.session_busy(umo)`：工具、生成及主动发送的取消边界。
- `guard.store.claim_progress(turn_id)`：单轮一次持久 claim。
- `guard.register_delivery_observer(callback)`：轻量异步观察，失败不重试发送；Memory 使用持久账本游标补录，不能靠一次内存通知保证归档。

只启用纯文本出站。图片输入仍走现有图片理解；图片、语音、文件等新增出站被明确拒绝并记账，不宣称媒体出站全覆盖。

## 主动桥接与发布条件

`native_proactive_config` 使用真实上游分组 schema，磁盘始终保存日限额 0 和生成关闭；只有全局策略完成后，runtime `activate` 先 attach 桥接，再只在实例内启用每天最多 2 次。detach 先归零再恢复原生方法。保持至少 4 小时间隔、原生安静时段和一次未回应暂停；禁用群跟进、额外 LLM 事前/事后审核、生活模拟和媒体动作。`off` 是原生手动强度预设，实际关闭依赖日限额 0 和开关。

现有用户状态不会被新默认配置覆盖。用户已批准单一主QQ的原生 capability 迁移，仍必须在备份后执行 `authorize_native_target`（已验证 UMO、原生 `_req036_update_capabilities`、目标用户限额 2），并单独配置至多 7 天的 Gate 窗口，才能具备主动资格。配置文件不是授权；该迁移函数默认拒绝执行。

Gate 在原生 tick 和模型生成前检查授权、窗口、暂停、busy、生命周期、来源、冷却及预算。生成最多 60 秒，框架 Agent 最多 4 步，原生直接文本 fallback 仍属于该有界时间；同候选不重复生成，每天最多 2 个生成尝试，失败也消耗预算。生成结束及最终发送再检查时效。动作入口只允许 message，前置 poke 关闭。

事件来源必须由 Memory 返回 `eligible=true`；completed/responded/cancelled/do_not_discuss/expired 等不能再次追问。发送成功只记录已问回执，不伪造用户已回应。公开发现需要真实可信搜索来源；当前后台搜索未批准，相关候选保持拒绝。

Memory `due_events` 经所有权及原文校验后，由已存在的 Private `_run_scheduler_cycle` 每轮最多送入一个原生 `_offer_proactive_candidate`。`origin_event_id` 和 `context.phase3_event_revision` 关联真实 Memory 条目，接受后的 event/revision 持久去重；生成前后及发送前重新核对版本。没有新增定时器。原生可能因冷却/优先级拒绝候选，此时不能记为已发送。

主动框架在 `build_main_agent` 之后、`step_until_done` 之前，使用实例级原生筛选入口清空同一个 `ProviderRequest.func_tool`。因此主动模型不能调用被动搜索、Fetch、文件、cron、事件修改等工具，背景网络授权不会因被动搜索开关而扩大。生成保留既有 Persona 和原生模型路径。

`我现在忙，今天先不要主动找我。` 会从明确用户原文产生当日暂停；`这件事不想再聊了` 会抑制最近主动话题。引号、假设和“别忘了主动找我”等歧义不自动改变权限。解析结果写入持久 Gate，保存来源消息 ID；不能只让模型口头答应。

## 隔离测试证据与限制

| 文件 | 层级与验证范围 |
|---|---|
| `tests/test_phase3_delivery.py` | T1：19 项账本、迁移、唯一尝试、回执、未知及异常路径 |
| `tests/test_phase3_turns.py` | T1：10 项连续输入、完整原文、重复 ID、后继取消、窗口、会话隔离及异常 runner 过期 |
| `tests/test_phase3_delivery_native.py` | T2：9 项真实 AstrBot event + aiocqhttp HTTP 客户端 + 本机 OneBot 夹具；真实 Private 解析器、2/4 气泡账本、原生停止注册表、默认关回归 |
| `tests/test_phase3_proactive_native.py` | T2：15 项真实 Private 候选构造、计划 ID、两级生成入口、scheduler cycle、Outcome、capability API、工具清空、事件导入、暂停解析、冷启动及批准窗口到期边界；模型/transport/用户 tick eligibility body 为隔离夹具 |
| `tests/phase3_proactive_probe.py` | PASS：完整已加载 Private 实例的 `_offer_proactive_candidate` → `_tick_user` → 原生模型 → Guard；合成中午时钟、Mock HTTP 一次且 tools 空、sent 一次、Memory prompted 真。详见 [主动验证](phase3-proactive-validation.md) |

运行 native 测试必须使用原锁依赖环境，设置独立 `ASTRBOT_ROOT` 并把固定 AstrBot 源目录加入 PYTHONPATH。测试不加载生产 QQ 登录态、不发真实 QQ、不调用真实付费模型。

主动测试**未**证明完整原生 timer loop 的长期候选自然度、全量用户 tick 策略/结算、真实 DeepSeek 文风或真实 QQ 首次主动发送。完整框架链路证据由总验收报告另列。上述生产项保持 BLOCKED/NOT TESTED，不能用组件测试代替 P28–P33 真实验收。

Markdown清理发生在语义解析之后、计划和发送之前；实际正文用于ledger、观察器和确认后的历史。原始生成关联仍用generation_sha256保留。进度和主动路径同样清理，不把格式处理当成新的分段或重发理由。
