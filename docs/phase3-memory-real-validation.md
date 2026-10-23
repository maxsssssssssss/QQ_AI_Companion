# Phase 3 真实模型记忆组件验证

日期：2026-10-21 至 2026-10-22。总体 **PARTIAL**；新增“用户更正原文 → 原生断言修订 → 重开召回”路径 **PASS（1 个合成单值住所案例）**。本报告只覆盖合成资料、真实 DeepSeek 与固定 MemoryCompanion 2.3.1 的组件调用；不是实际 QQ、真实用户资料导入或生产记忆验收。

采用上游 commit `e40a83005b0a2e0817e5680c7d14398c9ab49790` 的真实 `MemoryCompanionService`、原生总结 prompt、JSON 解析、引用校验和 SQLite 存储。临时库独立，未读生产聊天库、访谈资料或生产记忆。四类输入为合成职业、面试安排、饮品偏好和未完成相册；原始事件时间与来源 ID 一起进入原生链路。

`scripts/phase3-test-memory-real.py` 默认 dry-run。真实调用使用已验证 SSH 别名及既有受限 transport；Key 留在 VPS，未写入命令参数、报告或本地文件。HTTP 超时 25 秒、客户端超时 40 秒、零重试、每次最多 1200 输出 token。原轮次上限 4 次尝试，之后为验证实际发布的断言纠错路径，单独增加最多 2 次尝试；累计实际 4 次 HTTP 200、另 2 次 SSH 提前失败未取得 HTTP 响应。新增模式使用 `--scenario native-assertion-correction --max-calls 2`，未调用 Search/Fetch 或 QQ。

| 阶段 | 实际执行 | 结果及限制 |
|---|---|---|
| 第一轮原生摘要 | 1 次真实 HTTP 200，2953 输入 / 815 输出 token | 四类事实与来源时间存储 PASS；**摘要质量 FAIL**：正文添加了原文没有的 Bot 实体动作及未来提醒意图。不能用 canonical_summary 较准确来掩盖正文错误 |
| 第一轮第二次请求 | SSH 子进程提前退出，未取得 HTTP 响应 | 原生批次隔离，原始事件保留。不是成功纠错或召回 |
| 第二轮 | 第一次 SSH 子进程提前退出，未取得 HTTP 响应；停止继续请求 | BLOCKED；没有真实模型结果，不计作模型通过或失败 |
| 第三轮来源约束复测 | 1 次真实 HTTP 200，3172 输入 / 836 输出 token | 四类事实、来源和时间 PASS；人工审阅 summary、canonical_summary、key_facts、assertions、associations 均未再出现无来源 Bot 行为/提醒。**只证明这一个样本** |
| 自动摘要记录原生纠错 | 对真实抽取出的 conversation_summary 调用原生修订 API | **FAIL（原生能力边界）**：返回 `memory_type_requires_domain_revision`。未手造另一种 record 来替代验收 |
| 纠错后新会话召回 | 第三轮严格限 1 次真实调用，第二次摘要无额度 | **NOT_RUN**：不存在经过真实第二次抽取的“白水”事实，不能将空召回误报为已完成纠错链路。原生持久化/召回模拟组件证据另见记忆专项报告 |
| 新增实际发布纠错链 | 2 次真实 HTTP 200；四类首批加合成单值住所，第二批是明确确认的住所更正原文 | **PASS（1 例）**：真实抽取并存储旧住所 assertion；新原文经原生总结生成新住所 assertion；原生 upsert 建立 superseded/link；Coordinator 自动归档旧来源摘要并清缓存。无手造提取结果、无直接修改数据库 |
| 新增纠错后重开召回 | 关闭并重建原生 MemoryService/Coordinator，仅保留隔离 SQLite，无近期消息历史 | **PASS（1 例）**：召回更正后的住所，当前查询不返回旧 assertion/旧 summary，历史修订链接仍在；全部原始来源保留 |

前轮真实 token：输入 6125，输出 1651，总计 7776。新增纠错链为输入 6187、输出 1261、总计 7448。**累计输入 12312，输出 2912，总计 15224**；是响应 usage，不是账单金额。SSH 早退出原因未确定；无 Key、无 HTTP 的同协议/长 Unicode/原生服务上下文诊断均通过，后续 3 次真实请求也通过，不能据此宣称底层偶发故障已彻底解决。

新增实际发布路径的 12 项机器检查全部通过。原文分别为合成“我住在苏州工业园区”和“确认更正：我现在住在杭州西湖区”；明确确认用于真实身份变更门禁，不跳过它。`co.summarize()` 调用原生总结、assertion 更新及 `co.reconcile_assertions()`，重复 reconcile 返回 0。首批四类事实未删除；职业、饮品偏好 assertion 在重开查询中仍返回。旧混合摘要整体归档后，未完成相册事项只验证了原始来源保留，未单独验证其后续召回，不外推所有旧事实都已逐项复验。两次新增摘要未再出现最初的 Bot 实体动作/未来提醒扩写。

直接对 conversation_summary 调用 `correct_user_memory` 被原生类型检查拒绝的历史结果仍保留；这不是上述发布路径。新结果证明新增更正原文走原生 assertion 的一个端到端案例，并不把旧“白水”未执行测试改成 PASS，也不证明多值偏好、歧义更正、全部否定表达或所有长期记忆都已通过真实模型测试。

修复使用 `integrations/phase3_memory/summary_provider.py`：仅包装该 MemoryService 的 context/provider 委托，在完全匹配固定原生 summarizer 请求协议时追加“所有字段均须有原始来源、不虚构 Bot 能力和未来行为、不把未完成事项变成提醒”的生成约束。原生 prompt、schema、模型、调用次数、解析与存储不变，其他工具/聊天请求透传；未改上游源码、未添加分类模型，也未用针对本例的正则删句。Coordinator 生命周期负责安装与恢复。生成约束不能保证所有未来输出都忠实，仍需扩大授权后的真实样本评估。

`tests/test_phase3_memory_summary_provider.py`：4 PASS / 0.080 s。验证固定上游 prompt 匹配、一次调用透传、非摘要请求不变、共享 context/provider 不变、异步 getter 和重复 restore。原生 store 全链路由上述真实调用及记忆组件套件另外覆盖。

原始 owner-only 合成输出保存在工作机 `/tmp/qq-companion-phase3-private/` 下 `memory-real.json`、`memory-real-r2.json`、`memory-real-r3.json` 和 `memory-real-correction.json` 中，未加入仓库。本报告保留失败与修复后的证据区别。真实用户生产记忆、生产迁移和自动记忆总体质量均尚未验收，不标整个 Phase 3 PASS。
