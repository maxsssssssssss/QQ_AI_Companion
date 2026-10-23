# Phase 3 增量实施与发布边界

当前阶段：用户已集中批准首轮受限上线，V0.1/自然文本/自动记忆/多气泡/受限搜索和精确获准15组L1/L2已部署，继续原 Gate 2–6 的真实验收。完整判定见 [36 项验收](phase3-validation.md)与 [26 项自然搜索验收](natural-agent-validation.md)。资料抽样先空正文、后有回复但不正确/完整；一次有界补答及原生记忆导航指引/部分主题目录已上线，等待本人真实复测；主动权限已批准但条件窗口尚未开启，后台搜索继续关闭。

## 依据与基线

以用户提供的 Phase3 Master Implementation v1.0 为执行规范，其余 Natural Agent 附件、Persona V0.1 和完整访谈为设计依据。实际收到的 Persona 文件名没有 `(1)` 后缀，内容是 V0.1。任务书和访谈原件在本地 Downloads，未复制进 Git。

开发起点为 `b6dfc82abbf388d93e0012fd91a55965e66fc4fe`；Phase 1 提交 `0b6bbf147a83f3e17c598c7a4d49386b6553c823` 保留。没有 reset、覆盖旧进度、推送或升级依赖。Phase 2 原有 139 项测试先作为基线运行。只读 SSH 检查确认生产两容器健康、QQ 已登录、原策略通过，管理映射仍为 `127.0.0.1:6185/6099`。这不是 Phase 3 QQ 验收。

AstrBot 4.29.1、MemoryCompanion 2.3.1、PrivateCompanion 6.7.0、Message Recorder 0.0.4、NapCat 4.18.38 继续锁定，提交及摘要沿用 `config/plugins-lock.json` 和 Phase 2 清单。原上游源码未修改、未收录或打包公开；测试镜像只有锁定 Python 依赖，测试时以只读卷挂入上游。

## 接线与职责

- `phase2_policy` 继续负责白名单、必需插件、唯一 Persona 和启动阻断。仅在显式 Phase 3 配置下开放选定工具；先验证凭据及组件，`prepare` 只接线，待人格持久化后才 `activate` 后台任务。任何启动失败都撤销新任务；已请求 Phase 3 但插件加载失败时，不悄悄降级放行。
- `phase1_guard` 保留旧表和默认路径，增加显式开关下的 turn、source、delivery、progress 状态。所有允许的文本输出必须有独立状态，超时与崩溃视为 unknown，不能盲目重发。
- `phase3_runtime` 绑定单一 V0.1 人格、原生 MemoryCompanion、原生 PrivateCompanion 和既有 Guard，不创建第二个 Agent、记忆主库或聊天平台。
- `phase3_memory` 是原生 MemoryService 的来源、权限、批次、事件状态适配。正文仍由原生库保存；原始消息以 Guard/Recorder 档案为证。持久游标从原文档案增量恢复，首次启用跳过旧 Phase 2 档案；原生库已有的未摘要历史则另以精确清单、内容指纹及模型使用授权核验，缺少授权就阻断自动摘要。
- `phase3_search` 包装当前请求中原生 Search/Fetch 的真实调用边界，完成权限、参数最小化、一次进度、预算和回执检查。沿用原生工具结果与 tool_call_id，不另起搜索 Agent。普通闲聊没有额外搜索分类模型。
- `PrivateCompanion` 仍产生候选与正文，薄层仅负责批准窗口、暂停、生命周期、模型预算和最终受控发送。未批准的动作、群发送、虚构生活、后台联网均关闭。

模块细节：[人格](persona-integration.md)、[资料预览](private-profile-import.md)、[归组与发送](phase3-delivery.md)、[记忆](phase3-memory-validation.md)、[搜索](natural-agent-validation.md)、[主动](phase3-proactive-validation.md)。

## Gate 0–6 的实际含义

| Gate | 本地可自主执行 | 生产放行条件 |
|---|---|---|
| 0 | 基线核对、回归、合成停写备份/恢复及旧表迁移测试 | 批准维护窗口后制作新的生产一致快照，恢复到隔离目录验证 |
| 1 / 3A | 单一人格、真实合成模型场景、私人分层预览 | 确认人格体验；真实资料另按条目/用途授权 |
| 2 / 3B | 真框架合并、取消、语义气泡、逐条回执、迁移 | 白名单下小样本真实 QQ 回归 |
| 3 / 3C | 原生服务抽取、来源、修订、召回、重启与停用测试 | 批准新记忆捕获；初始资料导入独立处理 |
| 4 / 3E | 原生工具安全与合成对端、真实模型选择 | 已批准复用服务器Key的独立搜索配置；限定公开主题、每轮3/每日60尝试及10元估算验收预算 |
| 5 / 3D | 原生候选、暂停与发送边界 | 已批准：被动真实验收后下一个09:00起72小时、每天2次及4小时冷却；按真实通过时间生成绝对窗口 |
| 6 | 隔离联合、恢复、报告 | 已批准配置下 QQ+DeepSeek 联测及生产资源观察 |

并行开发与离线测试不等于跳过上线 Gate。后台定期搜索继续 `NOT_ENABLED`；Phase 4 可靠提醒、Todo、失败补发和长周期成长不在本轮扩大实现。

## 复现与证据

`python3 scripts/phase3-build-test-image.py` 校验既有离线基础镜像和 wheel 锁后构建依赖镜像；`bash scripts/test-phase3-isolated.sh` 在 `--network none`、只读根、无额外权限的容器里跑完整 AstrBot，挂入的状态全部为新建合成状态。无需挂载访谈、生产凭据或 QQ 登录态。

原生组件测试需要 `.runtime/phase3-venv`、`PYTHONPATH=.runtime/upstream/AstrBot:.` 和独立 `ASTRBOT_ROOT`。Windows/WSL 的沙箱无法运行某些本地套接字/线程时，使用 Codex 正常审批运行同一隔离测试，不关闭审批或沙箱策略。

证据目录 `evidence/phase3/` 与 `.runtime/` 被 Git 忽略；最终文档保留测试数量、层级、路径与摘要，不收录原始私聊、Key、token、二维码或访谈。真实人格输出仅保留在 owner-only 本地文件，模型内容检查与用户主观评分分开。

## 多气泡与后续增量

专项复用 Private 6.7.0 的分隔标记/解析器，通过同一次主模型生成选择1–4个语义气泡。Guard新增持久计划/逐气泡状态，首条不额外等待，后续0.6–1.8秒，期间重检插话、取消、supersede和停机。原生历史仅投影已确认发送的正文，修复Private接管后的`_no_save`释放；主动和搜索最终回答复用相同发送循环。

按用户真实QQ反馈增加可独立关闭的`phase3_plain_text_enabled`：纯文本发送前清理常见Markdown装饰，保护代码和URL，32,768字符上限、异常/空结果回退，不增加LLM调用。混合组件保持原结构，仍受既有文本出站范围约束。详细层级、计数和生产状态统一见[专项报告](phase3-multibubble-validation.md)，历史结果不覆盖。

资料生产入口拆为本机默认dry-run、隔离原生worker和只安装两份数据库的维护控制器。真实生产需实际停写和一致快照验证，不能由JSON中的“已备份”代替；候选WAL归一化，保留原usage文件，安装/恢复保留服务UID/GID。该入口本身不授予许可；本人后来明确批准的15组L1/L2已通过精确manifest执行，L3和其余访谈仍不导入。
