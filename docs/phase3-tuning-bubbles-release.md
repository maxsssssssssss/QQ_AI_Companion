# 主动参数与多气泡清理联合发布（2026-10-25）

状态：**已生效，18:35:21 +08:00 启动核验 PASS**。用户明确授权两个待发布候选一起启用；18:34:21 +08:00只重启同一 AstrBot 容器一次，NapCat身份、启动时间和登录态保持。启动、配置和连接检查不能替代新增真实QQ自然度验收。

## 实际生效范围

| 项目 | 当前生效值 |
|---|---|
| 主动对话摘录 | 最近80条；最近8条正文各260字符，其余最多72条沿用各160字符截取；总预算16000字符 |
| 主动发送摘要 | 最近8条，各80字符；普通摘要保留24小时；原生话题去重仍240分钟 |
| 主动模型评估 | 每日最多48次；skip/defer及失败继续计数，无新增硬评估间隔 |
| 主动发送尝试 | 每日最多8轮，每轮可多泡；间隔至少7200秒 |
| 每日时段 | 北京时间09:00:00 ≤ t < 23:00:00，长期模式无72小时到期 |
| 多气泡修复 | 兼容原生删除行尾ASCII空白，以及与末尾中文句号删除组合；保留首次失败原因 |

DeepSeek继续自主选择send/defer/skip及正文。V0.2、Thinking Off、有效模型标记优先、V3.1规则、1–2秒气泡间隔、Guard确认/取消/归档不变。普通聊天、记忆、Search预算与权限未扩大。原生磁盘生成仍关闭/全局日限0，由已获准的Runtime实例接管；唯一原生授权目标日限由5改8，`-1`间隔覆盖值继续继承120分钟。

运行目标：Guard的`bubbles.py/main.py`，Runtime的`main.py/settings.py/proactive.py/proactive_history.py/_conf_schema.json`，共7份（新增history模块）。只窄改Runtime与Private两份配置中的批准字段和原有唯一目标的`proactive_daily_limit`，不重写整个生产data。51份第一方源库存核对通过，其余源码、全部非目标配置、Compose、镜像及端口保持。

## 发布验证与保留证据

- 复用主动候选174项回归及完整AstrBot合成联调、多气泡修复158项回归和两例原始哈希离线复现；不把重复范围相加为新增测试。
- 新窄发布控制器最终24/24测试PASS：真实ZIP/SQLite、模拟Docker，覆盖新增文件、配置/native单字段、漂移、备份失败、部分安装/启动失败恢复及回滚保留新增聊天/状态。
- 真实停写备份、隔离恢复及14个SQLite完整性校验PASS，恢复副本未启动。只有该检查通过后才安装。
- 第一次r1只读PLAN发现安装器误从Runtime配置读取授权身份，停服前安全拒绝。已按实际启动链改为Phase2Policy身份，与Guard、唯一管理员、私聊白名单及原生既有能力交叉核对。失败包与证据保留；r2在相同冻结源码下通过24项检查和生产PLAN。
- 18:34:32第一次观察尚为启动中，旧readiness早于本次启动、OneBot连接为0，不记成功。18:35:21新readiness已写入、策略PASS、两容器healthy、QQ在线、OneBot连接1；启动Traceback/ERROR/原生主动循环异常均0。
- 当天主动账本仍为5次评估（1 generated、4 skipped）、1轮已发送、2泡确认，无pending/failed/unknown。发布未重置额度，剩余额度不是发送目标。已有候选和暂停/回应状态保留。

当次实际资源采样：AstrBot内存约353MiB、CPU 0%；NapCat约212MiB、CPU 0.38%；主机可用内存约2.42GiB、磁盘空闲约41.2GiB。为单次采样，不是配置限额或长期负载结论。

控制器没有主动调用模型或发送测试QQ；服务恢复后按授权规则正常运行。新参数和修复后的本人自然度仍只能由后续实际聊天评价，不因本次启动成功自动升级P/S验收矩阵。

私有证据：`evidence/phase3/tuning-bubbles-release-20261025/`中的`release-tests-r5.json`、`stage-r2.json`、`plan-r2.json`、`apply-r2.json`、`after-start-r2.json`、`after-ready-r2.json`、`verification-r2.json`。公共报告不包含账号、密钥或私人正文。

## 发布包、备份与回滚

运行发布包：`/opt/qq-ai-companion/releases/phase3-20261025-tuning-bubbles-r2/`；manifest SHA256为`8d17e60db458e57c20b12219943a9eaa71f48cbecdf61f6d2caf84264fdfef5f`。两个原待发布目录及r1失败目录保留，不能通过再次复制旧候选覆盖当前配置。

一致快照：`/opt/qq-ai-companion/deployment/backups/phase3-tuning-bubbles-20261025-r2/astrbot-state.zip`；旧代码和受影响配置字段在发布包`release-journal/`。快照及journal含私密资料，仅留服务器受保护目录。

回滚入口（本次未执行）：

```sh
sudo -n python3 /opt/qq-ai-companion/releases/phase3-20261025-tuning-bubbles-r2/scripts/phase3-deploy-tuning-and-bubbles.py --source /opt/qq-ai-companion/releases/phase3-20261025-tuning-bubbles-r2 --run-id phase3-tuning-bubbles-rollback-20261025-r2 --rollback --apply
```

回滚会先备份届时最新数据，只还原这7份源码及本次拥有的配置字段/目标日限，保留新聊天、账本、其他配置和原生状态，NapCat继续运行。已被后续修改的源码或受影响字段会阻止回滚，需要重新核对；不会静默恢复整份数据库或把今日超限计数清零。
