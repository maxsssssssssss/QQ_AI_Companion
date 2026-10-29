# Phase 3 主动陪伴验证

2026-10-29 收尾更新：工程修复与内容可靠性分开验收，发布组合改为普通稿直通、明确问题至多一次定点修订。最终版本、部署回执、301项受影响回归及仍保留的内容PARTIAL见[本轮收尾报告](phase3-autonomy-closeout.md)。本文较早的“普遍一至两次修订”和“未发布”描述仅属于对应历史快照；原失败及验收矩阵保留，不反写为新QQ验收通过。

后续[定点修复候选](phase3-autonomy-targeted-fixes.md)对确认父回复的同交流补充采用5分钟避让、普通新话题仍45分钟，并验证约10分钟的完整原生路径；这不是定时发送，也未部署。内容复验的客观错误单列，当前线上90分钟/96机会/12轮/09–23保持。

2026-10-28：[自主主动专项第二阶段](phase3-autonomy-validation.md)已上线，开放选题、冷却探索、跨机会待分享及确认后自身偏好接线生效。发送仍由DeepSeek决定并经Guard，90分钟/96机会/12轮/09–23保持。程序及真实调用已验证；真实内容质量PARTIAL，QQ自主行为和本人自然度未冒充通过。

2026-10-27：主动频率已调整为**90分钟最短冷却、每日96次模型评估机会、12轮发送尝试**，09:00–23:00不变。22:47:10 +08:00仅重启AstrBot生效，健康核验PASS；空闲配置60分钟、原生普通有效值45分钟保持。原生日内节奏等限制继续保留，额度不是发送目标。[参数、验证与回滚](phase3-proactive-frequency.md)。

2026-10-25 18:35 +08:00：[80条上下文、8条/24小时摘要及48/8/2小时/09–23联合发布](phase3-tuning-bubbles-release.md)已生效。唯一目标日限8、继承120分钟，今天5次评估/1轮/2泡原账本保持；仍由DeepSeek自主决策。以下只同步/旧规则是历史阶段，不代表当前配置。

2026-10-25待发布增量：[主动上下文与频率调整](phase3-proactive-tuning.md)。保留此前真实首次主动已获用户肯定的事实；本批只做合成自动验证，不据此追加真实QQ/DeepSeek自然度PASS。

2026-10-24最新增量：[主动空计划恢复](phase3-proactive-plan-recovery.md)修复安全冷启动后没有候选、前置门禁又阻止原生补排程的问题，并补齐原生随机候选`context=None`的延期和送达元数据。长期规则已在此前发布启用；下文“生产OFF”和72小时描述均为历史记录。此次只更新第一方桥接，最终自动验证和AstrBot单服务发布结果见专项报告，不把配置开启等同真实主动体验通过。

当前增量（2026-10-24）：用户要求长期模式、每日09:00–23:59、5次尝试、至少3小时、5次模型评估（skip/defer也计数）。[长期候选](phase3-proactive-continuous.md)已保存，沿用“先修改、不重启、后续一起生效”，生产仍OFF。下述72小时及2次/4小时是此前试运行的历史记录，不是新候选策略。

历史状态：**PARTIAL；用户豁免真实主动及72小时验收，生产保持OFF，无有效窗口。**2026-10-23 四项体验 V2 候选补齐主动决策、未回复新话题策略及逐计划计数，详见文末增量记录。下列 T1/T2 结果不是首次真实 QQ 主动发送验收，不证明主动文风自然度。

## 原生实现与第一方边界

固定 PrivateCompanion 6.7.0，沿用原生候选、`_offer_proactive_candidate`、`_planned_proactive_delivery_key`、生成、`_tick_user` 和原生调度循环。第一方 `NativeProactiveBridge` 只在当前实例绑定权限/预算/生命周期/文本发送边界，可 detach 恢复，未修改上游源码，未添加第二个调度器或 Agent。

Memory 的已确认、到期、未问过的 awaiting_result 事件通过既有 `_run_scheduler_cycle` 每轮最多一次进入原生候选入口。`origin_event_id` 和 `context.phase3_event_revision` 保持事实关联，接受的 event/revision 持久去重；已回应、完成、取消、不想聊、过期、版本变化及记忆暂停会阻止旧候选。发送成功仅保存 prompted 回执，仍等待真实用户回应。

已有目标用户按原生 `data.users` 中**唯一且已验证的完整 UMO** 查找，兼容上游的 platform/account scoped storage key，不能用裸 QQ 号取代实例与 Bot 身份。歧义或身份缺失拒绝发送。只允许部署白名单对应的私聊，不允许群聊或额外用户。

## 权限与费用门槛

生产备份/迁移/上线、单一原生目标capability迁移及条件72小时首次主动窗口均已获用户集中批准；仍须等真实被动验收通过，不能把批准当作验收完成。`authorize_native_target` 默认 `approved=False` 拒绝修改，只对已有验证身份调用原生 capability API；配置 enabled=true 不是迁移授权。

Gate 开关默认关闭。首次试运行按新批准条件限定为真实被动验收通过后的下一个上海时间 09:00 起 72 小时，每天 09:00–23:00、最多 2 次尝试、最少 4 小时间隔；未取得真实 PASS 时间不计算或启动。旧发布的一次未回应暂停是临时保守策略；V2 候选已改为允许新话题，尚未因此启用生产。详见 [窗口规则](phase3-proactive-approved-window.md)。发送失败/unknown 也消耗尝试预算，pending 重启变 unknown，不自动重发。生成每日最多 2 次，默认总时限 60 秒、框架最多 4 步；失败也计入生成预算。V2 限制同一评估只执行一次原生框架模型生成，禁用该评估内原生 direct fallback。明确 defer 可在未来时间重新评估，仍受每日生成额度和候选原截止时间限制；失败、empty、unknown 不重试。

冷启动采用两段激活：候选磁盘配置始终保存 `max_daily_messages=0`、`enable_llm_proactive_message=false`，包括请求启用主动的候选。`prepare` 只校验并准备；策略和人格完成后，`activate` 先绑定受控桥接，再只把 Private 当前实例属性改为预算 2 和允许生成。AstrBotConfig 不变，不调用保存。插件加载失败或重启时原生后台仍关闭；detach 先归零属性再恢复原生方法，避免未受控发送窗口。

生成前、生成后及发送前分别检查授权、忙碌、暂停、话题状态和候选版本。`我现在忙，今天先不要主动找我。` 从原始用户消息持久化当日暂停；`这件事不想再聊了` 抑制最近主动话题。引号/假设和“别忘了主动找我”等歧义不自动修改权限。保存来源消息 ID，不依赖模型口头承诺。

仅开放普通文本。非 message 原生动作、前置 poke、引用消息和媒体发送关闭；无来源的新闻/公开发现保持拒绝。背景自主搜索尚未批准。

主动 `_run_framework_agent_text` 在 `build_main_agent` 后、`step_until_done` 前调用原生实例筛选入口。桥接在此清空 `ProviderRequest.func_tool`。固定 AstrBot runner 保留该请求对象；模型请求及工具执行器都无法使用 Search、Fetch、phase3_event、文件、cron 等工具。被动搜索开启不会自动授权主动搜索。

## 已执行的组件验证

环境：原锁依赖的 `.runtime/phase3-venv`、固定上游源码、独立 `ASTRBOT_ROOT=/tmp/qq-phase3-proactive-test`，没有加载生产 QQ 登录态或真实 Key。

`tests/test_phase3_proactive_native.py`：**15/15 PASS（T2 组件）**，包括新增窗口/到期检查。

| 场景 | 证据范围 |
|---|---|
| 原生候选、计划 ID、生成和发送结果 | 执行真实 `_build_proactive_impulse`、`_planned_proactive_impulse`、`_planned_proactive_delivery_key`、`_generate_proactive_message_with_llm`、原生 Outcome |
| 暂停、busy、结束事件 | 生成前零模型调用 |
| 生成中用户回应 | 丢弃过期正文，没有发送 |
| 超时和同候选再次执行 | 有界退出，同候选不再生成 |
| 无来源公开发现及媒体动作 | 生成/发送拒绝 |
| 重启预算 | 同候选不重复模型调用 |
| 配置与目标授权 | 真实上游 schema 键校验、默认拒绝迁移、原生 capability API |
| 原生 scheduler cycle | 实际循环入口驱动；该组件测试的用户 tick eligibility/settlement body 是夹具 |
| Memory 事件进入原生入口 | ID、来源、revision 关联及持久去重；本组件的候选 admission policy 为夹具 |
| 用户暂停及话题拒绝 | 直接原文解析，含歧义反例 |
| 主动工具集 | 真实 ToolSet 清空 Search/Fetch/文件/cron/Memory tool |
| 冷启动与激活 | 未授权 Gate 不可激活、仅实例内启用、磁盘不变、detach 先关闭 |
| 窗口与到期 | 开始前/夜间零主动模型调用；到期原生实例归零，保留被动周期；到期重启不阻断被动服务 |

该套测试的模型、transport 和部分用户 tick 策略是合成夹具。日志保留在本地 `/tmp/qq-phase3-proactive-native.log`，不包含生产数据。

## 完整原生进程探针

`tests/phase3_proactive_probe.py` 由仅用于隔离环境的 `tests/phase3_launch.py` 读取固定 flag 触发。主程序先验证所有 Provider 为 loopback；探针仅允许 `offline:FriendMessage:10001` 合成身份。创建合成来源及 Memory 事件，在已加载的完整 Private 实例执行实际候选 admission 和 `_tick_user`，通过原生框架模型调用及 Guard 到隔离 OneBot。

探针输出候选接受结果、原生 preflight、实际生成次数、独立主动账本状态及 Memory prompted 回执；综合测试 I11 还应验证 HTTP 模型请求没有 tools。它没有手动直接调用 sender 来伪造完整调度成功。

该探针使用明确的合成中午时钟（只替换实例 `_environment_now` 为 12 点），使午夜执行的测试也能覆盖原生允许时段；候选 admission、`_should_send` 和整段 `_tick_user` 仍是固定原生代码。首次夜间实测被原生时段正确拒绝，模型调用和发送均为零。生产保持真实时钟；事件源在原生 follow-up 时段外不提前消耗 event/revision。

**完整进程探针：PASS（T2）**。独立证据 `evidence/phase3/proactive-20261021T160337Z/proactive-native.json`：原生候选 accepted、preflight `[true,"ok"]`，一次原生生成、一次本地 HTTP 模型调用且 `tools=[]`，一条 Guard `proactive/sent`，Memory `prompted=true` 并保留 `awaiting_result`。发送和生成均没有重试。

综合测试 `evidence/phase3/20261021T160248Z-15511/` 中 I11 亦通过，包含工具集为空断言。12 项联合测试该轮退出码 0；后续收紧其他记忆断言的运行由总验收文档记录。这是完整固定框架、Mock HTTP 模型、隔离 OneBot 的联通证据，不能替代真实账号或真实模型主动验收。

## 未验收和已知限制

- 真实 QQ 首次主动发送、用户短窗口主观体验：**BLOCKED于真实被动验收条件；授权已具备**。
- 真实 DeepSeek 的主动文风、自然度和内容质量：**NOT TESTED**；人格单独样本不能替代真实主动链。
- 完整原生定时循环的长期候选质量、所有候选来源和连续多日节奏：**NOT TESTED**。
- 背景自主搜索与公开发现分享：**未启用，待单独授权和验收**。
- 富媒体主动出站、平台已读、远端 message_id：**不在当前启用能力内**。原生 QQ 适配器丢弃 message_id，账本只记已收到发送成功回执。
- 已发出的消息不能靠插话撤回，未知回执不能被解释为确定未送达。停止只影响尚未执行的边界。
- 自然语言控制采用明确原文和保守匹配；复杂指代/含糊暂停时长仍需澄清，不能宣称任意自然表达都已解析。
- 该能力不是可靠提醒或待办系统，原有 Future Tasks 一次性失败删除问题仍保留。

## 四项体验 V2 增量（2026-10-23，候选）

复用原生候选、同一框架 Agent 和 Guard，新增一个可选的首行决策契约：`[PHASE3_PROACTIVE:send]` 后是原生多气泡正文；`defer:分钟` 与 `skip` 不带可发送正文。普通未带首行的有效正文仍走既有本地检查与 Guard；格式异常不会将控制字符发给用户。空正文记为 `empty`，不能冒充模型有意 skip。没有第二个决策模型，主动工具集继续为空。

此次完整原生探针发现并修复了**提示词进入实际请求的缺口**：原生 builder 将 `action_context` 当短事件摘要裁剪，把追加在末尾的决策契约截掉。现在在现有 `_run_framework_agent_text` 实例入口、原生提示词构建完成后注入完整的多气泡及决策契约。`request-contract-check.json` 验证三条实际 HTTP 请求都含完整契约，不以配置存在或组件测试代替请求证据。

决策、到期和下次考虑时间存入原生 impulse 的 `context.phase3_decision`。主动生成额度继续存在既有 Gate 表。defer 被限制为 60–240 分钟，不能延长原候选有效期；若延后已超出有效期，则作废。明确 skip 保留作废状态，不恢复尾部或保留原样重发正文。已回应、修订变化、暂停和候选过期仍在生成前后校验。

未回复策略与已确认偏好对齐：已绑定的唯一授权用户不再因一次未回应全面停发；新话题继续受四小时间隔、每日两次尝试及明确暂停限制。使用原生话题签名，对已确认提起且尚无新用户回复的同一话题增加 24 小时冷却。它不是“任何未回复都必须再次联系”的指标，也不等于任意同义话题都可完美识别。

用户明确“我现在忙”等原文会持久化临时 `busy_until`（无明确结束时间时最多四小时，不越过批准窗口）；“我忙完了”仅清除此临时忙碌状态。它和用户明确要求的 `pause_until` 分离，不把恢复忙碌解释为撤销暂停。引号、假设、问对方是否忙和“忙着想你”等不命中这些控制，不调用分类模型。

计划尝试与气泡送达分别记录。既有 `proactive_attempts` 表增加 `sent_count`、`part_count`、完成时间；每个气泡及实际文本仍只由 Guard 保存。失败耗费一次尝试，不记作已经说过；部分成功保留已确认前缀及尾部 cancelled/failed/unknown。只有确认至少一个气泡才更新最近已提话题和事件 prompted。原生默认会把已送达前缀所在候选标成整组 sent；实例适配现在将未完整计划结束为 dropped，并在原生 `context.phase3_delivery` 保存真实聚合状态和计数，保留原生已确认前缀归档。中途任务取消从 Guard 账本对账；不会为了补统计再次发送。

| 验证 | 结果与范围 |
|---|---|
| 新主动组件 | `evidence/phase3/proactive-v2-20261023/native-final.json` 26 项通过；原生候选/生成入口、合成时钟和 transport，不是真实 QQ |
| 完整固定框架主动 probe | `evidence/phase3/20261023T141752Z-7688/proactive-native.json` PASS（进一步检查真实原生池经 cleanup 后仍为 deferred/blocked）；早一轮 `20261023T141335Z-6904` 同样通过：真实 Private admission/tick；defer、skip 各一次模型请求零发送；send 一次模型请求、两条 Guard sent |
| 实际请求注入 | `20261023T141335Z-6904/request-contract-check.json` PASS：3 次 HTTP 均含完整决策和气泡契约，工具为空 |
| 保留的失败 | `20261023T140753Z-5679` 首轮联合失败及 `20261023T141136Z-6551` 快速探针失败；前者运行中更新 fixture，后者证明原生摘要截断缺口；不覆盖历史 |
| 真实 DeepSeek | `scripts/phase3-test-proactive-real.py` 已准备，最多 3 次，沿用累计预算；结果由主线程统一执行记录。它仅测试真实契约与合成场景，不声称完整原生调度或 QQ |
| 真实主动窗口 | 尚未开启；必须先完成真实被动链路验收，之后才产生明确起止时间 |

以上为候选实现与测试记录，正式部署版本、完整联合回归及最终自动验证计数以主验收报告为准。真实 72 小时运行、自然主动体验、暂停和到期后的生产记录仍需在批准窗口内验证。

保留的诊断边界：Private 原生对主动 defer/skip 的空可见正文仍可能写出旧的“框架返回空文本／直接兜底返回空”诊断。本候选没有修改上游日志文案；第一方生成状态是 `deferred` / `skipped`，实际 HTTP 每例一次，不能把该“两级”文案当成发生了两次模型调用，也不能据此声称是真实用户无回复故障。

## 窗口增量验证

证据目录：`evidence/phase3/proactive-window-20261022T001034Z/`。`runtime.json` **22/22 PASS**，`native.json` **15/15 PASS**，`staging.json` **5/5 PASS**，均无跳过。新增纯时间换算、跨午夜边界、09:00 包含/23:00 排除、72 小时结束排除、每日尝试及四小时冷却、被动配置兼容测试；新增真实 Private scheduler 到期归零与过期重启保持被动可用的测试。这些测试不产生真实主动消息，不是生产被动验收的时间来源。

### 23:05 +08:00 登录恢复与当前状态

本人扫码后已核验当前在线备用QQ身份，原生自动登录设置重新保存并验证落盘；没有额外重启或再次登录请求。23:05:16实际isLogin/coreReady=true、loginPhase=ready，内部OneBot ESTABLISHED=1，策略PASS、47源文件摘要匹配。原先waiting_qrcode及一次快捷登录未恢复的证据保留为本次发布历史。

当前为 **CANDIDATE_READY_FOR_UAT**，仅表示完整候选及自动验证/发布前置条件满足；P08真实FAIL尚未关闭，总体PARTIAL，主动OFF。已按顺序仅邀请此前失败的资料问题，未发整组作业。证据 `evidence/phase3/four-capabilities-20261023/candidate-readiness-r2.json`、`production-health-after-login-r2.json`、`napcat-save-current-session-r2.json`。

## 2026-10-24 用户最终决定

00:30:28+08完成本轮限定被动基线核对，曾计算原规则下窗口为10月24日09:00至27日09:00(+08)。尚未启用时，用户明确选择“跳过72小时主动验收，主动功能先保持关闭”，随后“跳过 全部”。该计划已取消，未配置生产窗口、未迁移目标native proactive capability、未发送主动消息。T1–T3/原生调度隔离证据保留，P29标WAIVED/NOT TESTED，不标真实PASS，不自动重启或续期计划。
