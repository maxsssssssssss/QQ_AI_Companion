# V4多气泡＋V0.3人格联合发布（2026-10-26）

**已同步、已部署并生效。** 用户明确选择只重启AstrBot；未重启整台VPS或NapCat。AstrBot于北京时间 **2026-10-26 00:56:15 +08:00** 启动，00:56:45启动核验全部通过。

## 发布范围与保护

发布目录：`/opt/qq-ai-companion/releases/phase3-20261026-bubbles-v4-persona-v03-r1/`。运行目录仅替换4个文件：

| 安装位置（相对deployment/state） | 内容 |
|---|---|
| `data/plugins/phase1_guard/bubbles.py` | V4积极句末分段、原生模型标记优先 |
| `data/plugins/phase1_guard/main.py` | 最终放行正文兜底、保留Guard发送与历史链路 |
| `data/plugins/phase3_runtime/persona.py` | V0.3标记与四处称呼校验，兼容V0.1/V0.2 |
| `data/phase3/persona-v0.1.md` | 用户批准的V0.3全文；保留既有兼容文件槽名称 |

预检确认51个第一方源码文件与上次通用搜索修复发布完全一致，无未知漂移；安装仅改变上述3个插件源码及1个模板。其他源码、全部8份配置、Compose、镜像环境、固定上游插件均核验保持。保留聊天、记忆、登录态、额度和发送账本，未覆盖data目录或重导L1/L2。

Thinking Off、原分段提示词、1–2秒间隔、主动48次评估/8轮发送/2小时冷却/北京时间09:00–23:00、公开Search/Fetch每轮3次/每天60次保持。

## 验证结果

| 检查 | 结果与范围 |
|---|---|
| V4实现与回放 | 264项组件/原生回归通过；42条旧真实合成回复对比见[V4报告](phase3-multibubble-v4-validation.md)，不是新增真实模型/QQ验收 |
| V0.3本地接入 | 42项渲染、staging及打包检查通过，见[人格接入](persona-integration.md) |
| 本次最终组合 | V4＋V0.3完整固定AstrBot进程23/23 PASS，网络隔离、纯合成数据；安装的4文件与隔离副本逐一同SHA |
| 窄发布/回滚控制器 | 21/21 PASS，真实临时ZIP/SQLite＋模拟Docker；含失败恢复、数据保留和NapCat不重启 |
| 停写备份及隔离恢复 | PASS，14个SQLite完整；恢复副本未启动QQ或AstrBot |
| 原生人格 | `qq_companion`仍为同一记录，精确匹配V0.3渲染正文；工具、skills和begin_dialogs字段哈希不变 |
| 新启动策略与连接 | policy/readiness PASS且在本次启动后生成；QQ在线，Reverse WebSocket已连接 |
| 服务与端口 | 同一AstrBot容器启动；NapCat容器ID、镜像、启动时间、重启计数均未变；WebUI端口保持127.0.0.1 |
| 新启动异常采样 | Traceback、ERROR、private_memory_store_invalid、主动循环异常、未处理Task异常均0 |

发布核验没有调用真实模型、发QQ消息或重新登录。原生人格数据库由既有启动策略更新，不由发布器直接写数据库；不把启动成功判成新的人格自然度或真实QQ多气泡体验通过。

00:56:45的实际资源采样：AstrBot约357.4MiB、CPU0.00%；NapCat约214.7MiB、CPU0.43%；主机可用内存约2.43GiB，磁盘空闲约40.81GiB。这是短时测量，不是配置限额或长期稳定性结论。

## 备份、回退与证据

一致性备份：`/opt/qq-ai-companion/deployment/backups/phase3-bubbles-v4-persona-v03-20261026-r1/astrbot-state.zip`，同目录有隔离恢复副本及完整性回执。

四文件原件：发布目录中的 `astrbot-code-journal/original/`，路径和权限见 `journal.json`。需要完整回退本次代码/人格时，使用发布目录中已冻结的 `scripts/phase3-deploy-bubbles-v4-persona.py`，指定该release为`--source`、一个新的`--run-id`及`--rollback`先审查PLAN，再按授权使用`--apply`。控制器仍只停启AstrBot，先备份当前数据，再恢复4个文件，由正常启动策略切回原人格；不恢复聊天数据库，不丢弃切换后新聊天。关闭既有fallback开关只能停用自动兜底，保留模型标记；不是本次完整回退的替代。

Source manifest SHA256：`b9b3e29ea58c04db0c3e656f1116f67a129a1b6dd3b8271d0f00ef29d452c8ca`。

本地证据：`evidence/phase3/bubbles-v4-persona-release-20261026/`中的`preflight.json`、`pre-release-validation.json`、`controller-r1/summary.json`、`stage.json`、`plan.json`、`apply.json`、`health-after-start.json`。本次完整组合证据为`evidence/phase3/20261025T165212Z-46177/result.json`。服务器source/template、文档测试副本与受保护健康回执分别保存在发布目录；没有自动push GitHub。
