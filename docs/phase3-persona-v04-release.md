# V0.4 人格替换（2026-10-26）

**已同步、部署并生效。** 用户授权替换、同步、部署及重启，沿用已确认的仅AstrBot重启范围。北京时间2026-10-26 **12:24:27 +08:00** 启动，**12:25:04 +08:00** 健康核验PASS。

本次保留用户给出的标题 `# QQ_COMPANION_PERSONA_V0_4` 和完整正文。8处称呼由原配置渲染，渲染正文1067字符，SHA256 `f49222c184bee07304d3d1d96584c332826d70d685548ff46a0cea94bbeea08d`。没有追加性格或关系要求。兼容V0.1/V0.2/V0.3及Candidate以便历史复现和回退；单一人格校验同时拒绝重复、混合和未知标题。

## 改动及验证

只替换运行目录 `data/plugins/phase3_runtime/persona.py` 与 `data/phase3/persona-v0.1.md`；新源模板为 `persona/phase3-v0.4.md`。固定加载槽保持，启动策略更新同一个 `qq_companion`，不改其他配置、聊天或记忆。

- 模板、staging及打包检查42/42 PASS。
- 新发布及两份历史发布控制器63/63 PASS；历史加载器为精确固定的第一方夹具，不放宽旧发布合同。
- 新增2项真实原生启动策略/PersonaManager/SQLite集成：精确人格替换、权限、实际请求核心注入、数据库重开、原聊天与切换后新聊天保留、源人格回退。2/2 PASS。原生测试首次在受限sandbox的SQLite初始化阶段超时（native-r1）；同一代码通过正常命令审批后15.679秒完成，原证据保留（native-r2）。
- 复用前一版未受影响的聊天、多气泡和恢复证据；前一版完整23项为合成HTTP/OneBot，未将其改写为V0.4新执行。
- 一致性备份及隔离恢复PASS，14个SQLite完整。原生人格ID仍为2，正文逐字符一致，tools/skills/begin_dialogs保持；唯一人格权威。
- 其他源码、8份配置、Compose及镜像环境保持；NapCat容器ID、镜像、启动时间和重启计数不变。QQ在线、Reverse WebSocket已连，启动异常采样为0。
- 本次无真实模型调用或QQ测试发送，不据此评价人格自然度。

## 发布与回滚

服务器目录：`/opt/qq-ai-companion/releases/phase3-20261026-persona-v04-r1/`。

备份目录：`/opt/qq-ai-companion/deployment/backups/phase3-persona-v04-20261026-r1/`。控制器 `scripts/phase3-deploy-persona-v04.py` 默认只给PLAN；正式应用在无活跃生成/发送时停AstrBot，先一致性备份和隔离恢复校验再原子替换两文件并启动。

原两文件及权限保留在发布目录 `astrbot-code-journal/`。需要回滚时使用该目录内冻结控制器，`--source`指定该目录、另取新的`--run-id`并使用`--rollback`，审查PLAN后按授权加`--apply`。重新备份最新数据，仅恢复两文件，由正常启动切回Candidate；不回滚整库或丢弃新聊天。

私有证据：`evidence/phase3/persona-v04-release-20261026/`。本次未自动提交或推送GitHub。
