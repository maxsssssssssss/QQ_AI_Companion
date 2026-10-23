# Phase 3 只读状态采集

`scripts/phase3-status.py` 复用同目录的 `phase2-status.py`，在 VPS 上由部署操作者运行；不登录 QQ、不调用模型、不启动或重启服务、不迁移数据。默认目录为 `/opt/qq-ai-companion/deployment`，可通过 `--deployment` 指定。读取容器用户所有的 0600 配置时需要现有管理权限。

输出包括主机 CPU 采样、当前 RAM/交换区/磁盘占用、Docker 实际 CPU/内存/PID，以及独立标注的容器配置限额。CPU 核心数和容器配额不充当实际 CPU 使用率。Docker 内存遵循 Linux Docker stats 扣除非活跃文件缓存的口径。

SQLite 以 `mode=ro`、`query_only=ON` 打开，查询有时间上限；拒绝符号链接，不创建缺失数据库。计数覆盖 Guard 消息类型与发送状态、原生 Memory 时间线/摘要/断言、每日搜索尝试、主动窗口/预算/暂停。仅输出固定字段、布尔值、枚举和计数，丢弃身份、正文、凭据、任意 Docker 元数据和原始异常。

配置开关是磁盘配置快照；主动窗口状态由配置与持久账本计算，不声称读取了运行中插件的 Python 属性。原生 summary/proactive 的磁盘开关保持关闭属于正常的启动保护设计。缺少尚未初始化的数据库显示 `NOT_INITIALIZED`。采集结果不能代替真实 QQ 和真实 DeepSeek 验收。

合成测试：`tests/test_phase3_status.py` 6/6 PASS，证据 `evidence/phase3/status-tool-20261022T001743Z/status.json`。测试验证敏感字段过滤、数据库写入被拒、读取前后数据库哈希不变、缺失/符号链接拒绝、实际用量与限额区分、窗口边界；不包含 VPS 实测。
