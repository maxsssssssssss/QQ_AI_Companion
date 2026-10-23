# Phase 3 最终请求组装探针

本工具用于排查“前置资料检索成功，但真实回答声称没有资料”。它只证明固定栈最终请求内容；不执行真实模型、不发送 QQ，也不能代替回答质量验收。

2026-10-24最终收尾：候选捕获、自动验证和源码发布后，原资料题、关系区分、新会话召回及后续自然聊天、技术长答、单图/追问均已有本人真实确认。**剩余验收依本人“跳过全部”结束，整体PARTIAL；P0 31/36限定PASS、5 WAIVED（未实测），Search 26/26限定PASS。** 私人模型重放、实际发布、真实QQ证据与本工具的零网络捕获严格分开，未测项目不改PASS；主动始终OFF，没有生效窗口或后续自动测试。

脚本：`scripts/phase3-probe-final-request.py`。依赖当前固定 AstrBot 4.29.1、插件与现有镜像依赖。没有修改上游源码。

## 输入与隔离要求

- 必须在 `--network none` 容器运行；脚本验证网络接口只有 `lo`。
- 使用一次性可写状态副本。每个 SQLite 用 backup API 制作一致性副本，核对复制前后 Guard 完成态和原生记忆 revision；不挂载可写生产数据。
- 需要 `data/cmd_config.json`、`data/config/`、相关 `data/plugin_data/`、主库、人格配置及 `data/phase3/persona-v0.1.md`。`data/plugins` 可只读挂载当前版本。排除 QQ 登录态、原日志、临时媒体及真实凭据。
- 副本根目录 `probe-snapshot.json` 必须包含 `{"purpose":"isolated-final-request","disposable":true}`。这只是明确调用者意图的门禁，不替代操作员核对挂载来源。
- 私有 `probe-input.json` 包含 `owner_id`、`platform_id`、`self_id`、`message_id`、`text`，均来自本轮已授权身份与待复现轮次。不能把正文放到 shell 参数或公共证据中。
- 可选 `trim_matching_last_pair:true` 仅在副本最后两项恰为匹配原问题的 user/assistant 且无 tool call 时，删除这两项以重放之前历史；其他结构拒绝处理。生产历史始终保留。

## 捕获路径

1. 副本配置支持 UTF-8 BOM；凭据改 dummy，QQ platform 配置清空，dashboard/metrics 关闭；保留当前 provider ID/model、Search 配置和工具契约。后台摘要与主动工作不启动。
2. 调用原生 `core.initialize()`，不调用 `core.start()`。固定版本会无条件创建 WebChat 本地队列；脚本验证只有该内置队列并立即停止全部 platform。不会创建 NapCat/OneBot 连接。
3. 调用现有 Runtime.prepare、原生人格安装和命令 ACL。仅将 runtime.ready 设为真以运行前台 hooks；不调用会启动摘要/主动 worker 的 activate。差异写入报告。
4. 构造原生私聊事件，经现有 Guard claim/group 与 Runtime 身份观察。仅副本入站 ID 添加唯一探针后缀，避免已完成原轮被去重；不会清空原账本。
5. 用原生 `InternalAgentSubStage.initialize()` 取得实际配置，经 `build_main_agent(apply_reset=False)`、全部 `OnLLMRequestEvent` hooks、原生图片准备、原生 reset、runner.step。
6. 在实际 Provider SDK 的 `httpx.MockTransport` 捕获最终序列化请求，立即终止。没有返回模型消息，因此没有执行工具或发送路径。预期只捕获一次主轮请求；若前置压缩等先调用模型，标为 `PRE_MAIN_MODEL_REQUEST`，不能冒充最终请求。

SDK 请求及原生运行日志可能包含私人资料，只写副本内权限 `0600` 文件。公共摘要仅记录 role、工具名、大小、SHA256、记忆 ID、版本和文本出现位置。

```sh
python /project/scripts/phase3-probe-final-request.py \
  --state /snapshot --source /AstrBot \
  --input /snapshot/probe-input.json \
  --report /snapshot/private/request-summary.json \
  --payload /snapshot/private/request-payload.json
```

`--payload` 可省略。提供时该文件留在服务器私有目录，不上传到公共仓库或聊天。工具不会自行复制文件至本地，也不会调用 DeepSeek。

## 诊断输出与边界

摘要包含：原历史条数及摘要、实际 Provider/model、hooks 后工具与 HTTP 实际工具列表；当前 `[PHASE3_PROFILE_EVIDENCE_V2]` 及其实际 L2 正文的消息/段/字符位置；原生 core memory 正文出现位置；原生“没有检索到足够相关的长期记忆；只依据当前用户消息回复。”出现位置和次数。正文出现位置允许核对相对顺序，但不能独自证明该提示导致错误答案。

采用直接主 Agent 组装而不是 EventBus 重放；不验证真实入站下载、排队、出站、UI 或模型自然度。当前实现为文本资料问题探针；不凭空替换图片、撤销或权限数据。不调用任何已有失败提示来伪造模型回答成功。

## 最初版本的合成自测（历史保留）

2026-10-23，固定依赖镜像 `qq-companion:phase3-test`，digest `sha256:6f8ba2bcd47e82d4c1956e79e3d021c7af322aef1309bb47d2cbea654cbd1934`；源码只读挂载，state 位于 Linux `/tmp`，network none。

- 纯契约 3/3 PASS：脱敏定位、JSON 转义匹配、BOM/dummy key 且 Search 配置保持。
- 固定 AstrBot + Private + Memory + Guard + Runtime：原生导入合成 4 L2 + 1 L1、L1 原位提升为 core、保留 16 条旧失败/工具历史。实际 SDK 18 messages，L2 正文位于 system[0]，L1 正文位于最后 user[17] 的临时段；捕获 1 请求，0 API、0 QQ、0 工具执行。
- Search 保留对照两轮：启用 Search 且问题明确“不要上网”时，现有 NO_WEB 策略产生 5 工具；相同主题问题去掉该禁网语句后产生 7 工具，包含 Search/Fetch。两轮均由原生 hook 决定，探针没有关闭 Search。初次对照误把禁网输入预期为 7 工具，检查失败后按现有策略核对并单独追加正确对照，不改原输入证据。
- 首轮探针错误把固定原生内置 WebChat 队列视为外部平台，主动拒绝。修复验证检查并立即终止内置队列后通过；不是生产故障或真实模型结果。

证据索引：`evidence/phase3/v2-final-request-probe-20261023/`。真实副本结果由部署主流程另行记录，不与合成结果混用。

## 较长合成请求的投影 A/B 夹具

`tests/phase3_request_shape_fixture.py` 完全自造游戏名称及事实，包含合成的常玩、玩过、最喜欢、未知最长时长等关系。没有读取或复制生产私有载荷。`scripts/phase3-build-shaped-model-fixture.py` 只接受早前标记为 synthetic_only 的固定栈合成 SDK 捕获，构造两份仅资料来源投影不同的 HTTP 请求。

输出位于 `evidence/phase3/v2-shaped-request-20261023/`：

- `before-verbose-refs.json` / `after-compact-refs.json`：18 条消息；16 条旧历史精确保留合法角色顺序，3 个工具响应分别为 4619/4710/1883 字符；游戏正文 450 字符，33 条 94 字符合成来源。当前用户段含 3 个合成 core、4 个无游戏答案的普通候选，记忆段 2921 字符。system 从 8634 变为 5493 字符。两份除了 V2 资料投影完全相同。
- `expected.json`：预期只来自合成资料，不能拿来判断真实用户游戏数量或偏好。
- `shape-summary.json`：明确这是原生合成捕获之后构造的模型夹具，不能冒充新的一次完整原生 T2 验证。
- `native-before-verbose-refs.json`：额外使用原生 ProfileRelease、core 提升、原生 store 与完整探针生命周期捕获；旧 renderer 来自早前合成测试状态，未读生产插件数据。实际 system 8626 字符、3 core、33 refs、3 个工具对均已核对。
- `native-after-compact-refs.json`：对上述原生捕获应用当前候选的纯投影函数，仍是离线 A/B 文件，不是另一轮实际 SDK 捕获。

原生补跑有明确差异：当前 user 的记忆段为 2287 字符，并实际含合成游戏正文；4 个无关候选没有入选。因此它不能替代“当前 user 没有游戏正文”的主要 A/B 夹具。`native-shape-summary.json` 保留该差异，不能为贴近目标而伪造原生检索结果。所有构造和捕获均未调用真实模型或发送 QQ；付费模型 A/B 由主流程在既有预算内另行记录。夹具结构与来源测试 3/3 PASS。

### 同内容不同作用域的合成对照

`scripts/phase3-relocate-shaped-evidence.py` 另外输出 `wrapped-system-control.json` 与 `unified-native-context.json`。后者将原样 1049 字符的完整 V2 资料块从 system 移至当前 user 的记忆子块；资料正文、覆盖范围、关系、人格、检索指导、主题目录、旧历史、工具、core、普通候选及 Private 均未改写。

重要差异：shape-only 夹具的记忆段本来是纯 JSON，没有原生标签。两份新对照同时补相同的 `<memory_companion_context>` 结构包装，只有 variant 改变 V2 资料块的位置。应比较这两份文件以隔离作用域变化；不能把原 after 文件到 variant 描述成只有移动、没有结构包装。`unified-diffchecks.json` 的 12 项检查通过，保留逐块 SHA 和完整字节一致性证据。这一步只是离线模型夹具变换；后续真实模型A/B和正式原生适配分开验证如下。

## 最新真实合成对照与原生发布验证

仅compact来源的真实Flash对照仍复现否认资料，不能将投影压缩称为完成修复。`four-capabilities-20261023/real-shaped-profile-native-scope-r1.json` 的两次作用域对照输入均为14169 tokens：system证据面仍否认，统一native user记忆面正确使用合成的5项常玩、2项玩过及关系/未知时间边界。两次无重试、估算0.057876元，没有使用私人资料或发送QQ。该结论限定于记录的样本，不能证明所有请求正确。

最终候选并非只搬字符串：`profile_publication.py` 通过原生 `_publish_reply_memory_context` 将完整L2证据放入唯一当前 `<MemoryCompanion-Context>` 的 `<phase3_approved_profile>` 子块，保留原L1、其他原生块及refs，system不再放V2事实块。发布前及统计await后重验ACL、撤销、精确修订、当前事件身份及连续性，取消或失效则移除本轮不可信输入；统计异常必须有新原生snapshot/ref/digest回执才可接受。未新增事实库或用户答案字符串。原生角色发布边界已验证，不将未知可选来源标为完整，也不声称单独覆盖Private角色注册全生命周期。

自动证据分开记录：23项原生publication/evidence、12项严格fixture、8项audit、4项probe契约，见 `four-capabilities-20261023/profile-native-publication-validation.json` 及同目录 `profile-http-fixture-native-context-r1/summary.json`、`native-publication-audit-r1/summary.json`、`native-probe-contract-r1/summary.json`。最初3项probe索引保留。最新完整固定栈 `20261023T160122Z-23369/result.json` 20/20 PASS，I18保留16条旧失败历史/4个工具对，只读取最后user唯一原生块，system证据标记0、1次主请求、1条Guard确认历史；包括媒体、记忆、主动和空正文恢复的相关回归。以上不是T4或本人评价，计数不叠加为新的全量总数。

## 最新生产副本捕获及后续获准私人重放

旧 `private-final-request-r3.json` 已证明完整452字L2存在于最终system输入、3条L1存在于最后user输入；33条source_refs共3168字排在L2正文前，见 `private-request-metadata-r1.json`。不能继续说尚未证明最终payload含正文，也不能将该副本重组当作原失败请求抓包。

新 `four-capabilities-20261023/private-native-final-request-r1.json` 同样只在隔离副本重组：保留16条旧历史，18条最终messages；完整452字L2及3条L1均在最后user的同一原生临时块，system无V2证据标记，没有原生空召回提示，8个refs和snapshot确认原生发布。SDK捕获1、真实API0、工具0、QQ0、生产修改0。副本删除匹配末尾问答不触及生产历史。

首次私人payload模型重放曾被自动审批拒绝，该次未执行，不是API失败且不记调用/预留。之后本人明确授权这一诊断用途，独立主流程才执行一次真实Flash重放，`private-native-model-r1.json` 记录1.927秒、stop、196字非否认实质回答，以及来源文本对应；没有工具执行、QQ发送或生产修改。探针本身仍未联网。完整payload及回复保留服务器私有目录，公共证据不含正文。

该次真实重放估算0.025070元，该私人诊断时点共享保守预留5.50元，仍为原10元预算而非账单。**这不是完整生产QQ链路；非否认和来源对应不能替代完整性/自然度的本人验收。** 该私人重放时点P08原FAIL仍保留。

## 后续源码发布（与探针隔离执行分开）

`phase3-20261024-profile-native-r2` 的三文件源码发布已完成；r1只在PLAN阶段失败，未安装。主流程在AstrBot停写后完成新备份及16库隔离恢复，只更新Runtime源码，不写数据库/配置；NapCat未重启、QQ在线、OneBot连接1。00:14:41 +08:00健康、启动策略及49份源码/配置/上游锁核验通过，已有34条记忆、90条timeline原字段、48条入站及13条legacy出站保持。源码journal支持窄回退，不恢复整套data或删除新聊天。

证据 `four-capabilities-20261023/runtime-code-{plan,install,health,preservation,readiness}-r2.json`。这项实际安装并非探针行为，也不把SDK捕获或模型诊断当作QQ通过。00:14历史时点为 `CANDIDATE_READY_FOR_UAT`，仅邀请此前失败的资料单题，主动仍OFF。

## 后续真实QQ结果（不是探针结果）

本人随后确认原资料题“通过”；`four-capabilities-20261023/uat-profile-native-pass-r1.json` 记录00:16两个相关新轮次均found完整452字并原生发布成功、snapshot有效，原final129/126字→清理128/125字，各一条一次尝试确认sent、历史hash一致，补答0、Search0。此前副本捕获和私人API重放本身不能提供这项T4证据；本次PASS来自本人确认与真实链路审计。

首题通过时转为 `UAT_IN_PROGRESS`，P08在获准L1/L2资料题范围内通过；当时只邀请关系区分，随后该题及新会话另有通过证据，见下节。统计2条Agent run的峰值未缓存估算0.050580元，另1次原生摘要估算0.016106元，非账单；不将Agent run数当确定HTTP请求数。旧失败和审批拒绝历史仍保留，主动仍OFF。

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
