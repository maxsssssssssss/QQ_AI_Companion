# Phase 3：清理旧聊天并发布统一 r4（2026-10-24）

**本次部署及启动检查：PASS。** 2026-10-24 05:10:10 +08:00最终只读核对确认：旧AI聊天数据与备份清理完成，r4及相应配置正式运行，两容器健康，QQ自动登录、OneBot和启动policy/readiness正常；Private空壳兼容故障修复后未再出现对应错误。本次未新增真实QQ发消息或模型实验，不以部署成功代替全部体验验收。

本次依据用户明确授权：保留人格、已批准 L1/L2、登录态和配置，清除旧聊天、聊天派生记忆和旧聊天备份，新聊天继续正常保存，并让最新版及相应配置运行。此前“不重启、只同步”的约束由这次明确发布指令更新；旧 V3 配置校验缺口按要求暂不修复。

## 清理及保留范围

停下 AstrBot、NapCat 后，从一致副本清理并验证恢复，再只替换发生变化的数据库/JSON 和明确删除的旧文件，没有覆盖整个生产 `data`。首轮清洁候选共检查 **13 个 SQLite 数据库**；实际替换 **11 个文件**、删除 **32 个文件**。启动后发现并修复Private空壳结构兼容问题，再以 **14 个 SQLite 数据库**完成新的清洁基线及隔离恢复检查；该修复没有再次清除聊天，详见失败记录。

| 范围 | 实际结果 |
|---|---|
| 人格 | 保留当前 V0.2 人格与相关配置；未换外部角色。 |
| 获准资料 | L1 3组、L2 12组，共15条完整原生记录与既有导入账本保持；没有重导入或扩展到L3。 |
| 派生记忆 | MemoryCompanion由38条降为获准15条，删除23条聊天派生记忆；111条旧时间线及其全文索引清空，相关图谱、旧画像证据、摘要与学习队列清理。 |
| 聊天 | 清除AstrBot旧会话/当前会话选择、Recorder旧59条消息正文、Guard旧入出站正文与交付文本；清除Runtime旧来源与工作队列、Private旧对话/候选正文。Guard保留无正文的必要去重及状态骨架。 |
| 不再回灌 | 保留无正文来源墓碑与处理游标，关闭旧队列继续派生的路径；旧22条授权只保留哈希/审批元数据，不保留原聊天正文，不重新调用模型整理。 |
| 安全与额度 | 身份绑定、ACL、既有授权关系、必要幂等元数据、已消耗模型/搜索额度保持；没有通过删账本重置预算。 |
| 后续聊天 | 原Recorder、Guard、Memory捕获链继续使用，新消息可以正常入库与整理。此次没有用新QQ消息或API实验验证该运行链。 |
| 登录与配置 | QQ登录态、凭据及固定镜像保留；随后只合并本次获准配置字段。 |

私聊正文、L1/L2正文、账号凭据和加密密钥均未写入公共报告。

## 统一 r4 安装

候选来自 `/opt/qq-ai-companion/releases/phase3-20261024-unified-pending-r4/`，其清单SHA256为 `3f9b5277804f5ee5e5c2a13c5f8b9a21f7593231e1e9f94858fab41150b7f0ae`。独立入口 `scripts/phase3-apply-unified.py` 校验完整候选哈希、固定镜像、实际运行源码基线与每个配置差异，安装 **13 个第一方代码文件＋5 个配置文件的指定字段**。只有 `context_compression.py` 是新增文件；没有复制182文件源码快照覆盖生产。

| 安装能力 | 已写入的配置与边界 |
|---|---|
| 会话压缩C | 原生Token摘要压缩、取消固定20轮裁剪、128000操作预算；摘要失败回退最多80个完整轮次，继续受Token保护。 |
| 长期主动 | Runtime/Guard启用；唯一获准目标原生capability已迁移为daily limit 5。北京时间09:00–23:59，无72小时截止；每天最多5次发送尝试、至少3小时、最多5次模型评估，skip/defer也计评估额度。 |
| 原生主动安全 | Private磁盘全局仍为false/0；Runtime检查并绑定既有Guard桥接之后才在实例内启用。未扩大到其他联系人、群聊、媒体或后台搜索。 |
| 多气泡提示词与V3.1 | 新 `[Phase3 自然多气泡]` 提示词及示例说明；有效模型标记优先，原生清理兼容兜底最多4泡，后续间隔1–2秒，搜索progress/final继续排除自动兜底。 |
| 通用公开搜索 | 取消三个主题和域名白名单，`general_public`；保留每轮合计3次/每天60次尝试、本轮来源、隐私及URL/DNS安全检查。 |

AstrBot4.29.1、NapCat4.18.38、PrivateCompanion6.7.0、MemoryCompanion2.3.1和Recorder0.0.4保持固定版本。DeepSeek Flash继续Thinking Off，已有模型参数保持。安装收据为 `INSTALLED_SERVICES_STOPPED`；容器随后按同一固定镜像重新创建，旧容器日志路径确认移除，最终运行核对见下节。

旧 `phase3-deploy-bubbles-v3.py` 的严格JSON字节校验问题未修改，也没有绕过其门禁。本次使用针对r4的独立窄安装器：配置按已审字段合并，值冲突停止；journal记录前后配置与源码。它不迁移/恢复数据库，不调用模型，不启停服务。

## 删除旧备份与清洁基线

| 清理类别 | 数量/逻辑字节 |
|---|---:|
| 已确认旧备份和清理临时旧副本 | 55,614文件 / 8,094,373,535字节（约7.54GiB） |
| 首次未完成的清理副本 | 1,566文件 / 66,643,896字节 |
| 旧项目worker材料 | 18文件 / 304,477字节 |
| NapCat本地日志缓存 | 63文件 / 13,320,948字节 |

上述合计约 **7.61GiB逻辑字节**；不是磁盘实际释放量或物理安全擦除保证。旧聊天备份已删除，不能再用旧报告中的路径恢复原聊天。代码/镜像及不含旧聊天的配置材料可保留用于回滚。

收尾另删除6,185个废弃/重复的清洁副本文件，共324,921,608逻辑字节（约309.9MiB），包括旧清洁ZIP、两轮隔离候选/恢复目录、原生迁移临时目录和空壳修复前副本。`obsolete-copies-removal.json`确认未触碰运行数据、未重启服务；这些副本不再作为可用恢复入口。

已保留新的受保护清洁部署基线：

- `/opt/qq-ai-companion/releases/phase3-20261024-fresh-start-r1/deployed-clean-baseline-r2.zip`
- SHA256：`d9f1a0c06acf4b273d2c698338ed15481b952064769c8a4ea21493da2685d8a4`
- 清理及隔离恢复收据：同发布目录下 `operation-r2/`，现仅保留证据元数据，候选/恢复数据副本已删除。
- 代码配置journal：同发布目录下 `code-config-journal/`。

原`deployed-clean-baseline.zip`的SHA256为`e499f4f15ad7e1be2af5734c562a43aa19fdebabae637432b207d38e147f09ff`；该副本存在下述Private空壳兼容缺陷，现已废弃并删除。旧SHA仅作历史标识，恢复只使用上述r2清洁基线；代码配置journal、元数据收据及源码/工具保留，不恢复旧聊天。

**QQ客户端边界：** 保留QQ登录态所需的NTQQ目录及未能可靠分离的加密数据库，包括`nt_msg.db`和`buddy_msg_fts.db`。固定NapCat的`clean_cache`只清媒体/临时文件，不清文字消息库；`delete_msg`属于撤回消息，不用于本地清理。因此，本次完成AI应用层的全新开始，**不声称QQ客户端、腾讯云端或用户其他设备上的历史已全部删除**。

## 验证与失败记录

新增 **68项自动验证通过**：Memory清理13、聊天清理22、统一安装/回滚25、清理控制器8。聊天清理由19项增加3项原生空壳结构兼容回归。覆盖保留获准资料、旧来源不重入、撤销/额度保留、新聊天写入、窄替换、完整性、隔离恢复、代码回滚及失败保护；未新增真实DeepSeek或QQ实验。

本次首轮prepare因原生迁移outbox尚未完成被拒绝；核对既有原生协调器后重做候选，`prepare-r2`通过。首次原生capability迁移因文件权限不符合要求被拒绝，修正受保护权限后`install-r2`通过。保留两次失败收据，未删除检查或放宽授权。

首次启动后，容器健康、QQ自动登录和OneBot已恢复，启动策略也PASS；但原生ScopedStore投影持续报告`private_memory_store_invalid`，因此没有以这些健康信号结束验证。根因是清理器将两个Private位置的`_req041_private_memory`设为`{}`，该空壳不满足固定原生schema，导致投影worker反复拒绝。

修复将两处替换为合法、无旧记录的schema/records空结构，补齐3项回归并以独立窄修复器应用。只短暂停止/启动AstrBot，NapCat保持运行；不再调用整套聊天清理，不删除发布后新聊天，不改人格/获准资料/权限，未绕过启动策略。`private-root-repair.json`确认`APPLIED`、2文件、`no_chat_deletion=true`、`napcat_not_restarted=true`，14库检查和新基线隔离恢复PASS；AstrBot启动返回码0，修复后最终观察的Traceback、ERROR、`private_memory_store_invalid`和Runtime延迟激活错误均为0。

脱敏证据目录：`evidence/phase3/fresh-start-release-20261024/`。包括`prepare.json`、`prepare-r2.json`、`install.json`、`install-r2.json`、`purge.json`、`poststart-native-error.json`、`private-root-repair.json`、`activation-after-repair.json`、`obsolete-copies-removal.json`及模块验证记录；私有数据和清洁部署ZIP不进入Git。

P01–P36继续为 **31/36限定层级PASS＋5项用户豁免**；Search S01–S26为 **26/26限定层级PASS**。本次T1/T5和生产安装证据不升级为新的T3/T4/T6，也不证明V3.1自然度、通用搜索的真实体验或长期主动已验收。

## 启动与运行核对

**PASS，采样时间2026-10-24 05:10:10.255980 +08:00。** 证据为`activation-after-repair.json`。检查为只读状态/数据库检查，没有要求扫码、发送QQ或调用模型。

| 项目 | 实际核对 |
|---|---|
| AstrBot | running/healthy；最近启动05:09:13 +08:00；启动后新写readiness PASS、policy_ready日志存在；OneBot established连接1。 |
| NapCat / QQ | running/healthy；自05:01:19 +08:00保持运行，修复没有重启NapCat；isLogin/coreReady=true、isOffline=false、loginPhase=ready，自动登录成功。 |
| 错误与重启 | 两容器OOM=false、restart_count=0。修复后Traceback、ERROR、private_memory_store_invalid均0，Runtime延迟激活错误0。受控stop/start不计为自动restart_count。 |
| 保留资料 | 获准15条L1/L2完整行和导入ledger哈希一致，L1=3/L2=12；没有新增导入。 |
| 历史清理 | AstrBot conversations、Recorder消息、Memory timeline、Runtime sources/jobs/events均0；Guard入站/出站/delivery残留正文均0。保留无正文去重状态、11条历史摘要调用预算记录，不能据此误判仍保留11段旧摘要正文。 |
| 模型与人格 | V0.2标识；deepseek-flash、Thinking disabled、输出8192、超时120秒、40次/分钟、Agent10步。 |
| 压缩与气泡 | max_turns=-1、llm_compress、128000预算、失败80轮；V3.1兜底开、间隔1–2秒、全局segmented_reply关闭。 |
| 搜索 | general_public，主题/域名名单均空，3次/轮、60次/日；历史预算保持，本日工具尝试0。 |
| 主动 | Runtime/Guard配置开启，continuous、每日5次尝试/5次评估、10800秒间隔、静默00:00–09:00；采样时QUIET_HOURS、未人工暂停、今日评估/发送/确认气泡均0。 |
| 管理端口 | AstrBot6185及NapCat6099仍只绑定127.0.0.1。 |

配置和持久账本读数不等同对运行对象逐字段内省；本次同时核对启动后readiness与policy就绪、协议连接及无对应激活错误。静默时段没有主动发送，不能将本次启动核对记为真实主动私聊或自然度PASS。

实际资源（不是配置限额）：主机2逻辑CPU，0.2秒短采样CPU0%，load average为0.146/0.195/0.174；RAM使用1,299,894,272 / 总3,904,782,336字节，磁盘可用44,453,822,464字节（约41.40GiB）。Docker实际AstrBot使用371,091,046字节（约353.9MiB）、CPU0%；NapCat191,574,835字节（约182.7MiB）、CPU0.43%。一次采样不等于长期稳定性验收。

## 回滚入口

先停止AstrBot并确认无写入，再以当前安装journal进行代码/配置回滚：

```bash
release_root=/opt/qq-ai-companion/releases/phase3-20261024-fresh-start-r1
sudo -n python3 "$release_root/controller/phase3-apply-unified.py" \
  --deployment /opt/qq-ai-companion/deployment \
  --journal "$release_root/code-config-journal" --rollback --apply
```

去掉`--apply`只生成回滚计划。新值发生漂移时停止，不能覆盖后续修改。此回滚只恢复18个代码/配置目标，不恢复旧聊天，不覆盖数据；清理后新增聊天继续保留。清洁整库基线的恢复是单独的数据操作，恢复前仍需保存后来新增的数据，不能直接用ZIP覆盖整个生产data。
