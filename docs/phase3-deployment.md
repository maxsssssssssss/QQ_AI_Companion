# Phase 3 发布与回滚操作说明

2026-10-25 14:04 +08:00已授权发布[气泡边界换行修复](phase3-bubble-boundary-validation.md)：只替换Guard `bubbles.py/main.py`，一致性备份及14个SQLite隔离恢复检查通过后，仅重启AstrBot一次。QQ在线、OneBot重连、启动策略及源码核验PASS；NapCat、配置、原聊天/记忆及主动额度保持。发布包`phase3-20261025-bubble-boundary-r1`中的两文件源回滚入口和快照位置见专项报告。

2026-10-24后续窄修复：[主动空计划恢复](phase3-proactive-plan-recovery.md)。新控制器`phase3-deploy-proactive-recovery.py`锁定现有50份第一方源码、完整配置及固定上游，只替换Runtime的`proactive.py`；停写备份/隔离恢复后仅启动原AstrBot容器，NapCat保持运行。回滚只还原该源码，保留新聊天和记忆，完整命令与结果见专项报告。

## 2026-10-24 清洁开始与统一r4正式安装

用户已明确批准保留V0.2人格、L1/L2、登录态与配置，清除旧聊天、聊天派生记忆及旧聊天备份，并发布最新版。停写候选清理/13库完整性及隔离恢复、11文件窄替换、32旧文件删除、r4的13代码＋5配置安装和旧备份清理已完成。同一固定镜像的两个容器已重新创建；05:10:10 +08:00启动健康、QQ/OneBot及实际配置核对**PASS**。

最新操作说明与代码配置回滚入口统一见[清洁开始发布记录](phase3-fresh-start-release.md)。本次独立r4安装器不改旧V3控制器，不绕过其JSON字节校验；旧缺口暂不修复。旧含聊天快照已按本次授权删除，只保留清洁基线，因此下文旧备份路径仅代表当时证据，不能继续作为可恢复旧聊天的入口。

下文“不重启/待生效/OFF”的段落属于本次明确发布授权之前的历史状态；本次安装的C/80轮、长期主动、新气泡提示词、V3.1及general_public以最新发布收据为准。

通用公开搜索只用 `scripts/phase3-search-config.py --source-config <600的phase3_search_config.json> --output-dir <全新私有目录>` 准备范围差异，不用全量stage改写现有生产。它保留身份/开关/限额，只改scope及两个旧名单字段。同步位置与回退要求见[通用公开搜索](phase3-public-search-validation.md)。新源码和general_public配置必须在获准统一生效时一起窄应用，原预算数据库保持；现在不执行应用或重启。

2026-10-24最新同步要求：将C/80轮、长期主动、新多气泡提示词及通用公开搜索合成一份服务器待发布候选，后续本地更新也同步；不修改运行目录、不热重载、不重启，等用户通知再统一生效。候选路径、核验回执与后续约定见[统一待发布同步](phase3-pending-sync.md)。下文“仅本地保存”描述提示词更新当时的历史状态，最新同步状态以该候选回执为准。

**当前变更方式：继续遵守用户2026-10-24“先保存、不重启、后面一起生效”的指令。** 本轮执行错误地把V3任务中的“部署候选”理解为覆盖该限制，实际安装V3并重启AstrBot一次；用户随后明确指出“没让你重启”。此次重启未获用户授权，属于执行偏差，不能描述为明确获准的独立发布。后续只保存，待用户明确批准统一生效；当前不得继续部署、回滚、重启或通过热重载提前应用改动。方案C压缩和长期主动两份候选仍未应用。下述模型参数发布及其AstrBot重启已在原“不重启”指令到达前完成，保留为历史记录。

2026-10-24新增待生效候选：方案C（取消20轮限制、原生Token摘要压缩、128000操作预算、最近15%完整上下文），摘要失败回退最近最多80轮并继续Token保护。配置与最小Runtime适配只保存为候选，未写入运行目录；不因本地staging默认值已更新而视作生产已生效。隔离证据、触发时机与后续字段合并要求见[会话压缩候选](phase3-context-compression.md)。

2026-10-24后续[长期主动候选](phase3-proactive-continuous.md)同样仅保存：无72小时截止，上海09:00–23:59，每日5次尝试、至少3小时；独立每日5次模型评估，skip/defer也计入。新候选继承已核验的80轮fallback，保留旧压缩候选，运行配置和容器不变。未来统一生效需合并两份候选，不能覆盖旧完整配置；本次未启用主动或迁移capability。下文72小时窗口继续作为历史证据，不再是待生效规则。

2026-10-24 后续人格替换：`phase3-20261024-persona-v02-r1` 已将当前核心改为用户原文 V0.2。仅覆盖模板和渲染器，使用新的AstrBot停写备份及16库隔离恢复；旧资料修复发布继续作为代码基线，其固定哈希校验不包含这次覆盖。回退范围和生效核验见[人格接入](persona-integration.md#v02-替换与生效核验)。

2026-10-24后续提示词候选仅在本地保存：用户提供的新`[Phase3 自然多气泡]`全文、示例说明及去重标识同步；控制分隔符不变。未上传或应用到VPS，线上旧提示词保持，等待用户明确通知统一生效；详见[提示词候选](phase3-multibubble-v3-validation.md#后续提示词更新仅本地保存未生效)。不得直接重用已发布V3包的旧hash或将本地新源码视为生产已加载。

## 2026-10-24 自然多气泡V3实际安装与执行偏差

`phase3-20261024-multibubble-v3-r1`已安装，控制器回执为`INSTALLED_AWAITING_HEALTH_AND_QQ`。本次仅替换Guard的`main.py`、`bubbles.py`、`_conf_schema.json`，并在`data/config/phase1_guard_config.json`中新增`phase3_casual_split_fallback_enabled=true`。保留原配置其余字段及UTF-8 BOM，安装前核对当前完整受保护文件清单，未套用旧47文件基线。

按用户随后“提示词暂时不改”的要求，原489字符分段提示词及人格正文原样保留；核心人格仍为V0.2，分段附加说明中的V0.1文字仅是本轮未修改的遗留引用。DeepSeek Flash继续Thinking Off，AstrBot全局`segmented_reply`关闭。Runtime、cmd_config、人格、资料和凭据均不随本包覆盖；主动仍关闭。此前`phase3-20261024-context-compression-r2`与`phase3-20261024-proactive-continuous-r1`两份待生效目录保持原样，不因本次重启变为已启用。

本次实际停止/启动原AstrBot一次，NapCat未重启。用户未授权这次重启；窄变更、备份通过或“部署候选”的措辞都不能替代此前明确的“不重启”约束。已停止进一步部署/回滚/重启，不能为撤销本次偏差而擅自再重启一次。新停写快照`phase3-multibubble-v3-20261024-r1`的16个SQLite完整性和隔离恢复均PASS，恢复副本未启动；通过后才写入上述四文件。发布回执位于`evidence/phase3/multibubble-v3-20261024/production-install-r1.json`。健康核验与新增真实QQ体验需分别记录；安装成功和备份PASS不代表本次QQ或自然度验收通过，详见[V3专项记录](phase3-multibubble-v3-validation.md)。

四文件原件和校验记录保存在`/opt/qq-ai-companion/releases/phase3-20261024-multibubble-v3-r1/source/bubbles-v3-journal`。独立关闭兜底的配置入口为`phase3_casual_split_fallback_enabled=false`，模型原生语义分段可保留；该变更现在也不能自动应用。后续只读核对发现AstrBot规范化Guard JSON序列化，语义与候选一致但严格字节哈希不符；现有回滚控制器会拒绝，需要先在本地补齐等价序列化校验及测试。以下命令仅保留为修复此门禁且以后获得明确批准时的恢复参考，当前不得执行回滚或重启。完整V3回退使用同包控制器，先运行只读计划：

```bash
v3_source=/opt/qq-ai-companion/releases/phase3-20261024-multibubble-v3-r1/source
v3_rollback_run=phase3-multibubble-v3-rollback-20261024-r1
sudo -n env PYTHONDONTWRITEBYTECODE=1 python3 "$v3_source/scripts/phase3-deploy-bubbles-v3.py" \
  --source "$v3_source" --deployment /opt/qq-ai-companion/deployment \
  --run-id "$v3_rollback_run" --rollback
```

以后获得用户明确回滚授权，并确认计划仍对应当前已安装版本后，才可在同一入口显式执行：

```bash
sudo -n env PYTHONDONTWRITEBYTECODE=1 python3 "$v3_source/scripts/phase3-deploy-bubbles-v3.py" \
  --source "$v3_source" --deployment /opt/qq-ai-companion/deployment \
  --run-id "$v3_rollback_run" --rollback --apply
```

上述run-id仅作首次回退示例；每次实际回退必须使用新的日期/序号，不能复用已有备份或操作记录。控制器共用`.astrbot-runtime-release.lock`，回退前再次停AstrBot、新建一致快照并完成隔离恢复，然后只恢复journal中的四个文件；配置或源码有后续漂移即拒绝覆盖。它不恢复数据库、不删除新聊天、不覆盖整个data、不更改两份待生效候选，也不重启NapCat。回退完成后仍需核对服务健康与实际聊天。

## 2026-10-24 主模型参数更新

用户要求的当前参数如下；Phase 3 staging 同步生成这些值，Phase 2 历史初始化模板保持原值。

| 参数 | 当前值 | 生产字段 |
|---|---:|---|
| Flash单次最大输出 | 8192 tokens | `provider[id=deepseek/flash].custom_extra_body.max_tokens` |
| 主模型请求超时 | 120秒 | `provider_sources[id=deepseek].timeout`；模型有同名覆盖时同步 |
| 会话请求频率 | 40次/60秒 | `platform_settings.rate_limit`，保留原`discard`策略 |
| Agent最大步数 | 10 | `agent_runner.config.misc.max_steps` |

频率是固定AstrBot对每个`unified_msg_origin`的入站消息限流，不是DeepSeek账户的全局HTTP请求RPM。120秒是SDK请求超时，不是整轮Agent总时长；原生Agent步数耗尽后仍可追加一次无工具final收尾。独立补答、搜索工具、后台整理的局部预算保持原值，Pro仍停用，未增加测试费用或发送消息。

本次发布为 `phase3-20261024-model-limits-r1`，仅修改`cmd_config.json`和第一方启动校验`policy.py`，将门禁上界同步为8192/10。新备份和隔离恢复、启动生效值及QQ连接以私有`evidence/phase3/model-limits-20261024/`回执为准。回退应停AstrBot后按该发布journal恢复两个文件并重启，不恢复整个数据目录；人格V0.2及已有聊天保留。旧版本发布器固定的配置/源码摘要不包含本次变化，不能用旧快照覆盖当前配置。

用户已集中批准首轮受限上线。生产仍使用 Phase2 锁定镜像，已增量启用 V0.1、自然文本、自动记忆、多气泡、受限 Search/Fetch；不再处于“等待全部部署授权”。实际状态与验收层级见 [P01–P36](phase3-validation.md)。

## 当前授权与已完成切换

允许维护停写、备份/隔离恢复、必要迁移和短暂服务重启；不包含整机重启、内核/SSH变更或新增公网端口。本人已核对并批准L1 3组/L2 12组及被动/批准窗口内主动模型使用，实际窄导入完成；L3和排除项仍不导入。此前单一主QQ的条件72小时主动授权已由用户最新“跳过全部、主动保持关闭”指令覆盖；主动未启用，没有有效窗口，不能按旧计划自动开启。后台搜索不在批准范围。

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
| 四项体验V2 r2 | `phase3-20261023-experience-v2-r2` | `phase3-experience-v2-20261023-r2`，16库PASS；3L1同ID核心迁移、12L2及88条timeline保持，QQ重启后待本人扫码 |
| 原生配置BOM策略兼容 | `phase3-20261023-policy-bom-r1` | `phase3-policy-bom-preflight-20261023-r1`，16库PASS；配置语义不变，保留BOM后本次启动策略PASS |

每次保留 source manifest、候选配置、installation journal及backup-result。恢复副本均未启动QQ。多气泡、搜索和格式清理都不覆盖整个data，不升级上游。Search单独新增持久尝试账本及原4次探针的幂等继承；安装器的`databases_touched=false`只描述文件覆盖动作，不能误称整个搜索发布没有任何数据库写入。

AstrBot镜像ID：`sha256:a0bf74942fc2ba504932897f7f6ba8d8bd01c1084ea6448c399b8df579bc77af`；NapCat镜像ID：`sha256:2f1dd81bdd62f27b3431ac9254000602ed7ed6dfbf0a38bb6e735fd16bc4ee87`。R1前镜像备份和配置/插件/一致状态仍保留。管理映射仅`127.0.0.1:6185/6099`，OneBot只在容器网内。

## 候选与安装

1. 固定当前生产镜像 digest、仓库 commit 和插件锁。只读再次确认 QQ 在线及策略就绪。备份前停止所有 AstrBot/NapCat 写入者，复用 `scripts/phase2-backup.py` 保存完整状态和登录态，检查 SQLite 与文件哈希；把快照恢复到**不启动 QQ 的隔离目录**。
2. 在新的私有目录生成配置候选：`scripts/phase3-stage.py --snapshot <已停写快照的state> --output <新的候选目录> --settings <私有已批准开关.json>`。启用已批准搜索时提供 `--search-key-file <600文件>`。脚本拒绝覆盖、只复制第一方插件、不会复制数据库或改生产。输出含聊天凭据，因此整个候选必须保持私有，不能加入 Git。
3. 新摘要及主动生成在磁盘配置中始终关闭，完成策略和人格持久化检查后，仅在进程内启用当前获准的记忆整理；主动仍关闭。若原生记忆库含未由本次授权来源映射覆盖的旧未摘要 timeline，启动拒绝 `old_native_timeline_requires_approval`；需要先审阅旧历史处理范围，不得假标已总结或自动回灌。审查候选 diff 与 `review-manifest.json`。管理端口和 OneBot 内部网络保持现状；不能用测试镜像替换生产镜像。新默认配置全关闭，不推断授权。Persona 使用运行时私有称呼绑定，不把真实资料写进公共模板。
4. 停写时仅覆盖批准的第一方插件及对应配置，并维持生产 UID/GID 10001 和 Secret 600。原始第三方来源保持不变，不把候选目录直接覆盖整个 data。Guard 首次启用新表之前自动创建 `guard.sqlite3.pre-phase3.sqlite3`，旧 inbound/outbound 保留；这不代替完整生产备份。
5. 先启用被动路径。启动后检查本次 `phase2_policy/readiness.json`、原生插件版本、单一 Persona、关闭的群聊/桌面/文件/cron 工具、新出站表与资源占用。记忆增量游标首次从旧档案末端开始；未经批准不回灌 Phase 2 历史。
6. 真实 QQ 小样本核对普通/图片、三条合并、语义气泡、纠错/召回、停止记忆、搜索进度/最终回复与逐条回执。未确认发送的 unknown 只诊断，不自动补发。
7. 历史主动方案（本轮已取消，禁止据此启用）：除窗口配置外，PrivateCompanion 原生用户 capability 和日限需窄范围迁移，调用 `authorize_native_target(..., approved=True)` 前备份原生用户数据，并核验已认证的 UMO。原方案仅文本、每日最多2次、冷却至少4小时、夜间静默、条件起点后72小时自动停用；这里只保留实现与审批历史。用户最新要求主动保持关闭，未来启用须有新的有效指令；后台联网保持关闭。

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

## V2 r2 已执行记录

2026-10-23已执行 `scripts/phase3-deploy-experience.py --apply`：源清单/生产基线/锁版本核对→停两原服务→新停写备份与隔离恢复→断网原生core worker→窄数据库安装与48目标overlay→恢复原服务。包SHA256 `708d7c916ffaa1722ea2ffbd49579e341b5b95c4356f40d9f70cbaa2a38c2bb1`，无额外模型调用。r1仅暂存未安装，避免混淆。

本次备份16个SQLite完整性PASS，AstrBot1553文件、QQ配置/登录态2274文件；恢复副本未启动。获准15条资料保留相同ID/正文/来源，3L1改为core_memory并以原生renderer验证完整注入（1600字核心预算），12L2逐行保持、其余记忆和88条timeline保持，总记忆仍34条。L3未导入。

22:56:39+08实测：两原容器healthy、固定镜像不变、47第一方源文件摘要匹配，readiness是本次启动生成的PASS，管理端口仅loopback。QQ等待扫码、OneBot监听但无已建立连接；本人随后明确要求快捷登录，已对备份匹配的原保存账号尝试一次原生登录，仍未恢复。自动登录配置存在但候选列表0，不能解释成容器成功即QQ恢复。当前 `INSTALLED_WAITING_OWNER_QQ_AUTH`，扫码及连接恢复后再进入UAT；不为此回滚记忆、改设备身份或反复登录。

窄回滚入口保留在本次发布 `source/scripts/phase3-deploy-experience.py` 的 `rollback_overlay` / `rollback_database`，对应 `source/installation-journal` 与 `backups/phase3-experience-v2-20261023-r2/.profile-candidate-zhylg5ua/install-journal/journal.json`。它们校验停止状态与安装后摘要，不覆盖新变化；不是可以任意时刻直接套用的全量回滚命令。已恢复写入后优先关闭相关开关/回退代码；需要数据回滚必须先另存新增数据并列明影响，不能恢复整套data冒充安全撤销。`memory_recall_guidance=false`同时关闭V2本地正文预取及指引，保留已批准原生核心资料。

本次证据位于 `evidence/phase3/four-capabilities-20261023/production-{plan,install,health,preservation}-r2.json`；全自动测试、费用、实际资源与仍缺的T4/T6见[四项体验](phase3-four-capabilities-validation.md)。

### 23:05 +08:00 登录恢复与当前状态

本人扫码后已核验当前在线备用QQ身份，原生自动登录设置重新保存并验证落盘；没有额外重启或再次登录请求。23:05:16实际isLogin/coreReady=true、loginPhase=ready，内部OneBot ESTABLISHED=1，策略PASS、47源文件摘要匹配。原先waiting_qrcode及一次快捷登录未恢复的证据保留为本次发布历史。

当前为 **CANDIDATE_READY_FOR_UAT**，仅表示完整候选及自动验证/发布前置条件满足；P08真实FAIL尚未关闭，总体PARTIAL，主动OFF。已按顺序仅邀请此前失败的资料问题，未发整组作业。证据 `evidence/phase3/four-capabilities-20261023/candidate-readiness-r2.json`、`production-health-after-login-r2.json`、`napcat-save-current-session-r2.json`。


### 23:07 +08:00 后续 UAT 失败

此前 `CANDIDATE_READY_FOR_UAT` 是开始验收时的状态；首项个人资料问答收到本人“失败”反馈后，当前为 `UAT_FAILED_INVESTIGATING`。P08 保持 FAIL，主动保持 OFF。随后只在服务器无网络、无 QQ 登录态的诊断副本中重组请求：完整452字L2与3组L1进入最终HTTP结构；未进行新的生产安装或服务重启。详见四项体验及资料召回报告，历史READY证据保留。

## 2026-10-24 三源码窄发布及最终停用决定

`phase3-20261024-profile-native-r2`已安装，只替换Runtime main/profile_recall并新增profile_publication；包SHA256 `86ef48b34a8a8b779ddd76347b84d4dbb6b48297427c58420c1fc9d2738a21d1`。仅停止/启动同一AstrBot，NapCat未重启。新16库停写快照/隔离恢复通过，49相关源文件、8配置、固定上游及镜像核对PASS，QQ在线/OneBot1。34记忆/90timeline/48入站/13旧出站切换前后保持；之后用户新聊天继续正常记录。真实资料原题/关系/new及自然文本/长答/单图追问已确认。

回滚用同包 `scripts/phase3-deploy-runtime-code.py --rollback`，默认PLAN；如实施仍先备份新增数据并验证恢复，只恢复3源码，不还原数据库。journal=`/opt/qq-ai-companion/releases/phase3-20261024-profile-native-r2/source/astrbot-code-journal`。详见[窄发布说明](phase3-runtime-code-release.md)。

用户最终决定跳过72小时及全部剩余验收，主动保持OFF。此前按00:30:28被动确认计算的10-24 09:00至10-27 09:00(+08)只是计划，未写入生产、未启用、未迁移native proactive capability；已取消。不要通过自动化恢复该计划。今后开启需遵从当时有效的用户指令。


### 00:41 收尾只读状态

用户取消全部剩余测试后只读核对（未继续验收或发消息）：QQ isLogin/coreReady=true，OneBot已建立1，两个固定容器healthy、策略PASS，49源码/8配置完整；主动requested=false、窗口start/end均null、DISABLED，未初始化主动账本。主机实际RAM 1,352,683,520 / 3,904,782,336字节，CPU0%（0.2秒短采样），空闲盘36,886,646,784字节；AstrBot412,509,798字节/CPU0%，NapCat197,237,145字节/CPU0.38%。启动以来0 ERRO/Traceback、2 WARN；WARN来源为固定AstrBot `utils.config_number:55` 的数值配置下限回退，不写成“无警告”，未为此继续改配置或重启。证据`final-close-health-r1.json`、`final-warning-headers-r1.json`。

本轮顺序UAT记录10个Agent运行、2次原生摘要，按已记录tokens峰值估算0.253504元；一次授权私人诊断另0.025070元。Agent运行数不冒充HTTP次数；含用户在测试时追加的正常对话，非账户账单。保守共享预留6.00/原10元。完整成本与本人逐项反馈见`uat-cost-summary-r1.json`、`uat-sequential-owner-results-r1.json`。
