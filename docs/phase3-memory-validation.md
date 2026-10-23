# Phase 3C 自动记忆适配与隔离验证

日期：2026-10-21 至 2026-10-24。**本轮按本人“跳过全部”剩余验收的要求收尾，Phase 3整体PARTIAL；P0 31/36限定PASS、5 WAIVED（未实测），Search 26/26限定PASS。** 原资料题、关系区分及/new后召回已由本人真实QQ确认；精确旧22条整理、L1 3组/L2 12组及原文来源审批关系保持，不重导入或扩展L3。原失败和限定层级证据保留，其他主题和长期质量未验不写PASS。主动始终OFF、准备已取消，不存在生效窗口或剩余UAT等待。

## 2026-10-23 V2 r2 历史增量：已安装，资料真实验收失败

- **原生 L1 常驻。** `core_profile.py` 在精确既有审批和导入账本下，把已有3条L1原位迁移为原生 `core_memory`；保留ID、正文、来源和撤销关系，不重复导入。固定原生核心预算默认800字符，候选设1600，并以原生 `_compose_memory_injection` 验证全部3条正文实际包含在注入包中。停用、撤销、正文或来源变化均拒绝静默重启/迁移；不能用“有3条记录”代替完整注入。
- **L2 有界正文预取。** 已获准12组继续保留在唯一原生库；主题问题通过原生主题/读取接口，在主生成前获取当前许可下的相关正文。不是每轮塞入12组，也不重导入。读取最多4页、每页32条、总12000字符、总1.5秒；校验身份、有效性、撤销和修订，区分 `found / partial / not_found / unavailable`。列表问题不把top-k当全集，部分覆盖仍回答可靠部分；区分玩过、喜欢、最喜欢、时长与近期范围，不写死用户答案。普通寒暄无额外分类请求，同一Agent的navigate/read补查继续保留。
- **摘要提交一致性。** `summary_consistency.py` 在原生模型调用前固定来源及派生记忆版本，提交时在原生事务内重验。模型调用不持有主会话锁或长事务；纠错/撤销仅与短暂提交阶段串行。陈旧结果进入既有有界退避/隔离，不覆盖新纠错、不提前标记原文已摘要。取消与关闭会等待已开始的本地写入落定再解除适配；摘要异常不阻塞普通Guard聊天发送。
- **主动共享记忆与发送边界。** V2继续复用Private原生候选/调度器，一次主模型决定send/defer/skip及全部气泡，使用同一Guard；未回复后可因新话题再次考虑，同话题冷却，忙碌/暂停/撤销仍生效。已确认气泡与尝试预算分开记录，unknown不自动重发。候选能力不等于生产启用：真实主动始终OFF，后来本人跳过全部剩余验收，首次窗口准备已取消，不存在生效窗口。

| 层级 | V2新增证据 | 结论边界 |
|---|---|---|
| 原生组件 | `four-capabilities-20261023/components-r2/test_phase3_profile_evidence_native.json` 10/10 PASS；`test_phase3_summary_consistency_native.json` 9/9 PASS | 合成资料；包括实际来源/正文、关系查询、撤销、L1原位核心及摘要纠错/撤销/停机竞态 |
| T2完整框架 | `20261023T143959Z-12559/result.json` 20/20 PASS（r2历史） | 固定AstrBot/插件；合成HTTP和OneBot。I18旧断言会从历史取答案，后续增强后才排除该干扰；I11原生主动send/defer/skip仍有效，不能替代真实QQ |
| T3真实Flash | `four-capabilities-20261023/real-memory-r1.json` 7个合成资料场景PASS；主动另3个合成场景PASS | 沿用累计付费预算；不是生产私人资料问答或本人自然度评价 |
| T5发布演练 | `experience-release-v2/result.json` 9/9 PASS；`disabled-core-check.json` 补测PASS | 固定原生worker、合成停写/恢复控制器；非本轮VPS实际迁移。保留12组L2、其他记忆、timeline及引用账本，失败窄回滚，上线后新数据变化时拒绝覆盖 |
| T5实际安装 | `four-capabilities-20261023/production-install-r2.json` | 新备份/隔离恢复PASS；原位L1核心3条且原生注入全文3条，L2保留12组，0次重新导入/模型调用；不表示健康或真实QQ已通过 |

证据均相对 `evidence/phase3/`。L3仍不导入，所有原生召回路径的过期控制缺口未因此宣告完成；原始访谈、既有聊天和历史失败记录保留。整体发布及费用见[四项体验V2报告](phase3-four-capabilities-validation.md)。下面保留原阶段证据与结论。

## 固定上游与唯一主库

MemoryCompanion `2.3.1` / `e40a83005b0a2e0817e5680c7d14398c9ab49790`，PrivateCompanion `6.7.0`，AstrBot `4.29.1`。测试核对实际提交；未改上游。协调器从激活的插件元数据取得唯一 `.star_cls.service`，原生 SessionContext 保留平台、私聊会话、用户、机器人和人格身份。

采集、摘要、断言、来源撤回、检索使用原生 `record_visible_turn`、`maybe_summarize_session(force=False)`、`search`、`record_external_event`、`record_external_memory`、`message_sources.manage` 及 store 公开接口。第一方 SQLite 只存 ID/散列、许可、路由、游标、任务观测、事件状态、导入批次引用。Guard/Recorder 保留原文，MemoryCompanion 是唯一派生记忆内容库。

## 真实接线及资源限制

Guard 优先级 `1000000` 保存原始入站，运行时 `900000` 绑定身份并唤醒采集，消息归组 `-2000000` 才发生。worker 一批最多 32 条，游标逐条提交，错误不前移；无无限消息队列或满队列丢原文。首次绑定 Guard 高水位不导入旧档案，重启补录已批准边界之后的来源。恢复源库时须同步恢复游标库。

只有 Guard `sent` 回执进入原生 timeline；failed/unknown/pending/progress 不作为共同经历。角色区分 user_statement / observed_bot_action，时间取原始 received_at/finished_at。缺少机器人身份时等待实际授权入站，不猜 ID。原生送达前捕获关闭，避免重复采集或将生成当作已发送。

生产候选每会话每小时最多 4 次摘要、并发 1、单批预算 1、至少 4 事件、12 事件或 30 分钟触发、单批 24 事件、输入 6000 字符、摘要 1200 字符、超时 30 秒；使用原生持久预算/失败隔离。不开额外分类模型、embedding、reranker、来源语义模型或每日画像分发。测试降低触发门槛。原生摘要未直接传 max_tokens，最终 token 上限依赖 Provider 配置。

运行时统一 event.persona_id 与后台采集身份。完整进程曾暴露空人格导致来源工具拒绝的问题；修复身份绑定后通过真实 Agent/HTTP 路径验证，未放宽身份检查。

`summary_provider.py` 仅在完全匹配固定原生摘要协议时追加全字段来源约束，模型、prompt、JSON schema、解析及调用次数不变。真实初测摘要有无来源 Bot 动作/未来提醒扩写；约束后的少量样本通过，但不保证所有未来输出无幻觉，见[真实模型报告](phase3-memory-real-validation.md)。

## 纠错、停用和事件

自动纠错走新用户原文 → 原生摘要 assertions → 原生 upsert_assertion。单值住所的新断言替代旧断言，保留 superseded 链接；协调器仅在原生明确建立修订关系后归档旧断言来源摘要并清缓存，原文保留。旧混合摘要中的其他事实可能依赖独立 assertion 或原文检索，不能宣称逐项召回无损。原生 correct_user_memory 直接修 conversation_summary 仍被拒绝，这项上游限制没有隐藏。

姓名、住所、亲友等明确重大身份变更先存来源 ID/散列待确认，不提取原文或对应 Bot 回声。真实入站“确认这次身份变更”通过 Guard 校验后才采集该原文；明确“确认更正”的新陈述也可进入。多个待确认项要求澄清。这是保守词法门禁，不等价于理解全部歧义和同义改写。

phase3_memory_control 已挂到真实 Agent，使用原生 search/read 产生的 read receipt、source_ref/version。**撤回以整条来源为单位，可能影响同一消息中的其他事实**；当前用户未明确确认“整条来源/整条原始消息”时返回影响说明，等待确认。成功的 stop_use/delete_derived/no_mention 均调用原生撤回，抑制普通召回和关联主动候选；墓碑防同一来源重放。Bot 复述是另一来源，须分别处理。分页或工具预算不足时报告 PARTIAL，不能承诺主题完全遗忘。

撤回成功调用原生 new_conversation，返回 context_reset:true，保留旧对话和 Guard/Recorder 档案，防止下一请求继续带旧近期上下文。全局“别记了/不要再记录我/停止使用我的记忆”先关闭原生 capture/recall ACL、取消在途任务，再开启新上下文。物理删除原档案需要独立审批，未作为隐式动作执行。

phase3_event 通过当前原文引文及 Guard source ID 绑定原生事件，同一 event_id/revision 支持待发生、改期、待结果、完成、取消、不想聊、过期。日期仅接受原文明确 YYYY-MM-DD HH:MM；相对或模糊日期澄清，不伪造提醒。Private 原生 scheduler 消费到期 awaiting_result 候选，发前核对 revision/过期/暂停/是否已问；sent 只记 prompted，仍为 awaiting_result，不伪造用户 responded。这不是可靠定时提醒服务。

conversation_observed 与 shared_experience 分开；外部自述、Bot 实际发言和 LLM 摘要不能自动升级为“我们共同经历”。长期语言质量仍需真实用户验证。

## 冷启动、旧历史、资料批次

候选磁盘 memory_summary.enabled=false；prepare 只接线，所有策略/凭据/人格落库成功后 activate 才开实例内摘要及原生主动开关。失败、terminate 同步关闭；重复 prepare 必须先 terminate，关闭幂等。启动通过原生 backlog/window/batch API 核对所有未摘要来源，Guard 高水位不能替代此检查。

最初只读盘点有 **22 条已有原生 timeline 未摘要**，证据 `evidence/phase3/production-memory-counts.json`；当时未获授权，曾由门禁阻止。后续本人已批准精确清单及DeepSeek处理，R1上线后22条全部真实摘要且22/22原文指纹保持，证据 `production-approved22-audit-20261022.json`。未知旧来源仍触发 old_native_timeline_requires_approval，不伪标已摘要或扩大历史回灌。

只读入口：

```bash
python3 scripts/phase3-review-native-backlog.py --database <native.db> --session <authorized-UMO> --output <private-file>
```

使用 SQLite mode=ro、query_only 和一致读事务；输出仅含 ID、内容/归属/时间散列、数量和 manifest hash，无原文，文件 0600、拒绝覆盖，默认 approved=false/allow_model_use=false。用户独立批准后，runtime 才接受 data/phase3/memory-backlog-approval.json 中准确 manifest + 两项许可。首次数量或内容改变需重审；已批准 ID/散列保留引用审计，新未知记录仍拒绝。生成 manifest 本身不等于批准。

原 preview_profile 适配器仍仅允许合成隔离导入；新增受保护的ProfileRelease和生产控制器已完成真实L1/L2导入，精确manifest、模型用途和来源引用均独立核验，不移除旧入口门禁。稳定原生ID、批次引用支持幂等和撤销。**L3仅预览、导入关闭**：固定record_external_memory不保证消费metadata.expires_at，所有召回路径的过期过滤尚未验证；不能以存在过期字段宣称实际过期生效。

## 证据与验收范围

- **T1 + 原生组件：36 tests PASS / 69.048 s**。机器报告 `evidence/phase3/components-retest-memory-final.json`，完整日志同名 .log。固定真实 MemoryService/SQLite/解析/存储/源治理，仅 Provider 合成。覆盖采集、预算、失败、取消、重启、领域修订、撤回、事件、冷启动、精确旧历史许可、只读导出、运行时生命周期、L3 拒绝。摘要委托器另有 4 PASS。
- **T2 完整框架：14 项已完成 PASS**，`evidence/phase3/20261021T162007Z-19206/result.json`。真实 AstrBot 进程/插件/Agent/HTTP Provider 协议/OneBot WebSocket/Private scheduler/Guard 回执，外部对端合成，Docker network none。严格 I09 只用真实 evidence.content/sources.excerpt，查询词回显不是证据；I12 两条来源撤回；I13 重大身份二次确认；I14 重启召回。I14 的新上下文由真实撤回工具创建，测试不替它清空历史；当前 Agent 最终保存、进程重启后，下一普通请求仍不含停用前资料。I10 另验证全局暂停自动 reset，下一模型请求不含暂停前的合成代号。
- **T3 真实模型组件：PARTIAL**。来源约束后四类合成资料及单值住所纠正/重开召回通过少量真实 DeepSeek 样本，初始扩写失败保留。专项报告列出实际调用、tokens、错误和 12 项真实纠错链检查。
- **T4/T5增量**：获准旧22条真实模型整理、资料受控导入、生产停写备份/隔离恢复已有证据；真实QQ更正代号新上下文召回已由本人确认；同摘要批次不等于两次独立断言修订，T6多日质量仍未完成。Phase2成果不替代这些新增测试。

| P0 | 可支持的结论 | 不能外推的部分 |
|---|---|---|
| P15/P16 | T2 自动四类提取；T3 少量真实样本有来源/时间 | 真实 QQ 和长期质量未验收；真实初测曾失败 |
| P17 | 原生重开、完整框架无近期上下文召回、T3 单值更正后召回 | 全类型、多跳问题未覆盖 |
| P18 | 原生断言修订/历史链接/旧摘要抑制/真实二次确认 | 歧义及多值语义未全面验收 |
| P19 | 原生 ACL、准确版本撤回/墓碑、影响确认、普通召回抑制 | 主题语义全覆盖、原档物理删除不在通过范围 |
| P20 | 原生 Agent 按实际入站更新同事件，Private 发前校验 | 模糊日期、任意自然表达、可靠提醒未通过 |
| P21 | 来源、角色、现实层、共同经历分隔 | 长期语言质量需用户体验 |
| P22 | 有界 worker、原生预算/隔离、启动许可、恢复游标 | 生产费用、4GB 多日资源测量未做 |

组件复现：锁定环境运行 scripts/test-phase3-components.py --child test_phase3_memory_native.py --output <report.json>；完整隔离：bash scripts/test-phase3-isolated.sh。缺失上游或SKIP不记PASS。当前新增空闲摘要恢复的当时38项原生回归和15项完整联调已通过，见 `memory-idle-recovery-20261022-r3/result.json`、`20261022T151726Z-10731/result.json`。本轮无GitHub推送。

生产纠错验收暴露空闲唤醒缺口：原桥接仅在有新归档时调度摘要，原生预算到期后没有新消息就不继续。修复在既有30秒归档循环中读取原生next_summary_batch(force=False)及原生时间窗口判断，有到期工作才唤醒；调用预算、并发、重试、隔离状态、暂停和原文许可保持不变。38项包含预算未到期零调用、重开后到期恢复、没有新入站也能执行、安静时间触发及暂停。没有第二队列/调度器，不使用force=True绕过预算。发布结果以最新主报告为准。

2026-10-23早期续接记录：空闲恢复增量已发布，原等待12条完成原生摘要；与发布前快照比较77条timeline原字段全部保持（当时84条），`memory-originals-preserved-20261023.json`。当时资料召回新增6项原生验证、17项完整联调及有限真实Flash样本，随后真实QQ复测再次失败。该历史结果保留；V2 r2安装并恢复登录后首项真实验收仍失败，不能继续写成等待旧修复的首次复测，详见[专项报告](phase3-profile-recall-validation.md)。

### 23:05 +08:00 登录恢复与历史READY状态

本人扫码后已核验当前在线备用QQ身份，原生自动登录设置重新保存并验证落盘；没有额外重启或再次登录请求。23:05:16实际isLogin/coreReady=true、loginPhase=ready，内部OneBot ESTABLISHED=1，策略PASS、47源文件摘要匹配。原先waiting_qrcode及一次快捷登录未恢复的证据保留为本次发布历史。

23:05历史时点曾为 **CANDIDATE_READY_FOR_UAT**，仅表示候选自动验证及发布前置条件满足；23:07本人反馈失败后转为 **UAT_FAILED_INVESTIGATING**，P08真实FAIL未关闭，总体PARTIAL，主动OFF。仅邀请过此前失败的资料问题，未发整组作业。证据 `evidence/phase3/four-capabilities-20261023/candidate-readiness-r2.json`、`production-health-after-login-r2.json`、`napcat-save-current-session-r2.json`。

## 2026-10-24 原生上下文发布候选与诊断边界

最新真实失败轮已找到完整452字L2，模型44字、清理后43字并经Guard确认发送，没有空正文恢复；问题是回答否认已有资料，不是已证实的下载或发送失败。生产副本的最终Provider请求重组也证明正文与3条L1仍在，不能再称“只查了数据库、尚未证明最终请求含正文”。它仍不是原失败请求的实时抓包。

真实Flash合成长历史对照中，仅compact来源投影仍失败；相同14169输入tokens的system对照否认资料，而统一当前原生记忆上下文后正确使用合成正文及关系范围。候选 `profile_publication.py` 因此将完整L2证据放在唯一当前 `<MemoryCompanion-Context>` 子块，通过原生 `_publish_reply_memory_context` 取得引用、连续性和发布回执；不另造主库、固定画像或导入批次。3条L1和原有native块/引用保持，12组L2未重导入，原文与来源未改变，L3仍不导入。

发布前及统计await后复核ACL、撤销、精确revision和真实当前事件身份；目录读取发生在最终发布之前。纠错/撤销、超时或取消后只移除不再有效的本轮输入，不删除历史。重复调用只退休此子块新增的refs；原native refs保留。非关键统计失败仅在确有匹配新snapshot/ref/digest时接受，不能catch后假称发布成功；未知可选角色来源继续为不完整。原生RoleInputSources发布边界已测，但不能外推成独立完整Private角色注册生命周期。

23项原生组件（publication11/evidence12）、12项严格当前块fixture、8项audit和4项probe逻辑测试通过；证据在 `four-capabilities-20261023/` 下的 `profile-native-publication-validation.json`、`profile-http-fixture-native-context-r1/summary.json`、`native-publication-audit-r1/summary.json`、`native-probe-contract-r1/summary.json`，旧3项probe索引保留。最新完整固定栈 `20261023T160122Z-23369/result.json` 为20/20 PASS，保留16条旧失败历史/4个工具对，I18只使用最后user唯一原生块，1次主请求和1条Guard确认历史；纠错/撤销、摘要、主动、媒体和空正文恢复回归通过。合成对端不代替真实QQ，计数不叠加为新的全量去重总数。

`private-native-final-request-r1.json` 在隔离生产副本捕获18条最终messages，完整452字L2及3条L1同在当前user，system无V2证据标记，native 8 refs/snapshot有效，模型/QQ均0。首次私人重放曾被自动审批拒绝且未执行；后来本人明确授权后，`private-native-model-r1.json` 才记录一次真实Flash重放：196字实质非否认回答、stop、估算0.025070元，工具0、QQ0、生产修改0。完整payload及回复只留服务器私有目录，文本对应不作为事实完整性证明；该诊断时点本人完整性/自然度仍NOT_TESTED、P08仍FAIL。该诊断时点共享保守预留为5.50元，沿用原10元预算、非账单。

### 最新源码发布与资料保持

`phase3-20261024-profile-native-r2` 已于00:14:41 +08:00完成健康核验，当时进入 `CANDIDATE_READY_FOR_UAT`；r1仅PLAN失败未安装。AstrBot新停写备份及16库隔离恢复通过后只更新三份Runtime源码，没有再次迁移/导入或写数据库和配置；NapCat未重启，QQ在线、OneBot连接1。49份第一方源码、配置及上游锁核验通过。已有34条memory、90条timeline原字段、48条入站和13条legacy出站逐行保持，原3L1/12L2及来源审批不变。部署调用模型0，源码journal可窄回退，不覆盖全data或新聊天。

证据 `four-capabilities-20261023/runtime-code-{install,health,preservation,readiness}-r2.json`。该安装时点此前真实资料FAIL仍保留，只邀请原资料单题。自动记忆继续原授权范围，主动仍OFF，L3仍不导入。

### 原资料题T4通过后的顺序验收历史

本人现已确认原资料题“通过”。`four-capabilities-20261023/uat-profile-native-pass-r1.json` 核对两个新轮次均found完整452字并有原生publication/snapshot回执，129/126字原正文经清理为128/125字，每轮单条、一次发送尝试、sent确认，历史hash一致，补答0、Search0。本题的实际正确完整性已通过，不再保留为当前FAIL；旧失败历史不删除。P08在获准L1/L2题范围内T4 PASS，当时为 `UAT_IN_PROGRESS`，总体PARTIAL；随后关系区分和新会话单独验证通过，见下节；跨主题及长期记忆质量仍需后续证据。

同窗口2条Agent run统计峰值估算0.050580元，另1次成功原生自动摘要估算0.016106元；Agent统计条数不当作HTTP调用数，估算不当账单。没有扩大摘要授权、重导入或清空原文，主动窗口仍未开始。

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
