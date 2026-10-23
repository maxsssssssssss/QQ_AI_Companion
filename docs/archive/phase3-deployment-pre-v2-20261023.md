# Phase 3 发布与回滚操作说明

用户已集中批准首轮受限上线。生产仍使用 Phase2 锁定镜像，已增量启用 V0.1、自然文本、自动记忆、多气泡、受限 Search/Fetch；不再处于“等待全部部署授权”。实际状态与验收层级见 [P01–P36](phase3-validation.md)。

## 当前授权与已完成切换

允许维护停写、备份/隔离恢复、必要迁移和短暂服务重启；不包含整机重启、内核/SSH变更或新增公网端口。本人已核对并批准L1 3组/L2 12组及被动/批准窗口内主动模型使用，实际窄导入完成；L3和排除项仍不导入。主动权限已批准为单一主QQ的条件72小时窗口，尚未满足全部真实被动验收条件，未开启。后台搜索不在批准范围。

旧22条未摘要聊天的精确清单hash为 `7b259636f609454a4170525d8be6baca61f24379389de907ecc42904d63ffbe6`。本人已明确批准这份清单的整理和现有DeepSeek使用；R1部署重新核验后，将授权置于私有 `data/phase3/memory-backlog-approval.json`。2026-10-22只读复核：22条全部真实摘要，22/22原文指纹保持。原临时审阅文件路径已不存在，以当前服务器受保护清单及脱敏核对证据为准；不扩大历史范围，不修改“已总结”状态伪造完成。

| 增量 | 服务器发布目录（`/opt/qq-ai-companion/releases/` 下） | 新停写快照与隔离恢复 |
|---|---|---|
| R1人格/自然文本/自动记忆 | `phase3-20261022-r1` | `phase3-preflight-20261022`，11库PASS |
| 多气泡/确认历史/主动发送适配 | `phase3-20261022-multibubble-r3` | `phase3-multibubble-20261022-r2`，13库PASS；首次安装因UTF8 BOM在覆盖前中止，修复后复用同一经核验快照 |
| 三主题受限Search/Fetch | `phase3-20261022-search-r1` | `phase3-search-preflight-20261022-r1`，14库PASS |
| QQ纯文本格式清理 | `phase3-20261022-plain-r1` | `phase3-plain-preflight-20261022-r1`，PASS；详见最新健康证据 |
| 已批准L1/L2资料 | `phase3-20261022-profile-r2` | `phase3-profile-preflight-20261022-r2`，15库PASS；R1在候选usage文件模式检查处中止并恢复服务，修复后新取备份 |
| 原生预算等待摘要恢复 | `phase3-20261022-memory-recovery-r1` | `phase3-memory-recovery-preflight-20261022-r1`，16库PASS |
| 空正文一次有界补答 | `phase3-20261022-empty-response-r1` | `phase3-empty-response-preflight-20261022-r1`，16库PASS |
| 资料导航指引/部分主题目录 | `phase3-20261023-recall-r1` | `phase3-recall-preflight-20261023-r1`，16库PASS；首次健康检查因策略读取BOM失败而BLOCKED，后续编码恢复/持久修复另记 |
| 原生配置BOM策略兼容 | `phase3-20261023-policy-bom-r1` | `phase3-policy-bom-preflight-20261023-r1`，16库PASS；配置语义不变，保留BOM后本次启动策略PASS |

每次保留 source manifest、候选配置、installation journal及backup-result。恢复副本均未启动QQ。多气泡、搜索和格式清理都不覆盖整个data，不升级上游。Search单独新增持久尝试账本及原4次探针的幂等继承；安装器的`databases_touched=false`只描述文件覆盖动作，不能误称整个搜索发布没有任何数据库写入。

AstrBot镜像ID：`sha256:a0bf74942fc2ba504932897f7f6ba8d8bd01c1084ea6448c399b8df579bc77af`；NapCat镜像ID：`sha256:2f1dd81bdd62f27b3431ac9254000602ed7ed6dfbf0a38bb6e735fd16bc4ee87`。R1前镜像备份和配置/插件/一致状态仍保留。管理映射仅`127.0.0.1:6185/6099`，OneBot只在容器网内。

## 候选与安装

1. 固定当前生产镜像 digest、仓库 commit 和插件锁。只读再次确认 QQ 在线及策略就绪。备份前停止所有 AstrBot/NapCat 写入者，复用 `scripts/phase2-backup.py` 保存完整状态和登录态，检查 SQLite 与文件哈希；把快照恢复到**不启动 QQ 的隔离目录**。
2. 在新的私有目录生成配置候选：`scripts/phase3-stage.py --snapshot <已停写快照的state> --output <新的候选目录> --settings <私有已批准开关.json>`。启用已批准搜索时提供 `--search-key-file <600文件>`。脚本拒绝覆盖、只复制第一方插件、不会复制数据库或改生产。输出含聊天凭据，因此整个候选必须保持私有，不能加入 Git。
3. 新摘要及主动生成在磁盘配置中始终关闭，完成策略和人格持久化检查后才在进程内启用。若原生记忆库含未由本次授权来源映射覆盖的旧未摘要 timeline，启动拒绝 `old_native_timeline_requires_approval`；需要先审阅旧历史处理范围，不得假标已总结或自动回灌。审查候选 diff 与 `review-manifest.json`。管理端口和 OneBot 内部网络保持现状；不能用测试镜像替换生产镜像。新默认配置全关闭，不推断授权。Persona 使用运行时私有称呼绑定，不把真实资料写进公共模板。
4. 停写时仅覆盖批准的第一方插件及对应配置，并维持生产 UID/GID 10001 和 Secret 600。原始第三方来源保持不变，不把候选目录直接覆盖整个 data。Guard 首次启用新表之前自动创建 `guard.sqlite3.pre-phase3.sqlite3`，旧 inbound/outbound 保留；这不代替完整生产备份。
5. 先启用被动路径。启动后检查本次 `phase2_policy/readiness.json`、原生插件版本、单一 Persona、关闭的群聊/桌面/文件/cron 工具、新出站表与资源占用。记忆增量游标首次从旧档案末端开始；未经批准不回灌 Phase 2 历史。
6. 真实 QQ 小样本核对普通/图片、三条合并、语义气泡、纠错/召回、停止记忆、搜索进度/最终回复与逐条回执。未确认发送的 unknown 只诊断，不自动补发。
7. 主动发送最后开启：除窗口配置外，PrivateCompanion 原生用户 capability 和日限也需执行已批准的窄范围迁移，调用 `authorize_native_target(..., approved=True)` 前备份原生用户数据，并核验已认证的 UMO。只开文本，一日最多 2 次、冷却至少 4 小时、夜间静默为候选默认，本轮该规则已获批准，不能重复索取相同权限；实际起止日期按被动通过时间计算。窗口到期自动阻断；后台联网保持关闭。

## 回滚

- 紧急停新能力：先停 AstrBot 写入，关 `phase3_runtime` 及 Guard 新开关、Search、原生主动开关；恢复 Phase 2 配置和人格。确认搜索工具、记忆/主动后台任务未留下。停止期间到达消息可能错过，不能承诺补发。
- 完整版本回滚：恢复同一份可信停写快照中的配置、插件和数据库，使用原来的生产镜像 digest，再启原实例。回滚可能失去快照之后的新记忆/聊天状态，执行前另保存当前状态并列清影响；不删除重要数据。
- 不让原实例和恢复实例同时使用同一 QQ 登录态。若原生账号要求重新验证，由账号持有人处理；不强行重试。
- 回滚后重新检查 QQ 登录、OneBot 重连、Phase 2 策略，以及本人发送的一条真实私聊。Docker healthy 不能替代上述验收。

本地迁移/安装失败恢复已有合成测试，新的生产停写快照和隔离恢复也已实际完成。没有执行破坏性的完整生产回滚；不能把隔离恢复写成已全量回滚生产。旧Phase2历史备份也不冒充新增切换后的快照。

## 可独立关闭的开关与剩余发布动作

`phase3_multibubble_enabled=false`保留一条经原解析器清理的文本；`phase3_plain_text_enabled=false`只关闭Markdown装饰清理；`phase3_empty_recovery_enabled=false`关闭空正文补答；Runtime的`memory_recall_guidance=false`关闭资料工具指引和主题目录。紧急停新能力优先关闭对应模块并正常重启AstrBot，保留所有新增聊天。修改配置仍遵守实际文件UID/GID与600权限，不能把root候选直接覆盖成服务不可读。

实际资料导入使用`phase3-profile-production.py`，默认dry-run。批准后先停两写入者，确认当前state与新停写快照逐字节一致；断网worker仅挂载原生Memory子集和精确批准批次，不能访问Key/QQ。候选完整性通过后仅安装Memory数据库与第一方引用账本，保留文件属主并用journal恢复安装失败；不覆盖整个data。L3仍拒绝，完整访谈仍未导入。

此次资料导入包SHA256为 `36fdb2cb7aa497af11b5e56edbcebfdd2ba46a41aabe82b352a22eefedb8c9a9`。原有15条记忆和77条时间线哈希保持，新增15条来源明确的资料；实际原生检索与问法限制见[资料报告](private-profile-import.md)。撤销批次须保留原文、使用原生接口及墓碑，并开启新上下文避免继续携带已撤回的旧模型上下文；不能通过全数据覆盖撤销。

首次主动窗口参照[窗口规则](phase3-proactive-approved-window.md)：记录真实被动验收通过时刻后，生成明确年月日及+08:00开始/结束时间。原生capability迁移默认拒绝，需要显式已批准目标参数；仅修改核验过的单一目标，不影响其他联系人或群。窗口和日限/冷却必须同时满足，不能为测试制造话题或重发unknown。

新增发布包：空闲摘要恢复 `bda37a8e17444f460cbdc3ba4423d83887d97d0e24fdd3b52b4e5669cfc37b59`；空正文补答 `555452e90924261088680d1dbfe105c12d4c7755e08313b3ddfe9f197a45cbdb`；资料召回 `c7320ec8f47dade1f3e690cf67fdf5da6aa752bc5d0f94ae16fb791f59826cc7`。这些版本沿用原镜像和上游依赖，均可独立关闭，不覆盖整套数据。

2026-10-23资料召回首启暴露策略读取器未接受原生配置UTF-8 BOM：聊天被门禁阻止，未绕过门禁。先备份配置并仅移除BOM（JSON值/归属不变）恢复策略PASS，再对读取器增加utf-8-sig并用真实AstrBotConfig保存格式做16项回归。永久修复单独发布 `phase3-20261023-policy-bom-r1`，包hash `f2ad3a635e6b133881d408282cba717f9000d7f6836e4e5933184f48ccd27896`；健康证据应同时验证实际BOM配置和新策略文件，不只看容器healthy。

最终00:24:12 +08:00健康验证：两原容器healthy、QQ在线，当前BOM格式配置和新策略源hash匹配，readiness更新时间晚于本次AstrBot启动；没有读取陈旧PASS替代上线核验。
