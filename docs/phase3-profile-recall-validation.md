# Phase 3 已导入资料召回修复

2026-10-24最终收尾。**P08在本次获准L1/L2资料题、关系区分和新会话召回范围内T4 PASS；本人已确认正确、完整且未乱猜。** 本人随后要求“跳过全部”剩余验收，本轮已收尾、整体PARTIAL，原矩阵31/36限定PASS、5 WAIVED（未实测），Search 26/26限定PASS。后续其他主题等不外推PASS、不再邀请测试；已通过的自然聊天、技术长答、图片和追问见文末。主动始终OFF，无生效窗口。旧无回复、资料不全、否认资料及修复失败证据保留。

## 实际缺口

第二轮 2026-10-22 23:53:23 +08:00 的回复已由 Guard 确认发送，未触发补答，也没有调用记忆工具。原生注入日志选中四条，包含其他主题和最近测试内容，未包含游戏资料；模型随后称没有游戏记录。已批准资料在原生数据库中，问题出在本轮选取及补查。

在生产数据库的在线一致副本、固定镜像及无网络隔离容器中比较：原句包含“只根据记忆、不要上网”等要求时，原生 slot 注入 top_k=4/6/8 都漏掉目标；简短主题查询能命中。此前 `service.search` 的命中不等于实际 `search_context_slots` 注入成功，也不等于模型答对。单纯调大注入量未作为修复。

## 最小改动

新增第一方 Runtime `recall.py` 和默认关闭的 `memory_recall_guidance`。沿用固定 MemoryCompanion 2.3.1 的 `memory_companion_navigate`/`memory_companion_sources`，不改上游、不新建检索引擎或资料库。

在原生记忆和 Private 提示处理后、Search 最终筛选前，补充资料工具指引：证据不足时按简短主题导航；原始聊天检索不等于已导入资料检索；上一轮“没找到”不是本轮事实；充分命中立即回答，不让用户重填已交资料。

有限真实模型初测中，游戏正确，但“口味”未命中题为“饮食”的合成资料。因此增加一个**部分主题目录**：仅使用原生可见性/当前修订/召回权限允许的第一方已确认 L1/L2 导入条目，提取固定格式的主题标题，不包含事实正文。原生候选最多64条、目录最多16项、标题30字以内、总等待1秒；失败或超时降级到普通工具指引。它不是完整资料清单，原生基础词法检索的同义词和问法覆盖仍有限。

普通聊天不额外调用分类或检索模型。需要补查时，仍在同一个主 Agent 中调用既有本地工具，共用原生导航预算。私聊 ACL、记忆暂停/撤销、Search 私人资料隔离保持。主动工具集仍为空，未扩大主动权限；未开启主动试验窗口。

## 分层证据

| 层级 | 结果 | 证据范围 |
|---|---|---|
| T2 原生组件 | 6 PASS | 实际导入后导航、无伪造聊天原文、撤销缓存、跨Bot、暂停、开关/工具可用性、仅主题目录 |
| T1/T2 Runtime回归 | 22 PASS | 人格、策略、权限、开关和条件主动窗口 |
| T2 完整 AstrBot | 17 PASS | 新增I17：实际导入→模型协议工具请求→原生证据→原Agent最终回复→Guard；最终请求保留指引和目录；既有气泡/摘要/撤回/重启/主动模拟回归 |
| T3 真实 Flash | r3：3个合成场景、5次HTTP响应 PASS | 游戏与饮食都由模型选择原生导航，答案覆盖已导入合成事实；问候不调用工具。未使用真实访谈，非完整生产Agent验收，非自然度评价 |
| T4 真实 QQ | 原资料题、关系区分及新会话召回均由本人确认PASS | 其他主题未外推，剩余项目由本人豁免未实测；旧空回复及“无资料”证据保留 |
| T5 原始资料保护 | PASS | 记忆恢复前77条timeline全部仍在，content/来源ID/时间/类型等原字段不变；现有84条，不清空生产会话 |
| T6 本人评价 | 原资料题正确完整性及后续自然聊天体验已由本人确认 | 其余评价豁免未实测，不由LLM自评决定，不外推所有语境 |

私有证据在 `evidence/phase3/profile-recall-20261023/`。`native-r3.json`为6项；`runtime-regression.json`为22项；完整框架为 `20261022T161033Z-14733/result.json`；`full-context-check.json`核验最终HTTP请求。生产副本目录核对得到10个可见主题，包含游戏主题；缺少规范标题或未进入候选的条目不伪称覆盖。

失败保留：`native-r1`受限环境运行阻塞后终止，正常审批后的r2/r3通过；第一次完整框架新增夹具因Windows挂载不能执行POSIX私有目录权限而未完成，改用容器tmpfs后通过，未削弱生产权限。T3 r1为游戏正确、饮食失败；r2在SSH传输处中断，无模型响应可核对；r3通过。r1与r3共11个真实HTTP响应，按已记录tokens估算0.063428元，r2费用未知仍保留预算占位。累计保守预留3.10元，非账户账单。

## 发布与回退

包SHA256：`c7320ec8f47dade1f3e690cf67fdf5da6aa752bc5d0f94ae16fb791f59826cc7`。新目录 `phase3-20261023-recall-r1`。部署入口 `scripts/phase3-deploy-recall.py` 默认计划，核对既有Runtime文件基线与包清单；实际切换前停原AstrBot/NapCat，创建新一致备份并隔离恢复，通过后只安装4个Runtime文件、单一开关及原样cmd_config。具体执行和健康结果以总验收报告为准。

可单独将 `memory_recall_guidance` 置false并正常重启AstrBot；也可按本次installation-journal恢复旧Runtime文件。无需回滚数据库，保留原聊天、导入来源与切换后新数据。不得以资料复测失败为由自动清空历史或重导入访谈。

实际发布已完成6文件窄安装及16库恢复检查，原容器恢复。首次健康检查策略为BLOCKED，原生配置添加BOM触发现有读取器缺陷；没有将healthy写成策略PASS。先备份配置、仅移除3字节BOM且确认JSON语义不变，00:20:54 +08:00策略恢复PASS、QQ在线。读取器永久兼容修复另有16项通过，其中实际AstrBotConfig写出BOM后启动成功、BOM中非法enabled仍拒绝。永久修复发布与最终健康见总报告。

永久BOM修复已发布：`phase3-policy-bom-preflight-20261023-r1`停写快照及16库隔离恢复PASS，只安装策略文件、同值BOM配置和原样cmd_config三文件。00:24:12 +08:00两个原容器healthy、QQ在线；新策略/Runtime哈希一致，原生BOM配置保留且本次启动readiness为PASS。该次历史有效组件汇总286项/21模块，原17项完整联调和16项策略回归分层记录。该版本随后真实QQ复测仍失败；未自动发测试消息或开启主动窗口。

## V2 r2 增量已上线后的调查历史

2026-10-23候选一次完成有界原生L2正文读取、关系/时间范围和partial语义、权限/撤销复核；3L1同ID进入native core，完整原生注入已验证。未新增画像主库、未重导入15组、未导入L3、未清空会话。34条记忆总数及88条timeline保持，12L2逐行不变。

382组件/20完整固定框架及资料7例真实Flash合成场景的历史通过结果保留；原I18断言强度不足及新增检查见下节。QQ本人认证已完成，但23:07资料问答再次失败，当前不要求重复提问、重填访谈或/new。完整根因、发布证据和回滚入口见[四项体验报告](phase3-four-capabilities-validation.md)。

### 23:05 +08:00 登录恢复与历史READY状态

本人扫码后已核验当前在线备用QQ身份，原生自动登录设置重新保存并验证落盘；没有额外重启或再次登录请求。23:05:16实际isLogin/coreReady=true、loginPhase=ready，内部OneBot ESTABLISHED=1，策略PASS、47源文件摘要匹配。原先waiting_qrcode及一次快捷登录未恢复的证据保留为本次发布历史。

23:05该历史时点为 **CANDIDATE_READY_FOR_UAT**，仅表示候选自动验证及发布前置条件满足，未代表P08真实通过；23:07本人反馈失败后，当时状态转为 **UAT_FAILED_INVESTIGATING**，总体PARTIAL、主动OFF。历史上仅邀请了此前失败的资料问题，未发整组作业；当时暂停下一项和无工程改善的重复测试。证据 `evidence/phase3/four-capabilities-20261023/candidate-readiness-r2.json`、`production-health-after-login-r2.json`、`napcat-save-current-session-r2.json`。

### V2 r2 首项真实验收失败（23:07 +08:00）

本人反馈“失败”，P08继续FAIL，候选状态转为 `UAT_FAILED_INVESTIGATING`，暂停邀请下一项或无修复的重复问题。实际同轮本地预取found、452字完整主题、16ms；模型44字stop/无工具，清理后43字，Guard一次确认发送并投影历史；没有空正文补答。因此不归因为空正文、网络或发送失败。最终回复仍表示未找到资料。随后在同生产状态的隔离副本重组原生Agent请求，已捕获最终Provider边界并确认完整452字L2与3条L1正文仍在；这不是原失败请求的实时抓包，不能据此把原轮语义失败认定已修复。最终边界、长历史与合成对照证据见下节。

本轮发现原T2 I18从完整messages取合成游戏名，旧工具历史也含相同名字，证据强度不足；现已补为只解析当轮资料block，并新增6项fixture测试通过。旧20/20执行结果保留，但不再把原断言当完整生产场景的充分证明；新fixture检查也不代表真实QQ答对。此前T3短上下文不替代生产长历史。证据 `uat-profile-r2-failed-audit.json`、`uat-profile-r2-failed-shapes.json`、`uat-profile-r2-owner-fail.json`。没有清空生产会话、再次导入资料或扩大公开搜索。


### 最终请求边界诊断（当时 UAT_FAILED_INVESTIGATING，历史保留）

`four-capabilities-20261023/private-final-request-r3.json` 在同生产配置/数据的隔离副本中，经原生主Agent组装和Provider发送边界捕获18条messages：452字L2正文完整保留，3条L1原生核心正文全部匹配，没有原生“未检索到足够相关记忆”的空召回提示；原生记忆工具仍在请求中。副本保留原有16条历史，只在副本移除失败轮的末尾问答后重组；network=none、付费调用0、QQ发送0、生产修改0。它是生产副本重组的最终请求证据，**不是原23:07失败请求的实时抓包，也不是模型正确使用资料的证明**。

`private-request-metadata-r1.json` 显示证据JSON中33条source_refs共3168字，排列在452字正文之前；实际配置为deepseek-flash、max_tokens=2048、thinking disabled、tool_choice auto。该位置最初列为假设；后续真实合成对照中**仅压缩来源投影仍会否认已有资料**，所以compact不能单独视为根因修复。完整私有正文和请求不进入公共报告。

此前自动诊断新增6项HTTP fixture测试、3项probe逻辑测试及3次固定原生离线最终请求捕获通过，分别见 `four-capabilities-20261023/profile-http-fixture-r1/summary.json` 与 `v2-final-request-probe-20261023/summary.json`。三次离线捕获均保留完整L1/L2；是否包含公开搜索工具遵守“不上网”要求，5/7个工具的差异不是记忆权限失败。真实Flash的完整**合成**上下文基线一次返回正确，含16条旧历史，估算0.014092元，见 `four-capabilities-20261023/real-full-context-baseline-r1.json`；该历史时点共享保守预留4.30元，非账户账单。

首次将私人完整payload再次发往DeepSeek的诊断重放被自动审批拒绝，该次未执行；这不是API失败，不计模型调用或预算预留。后来本人明确授权该诊断用途，主流程才完成下述一次私人重放；不抹去先前拒绝历史。未重导入资料、清空会话或启用主动。该诊断时点P08仍FAIL，P01–P36为30个限定层级PASS、1 FAIL、5 BLOCKED；Search仍26/26限定层级PASS。

## 2026-10-24 统一原生上下文候选的发布前验证

同内容真实Flash合成A/B两次输入均为14169 tokens：独立system证据面仍否认有资料；完整证据进入当前user原生上下文后，正确列举合成的5项常玩、2项玩过，保留最喜欢关系并说明未知时长/近期。两次无重试、估算0.057876元，见 `four-capabilities-20261023/real-shaped-profile-native-scope-r1.json`。该结果支持本失败模式中的提示面冲突诊断，不代表真实用户资料或所有问法已经通过。

`profile_publication.py` 复用原生 `_publish_reply_memory_context`，将L2正文证据作为 `<phase3_approved_profile>` 子块写入当前唯一 `<MemoryCompanion-Context>`，而非再建独立system事实面。原生L1和其他引用保留；主库、ID、12组L2与审批关系不变。原生身份/连续性、ACL、撤销与精确revision在发布及统计await后重验；本轮取消或失效时不保留过时输入。来源正文仍由原生库持有，投影压缩只是减少无关元数据，不能替代引用或权限。没有新事实库、硬编码游戏答案或额外分类模型。

原生publication 11 + evidence 12共23 PASS、严格HTTP fixture 12 PASS、audit 8 PASS、probe逻辑4 PASS；probe新证据为 `four-capabilities-20261023/native-probe-contract-r1/summary.json`，旧3项索引保留。最新完整固定栈 `20261023T160122Z-23369/result.json` 20/20 PASS：增强I18只读最后user的唯一原生块，system标记0，保留16条旧失败历史和4个工具对，1次主请求、1次Guard确认及历史投影。回归还涵盖原生记忆撤销/纠错、主动、两图和含图历史补答。均为T1/T2，不是T4；详细分组与边界见[四项体验报告](phase3-four-capabilities-validation.md)。

`private-native-final-request-r1.json` 的生产副本离线重组保留18条messages，452字L2及3条L1全文同在最后user原生块；无system证据标记、无空召回提示，原生8 refs/snapshot回执有效。它不是原23:07请求实时抓包；该捕获模型0、QQ0、生产修改0。

随后获准的一次真实私人重放见 `private-native-model-r1.json`：`deepseek-flash` 1.927秒返回196字实质回答、stop，没有此前否认资料的短语，文本与来源有对应；没有执行工具、发送QQ或修改生产。估算0.025070元，该诊断时点共享保守预留 **5.50/原10元**，非账单。完整payload及回复仅留服务器私有目录。**这关闭了该次重放的“仍否认资料”现象，不等于答案完整正确或真实QQ通过；该诊断时点完整性及自然度仍待本人评价、P08保持FAIL。**

## 00:14发布：已安装，等待单题真实验收的历史时点

00:14:41 +08:00，`phase3-20261024-profile-native-r2` 已安装并进入 `CANDIDATE_READY_FOR_UAT`；同名r1只在PLAN阶段失败，未安装。本次只更新Runtime三份源码，不写数据库/配置；AstrBot停写备份和16库隔离恢复PASS，NapCat未重启、QQ保持在线，OneBot连接1。49份第一方源码、配置和上游锁核验通过，34条记忆、90条timeline原字段、48条入站与13条legacy出站保持，无重导入或全data覆盖。

证据 `four-capabilities-20261023/runtime-code-{plan,install,health,preservation,readiness}-r2.json`。回退仅使用本次源码journal，不回滚聊天数据。当时P08原FAIL未因安装或私人模型回答而关闭；仅邀请原问题，不要求新会话或重填资料。主动仍OFF，详细资源与回退入口见[四项体验报告](phase3-four-capabilities-validation.md)。

## 原资料题通过后续接关系与新会话的历史

本人明确反馈“通过！！！！！！！”后，`four-capabilities-20261023/uat-profile-native-pass-r1.json` 核对00:16两个相关真实轮次：均found完整452字、native_published及snapshot有效，模型129/126字→清理128/125字，各单条一次尝试确认sent，历史hash与发送一致，补答0、Search0。远端ID未返回不影响已有固定适配器回执，未伪造ID。P08在这项获准资料题范围内T4 PASS，历史FAIL保留；原矩阵31/36限定PASS、5 BLOCKED，当时为 `UAT_IN_PROGRESS`。

首题后先邀请“玩得最久”的关系区分题；该题及新会话随后单独通过，见下节，跨主题完整性及自然度仍不能由首题替代。统计记2个Agent run而非确定HTTP数，峰值未缓存估算0.050580元；另1次成功原生摘要估算0.016106元，均非账单，沿用原预算。没有重填资料、清空历史、额外搜索或开启主动。

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
