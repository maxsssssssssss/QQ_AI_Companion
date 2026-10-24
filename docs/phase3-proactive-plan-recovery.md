# 主动空计划恢复（2026-10-24）

状态：**修复与发布核验 PASS**。自动验证通过后已上线，**仅重启 AstrBot 一次**；NapCat 登录态、现有聊天和记忆保持。原生已自动补建候选；这不等于真实主动文风或长期体验验收。

## 根因与修复

11:35 与 11:42 的生产只读检查确认：QQ 在线，原生调度循环持续执行，主动开关及单一目标能力已开启，处于允许时段，无暂停/忙碌，评估和发送尝试均为 0；候选和下一次主动时间为空。

固定 PrivateCompanion 6.7.0 在磁盘 `max_daily_messages=0`、生成关闭的安全配置下初始化，会清除主动计划。第一方桥接随后只启用实例属性，没有重新初始化计划；`tick_user` 的前置 Gate 把没有有效期的空计划判为不可执行，原生 `_should_send` 的补排程入口因此无法到达。这是初始化与门禁的顺序缺口，不是模型作出的 skip 决定。

现在在既有 `scheduler_cycle` 内、原生用户 tick 前维护计划：先核对当前授权、唯一验证身份、目标原生能力、时段、暂停/忙碌、发送及评估额度，再持有原生 `_data_lock` 调用 `_schedule_next_proactive`。没有新增定时器、模型或事实库，没有修改固定上游源码。

有效的未来计划与模型 defer 保持；过期、终态或已有生成/发送消耗证据的旧计划先原生终结，再重新评估下一次机会。旧候选及发送账本不会清除，不恢复已发送、失败、unknown 或 cancelled 的正文。维护本身不调用模型、不发送、不消耗额度，也不把下一次时间强行设为立即。模型与发送仍各自经过原有 Gate 和 Guard。

`proactive_runtime.phase3_plan_maintenance` 保存固定原因枚举、检查/考虑时间及修复/终结/错误次数。不存在候选与发生错误可以区分；不写话题、聊天内容、账号或凭据。

完整原生联调又发现：随机候选允许 `context=None`，原来的 `setdefault` 不会把这个空值转成字典，导致 defer/skip 元数据与送达统计漏写。只对缺失或 None 的原生元数据槽初始化字典，其他非法类型不覆盖。合法 defer 保留原有效期；超出有效期仍转为 skip，部分成功/unknown 仍如实记录，不以该修复重发。

## 保持的运行规则

- 长期模式；北京时间每日 09:00–23:59。
- 每天最多 5 次发送尝试，至少间隔 3 小时；失败或 unknown 仍计数。
- 每天最多 5 次模型评估，skip/defer/失败仍计数；重启不重置账本。
- 唯一已授权主 QQ、普通文本、同一 DeepSeek 与 Guard 出口；原有话题冷却、暂停和忙碌有效。
- 原生磁盘配置仍关闭生成，只在桥接完成后启用实例；被动对话不受主动额度限制。

## 验证与发布证据

私有证据目录：`evidence/phase3/proactive-plan-recovery-20261024/`。只保存脱敏状态、合成测试和哈希；生产正文及凭据不进入公共报告。

组件与发布测试 **137/137 PASS，0失败、0跳过**：恢复29、固定原生主动35、长期规则11、Runtime27、Guard发送20、窄发布15。发布测试实际执行临时ZIP/SQLite备份及恢复，Docker控制使用夹具；覆盖替换后异常、启动失败、NapCat身份变化、活动轮次、配置/源码漂移、回滚保留新聊天。完整结果汇总为`components-final.json`，重复运行不重复计数。

完整固定框架联合回归 **23/23 PASS**，证据`evidence/phase3/20261024T035111Z-78442/`。其中冷启动 send/skip/defer 三案 **42/42 检查 PASS**：从关闭生成的原生初始化开始，不预置候选、不替换 `_tick_user` 或 `_should_send`，执行原生排程、模型 HTTP 和 Guard，并验证持久化重开不重放。I11共6次合成主动HTTP（冷启动三案占3次），两次send合计4条Guard确认气泡；工具集为空。时钟、模型与OneBot都是隔离环境，不计真实QQ或DeepSeek体验。整个联合过程还覆盖聊天、图片、记忆、撤销、搜索、多气泡及重启恢复。

全程合成transcript中的第7条`proactive_defer`标签发生在I11之后，是旧Mock宽泛标记匹配误分类的普通被动请求；不计入主动评估，汇总已区分。这个夹具标签限制不影响生产代码。

保留前三轮完整探针失败：`20261024T034344Z-74774`为测试夹具深拷贝含锁配置失败；`20261024T034517Z-76010`及`20261024T034711Z-76753`揭示真实原生空`context`兼容缺陷。最初将defer失败怀疑为有效期不足，增加实际入参观察后否定该猜测并修复根因；没有放宽生产有效期或把失败改成通过。

## 发布与回滚

`scripts/phase3-deploy-proactive-recovery.py` 默认只输出计划；清单锁定当前配置、其他第一方源码、固定第三方插件以及两张生产镜像。只替换 `data/plugins/phase3_runtime/proactive.py`。停止 AstrBot 后创建一致性快照并在隔离目录恢复、检查 SQLite，通过后才替换代码并启动同一 AstrBot 容器。NapCat 的容器身份、启动时间和重启计数必须保持。

回滚使用同一发布包的 `--rollback --apply`，恢复单个旧源码；会先备份当时最新状态，不覆盖任何生产数据库或配置，不丢弃上线后新聊天。只有确有需要时才执行回滚。

回滚入口（本次未执行）：

```sh
sudo -n python3 /opt/qq-ai-companion/releases/phase3-20261024-proactive-recovery-r1/scripts/phase3-deploy-proactive-recovery.py --source /opt/qq-ai-companion/releases/phase3-20261024-proactive-recovery-r1 --run-id phase3-proactive-recovery-rollback-r1 --rollback --apply
```

修复排程不意味着每天一定发送或自然度已经验收；原生候选机会、模型 send/defer/skip 和实际平台回执分别记录。

## 实际安装

发布包：`/opt/qq-ai-companion/releases/phase3-20261024-proactive-recovery-r1/`。11:56:14 +08:00同一AstrBot容器重新启动；部署回执为`INSTALLED_AWAITING_HEALTH`，一致性备份与隔离恢复PASS，检查14个SQLite库。NapCat容器身份、启动时间与重启计数保持，QQ仍在线。源码SHA256为`970500a2b40f4e0315a24d78f712916570a840f154b64a7ef5d6110da744505c`。

本次备份位于`/opt/qq-ai-companion/deployment/backups/phase3-proactive-recovery-20261024-r1/astrbot-state.zip`，原源码位于发布包`astrbot-code-journal/`。不会以该备份自动覆盖生产数据。

11:57:24 +08:00启动策略PASS、OneBot已建立连接；启动以来Traceback/ERROR/原生循环错误均0。完整配置、Compose、环境文件摘要保持，50份第一方源码仅`proactive.py`发生变化；记忆汇总保持15条获准资料。原生`check_interval_seconds=300`保持原值，等待自然检查，不强制调用调度或发送。这个值是后台常规检查周期；原生最近到期任务可使检查提前，不会让被动聊天等待五分钟。

**12:01:52 +08:00最终核验PASS**：原生在11:59:30自动执行补排程，候选从0变为1，`repair_count=1/error_count=0`；下一次考虑时间为**2026-10-24 13:11:06 +08:00**，不是承诺发送时间。届时仍需原生资格、额度与模型决策通过。两容器healthy，QQ在线、OneBot ESTABLISHED=1，启动错误计数均0，配置/档案/记忆汇总均保持。今日模型评估0、主动尝试0；没有手动发送验收消息或额外真实模型诊断费用。

当次实测：AstrBot内存约350.3MiB、CPU0.03%；NapCat约190.9MiB、CPU0.40%；磁盘可用约41.6GiB。这是瞬时采样，不是资源上限或长期性能结论。脱敏核验在`release-verification.json`，全量安全状态在`scheduler-confirmed.json`。
