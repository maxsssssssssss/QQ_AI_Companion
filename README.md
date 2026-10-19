# QQ AI Companion

基于 AstrBot 的独立集成项目。当前工作范围是 **Phase 1：基础系统集成与离线端到端验收**，使用合成 OneBot v11 事件和 Mock LLM，在断网容器内运行真实 AstrBot、原生适配器、模型 provider、数据库和所选插件。

最终 P0 判定、逐项证据与剩余限制见 [Phase 1 验收报告](docs/phase1-validation.md)和[最终交付报告](docs/delivery-report.md)。启动成功、doctor 通过或单个测试返回零，都不能代替全部 P0 验收结论。

完整任务书的执行索引见 [Phase 1 范围](docs/phase1-scope.md)：只有 T01–T20 全部为 PASS 才能标记 Phase 1 PASS；P1 的已验证、受限和未运行项目另行记录。

## 固定版本与复用边界

- AstrBot：`v4.29.1`，提交 `dffe922773725e9c178392c423676ad65044748c`，原始源码保持不变。
- 运行环境：Python 3.12，`linux/amd64`；基础镜像以 digest 固定，Python 依赖以版本和制品 SHA-256 固定。
- 第三方候选：Message Recorder、MemoryCompanion、PrivateCompanion；仓库、固定提交、兼容范围和源码摘要见 [plugins-lock.json](config/plugins-lock.json)。
- 本项目只补充配置、启动/验收脚本、备份工具和一个轻量 `phase1_guard` 插件。插件通过 AstrBot 公开 API 补足私聊去重、双向原文归档及发送结果记录。

第三方插件仅下载到忽略跟踪的 `.runtime/upstream/` 做本地原样评估，不进入本项目 Git、构建镜像或发布包。Message Recorder 的许可证声明存在冲突；MemoryCompanion 与 PrivateCompanion 未找到项目级许可证。详细核查与使用边界见 [依赖与许可证](docs/dependencies.md)、[兼容性](docs/compatibility.md)和[插件说明](docs/plugins.md)。

普通 QQ 的后续协议接入优先 NapCat、SnowLuma 作为备选，本轮仅核查上游，未安装登录。任务指定的 `deepseek-flash` / `deepseek-v4-pro` 当前官方可用性尚未核实；离线测试使用 `phase1-mock`，不把 Mock 成功当成真实 DeepSeek 调用证明。

## 复现离线验收

宿主需要 Linux、Bash、Git、Python 3.12 和可访问 `/var/run/docker.sock` 的 Docker/BuildKit。使用普通非 root 用户执行；测试容器使用调用者 UID/GID，验收会检查非 root。首次构建和下载源码需要访问 GitHub、Python 包索引及基础镜像仓库；构建保留 TLS、依赖哈希和源码摘要校验。

在仓库根目录执行：

```bash
bash scripts/bootstrap.sh
python3 scripts/fetch_plugins.py
bash scripts/doctor.sh
python3 scripts/check-repro.py
bash scripts/test-phase1.sh
```

`bootstrap.sh` 获取锁定的 AstrBot 提交并构建 `qq-ai-companion:phase1`。已有上游 checkout 若版本不符或存在本地改动，脚本会拒绝覆盖。构建时只使用最小临时上下文；第三方插件、运行数据、Git 元数据和凭据不加入镜像。

`fetch_plugins.py` 获取并校验三个候选插件的原始固定提交。下载不代表获得再分发授权；缺少所需网络、源码或许可条件时，应保留明确的阻塞状态。

`doctor.sh` 是只读预检，检查配置、版本锁、路径、时区、Docker 镜像、公开端口和常见凭据误跟踪迹象；它不启动服务、不修复配置，也不代替功能验收。

`test-phase1.sh` 是总验收入口。真实消息链路由 `scripts/test-offline.sh` 在 `--network none` 容器中执行；运行器启动真实 AstrBot 子进程和 loopback 模拟对端，完成检查后关闭服务。它不是持续在线的 QQ 服务，不需要真实 QQ 登录或 LLM 密钥。每次集成运行使用独立状态目录，既有运行数据保留。

可单独定位功能：

```bash
python3 scripts/configuration.py config/offline.json
python3 -m unittest discover -s tests -p test_configuration.py -v
python3 -m unittest discover -s tests -p test_guard.py -v
python3 -m unittest discover -s tests -p test_backup.py -v
bash scripts/test-offline.sh
bash scripts/test-future-tasks.sh
```

新克隆不会包含本地验收证据，必须先运行 `python3 scripts/check-repro.py` 生成 T01 所需的重建记录，再运行总验收。修改执行代码或配置后，先保存本地提交，再重新生成复现证据，最后运行受影响的验收。

原生调度器测试依赖镜像中的 AstrBot；运行它应使用 `test-future-tasks.sh`。其余以上单元测试使用宿主 Python 标准库。不要把存储或配置单元测试当成真实协议集成验收。

## 配置、人格与数据

主要配置为 [config/offline.json](config/offline.json)。启动前进行字段检查，离线 profile 只接受 loopback OneBot/LLM 地址、明确的测试凭据、合成身份白名单和 `Asia/Shanghai` 时区。`.env.example` 不包含真实密钥；脚本不会自动加载其中变量。

人格模板位于 [persona/core_persona.md](persona/core_persona.md) 和 [persona/relationship.md](persona/relationship.md)。入口实际读取 `<ASTRBOT_ROOT>/persona.md`，再通过 AstrBot persona API 更新 system prompt；需要使用项目模板时，将所需模板内容放入该文件。资料示例 [user_profile.example.md](persona/user_profile.example.md) 是合成说明，不会自动成为用户事实。

集成验收有意使用带 `PERSONA_ALPHA` / `PERSONA_BETA` 标记的外置合成人格，以真实模型请求验证修改和重启生效。Mock 回复只能验证请求链路，不能评价人格自然度、亲密关系表现或模型能力。

| 路径 | 用途 |
| --- | --- |
| `config/`、`lockfiles/` | 可审阅配置、固定上游版本和依赖哈希 |
| `integrations/phase1_guard/` | 本项目轻量公开 API 插件 |
| `.runtime/upstream/` | 忽略跟踪的原始上游 checkout |
| `.runtime/runs/<run-id>/` | 每次运行的真实数据、配置及插件状态 |
| `evidence/runs/<run-id>/` | 本次命令输出、协议记录、启动信息、结果 JSON 和摘要 |
| `.runtime/latest-evidence` | 最近一次消息集成验收的证据目录 |
| `evidence/future-tasks/` | 原生任务调度行为的独立检查证据 |
| `evidence/phase1/latest/` | 最终交付选择的合成证据与机器可读验收汇总 |
| `backups/` | 本地备份；忽略跟踪 |

状态目录中的 `data/data_v4.db` 属于真实 AstrBot；`data/plugin_data/` 保存各插件自己的 SQLite 或 JSON；`data/config/` 保存本次插件配置。Git 只保留源码、配置、依赖锁、测试和必要文档；整个 `evidence/`、运行数据、日志、备份及第三方源码均不上传。验收证据由上述命令在本地生成；历史报告中的证据路径指向原开发工作区，克隆仓库后需自行重跑。报告采用哪次运行的证据，以验收报告列出的路径和摘要为准，不能混用旧运行结果。

## 停机备份和恢复

先停止 AstrBot 和所有会写入同一状态目录的进程，再备份整个 `ASTRBOT_ROOT`。集成运行器返回后已关闭其启动的服务；独立手动进程需要调用者自行停止。以下 `<run-id>` 替换为实际运行 ID：

```bash
state_path=".runtime/runs/<run-id>/base"
python3 scripts/backup.py create "$state_path" backups/phase1-state.zip
python3 scripts/backup.py restore backups/phase1-state.zip .runtime/restored-phase1
```

格式是 ZIP，包含 `manifest.json`、逐文件 SHA-256、配置、完整数据与插件状态。备份与恢复都会检查 SQLite 完整性，并处理已提交的 WAL 数据。归档必须位于状态目录之外且尚不存在；恢复目标必须完全不存在，包括不能预先创建空目录。

恢复会拒绝校验和不符、路径穿越、链接、缺失/多余文件和损坏 SQLite。SHA-256 证明完整性，不证明归档发布者身份；仅恢复可信来源的本地备份。备份不是内容脱敏工具，第三方插件源码与数据仍受原许可和本地评估边界约束，不应随备份公开发布。

停机后的合成聊天数据也可以单独导出为 JSON：

```bash
python3 scripts/export_data.py "$state_path" backups/phase1-synthetic-export.json
```

导出只选择已知聊天数据表和字段，保留原始时间语义，省略配置和命名秘密字段；输出文件不得已存在。聊天文本保留原文，工具不保证扫描掉文本中的秘密，因此只用于本轮合成状态。JSON 导出不包含完整可恢复状态，不能代替 ZIP 备份。

## 本轮范围

真实基础链路、持久化、进程重启、备份恢复、配置和隔离通过实际测试取证；三个插件按真实加载、命令、数据和上下文注入分别记录。P1 兼容性或授权问题单独列明，不由替身插件冒充完成。

AstrBot 上游 WebUI 仅作来源与能力核查，当前入口是 headless，仪表盘关闭，没有实际 UI 验收。原生未来任务的调度、落盘和重启行为有独立检查；其中一次性定时任务失败后会被上游删除，不能称为可靠重试或生产补发。

不登录真实 QQ、不部署 VPS、不使用真实用户隐私、不发布授权不明的第三方源码。本轮不进入 Phase 2。更多设计和边界见 [架构](docs/architecture.md)及[安全说明](docs/security.md)。
