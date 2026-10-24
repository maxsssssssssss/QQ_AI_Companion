# Phase 3 待发布目录与重新开始审查

审查时间：2026-10-24 04:27–04:29 +08:00。用户要求先审查服务器冗余文件，以及不保留旧对话、重新开始的影响。本次仅执行只读检查，未发布、重启、删除、迁移或修改服务器配置，未调用模型或发送 QQ 消息。

本报告只含目录占用、表记录数、版本及代码行为，不包含聊天正文、私人资料、密钥或登录态。仅在本地保存，不随本次审查自动同步服务器。所有清理范围仍待确认。

## 结论

- r4 是待发布快照，状态为 `SYNCED_NOT_APPLIED`；重启现有容器不会自动安装它。
- 待发布代码没有造成多个实例运行。AstrBot 只挂载现有 `deployment/state/data`，没有挂载 `releases`。发布应窄范围安装 13 个运行文件、合并 5 份配置的明确字段，不能把 182 个源码、测试和文档文件整体复制到运行目录。
- 主要可整理对象是历史备份、打包产物和构建文件，不能全部当垃圾直接删除，其中包含恢复材料或敏感历史副本。
- 清空会话不等于删除历史，也不等于忘记旧聊天。会话、归档、派生记忆、摘要队列、Private 连续性状态及历史副本需要一起规划。
- 当前没有经验证的跨这些存储层的一键安全清理入口。不能删整个 data、整库或直接调用原生全量清记忆来完成本需求。

## 实际占用

以下为普通文件逻辑字节数；不是承诺可释放空间，不包含目录块、文件系统保留空间，也不能与 Docker 共享层简单相加。运行中数据可能继续增长。

| 位置 | 当前占用 | 审查判断 |
|---|---:|---|
| `deployment/backups` | 7.52 GiB | 148 个归档约 4.51 GiB，其余备份/工作副本文件约 3.01 GiB；共 544 个数据库文件，主要是历次副本，并非 544 个运行中的数据库服务。需要按保留策略清理。 |
| `deployment/artifacts` | 1.60 GiB | 13 个文件，其中 12 个归档。需按镜像/发布清单识别可重建或已失效产物。 |
| `deployment/build` | 0.51 GiB | 构建材料。需核对当前及回滚镜像的重建依赖后整理。 |
| `releases` | 16.94 MiB | 25 个历史发布/候选目录。最新版 r4 为 2.41 MiB；旧候选不占聊天运行内存，不是主要磁盘负担。 |
| `deployment/state` | 79.36 MiB | 现用 AstrBot 数据、插件及配置，不能整目录删除。 |
| `deployment/ntqq` | 336.08 MiB | QQ 客户端状态、数据库及缓存，包含登录所需材料；不能当普通缓存整体清空。 |

文件系统使用率 40%，已用约 22.40 GiB，可用约 34.03 GiB。没有需要立即清理的空间压力。

Docker 报告 5 个镜像、2 个活动容器；镜像总计 6.469 GB，报告可回收 4.93 GB，构建缓存报告可回收约 404.7 MB。这些是 Docker 自身的统计口径；旧镜像可能是已要求保留的回滚版本，不可直接执行全量 prune。未检查云平台快照，不能声称所有历史副本仅限于本表。

## r4 完整性与发布缺口

目录：`/opt/qq-ai-companion/releases/phase3-20261024-unified-pending-r4/`。

只读校验确认：182 个 source 文件、10 份配置基线/候选文件的 SHA256 与 manifest 一致，manifest 与同步回执一致。预期运行覆盖为 Guard 3 文件、Runtime 5 文件、Search 5 文件。配置候选只合并获准字段，不能完整覆盖上线后产生的新配置。

发布前仍需处理：

1. 统一的窄范围安装、安装日志与回滚入口。现有 `phase3-install-overlay.py` 接受的 candidate/data 布局与 r4 的 source/config-candidate 不同；旧 V3、Runtime 单项脚本也不能覆盖完整 r4 清单。不得直接套用宽范围 stage 代替。
2. 旧 `phase3-deploy-bubbles-v3.py` 对 Guard JSON 逐字节 hash 的门禁会拒绝原生等价序列化；需要只接受语义等价、继续拒绝实值漂移的实现与回归，不能跳过校验。
3. 发布时重新检查运行基线、按字段合并、执行必要的一致备份/隔离恢复与授权内迁移，再安装和统一重启。旧数据清理应作为单独可审查的迁移，不能隐含在代码发布里。

两容器 ID、镜像、启动时间、重启计数及归一化后的挂载与审查开始一致。AstrBot 与 NapCat 均在运行，重启计数均为 0。原始 metadata 的 `containers_unchanged=false` 使用未排序的 mounts 列表比较；后续按挂载目标排序的复核为 true，未见容器替换或重启证据。保留原始结果，不改写它。

## 历史具体存在哪里

线上只读 COUNT 观察到 AstrBot 3 个会话、Recorder 59 条消息、Guard 59 条入站、MemoryCompanion 111 条 timeline 与 38 条 memory。不同表存在关联、聚合及复制关系，这些数字不能相加为聊天数或独立事实数；38 条 memory 也不全是可删除的聊天派生事实。

以 `D=/opt/qq-ai-companion/deployment/state/data` 为基准：

| 层 | 需要纳入旧内容清理的对象 | 不能误删的对象 |
|---|---|---|
| AstrBot `data_v4.db` | 旧 conversations，包括压缩上下文及相关会话内容 | 人格、服务配置、偏好、身份与管理资料 |
| Recorder `plugin_data/astrbot_plugin_message_recorder/messages.db` | 旧消息、FTS 及不再引用的媒体 | Schema；未来是否继续记录由用户另行决定 |
| Guard `plugin_data/phase1_guard/guard.sqlite3` | 入站/出站正文、消息链、气泡正文等 | 必要幂等身份、发送结果、failed/unknown 安全状态及额度元数据 |
| MemoryCompanion `plugin_data/astrbot_plugin_memory_companion/memory_companion.db` | 旧 timeline、聊天派生记忆、摘要/画像来源、关联与检索索引 | 若选择保留：已批准 L1/L2 的稳定 ID、来源、身份与权限 |
| Runtime `plugin_data/phase3_runtime/memory-controls.sqlite3` | 旧来源内容、未完成整理任务与旧处理范围 | 身份、授权、正确的清理截止点/游标、预算及控制状态 |
| Private `plugin_data/astrbot_plugin_private_companion/` | 最近对话、共同经历、未完话题、待跟进事件和旧候选 | 单一目标绑定、capability、暂停/禁谈限制及尝试额度 |
| 备份、恢复目录、诊断副本 | 内含聊天的数据库、归档、原生迁移副本 | 去留须明确；保留含旧聊天的备份就不能声称彻底不保留旧历史 |
| QQ `deployment/ntqq` | 可能保留客户端消息/媒体状态 | 登录态与客户端数据库相互关联，需另行评估，不能保证 AI 数据清理会抹去 QQ 客户端或云端历史 |

L1 3 组、L2 12 组是之前已批准的资料范围；它们分别接入原生 core/external memory，并由 `plugin_data/phase3_profile_release/ledger.sqlite3` 追踪。应按稳定 ID、来源插件和导入账本识别，不能仅按 memory_type 粗暴清除，也不应因新会话而重复导入。L3 不在已批准导入范围内。

仅执行 `/new` 保留旧会话和归档。仅删除派生 memories 仍保留原文与来源检索，尚未完成的摘要也可能重新写入旧信息。Runtime 会从 Guard 游标补捕获，Memory 原生流程会恢复 pending/retry_pending 队列；已完成的历史并不意味着会自动重新全量摘要，但也不能只删结果、不处理仍待工作的旧源。

原生 `clear_all_memory_data()` 会同时清理身份、ACL 和全部 L1/L2；`clear_scoped_memory()` 没有保留批准资料 ID 的门禁，删除 summary_batches 还会级联删除 summary_batch_calls，影响摘要额度。因此这些入口不符合“保留配置和资料，只清过去聊天”的需求。

## 建议确认的重新开始范围

推荐讨论基线，不代表已获删除授权：

- 保留小鲸 V0.2、批准的 L1/L2、插件与模型配置、QQ 登录态、身份 ACL、幂等安全状态及额度。
- 删除或清理截止点之前的聊天正文、聊天派生记忆、旧共同经历和待跟进候选；终止旧摘要任务，避免重新回灌。新会话从空上下文开始。
- 先在停写的一致副本做隔离迁移和验证，再修改线上；需测试新上下文不读取旧来源、原生资料仍可用、额度不重置、unknown 不重发。
- 发布/构建冗余按保留清单整理，当前和必要回滚代码可保留。含旧聊天的数据备份是否短期保留或在验证后删除，需要用户明确选择；两者恢复能力不同。
- “清除过去的历史”和“以后也不保存对话”必须分开。后者影响 Recorder、Guard 正文留存、摘要/自动记忆和恢复逻辑，需要单独实现保留策略，不能通过删库实现。
- 若要求存储上彻底移除旧正文，还需在受控停写阶段处理 SQLite WAL、空闲页、索引、媒体、日志及所有已确认副本。当前只读审查不构成物理擦除保证，也不覆盖用户设备、QQ 服务端或未检查的云快照。

执行前至少明确：保留哪些 L1/L2/记忆、是否只清旧历史、旧备份是否保留及期限。本次“先审查”未授权任何删除。

## 证据

本地受 Git 忽略的元数据证据：

- `evidence/phase3/fresh-start-audit-20261024/server-metadata.json`
- `evidence/phase3/fresh-start-audit-20261024/server-storage-followup.json`
- `evidence/phase3/fresh-start-audit-20261024/server-other-areas.json`

源码依据：`integrations/phase1_guard/store.py`、`integrations/phase3_memory/bridge.py`、`integrations/phase3_memory/core_profile.py`、`integrations/phase3_runtime/main.py`、固定 AstrBot 的会话命令、固定 MemoryCompanion 的清理和摘要实现、固定 PrivateCompanion 的 companion 状态。参考现有 `docs/phase3-pending-sync.md`、部署脚本与 r4 清单。未执行清理测试或付费联调，因为本轮范围为只读审查。
