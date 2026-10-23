# Phase 3E 自然搜索专项验收

日期：2026-10-21 至 2026-10-23。**受限自然搜索验收：26/26 限定层级 PASS；通用自主搜索仍未完成。** 已完成真实 QQ 的官方价格查询：先确认发送自然回应，再执行 Search 和 Fetch，结果进入原 Agent 上下文后发送最终答案，前后两条独立归档。用户反馈“可以”，另提出去除 Markdown 装饰的改进；该有界纯文本过滤已上线，具体新格式仍待 QQ 观察。审核主题仍仅 DeepSeek、AstrBot、炉石传说，不代表任意公开搜索或整个 Phase 3 通过。

## 固定上游与实现范围

AstrBot 保持 `4.29.1` / `dffe922773725e9c178392c423676ad65044748c`。使用既有 `ToolLoopAgentRunner`、`FunctionToolExecutor`、`DeepSeekWebSearchTool` 和 `DeepSeekFetchWebPageTool`；未修改上游源码、未创建第二个 Agent、搜索引擎或记忆内容库。

第一方 `phase3_search` 在每次 `on_llm_request` 中复制本轮 ToolSet、包装原生 Search/Fetch 的 `call`。安全检查位于真实调用边界；**不依赖抛出 hook 异常**阻断工具：固定上游会记录某些 hook 错误后继续。外部工具仅允许 Search/Fetch；记忆模块另按显式 capability 开放内部事件工具，文件、桌面、cron 和后台搜索继续关闭。

调用顺序：精确私聊白名单及当前轮次 → 公开查询/URL 验证 → 持久预算和 progress 独占 → 一次受限短句生成 → Guard 发送回执 → 原生工具调用 → 原 tool_call_id 返回 → 原生 Agent 最终回复 → Guard 发送与归档。Search 后 Fetch 共用一次 progress。工具说明中间文本由 `on_decorating_result` 清除，最终响应由 `on_llm_response` 标明后保留；不终止 Agent。

过渡短句复用当前聊天 Provider 的公开 OpenAI SDK client。固定 AstrBot 的 `text_chat(**kwargs)` 不把 `max_tokens` 传给 HTTP，因此薄适配直接调用既有 client、显式 `max_tokens=96`、`thinking=disabled`、无工具、无历史、无私人画像、最多 4 秒且 SDK 重试为 0。输入仅为同一核心人格和经审核的公开动作。超时、空文本、异常、越界内容退回一句中性行动提示。普通闲聊不增加分类或短句模型请求。

## 验证方法与实测

| 测试文件 | 结果 | 实际执行内容 / 边界 |
|---|---:|---|
| `tests/test_phase3_search.py` | 34 PASS（新增预算继承/工具时序审计） | T1：权限、公开词、DNS/URL、预算、并发、超时、取消、重复请求、故障、日志与回滚契约；全部合成 |
| `tests/test_phase3_search_native.py` | 12 PASS（含一次进度后最终两气泡） | T2 原生循环隔离集成：真实固定 Agent/Provider/工具/Guard/OneBot adapter，通过真实 loopback HTTP 接收合成模型、搜索、网页、平台回执；额外使用真实原生 SQLite/ConversationManager 重开后新 runner 继续；实际请求hook注入共享契约且重复触发不双重包装 |
| `tests/test_phase3_search_policy_integration.py` | 16 PASS / 76.751 s（新增原生BOM配置回归） | 原生 SQLite/PersonaManager、真实 Phase2Policy 启动及 Phase3Runtime prepare/activate/expose；持久化重开、关闭搜索/完整回滚、错误 endpoint/Key/范围 fail closed；Guard 或固定配置请求 Phase3 时，缺失/失效 runtime 不得降级；persona 持久化后才能 activate，异常撤销。命令 ACL 与必需插件发现使用 fixture 元数据，不能充当完整插件装载验收 |
| `tests/test_phase3_search_release_audit.py` | 5 PASS / 0.413 s | T1 发布审查：旧嵌套/扁平配置开关、磁盘主动发送始终冷启动关闭、搜索关闭不复用Key、HTML注入/CSP、私有预览不可入仓库/覆盖、发布列表不含私有状态 |
| `tests/test_phase3_search_real_probe.py` | 3 PASS / 1.211 s | 独立真实探针的无网回归：每日尝试跨重开保持 60 上限、10 元验收预算在保守预留接近 9 元时停、PublicPolicy 生成的原生兼容 payload 无 ori_question/全文 |
| `scripts/phase3-test-search-real.py` | 2 流程 PASS · T3 独立协议 | 真实 Search 2、Fetch 2、模型 4，全部 HTTP 200；既有同模型生成 progress，实际工具结果回填给真实模型。兼容固定原生请求格式，**未执行生产 Agent/Guard/QQ** |

T2 Search 测试没有替换上游工具方法或 Agent loop。测试专用 HTTP transport **只在测试进程**把两个固定官方工具地址映射至本机假服务器；生产 endpoint 校验仍严格拒绝 loopback。模型响应是确定性 fixture，所以可以证明真实协议和调用参数，不能证明真实模型能自主正确选工具。测试不绕过任何执行环境审批；本机 HTTP 和 SQLite 线程事件唤醒所需的执行权限经过正常审批。

代表性计数：普通闲聊为 1 次主模型、0 次 progress、0 次 Search/Fetch；Search→Fetch 为 3 次主循环模型、1 次 progress、1 次 Search、1 次 Fetch。未授权账号的主模型、progress、Search、Fetch 均为 0；私人查询、不联网指令和元数据 URL 的 progress/Search/Fetch 均为 0。后者可能已有主模型用于提出及消化被拒绝工具调用，不能把主模型数也报成 0。

实测 HTTP接收与 Guard 回执采用单调时钟，断言 progress 确认时间 ≤ Search 接收 ≤ Fetch 接收 ≤ final 确认。此处 `sent` 表示合成 OneBot API 的成功回执；**不表示真实 QQ 已展示或用户已读**。固定原生 `AiocqhttpMessageEvent.send` 未返回平台 message_id，故 ledger 正确保留 null，不用入站 ID 或本地序号伪造出站 ID。

本地脱敏机器证据可生成到被 Git 忽略的 `evidence/phase3/search-native.json`，包含用例、请求计数、发送状态、合成 usage；不包含消息、查询、账户 Key。合成 token 计数仅检验计量逻辑，不能当作真实费用。

复现（先按项目锁文件取得上游和依赖；缺失原生运行时会 SKIP，SKIP 不能算 PASS）：

```bash
python3 -m unittest discover -s tests -p 'test_phase3_search.py' -q
ASTRBOT_ROOT=/tmp/qq-phase3-search-native-tests \
  QQ_PHASE3_SEARCH_EVIDENCE="$PWD/evidence/phase3/search-native.json" \
  PYTHONPATH="$PWD/.runtime/upstream/AstrBot:$PWD" \
  .runtime/phase3-venv/bin/python tests/test_phase3_search_native.py
ASTRBOT_ROOT=/tmp/qq-phase3-search-policy-tests \
  PYTHONPATH="$PWD/.runtime/upstream/AstrBot:$PWD" \
  .runtime/phase3-venv/bin/python tests/test_phase3_search_policy_integration.py
```

## S01–S26 逐项矩阵

状态按本行明确列出的测试层级解释；T1/T2 PASS 不能转写为真实 DeepSeek/QQ PASS。需要真实模型或用户观察的项目保留 BLOCKED。

| ID | 状态 | 已执行证据 | 未覆盖的实际边界 |
|---|---|---|---|
| S01 普通聊天零额外调用 | PASS · T2/T3 小样本 | T2 主模型 1，其余 0；真实模型两轮各 2 个闲聊样本均未选工具 | 不代表所有日常语料永远不误判 |
| S02 明确要求搜索 | PASS · T2/T3/T4 有限样本 | 既有独立协议探针保留；新增真实 QQ 官方价格问题由原 Agent 调用 Search 1次、Fetch 1次并回答 | 仅已审核的公开主题，非通用搜索 |
| S03 隐含时效需求自主选工具 | PASS · T3 有限 3 例 | 首轮 0/3 完全合规；同一主 Agent 工具操作契约修复后，3/3 自主选 Search 且参数通过实际门禁 | 真实工具选择，不执行外部 Search；不是保证任意问题/多轮永远正确，首轮缺陷保留如下 |
| S04 私人回忆不外搜 | PASS · T1/T2/T3 小样本 | 私人合成查询实际 Search/Fetch HTTP 为 0；完整 T2 I09 只核对真正 evidence.content/sources.excerpt；原生记忆重开及真实单值住所纠错后召回见两份记忆报告；真实选择测试两轮各 1 个偏好回忆未选工具 | 各层证据分别覆盖门禁和召回，真实 QQ 的整条私人记忆路径仍未执行 |
| S05 明确不联网 | PASS · T1/T2/T3 小样本 | T2 progress/Search/Fetch=0；真实模型两轮各 1 个“不联网”样本未选工具 | 语义变体并非无限覆盖 |
| S06 提前回应顺序 | PASS · T2/T3/T4 | 真实 QQ progress 确认22:37:45.707 +08，Search开始45.714568，Fetch结束47.216536，final确认49.343；本人确认流程可用 | 平台API确认不等于用户已读；只验一轮真实时序 |
| S07 Search 连续 Fetch | PASS · T1/T2/T3/T4 | 真实原 Agent 同轮Search/Fetch各1次，共用1条progress；独立协议证据另保留 | 只覆盖本轮实际组合，不声称任意问题自主检索正确 |
| S08 独立轮次隔离 | PASS · T1/T2 | `test_separate_turns_isolated`；原生下一轮闲聊不受前轮工具状态污染 | 多设备真实补充消息待 T4 |
| S09 原生说明不重复 | PASS · T2 | 两次 fixture 工具调用均带中间说明，只有受控 progress + final 出站，final 不被吞 | 原生全进程不同插件排序由总集成另验 |
| S10 过渡错误有界降级 | PASS · T1 | timeout/异常/空/非法文字回退，原生调用继续；真实 async timeout 用例 | 不代表真实服务稳定性指标 |
| S11 failed/unknown 不重发 | PASS · T1 + Guard 原生组件 | 失败与未知回执保持状态、持久独占；不补发第二条进度，见发送验收 | 非破坏性注入，未故意中断生产 QQ 网络 |
| S12 无结果/401/429/超时 | PASS · T2 | 原生 HTTP 空结果、401、429、超时，每种 1 次工具；每轮最多 3 次合成模型；错误正文不入上下文 | “没有核实”的文本由 fixture 生成，真实自然表达待 T3 |
| S13 Search 成功 Fetch 失败 | PASS · T1 | 前者证据保留，后者返回未核实正文错误，不抹掉 Search 成果 | 真实模型是否诚实表达须 T3，不以指令保证代替模型观察 |
| S14 证据回填 | PASS · T2/T4 | 真实原生历史中2个tool_call/result成对，final位于全部结果之后且与sent正文哈希一致；不导出私人上下文 | 此审计证明结果回填，不独立保证答案中每个价格/引文事实准确 |
| S15 协议、多轮、重开 | PASS · T2/隔离持久化 | tool_call_id 成对匹配；原生 ConversationManager 写 SQLite，重建 DB/Manager，恢复历史后新 runner 继续 | 测试显式调用原生持久 API；完整 AstrBot 自动保存与生产重启另见总 T2/T5 |
| S16 进度和最终归档 | PASS · T2/T4 | 真实同一入站轮次具有progress/final两个独立delivery、sent确认、正文哈希和时间 | 不伪造被上游丢弃的平台 message_id |
| S17 同轮去重 | PASS · T1/T2 | 并发 progress 独占；相同工具参数复用同轮缓存；发送 ID 不可复用；final 保留 | 新合法轮次可重新搜索，不全局吞请求 |
| S18 进度后中断恢复 | PASS · T1/SQLite | `test_restart_durable_progress_not_replayed`；Guard `test_restart_interruption_retains_archive_without_replay` | 不将 pending/unknown 自动标 sent，不做生产破坏性故障 |
| S19 补充/取消 | PASS · T1 + Guard 原生组件 | 每次工具前/进度后重检轮次；取消、supersede、热停用 Key 不再开始新工具 HTTP | 已发出的外部请求可能已产生费用；不能撤销服务端收到的请求 |
| S20 未授权账号 | PASS · T2 | 非白名单用户实际主模型/progress/Search/Fetch/出站计数均为 0 | 只覆盖指定私聊范围，群聊仍关闭 |
| S21 最小化/ori_question | PASS · T1/T2 | 原 schema 删除 ori_question，出站也强制丢弃；不传完整会话；合成私人串不在工具 HTTP；原生工具诊断日志整体抑制 | 新公开主题需人工审核加入词表；不承诺凭通用正则理解所有隐私 |
| S22 恶意网页/内网 URL | PASS · T1/T2 | 固定工具权限；loopback/内网/元数据/混合 DNS/带凭据 URL 拒绝；元数据 Fetch HTTP=0；网页标为不可信数据 | 未证明真实模型免疫全部提示注入；不控制 DeepSeek 远端重定向/DNS，见下节 |
| S23 人格/自然度 | PASS · T4/T6 有限样本 | 用户对真实QQ搜索前后回复反馈“可以”；V0.1保持，并提出去Markdown改进 | 仅本人验收的一轮；格式修复的具体QQ效果、广泛语境仍待观察 |
| S24 核心人格重启 | PASS · 原生 SQLite/策略集成 | 16 项 policy suite：重开仍唯一 V0.1，旧 policy 不覆盖；拒绝缺 Key/错范围/不安全 endpoint；请求 Phase3 而加载失败时不得降级；持久化后 activate，失败撤销 | V0.1/自然文本/多气泡已发布并重启；搜索生产路径另验 |
| S25 资料预览 | PASS · 本地预览/独立授权导入 | 已生成L1/L2/L3/EXCLUDED主题预览；本人另批准L1 3组+L2 12组及模型用途，窄导入已执行 | L3 6组和排除21组未导入；私人资料不进入公开搜索；检索验收见 `private-profile-import.md` |
| S26 关闭与回滚 | PASS · T1/原生组件；生产 NOT_RUN | disabled 时剥除裸原生搜索工具；关闭搜索保留 V0.1/数据库；完整关闭恢复 Phase2 人格；16 项策略证据验证请求 Phase3 而插件失效时 fail closed、持久化后 activate 与异常撤销；5 项发布审计确认磁盘主动发送始终关闭 | 用户已批准并完成新的生产停写快照、13库隔离恢复及服务恢复；未执行破坏性完整生产回滚 |

截至本版：**26 PASS（限定表内层级）、0 当前 FAIL、0 BLOCKED**。旧25/26快照保留；未将全部行升级成T4。真实工具选择两轮20次聊天请求；既有独立协议Search 2、Fetch 2、聊天模型4；新增真实QQ Search 1、Fetch 1、progress/final各1条。当天持久工具计数6（含保守继承的4次探针），重启不归零。268项组件与15项完整联调不能替代尚未执行的真实故障、插话和跨语境测试。

策略最终机器证据为 `evidence/phase3/policy-final-storage-20261021T161814Z/policy.json`（15/15，无跳过）。独立审查另修复两条报告存储失败路径：只有PASS报告写成功才放行；初始报告或目录创建失败仍保留Policy拒绝入站实例，恢复存储后可重新启动。早期统一回归发现测试 fixture 缺少真实 ProviderRequest 字段，修正后 8/8；扩展阶段 12 项中 5 个失败源于先命中通用 provider 校验而未到达期望的缺失组件原因，前置 requested-component 检查后 13/13。早期结果保留，不以重测抹除故障记录。

真实选择首轮使用 `scripts/phase3-test-search-choice.py`：3 隐含时效、2 明确搜索、2 闲聊、1 合成个人回忆、1 不联网、1 用户 URL；全部 10 次收到模型响应，7/10 选择种类符合预期，6/10 同时通过实际参数门禁，隐含问题 0/3 完全合规。3 个隐含问题都尝试工具，不能误述为模型完全没有自主选择；实际缺陷是同次并列 Search/Fetch、猜官网 URL 或冗余查询含未审核词。明确私人回忆、不联网、普通闲聊均无工具调用。此次**仅执行聊天模型请求**，Search/Fetch HTTP 数为 0；没有发送真实私人画像。

修复：包装 schema 与实际 count/全文/域名约束一致；`contract.py` 将公开词、每步一个工具、先 Search 后按返回 URL Fetch 的操作契约加入同一 Agent system_prompt，真实探针直接复用同一契约。不会创建第二个人格/分类模型，不以提示代替强制边界。错误参数仍 fail closed。

同一 10 例修复后复测：**10/10 选择正确且参数允许，隐含时效 3/3**。首轮真实 usage 为输入 12,622 / 输出 722 token，复轮为输入 15,822 / 输出 588；均为 API 返回的实际 usage。未执行 Search/Fetch，不能把这些计数当作搜索费用或真实检索答案质量。结果保存在仓库外 owner-only 合成证据文件，原生产配置、聊天和 QQ 登录态未改。

## 用户批准后的真实公开协议探针

`scripts/phase3-test-search-real.py` 调用两个官方公开主题，每条流程只执行 Search 1、Fetch 1、progress 模型 1、读取结果后回答模型 1。URL 在测试输入中明确指定且经过现有 PublicPolicy/DNS 门禁；原生兼容 Search payload 为 query/count=3/need_content=false，无 ori_question，Fetch 仅含 url。网页正文只驻留传输/模型内存，证据仅记录长度、hash、状态和用量，不保存正文或 Key。

| 公开主题 | Search | Fetch | 真实 progress / 最终模型 | 结果 |
|---|---|---|---|---|
| 官方 Search/Fetch 文档 | HTTP 200，3 条，554 ms | HTTP 200，6405 字符，260 ms | 873 / 1350 ms；progress 无 fallback，先于工具；最终响应含官方链接 | PASS · 独立协议 |
| 官方模型价格 | HTTP 200，3 条，444 ms | HTTP 200，1763 字符，2805 ms | 873 / 1238 ms；progress 无 fallback，先于工具；最终响应含官方链接 | PASS · 独立协议 |

共 4 次模型调用：9097 输入、307 输出、9404 total token；8 个 HTTP 请求全部成功，无重试。按工具每次 0.005 元、Flash 高峰全部按未缓存输入 2 元/百万及输出 8 元/百万保守估算，总计 **0.04065 元**，其中工具 0.02 元；这是估算，不是账单。探针在独立持久 SQLite 预留 4 次日尝试与 0.30 元保守额度，10 元验收预算在预留接近 9 元时停止。生产账本未写入；同一验收日汇总须扣除这 4 次，不能因分开账本忽略它们。

该探针有真实 API、真实进度和最终模型响应，但固定两个工具步骤，未运行生产 Agent、Guard 发送或真实 QQ；最终文本仅记录 hash/长度/官方链接存在性，未逐条人工核验最终引用与结论准确性。不能借此声称自主全链路或自然度已验收。原完整 owner-only 临时结果为 `/tmp/qq-companion-phase3-private/search-real-protocol.json`，日预算曾在同目录；本轮接续时这些临时文件已不存在，因此保留原报告数值但不能声称原始文件仍可读取。上线预算按固定来源ID保守继承4次尝试，防止换账本绕过上限。

部署后的实际入口为 `Phase3Search.wrap_request → SearchRuntime._progress → bounded_progress → Guard.send_controlled(kind=progress)`，随后原生工具及 final 走相同轮次。模型 progress 为 96 token/4 秒/零 SDK 重试；持久 progress claim 不因重启重复。正式 QQ 验证应在已批准时间窗用公开主题，核对两种 delivery kind、单次 progress、请求计数及客户端先后顺序，不能把本探针的生成时间当平台送达回执。

## 默认安全、预算与已知限制

- 默认 `enabled=false`；必须同时满足 Phase2Policy、Phase3Runtime、Guard 的白名单/模块配置，以及 Search Key 和 V0.1 核心人格准备。缺失任一项就从本轮 ToolSet 移除 Search/Fetch；不存在“关闭前奏却保留无保护搜索”的退化路径。
- 查询由审核的公开主题词和有限公共修饰词/版本日期组成。初始仅 `DeepSeek`、`AstrBot`、`炉石传说`。未审核的新主题明确拒绝，不伪装成可以搜索任意公开问题。查询计数默认 5、最多 10，禁用 Search 自动全文，强制 Fetch 独立门禁。
- Fetch 只允许审核域名的 HTTP(S) 标准端口，无凭据、query、fragment；必须是本轮 Search 返回的原 URL 或用户本条明确给出的 URL，不能由模型编造带私密内容的 URL。DNS 所有地址必须为公网。默认域名为 `api-docs.deepseek.com`、`docs.astrbot.app`、`hearthstone.blizzard.com`。
- DNS 检查只保护**本地提交给 DeepSeek 的 URL**。外部 Fetch 服务独立解析 DNS/跟随重定向，本项目没有其控制权；不能据本地 DNS 检查宣称完全防止远端重绑定或重定向 SSRF。新域名必须审查。拒绝 URL 的错误不回显被拒绝内容。
- 每轮默认最多 3 次工具，工具 20 秒、整轮 60 秒、进度 4 秒；返回给模型的网页最多 10,000 字符。原生工具 HTTP body 仍先由上游完整读取，**没有证明传输字节级上限**；这不是大文件下载能力。外层原生 Agent 限 6 steps、tool timeout 40 秒、主模型 transport attempts=1。
- 每日工具尝试默认上限 60（上海时区），SQLite 持久计数；Search/Fetch 合并计数，不因失败重试，不因进程重启归零。日计数在进度前预留，取消可能消耗预算但不产生工具请求；保守消耗不能称精确计费记录。
- 一次进度 claim 持久化后失败/未知也不重发；同轮相同工具参数缓存避免重复费用。更改请求参数仍受每轮和每日上限。没有后台定时搜索。短句模型、主模型、Search、Fetch 分别计量。
- 原生 runner 在工具前日志记录参数，故对工具 runner/executor/search 和 trace 的诊断内容整体抑制，只留固定提示、计数和状态。搜索 Key 纳入既有凭据 redactor；原生异常原文不写入返回结果。不是声称整个应用永不记录用户私聊，私聊归档仍按既有范围保存。
- 仍有真实服务故障形态、模型选词/引用质量、语义不联网变体、授权用户提问超出公开词表等开放项。不得为扩大能力放松白名单或擅自读取/拼接访谈。

## 官方接口核对与后续真实验证

2026-10-22 重新核对 [DeepSeek 官方 Search API 文档](https://api-docs.deepseek.com/zh-cn/guides/search/)：使用 DeepSeek API Key 的 Bearer 鉴权，直接端点为 `POST https://api.deepseek.com/v1/tools/search` 与 `POST https://api.deepseek.com/v1/tools/fetch`。用户已批准复用现有 Key；配置、预算与权限仍独立管理，不另造一种 Key 类型。[官方模型价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)列出 Flash 高峰未缓存输入 2 元/百万、输出 8 元/百万，空闲时段减半；探针估算使用较高价格且忽略缓存优惠。

官方直接接口 Search/Fetch 各为每千次成功且有内容的调用 5 元；失败、空结果和超时不收费。账户级 QPS 分别为 Search 100、Fetch 25，多个 Key 不增加账户总限额，Fetch 另有域名限流。Harness/Codex 等特定服务端工具的临时免费条款**不代表本项目直接 API 免费**。按当前价，60 次日尝试的工具成功费用上界为 0.30 元，另加模型 token；当前10元估算验收预算与每日60次尝试已由用户批准，接近9元保守预留即停新增付费测试。

已批准的后续部署和真实 QQ 验证按总发布流程继续：明确搜索、未说“搜索”的最新游戏更新、普通闲聊、明确不联网、Search→Fetch 和错误说明，合计每轮最多 3 次、每日 60 次，并扣除本探针的 4 次。保留真实 usage/延迟/状态，核对 progress/final 展示及归档，由用户评价人格和自然度。不得上传访谈来测试搜索，不进行压力测试，不启用后台探索。

## 2026-10-22 多气泡专项续接

原阶段矩阵保留在 [历史报告](archive/natural-agent-validation-pre-multibubble-20261022.md)。真实 QQ 两气泡及用户评价已通过，范围属于被动聊天，不代替 S23 的搜索前后自然度。完整框架15项通过，Search原生套件扩至12项，证明一次progress、一次Search、一次Fetch、最终两个气泡分别送达，主模型/工具次数不随气泡增加。

生产启用继续使用单独搜索配置，复用VPS本地Key而不覆盖聊天Provider；审核主题仅DeepSeek/AstrBot/炉石传说，非通用自主搜索。新增持久 `web_tool_attempts` 记录progress的delivery关联、工具开始/结束/状态；不存query或网页正文。崩溃中的工具状态变为unknown_interrupted，日计数保留。早先4次协议探针按来源ID幂等计入2026-10-22，失败和重启不能恢复额度。后台搜索仍关闭。

真实证据为 `evidence/phase3/search-qq-audit-20261022-r1.json`、`search-native-context-production-20261022.json` 和 `search-owner-acceptance-20261022.json`。已结合平台确认、原生历史及本人观察核对上述链路。工具审计的returned仅表示原生方法返回；原始Search正文被截为10000字符，Fetch为原生文本格式，旧诊断字段result_count=0是无法解析JSON时的默认值，不代表零结果。两者均有非错误正文；不据时序字段单独推断HTTP状态。

原生provider_stats按Agent运行汇总，不能把记录行数当作每一步HTTP请求数。当前诊断输出改为internal_agent_recorded_runs，HTTP请求数未知则为null；progress、工具及摘要另计。22:49后的Markdown过滤为同步有界文本处理，无外部请求、无额外模型，不改变Search/Fetch的授权或限额。

2026-10-23续接：新增记忆导航指引/部分主题目录已通过完整17项隔离联调并上线；只使用本地原生Memory工具，不向Search/Fetch发送资料。S01–S26原有26项限定层级结果保持，真实QQ资料回答准确性仍归P08 FAIL，不以搜索已通过替代。

## 2026-10-23 四项体验 V2 回归

S01–S26继续为26项限定层级PASS，未提升为全部T4。当前组件382项/28模块、完整固定框架20项通过；其中12项原生搜索回归明确验证单次progress、Search/Fetch、最终多气泡、历史协议与逐条Guard，计入组件而不重复计数。新增资料正文预取是有界原生本地读取，不额外分类模型、不把私人问题或正文交给公开Search/Fetch。主轮3次、每日60次尝试及审查域名不变，未添加后台搜索。

V2 r2已发布，原有真实搜索时序/本人评价继续复用；本次重启QQ待本人认证，新候选真实四项联合尚未验收。新增T3的13次是合成资料/主动/图片，公开Search/Fetch新增次数0，不能称新增搜索T4。最新资料复测失败属于P08 FAIL，不因搜索26项通过而消失。见[四项体验V2](phase3-four-capabilities-validation.md)和原[36项矩阵](phase3-validation.md)。

### 23:05 +08:00 登录恢复与当前状态

本人扫码后已核验当前在线备用QQ身份，原生自动登录设置重新保存并验证落盘；没有额外重启或再次登录请求。23:05:16实际isLogin/coreReady=true、loginPhase=ready，内部OneBot ESTABLISHED=1，策略PASS、47源文件摘要匹配。原先waiting_qrcode及一次快捷登录未恢复的证据保留为本次发布历史。

当前为 **CANDIDATE_READY_FOR_UAT**，仅表示完整候选及自动验证/发布前置条件满足；P08真实FAIL尚未关闭，总体PARTIAL，主动OFF。已按顺序仅邀请此前失败的资料问题，未发整组作业。证据 `evidence/phase3/four-capabilities-20261023/candidate-readiness-r2.json`、`production-health-after-login-r2.json`、`napcat-save-current-session-r2.json`。

## 2026-10-24 收尾兼容核对

原S01–S26仍26项限定层级PASS。本轮增强20项完整固定栈覆盖搜索progress/final与禁外搜路径；真实资料/图片测试未新增Search尝试。Runtime仅合并原生资料上下文，没有改变每轮3/每日60、审核主题和私料不得外搜边界。用户豁免全部剩余体验测试并关闭主动，通用自主搜索仍未实现，不能由本轮收尾推导通过。
