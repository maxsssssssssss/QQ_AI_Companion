# 通用公开搜索候选 · 2026-10-24

2026-10-25最新：[公开搜索误拦修复与发布](phase3-search-relaxation.md)已生效，取消下文历史版本的逐词当前输入门槛、泛词封禁和全部URL参数拒绝；保留真实权限、敏感值、3/60及URL来源/公网校验。177项受影响自动测试通过，原失败公开查询真实Search取得3条结果。下文来源词面限制属于旧版本记录，新的语义隐私边界以本次报告为准。

## 后续统一发布状态

2026-10-24用户明确授权后，`general_public`及对应Search五文件已随r4窄安装；持久化3/60额度与隐私/URL/DNS校验保留。固定镜像容器已重新创建，**05:10:10 +08:00启动policy/readiness与general_public配置核对PASS**。本次没有新增真实Search/Fetch/模型/QQ测试，既有S01–S26限定层级PASS不扩展为通用范围真实验收。见[本次发布](phase3-fresh-start-release.md)。以下“只同步、不应用、线上reviewed”保留为准备阶段原始记录。

本次按用户要求取消固定三个主题和域名白名单；只同步服务器待发布目录，不应用、不重启、不开启后台搜索。当前线上仍使用原 reviewed 范围，新的 `general_public` 模式等待统一生效。

## 改动与保留边界

- 复用固定 AstrBot 4.29.1 的原生 DeepSeek Search/Fetch、同一个 Agent、Guard 和现有日预算 SQLite；不升级依赖，不增加模型或搜索服务。
- `scope=general_public` 不再检查 `public_terms/fetch_hosts`，候选将两列表清空。缺省仍为 reviewed，保证旧配置不会因仅更新源码而自动扩大范围；未知 scope 拒绝。
- 每轮 Search+Fetch 合计最多3次、北京时间每天60次尝试，失败消耗额度，重启/切换范围不重置；Search 默认5结果、最多10，`need_content=false`，Fetch 独立校验。
- 取消主题表之后，查询内容须来自 Guard 保存的本轮原始输入。允许少量通用公开修饰词，但不能由模型从历史、已注入记忆、画像或工具指令中补入名称、日期、数字。模型提交的 `source_text/ori_question/full_history` 等参数不能提供授权，也不会转发。
- 私聊身份、NO_WEB、取消/supersede、Key 校验与日志脱敏保持。私人资料问答继续使用本地记忆，不能因为模型选择了搜索就外发。
- Fetch URL 须精确来自本轮 Search 结果或用户本条输入，在 DNS 查询之前先核对来源。任意公开域名可以通过，但凭据、query/fragment、非标准端口、编码敏感路径、非法/内网地址、混合 DNS 继续拒绝；不静默剥参数后读取另一页面。
- progress 仍只发一次且在工具之前确认，工具结果回填原 Agent，final 继续经过现有气泡发送和归档；网页内容是非可信数据。

## 验证

本轮受影响验证 **124/124 PASS，无失败或跳过**；证据 `evidence/phase3/public-search-20261024/affected-r1/summary.json` 及同目录逐模块日志。没有把旧测试复跑累计成新的Phase3独立验收项。

| 模块 | 数量/结果 | 范围 |
|---|---:|---|
| `test_phase3_search.py` | 55 PASS | T1：任意本轮公开主题、新域名、私料/伪造来源拒绝、DNS前来源校验、URL安全、3/60预算/重开、故障与取消 |
| `test_phase3_search_native.py` | 16 PASS | T2：固定原生Agent、Provider、Search/Fetch、Guard和OneBot适配器；新主题/新域名、模型请求含合成记忆但工具payload不含、重复调用和progress/final顺序 |
| `test_phase3_search_policy_integration.py` | 18 PASS | T2：原生配置保存重开保留scope、默认兼容、非法scope拒绝及既有启动/人格/停用/权限回归 |
| `test_phase3_search_pending.py` | 10 PASS | T1：窄候选仅3字段、额度不放宽、源字节不变、BOM/权限/symlink/目录保护 |
| `test_phase3_staging.py` | 10 PASS | T1：隔离候选、搜索Key独立、未来stage生成通用范围并保留3/60 |
| `test_phase3_status.py` | 10 PASS | T1：只读脱敏状态、主动状态/计数、异常边界 |
| `test_phase3_search_release_audit.py` | 5 PASS | T1：冷候选与发布清单/隐私保护回归 |

新公开主题使用合成 James Webb / 天气 / 电影 / Python 等，不读取生产私人资料。T2 与 loopback 合成模型、搜索、网页及平台对端联调，不等于真实 DeepSeek 或 QQ；未重复运行搜索关闭的全进程23项套件来冒充新搜索正向验证。

本次真实 DeepSeek 调用0、真实 Search/Fetch HTTP 0、QQ发送0，额外API费用0；未修改模型、人格、长期记忆或生产日预算。历史三主题范围的T3/T4证据保留，不能转记为通用搜索已经实测通过。

## 同步与回退

专用准备脚本 `scripts/phase3-search-config.py` 仅改变 Search 配置的 `scope/public_terms/fetch_hosts` 三个字段，保留现有身份、开关与全部限额，拒绝超过3/60的源配置。输出全新私有候选，源配置不动。后续全量隔离 staging 同样生成 general_public 配置，不会重新写回三主题默认。

联合候选目标：`/opt/qq-ai-companion/releases/phase3-20261024-unified-pending-r3/`，同时保留上一份r2中的Token压缩/80轮回退、长期主动和新气泡提示词。源码与配套脚本存入 `source/`，Search 基线/候选在 `search-candidate/` 和合并后的 `config-candidate/`。以 `sync-receipt.json` 和本地 `evidence/phase3/unified-pending-20261024/sync-receipt-r3.json` 成功回执确认同步；同步不代表生效。

待安装新增 Search 文件为 `policy.py/main.py/runtime.py/contract.py/_conf_schema.json`；其余三个待生效组保持。未来收到统一生效通知后，重新检查最新基线、备份/隔离恢复，窄合并字段，不覆盖整个data。需恢复旧搜索范围时，恢复原 reviewed 模式及原主题/域名列表和匹配源码，继续使用同一日预算；当前未应用，因而无需执行生产回滚。

## 仍然存在的边界

“通用公开”表示没有固定主题/域名名单，不表示允许查询任意私人资料。本轮来源核验是保守保护：依赖旧聊天的省略指代、模型自行翻译/改写为本轮没有的主题词会被拒绝，需要明确公开主题；它不是完整语义隐私识别器，也不保证识别输入中的所有私人信息。含Token等敏感词的公开技术问题可能被保守拦截。

本地 URL/DNS 预检不能控制 DeepSeek 远端 Fetch 的每一跳重定向或 DNS 重绑定；不能宣称网页提示注入完全免疫。真实通用搜索的模型选词、答案与引用质量以及本人自然度本次未测。原 P01–P36、S01–S26 的限定层级结论保留，不因这次自动验证提升为全体验PASS。
