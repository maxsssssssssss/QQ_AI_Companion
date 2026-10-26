# 公开搜索改写误拦修复与发布 · 2026-10-25

用户授权“开始修复，然后更新线上让其生效”。搜索四文件已于北京时间 **20:14:50 +08:00** 切换并重启 AstrBot；20:15:11 的新启动策略、QQ 在线及 OneBot 连接核对通过。NapCat 未重启，配置、人格、聊天、记忆和主动规则保持。

## 原因和修复范围

19:44 的真实失败工具返回 `query_not_grounded_in_current_input`，没有发出外部请求。当时已是 `general_public` 且主题/域名名单为空，但程序仍要求查询词逐字出现在本条输入。主 Agent 补充公开关联词，及“新版本”被删除“版本”后留下单字，都可能触发误拒。旧失败证据保留，不改写为网络故障或额度不足。

本次仅修改 `integrations/phase3_search/` 的 `policy.py`、`runtime.py`、`contract.py`、`main.py`：

- general_public 移除逐词命中和修饰词表限制。允许公开关联词、翻译、同义改写、近轮公开话题承接及正常搜索过滤语法，仍由原主 Agent 组织查询。没有新分类模型、Agent、话题名单或默认额外模型调用。
- Guard 当前入站仍是身份、禁止联网和取消权限的依据；模型提供的 `source_text/public/authorized/protected_values` 等字段不能授予权限。外发字段限定为 `query/count/need_content`，不附带原问题、历史或画像。
- `token/password/我的世界` 等泛词不再直接拒绝。保护具体凭据、联系方式、身份标识及敏感赋值；复用 Phase2Policy 的 `configured_secrets`，每轮从真实配置刷新本地保护值，额外保护授权账号标识。收集失败撤下外部工具，普通聊天仍可运行。
- “你记得这个游戏吗”“只根据官方资料回答”不再误作私人记忆意图。明确仅使用已保存资料、私人回忆或禁止联网/外传仍阻断。
- 普通 URL 查询参数原样保留；去除不参与 HTTP 请求的 fragment。解析参数后再次检查敏感值，覆盖 `+` 空格编码；`%2B`、`%26` 等保留原含义。带认证/签名参数、内网 IP、混合 DNS、凭据或非标准端口继续拒绝。
- Fetch 仍须来自本轮真实 Search 结果或用户当前提供的链接，在 DNS 前验证来源。没有允许模型任意拼接主机名，也没有自动扩展跨轮 Fetch 来源。
- 拒绝原因写入现有 Guard 的 `model_request.search_rejected` 诊断，仅保存固定原因与“未外发”标记，不保存被拒绝查询。工具说明区分本地策略、额度和网络错误，禁止用改写试探敏感信息拒绝。

每轮 Search/Fetch 合计 3 次、北京时间每天 60 次尝试及持久化失败计数不变；progress 仍一次、结果返回原 Agent、final 继续经过现有 Guard、多气泡和归档。reviewed 旧范围兼容保留。

## 自动验证

受影响 **177/177 PASS**，按不同模块去重，非复跑次数累加：

| 模块 | 数量 | 层级与覆盖 |
|---|---:|---|
| `test_phase3_search.py` | 59 | T1：策略、授权、3/60、持久预算、故障、取消、进度与去重 |
| `test_phase3_search_relaxed_policy.py` | 36 | T1：公开改写、译名、承接、泛词、私源意图、URL 编码及敏感值 |
| `test_phase3_search_native.py` | 22 | T2：固定 AstrBot/Provider/Agent/Search/Fetch/Guard/模拟 OneBot；实际请求契约、正文回填和发送归档 |
| `test_phase3_search_policy_integration.py` | 18 | T2：原生配置、人格、启动与权限 |
| `test_phase3_search_pending.py` | 10 | T1：配置准备保留限额与身份 |
| `test_phase3_staging.py` | 10 | T1：既有隔离 staging 回归 |
| `test_phase3_search_release_audit.py` | 5 | T1：发布清单与隐私回归 |
| `test_phase3_search_relaxation_release.py` | 17 | T1/T5：真实 ZIP/SQLite 恢复、合成 Docker；漂移、安装/启动失败和保留新数据的源码回滚 |

证据：`evidence/phase3/search-relaxation-20261025/automated-summary.json`，最终变更模块在 `final-r1/`，其余有效回归在 `affected-r2/`。保留 `affected-r1` 原生初始化在沙箱内超时及 `native-r1` loopback 权限失败；正常执行审批后测试通过，未绕过审批或放宽测试要求。

## 真实服务验证

20:15:43 使用线上新策略和现有配置凭据，对公开查询“炉石传说 米尔豪斯 法师 新版本 卡组”执行 **1 次真实 DeepSeek Search**：HTTP 200，3 条结果、3 个来源 URL，耗时 **0.507 秒**。外发只有 query/count/need_content，没有私聊历史、画像或原问题；不读取长期记忆。

这是 T3 搜索协议验证，不是主模型答案质量或真实 QQ 端到端验收。本次模型生成 0 次、Fetch 0 次、QQ 测试发送 0 次、重试 0 次；T2 验证了原 Agent 的完整工具链，不能将其冒充 T4/T6。

该请求通过现有持久预算表原子预留并登记为独立验收来源，当日计数从 0 到 1；没有在附加进程构造 DailyBudget 并误对账生产中的运行项。原 10 元验收预算新增保守预留 0.05 元，累计由 8.75 到 **8.80 元**；这是预算占位，不是账户账单。证据：`real-public-search.json`、`paid-reservation.json`。

## 上线和恢复

发布目录：`/opt/qq-ai-companion/releases/phase3-20261025-search-relaxation-r1/`。

源码清单 SHA256：`f418ef0dce3324fc187d32047ce78f54d8cb74c2cdaad426d171b2135e07a094`。

发布脚本 `scripts/phase3-deploy-search-relaxation.py` 在空闲检查后，仅停止 AstrBot；新停写备份、隔离恢复与 **14 个 SQLite** 检查通过后原子替换4个文件，再启动同一个固定镜像容器。验证51个第一方源文件仅目标4个变化，8份配置与 Compose/.env 指纹不变；NapCat 身份/启动时间保持。证据 `plan.json`、`apply.json`、`after-start.json`、`verification.json`。

备份：`/opt/qq-ai-companion/deployment/backups/phase3-search-relaxation-20261025-r1/astrbot-state.zip`。恢复副本未启动，未覆盖生产 data。

回滚入口使用同一发布脚本、同一 `--source` 和全新 `--run-id`，增加 `--rollback` 先执行预检；确认需要执行时加 `--apply`。回滚先保存当前新数据，仅恢复4个旧源码，保留切换后的新聊天、记忆、预算及主动状态；不全量恢复数据库，不操作 NapCat。旧源码保存在发布目录的 `astrbot-code-journal/`。

## 真实边界

移除词面门槛后，程序不再证明所有查询词都来自当前输入。主 Agent 负责提炼公开主题，程序负责最小外发和可识别敏感值保护；任意私人语义并非正则能够完整识别，不能宣称零误拦或绝对隐私隔离。明确敏感值和危险链接仍会被拒绝。

本地 DNS/URL 检查无法控制远端 DeepSeek Fetch 的每跳重定向或 DNS 重绑定。新范围的真实 QQ 回答与引用质量本次未再测试，原 P01–P36/S01–S26 的层级结论保留，不因修复发布或单次 HTTP 成功提升整个 Phase3 状态。
