# Phase 3C 自动记忆适配与隔离验证

日期：2026-10-21 至 2026-10-23。**Phase 3C 总体验收仍为 PARTIAL，自动记忆已受限上线。** 精确获准旧22条已真实摘要且原文指纹保持；本人新批准L1 3组/L2 12组已窄导入并核对来源，检索限制见[资料报告](private-profile-import.md)。真实QQ更正代号经摘要、重启及新会话召回已由本人确认，资料抽样准确性仍FAIL，生产主动关闭。下列原组件/合成测试只代表其实际层级，不能改写成真实QQ结果。

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

2026-10-23续接：空闲恢复增量已发布，原等待12条完成原生摘要；与发布前快照比较77条timeline原字段全部保持（现有84条），`memory-originals-preserved-20261023.json`。资料召回新增6项原生验证、17项完整联调及有限真实Flash样本；真实QQ资料仍待修后复测，详见[专项报告](phase3-profile-recall-validation.md)。
