# V0.3 Candidate 人格替换（2026-10-26）

**已同步、部署并生效。** 用户明确授权替换、同步、部署和重启，沿用已确认的 AstrBot 单服务范围。北京时间2026-10-26 **11:53:50 +08:00** 启动，**11:54:44 +08:00** 健康核验PASS；NapCat未重启。

## 内容与安装范围

新增 `persona/phase3-v0.3-candidate.md`，保留用户给出的 `[QQ_COMPANION_PERSONA_V0_3_CANDIDATE]` 标记和全文。6处称呼由既有私有配置渲染；生产渲染正文703字符，SHA256为 `7b2535ec77ce6ab449d60dfe0c1a453f6850c34e81b79001d4600848c7402841`。不追加新的性格约束。原V0.1/V0.2/V0.3模板保留供历史复现与回退。

运行目录只替换 `data/plugins/phase3_runtime/persona.py` 与 `data/phase3/persona-v0.1.md` 两个文件；后者继续作为固定加载槽。由原生启动策略更新同一个 `qq_companion` 记录，不清空会话或修改长期记忆。V4分段、Thinking Off、主动48次评估/8轮发送/2小时冷却/09:00–23:00、Search/Fetch 3/60及上下文压缩配置保持。

## 验证

- 人格渲染、staging和打包检查42/42 PASS。
- 新发布/源码回滚控制器21/21 PASS；旧发布历史夹具21/21 PASS。
- 本地Docker未启动，容器联调未运行；改用固定依赖虚拟环境、独立合成状态和本机模拟HTTP/OneBot进行完整进程检查，23/23 PASS；两份安装文件与完整进程受测副本逐一同SHA。
- 停写备份、隔离恢复通过，14个SQLite完整；其他源码、8份配置、Compose、镜像环境及NapCat容器身份均保持。
- 原生人格记录ID仍为2，正文与批准文本逐字符一致；tools、skills、begin_dialogs未变。QQ在线，Reverse WebSocket已重连；启动错误采样为0。
- 11:54:03第一次采样仍处于启动阶段，保留为`health-starting.json`；启动策略完成后所有检查通过，不覆盖早期采样。
- 本次不调用真实DeepSeek，不发送QQ测试消息；不将加载成功视为用户自然度评价。

## 发布与回滚

发布目录：`/opt/qq-ai-companion/releases/phase3-20261026-persona-candidate-r1/`。使用 `scripts/phase3-deploy-persona-candidate.py`，严格核对上一版本源码、配置及镜像，等待无活跃生成/发送；只停AstrBot，完成一致性备份与隔离恢复检查后替换两文件并启动。

备份目录：`/opt/qq-ai-companion/deployment/backups/phase3-persona-candidate-20261026-r1/`。旧两文件及权限保存在发布目录 `astrbot-code-journal/`。需要回滚时用该发布目录内冻结脚本，`--source`指定该目录、另取新的`--run-id`、加`--rollback`审查计划后按授权`--apply`。会再次备份最新数据，只恢复两文件，通过正常启动切回V0.3；不恢复旧数据库，不丢弃新聊天。

私有证据目录：`evidence/phase3/persona-candidate-release-20261026/`。无自动Git提交或推送。
