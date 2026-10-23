# Phase 3 P0 验收报告

**Phase 3 Status: PARTIAL / APPROVAL_PENDING**。这是开发候选，不是生产上线报告。生产保持 Phase 2；真实访谈未导入，主动 QQ/后台搜索未开启。

截至2026-10-22：**P0 24/36 PASS，FAIL=0，BLOCKED=12，NOT_RUN=0**；PASS仅限逐行层级。**Search 24/26 PASS，BLOCKED=2**。未执行的真实QQ/生产子项在相应行保留，不能从组件PASS推导上线PASS。早期失败及修复证据不删除。

本表严格区分 T1 逻辑测试、T2 固定框架/插件及真实本机 HTTP/WS 对端（模型响应为合成）、T3 真实 DeepSeek（只含合成语料）、T4 真实 QQ、T5 数据恢复、T6 本人主观评价。PASS 只覆盖本行所写层级，不把模拟模型改称真实 DeepSeek。

## P01–P36

| 项目 | 状态 | 层级 | 证据与剩余边界 |
|---|---|---|---|
| P01 | PASS | T1 + 生产只读 | 工作区及锁版本核对；保留 Phase 2 基线，生产两容器与 QQ 在线，未覆盖历史。 |
| P02 | BLOCKED | T5 合成 PASS | 停写合成快照/精确恢复和旧表迁移已测；新的生产一致快照须先批准维护窗口。 |
| P03 | BLOCKED | T1/T2 + 生产只读 | 白名单/群拒绝、端口及 Secret 保持；新版本真实 QQ 普通/图片回归尚未部署执行。 |
| P04 | PASS | 原生 T2/SQLite | 同一 PersonaManager ID，重开、policy 重跑及关闭搜索均保留唯一 V0.1；失败启动清理已测。 |
| P05 | BLOCKED | T3 已执行 / T6 待本人 | 20 场景旧版/新版40次，修复后新版20次；接口成功与内容问题分别记录，主观自然度待评分。 |
| P06 | PASS | T3 有限样本 | 初轮无来源情绪已修；修复样本未确认虚构职业、共同旅行、已读网页或持久改写。非任意输入保证。 |
| P07 | PASS | 本地离线预览 | L1 50/L2 264/L3 38/排除103个行级待审单元，附原文来源/哈希/范围，非确认事实。 |
| P08 | PASS | 未批准不导入分支 | 真实资料0生产写入/0模型外发；生产导入硬拒绝，状态PENDING_APPROVAL。 |
| P09 | PASS | T2 完整 AstrBot | 三条真实本地WS输入→一条合并模型请求/回复；原文三条各自保留，重复事件不重跑。 |
| P10 | PASS | T1 + 原生 T2 | 补充归属、已解决/别搜了、supersede/cancel在受控发送/工具边界重检；已发网络不能撤回。 |
| P11 | BLOCKED | 原生 T2 PASS | 真实原生parser和HTTP对端验证2/4语义气泡、逐条ledger及常规单条；真实QQ语义自然度待测。 |
| P12 | PASS | T1/原生及完整 T2 | 普通/气泡/progress/final/proactive 独立状态已测；完整 Private 原生 tick 经 Guard 发1条并回填事件，未将 prompted 记成用户已回应。 |
| P13 | PASS | T1/T2/T5 | 重复输入、失败、超时unknown、崩溃无盲重发；旧Guard表保留、预迁移副本和完整合成恢复。 |
| P14 | BLOCKED | T1/T2 PASS | 原文档案不随摘要清理，受控出站有状态；新版真实QQ逐条回执关联待部署。 |
| P15 | PASS | 完整 T2 + T3 有限样本 | 原生自动摘要实际提取稳定事实、事件、偏好、未完事项；真实 DeepSeek 首轮扩写失败已保留，修后四类单样本通过，非长期质量验收。 |
| P16 | PASS | 原生 T2 + T3 有限样本 | 原文引用、scope/时区、自述来源经实际原生存储核对；真实修后样本无未支持的 Bot 行为，不能外推所有输出。 |
| P17 | PASS | 完整 T2 + T3 有限样本 | 完整进程重启/新上下文通过原生证据正文召回，已排除把查询回显当证据；真实住所更正后新服务也召回新状态。 |
| P18 | PASS | 原生/完整 T2 + T3 单例 | 原生assertion修订保留superseded历史，旧摘要退出活动召回；真实住所更正重开后仍正确。直接修订conversation_summary被上游拒绝，不能用于该路径。 |
| P19 | PASS | 原生 + 完整 T2 | Agent 精确来源/版本撤回用户及Bot副本，墓碑防重抽；整条来源影响须明确确认。撤回/全局暂停实际开启新上下文，下一请求及重启不带旧内容；原档案保留。 |
| P20 | PASS | T1/原生 + 完整 T2 | 同事件scheduled→awaiting_result→completed已通过真实Agent工具协议；其他终态/过期组件已测。 |
| P21 | PASS | T1/T2/T3 有限样本 | 共同经历只取允许的已确认聊天，用户外部往事不自动转共同经历；共同旅行否认样本通过。 |
| P22 | PASS | 原生/完整 T2 | 持久档案游标、异步worker、原生小时预算、失败诊断和暂停；新增档案与旧原生未摘要历史分别核验，旧22条未授权不回灌。 |
| P23 | PASS | T3 10例最终轮 | 真实同一模型自主选择，无额外分类；3个隐含时效、明确请求、闲聊/私密/不联网选择最终10/10。 |
| P24 | BLOCKED | 原生 T2 PASS | progress确认→实际本机Search/Fetch HTTP→原协议final已测；真实外部服务/QQ进度尚未执行。 |
| P25 | PASS | T1/原生 T2 | 每轮一次进度、跨轮隔离、故障/空回复有界回退；实际外部服务表达质量保留待验。 |
| P26 | PASS | 真实请求边界 T2 | 禁止联网、非授权、私人查询、恶意URL被挡；本地外部工具HTTP计数0，不只检查prompt。 |
| P27 | BLOCKED | S01–S26 独立表 | 逐项专项已记录；真实Search/Fetch与QQ/主观自然度必需项未完成，不宣布Natural Search PASS。 |
| P28 | PASS | 原生组件 + 完整 T2 | 完整加载的 Private 实例从原生候选触发真实框架生成，Mock HTTP 1次、Guard发送1条；采用合成中午环境，不是假装真实QQ主动。 |
| P29 | BLOCKED | T4 首次主动未授权 | 未发真实主动QQ；需明确短窗口、日限、冷却及原生目标capability迁移批准。 |
| P30 | PASS | T1/原生 T2 | 暂停/忙碌/未答/拒绝话题/终态/预算在生成前及发送前检查；未知失败消耗预算，不重试轰炸。 |
| P31 | PASS | T1/T3 有限样本 | 公开兴趣可作为意见表达，不声称读过未读网页；无来源discovery被门禁拒绝，后台探索未开。 |
| P32 | BLOCKED | 组件与部分联合 T2 | 各模块分别真实接线并有并发边界；全部能力同会话真实QQ联合尚未部署。 |
| P33 | BLOCKED | T5/原生策略回滚 PASS | 合成停写恢复、功能关闭、旧人格恢复已测；新的生产镜像/数据切换与回滚需批准。 |
| P34 | BLOCKED | T4 联合未执行 | 真实QQ+真实DeepSeek人格/合并/纠错/搜索/主动全套样本尚缺。 |
| P35 | BLOCKED | T1/T2 + T3 计量 | 84次真实DeepSeek成功：输入67955/输出7777，估算峰值上界US$0.0297189；2次SSH未取得HTTP另计，新版生产4GB资源观察待上线。 |
| P36 | PASS | 本地交付/审查 | 36/26逐项矩阵、实现、测试、隐私预览、已知问题及发布/回滚已更新；第一方审阅包与精确清单单独生成，无生产变更/推送。 |

## 最终测试证据

| 套件 | 最终结果 | 机器证据 |
|---|---:|---|
| Phase 1 既有单测 | 38 PASS | `evidence/phase3/phase1-unit-regression.json` |
| Phase 2 既有回归 | 139 PASS / 4.153 s | `evidence/phase3/phase2-regression-final.json`；原日志 `/tmp/qq-phase3-phase2-final-regression.log` |
| Phase 3 组件合计 | **172 PASS，0失败/错误/跳过** | `evidence/phase3/components-release-summary.json` |
| 完整固定 AstrBot 隔离联调 | **14 PASS** | `evidence/phase3/20261021T162007Z-19206/result.json` |
| 原生 Private 完整独立探针 | PASS；生成HTTP=1、sent=1 | `evidence/phase3/proactive-20261021T160337Z/proactive-native.json`；完整联调I11另重复验证 |

172项由 runtime16、staging5、delivery19、turns10、原生delivery9、原生memory36、原生proactive12、search30、原生search11、原生policy15、发布审计5、摘要provider4组成。最初统一运行150项中2个模块失败，修复后仅重跑受影响套件，并增加边界用例；汇总保留各轮路径/哈希，不把初轮文件改成成功。

完整14项在 `network none` 下运行真实固定框架、插件、Agent、SQLite和HTTP/OneBot WS协议；外部模型/搜索/QQ对端为合成。测试专用headless启动器调用同一 runtime prepare/activate，**生产Phase2Policy端点校验由独立15项真实SQLite策略套件验证**，未为loopback放宽生产校验。这些证据不等价于实际生产入口、真实QQ或真实搜索服务全部验收。

复现：`scripts/test-phase3-components.py --output <新的证据目录>` 使用锁定的 `.runtime/phase3-venv/bin/python`；`bash scripts/test-phase3-isolated.sh` 使用已核验的 `qq-companion:phase3-test`。构建和原生依赖准备见 [实施说明](phase3-implementation.md)。发布审阅包为 `evidence/phase3/phase3-review-20261022.tar.gz`，清单与SHA-256另存 `evidence/phase3/release-review.json`；只供审阅，不能替代生产镜像。

## Gate 与真实执行范围

| Gate | 状态 | 当前边界 |
|---|---|---|
| 0 基线/备份 | BLOCKED（本地机制 PASS） | Phase 1/2 回归与合成迁移/恢复已测；生产新快照、停写及恢复待维护窗口批准 |
| 1 人格/资料 | BLOCKED（代码及模型样本已测） | 单一Persona及20组真实前后样本已完成；本人自然度评分待做；资料仅预览 |
| 2 交互/发送 | BLOCKED（T1/T2 PASS） | 合并、取消、语义气泡、去重、逐条状态已测；新版QQ展示/回执未测试 |
| 3 自动记忆 | BLOCKED（T1/T2及有限T3 PASS） | 来源、纠错、召回、撤回、重开已测；旧22条历史需授权，真实长期质量未验收 |
| 4 自然搜索 | BLOCKED | S01–S26 为24限定PASS/2BLOCKED；真实Search/Fetch、QQ及自然度待验 |
| 5 主动陪伴 | BLOCKED（T2 PASS） | 原生候选至发送接线已测；首次主动QQ的窗口、次数和目标迁移未批准 |
| 6 联合验收 | BLOCKED | Docker隔离联合已测；新版真实QQ＋DeepSeek、生产回滚及资源观察待授权 |

Gate 是生产放行状态，不把本地测试通过改写为 Gate 全部完成。`User Profile Import: PENDING_APPROVAL`，`BACKGROUND_WEB_EXPLORATION: NOT_ENABLED`。

## 真实模型计量与内容检查

- 人格：旧/新各20组，修订后新版再20组，共60个成功请求；真实样本曾暴露虚构历史情绪和技术长答截断，修复后复测，主观语言体验仍留给本人。
- 搜索决策：两轮各10例，首轮7/10选择正确、6/10参数允许；修复后10/10，两轮实际Search/Fetch均0。不能把工具选择当真实网页检索。
- 原生记忆：4个成功请求，累计输入12312/输出2912；另2次SSH提前失败未取得HTTP。首次摘要扩写FAIL，来源约束后单样本PASS，发布路径单值住所纠错/重开12检查PASS。混合旧摘要被整体归档后，职业/偏好断言仍可召回，其他未完事项仅核对原档案保留，未证明所有旁支信息召回不变。
- 全部84个成功请求都使用合成资料；真实访谈外发0，真实Phase3 QQ发送0。普通主模型、搜索决策、记忆调用分别计数；没有真实progress、主动正文或Search/Fetch费用样本。

按2026-10-21核对的[官方模型价格](https://api-docs.deepseek.com/quick_start/pricing/)和全部输入按峰值未缓存估算，合计上界约US$0.029719，**不是账户账单**。本机私有输出及哈希/usage摘要分别保存，见 `evidence/phase3/real-model-summary.json`。[真实记忆报告](phase3-memory-real-validation.md)保留失败和修后样本，不以HTTP200替代内容正确。

## 隐私、运行与已知限制

生产两容器在只读检查时均healthy，QQ已登录，重启计数0、无OOM；UI仍仅127.0.0.1:6185/6099。这里只证明原Phase2运行状态。报告中的容器memory_bytes/nano_cpus是**配置限额，不是实测占用**。隔离测试使用3GB/2CPU上限、network none、只读根；新版4GB VPS实测资源、长期稳定和多日自然度尚无数据。

发布仅包含第一方薄层、模板、脚本和测试；不含受限上游源码、数据库、真实对话、访谈、登录态、Key或私有配置。原上游版本/内容锁保持，旧Phase1/2提交未改写，没有推送GitHub。源资料及人格输出仅在仓库外owner-only目录供本人审阅。

其他边界：原生OneBot发送API丢弃message_id时保留null，不伪造平台回执ID；unknown不重发。特定记忆撤回以整条原生来源为单位；L3外部导入因原生API未执行过期字段而拒绝，仅保留预览。后台自主搜索关闭。Future Tasks一次任务失败后删除及可靠提醒/Todo仍交Phase4，不被本轮主动关心替代。

待批准的具体范围与回滚步骤见 [Phase 3 部署](phase3-deployment.md)；私人资料用途见 [资料预览](private-profile-import.md)。
