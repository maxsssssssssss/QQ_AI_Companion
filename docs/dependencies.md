# 依赖、许可证与兼容性核查

核查日期：2026-10-19（Asia/Shanghai）。以下仓库来自用户完整 Phase 1 任务书；版本通过 GitHub HTTPS Git 与固定提交的实际文件核查。未修改第三方源码，未安装或登录真实 QQ 协议端。静态兼容声明、离线行为和真实服务验证分别记录。

## 固定基线

| 组件 / 上游 | 本项目固定版本与源码 commit | 许可证证据 | 兼容性与用途 |
| --- | --- | --- | --- |
| [AstrBot](https://github.com/AstrBotDevs/AstrBot) | `v4.29.1`；`dffe922773725e9c178392c423676ad65044748c` | [LICENSE](https://github.com/AstrBotDevs/AstrBot/blob/dffe922773725e9c178392c423676ad65044748c/LICENSE) 为 AGPL v3；[pyproject](https://github.com/AstrBotDevs/AstrBot/blob/dffe922773725e9c178392c423676ad65044748c/pyproject.toml) 声明 `AGPL-3.0-or-later` | Python ≥3.12；原生 OneBot v11、OpenAI compatible provider、Future Tasks、DeepSeek Search/Fetch |
| [Message Recorder](https://github.com/leafliber/astrbot_plugin_message_recorder) | metadata `0.0.4`；`8cb184e2890dee7d5031d3a8ef6b753209dc7d11` | [LICENSE](https://github.com/leafliber/astrbot_plugin_message_recorder/blob/8cb184e2890dee7d5031d3a8ef6b753209dc7d11/LICENSE) 是 GPL v3，README badge 却声称 AGPL-3.0，需保留此差异 | metadata `>=4.16,<5`；aiosqlite、quart、aiohttp、Pillow；记录实际接收消息到 SQLite |
| [MemoryCompanion](https://github.com/menglimi/astrbot_plugin_memory_companion) | metadata `2.3.1`；`e40a83005b0a2e0817e5680c7d14398c9ab49790` | 未发现项目级 LICENSE/COPYING；字体和 rolling-number 的单独许可不能授权整个插件 | metadata AstrBot `>=4.22.0`、aiocqhttp；SQLite 记忆、时间线、主链注入与 Companion bridge |
| [PrivateCompanion](https://github.com/menglimi/astrbot_plugin_private_companion) | metadata `6.7.0`；`da953559f34d28a43d8a2d44c1064b293229c1b8` | 未发现项目级 LICENSE/COPYING | metadata AstrBot `>=4.22.0`、aiocqhttp / qq_official；状态与关系持久化、LLM 及发送钩子 |

AstrBot 最新稳定 tag 的核查结果是 `v4.29.1`。其源码**没有 uv.lock**；自有项目通过 [`config/upstream-lock.json`](../config/upstream-lock.json)、[`lockfiles/astrbot-requirements.txt`](../lockfiles/astrbot-requirements.txt) 与构建工具锁保存完整版本和哈希，保留 TLS、包哈希与不可变基础镜像校验。Python 锁针对 `linux/amd64`、Python 3.12；不声称已验证其他架构。

插件固定值见 [`config/plugins-lock.json`](../config/plugins-lock.json)。源码在忽略的 `.runtime/upstream/` 中原样获取；仓库和基础容器不 vendor 三个插件源码，配置与测试由本项目独立维护。若外发包含 GPL/AGPL 组件的镜像或组合软件，须履行实际适用的声明、源码和分发义务；当前审计不授予额外权利。

## 版本差异与可复现性

- Message Recorder 未发现 release tags，以 commit 固定。初始化异常会在插件内部捕获，loader 列表不能证明数据库可用；必须验证真实命令与 SQLite 入库。
- MemoryCompanion 最高稳定 tag `v2.3.2` 指向 `8cff40d217438d5c0ac1b9a1fe6f935d1f46a5c1`，但当前 main metadata 是 `2.3.1`。本项目固定上表 main commit，**不称为 2.3.2**；两者主要有注入轨迹页面差异。
- PrivateCompanion `v6.7.0` 的 annotated tag 对象是 `d4e1e0e6c0f3124d420abba81a04b94c5f7a58fe`，源码 commit 是 `f2f87183993a0667800056f73bcf12df298cdbcb`。本项目固定较新的 main commit，包含额外的线程安全和增量持久化修复；metadata 仍为 6.7.0，不能仅靠版本字符串复现。
- PrivateCompanion 的额外依赖包括 `chinese-calendar>=1.9.2`、`lunarcalendar>=0.0.9`、`markdown-it-py>=3,<5`；其 aiohttp / Pillow 范围与 AstrBot 一并求交锁定。MemoryCompanion 没有 requirements.txt，依赖宿主接口和 quart。

两款 Companion 缺项目许可证，Message Recorder 声明不一致。本次按用户授权进行**原样、本地、合成数据评估**，不修改、公开复制其源码或发布包含其源码的产品。后续修改、打包分发、商业使用的授权边界须向权利人取得明确答复；公开可读不等于普通开源许可。

## QQ 协议端：只审计，未安装或登录

| 项目 | 核查版本 / commit | 授权限制 | OneBot 与持久化证据 |
| --- | --- | --- | --- |
| [NapNeko/NapCatQQ](https://github.com/NapNeko/NapCatQQ) | 最新稳定 `v4.18.38`，peeled source commit `bbd0d2aca3d470c20b33b1d4f95123335633bf26`；同时审计 main `450fb0429a064e3418e0c698b1b4d78324101929` | [Limited Redistribution License](https://github.com/NapNeko/NapCatQQ/blob/450fb0429a064e3418e0c698b1b4d78324101929/LICENSE)：限制商业用途和公开修改版；README 禁止未经作者授权基于其代码开发项目。不可当成任意复用的 OSI 开源库 | `packages/napcat-onebot/config/config.ts` 的 `network.websocketClients` 支持反向 WebSocket、token、5 秒重连、30 秒心跳与 TLS 校验；`NAPCAT_WORKDIR` 下含 config/logs/cache/plugins；NTQQ 账号数据目录由原生客户端决定 |
| [SnowLuma/SnowLuma](https://github.com/SnowLuma/SnowLuma) | `v2.0.3` / `db142db9e6398c779283eab9afc3552da1e2c361` | [Source-Available Non-Commercial License 1.0](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/LICENSE) 明确不是 OSI 开源许可；非商业自托管允许，商业或公开衍生版需书面授权；native addon 专有且不可单独再分发 | README 声明 OneBot v11 WS client/server、HTTP；Node ≥24 / pnpm 11.25.0；`config/onebot_<uin>.json`、`config/webui.json`，`data/<uin>/` 下消息/媒体 SQLite 等需持久化 |

NapCat 的 `v4.18.38` tag 对象 SHA 为 `e0f817edf1a32d0825d433bbf9c8e9dc06b32873`，它与上表实际源码 SHA 不同。SnowLuma 的内置 WebUI 备份是配置白名单导出，**不等于 QQ 登录态、消息 SQLite 与全部媒体的完整备份**；OneBot token 与 WebUI 密码哈希属于凭据项。相关证据：[`packages/core/src/webui/backup.ts`](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/core/src/webui/backup.ts)、[`storage-management.ts`](https://github.com/SnowLuma/SnowLuma/blob/db142db9e6398c779283eab9afc3552da1e2c361/packages/core/src/webui/storage-management.ts)。

Phase 2 可按授权与平台兼容性选择其中一个独立协议进程，通过 OneBot 接口互操作；本项目不 fork、链接复制其内部实现。其真实 QQ 版本兼容性、扫码、掉线与重连均为 **NOT_RUN**，不得用模拟 OneBot 结果代替。

## P1-06：Future Tasks 的真实边界

AstrBot [`cron_tools.py`](https://github.com/AstrBotDevs/AstrBot/blob/dffe922773725e9c178392c423676ad65044748c/astrbot/core/tools/cron_tools.py) 的 `future_task` 支持 create/list/edit/delete。一次任务用 `run_once=true` 与 ISO `run_at`；工具从当前配置读取 `timezone`，任务保存 session 与 sender_id，修改/删除检查所属发送者。主链是否调用它取决于模型工具调用；只验证调度器不等于自然语言解析质量验证。

真实原生 `CronJobManager` + APScheduler + SQLite 的离线结果见 `evidence/future-tasks/result.json`，运行命令 `bash scripts/test-future-tasks.sh`：

| 行为 | 实测结论 |
| --- | --- |
| 定时触发的一次成功任务 | 执行 1 次，然后删除任务行 |
| 定时触发的一次失败任务 | 捕获失败后仍删除任务行，没有原生自动重试，失败行不保留 |
| 手动 `run_job_now` 的一次失败任务 | 保留任务行、`status=failed` 与 `last_error` |
| 循环任务失败 | 保留任务与失败信息，后续调度仍存在 |
| `Asia/Shanghai` 的未来一次 active task | 无时区输入按配置解释；UTC 时间一致；真实 SQLite 关闭重开后重新调度一致 |

根因在 [`manager.py` 的 `_run_job()`](https://github.com/AstrBotDevs/AstrBot/blob/dffe922773725e9c178392c423676ad65044748c/astrbot/core/cron/manager.py#L335)：finally 写失败状态后，在 `job.run_once and delete_run_once` 时无条件 `delete_job()`，注释明确 `regardless of success`。`run_job_now()` 特意传 `delete_run_once=False`，所以不能用手动执行结果推断定时行为。调度器 `misfire_grace_time=300`，不能承诺离线超过 5 分钟的一次提醒一定补发。

这组测试 PASS 表示**行为核查成功**，不表示“可靠提醒、失败保留、重试投递”功能 PASS。Phase 2 若需要可靠提醒，必须另行验收失败审计、告警与用户确认的重试流程；本次不修改上游调度实现。

## P1-07：DeepSeek 原生能力与未核实项

源码确认 AstrBot 原生 DeepSeek provider 使用 `openai_chat_completion` / `openai_responses`，默认 `api_base=https://api.deepseek.com/v1`。[`openai_source.py`](https://github.com/AstrBotDevs/AstrBot/blob/dffe922773725e9c178392c423676ad65044748c/astrbot/core/provider/sources/openai_source.py#L1008) 对 v4 模型及 `api.deepseek.com` 处理 reasoning_content；模型 ID 原样传给服务，源码包含字符串不能证明当前官方 API 一定接受该模型。

[`web_search_tools.py`](https://github.com/AstrBotDevs/AstrBot/blob/dffe922773725e9c178392c423676ad65044748c/astrbot/core/tools/web_search_tools.py#L1911) 已内置：

- `web_search_deepseek` → `POST https://api.deepseek.com/v1/tools/search`。
- `deepseek_fetch_web_page` → `POST https://api.deepseek.com/v1/tools/fetch`。
- 配置为 `provider_settings.web_search=true`、`websearch_provider=deepseek`、`websearch_deepseek_key=[…]`。搜索配置与模型 provider key 是不同字段，可由同一安全注入来源供值；源码不会自动把模型 key 回填到搜索配置。
- 未配置 key 时明确返回错误，不发出请求。fetch 只接受 HTTP(S)、合法主机/端口、无内嵌用户名密码的 URL；这不替代外网内容与间接提示注入防护。

[`tests/test_deepseek_tools.py`](../tests/test_deepseek_tools.py) 已在 `--network none` 容器中直接调用未修改的原生工具：Search/Fetch 无 key、非法搜索词、带内嵌凭据的 URL 共 4 项通过，见 `evidence/deepseek-tools/result.json`。输入配置/会话使用合成 SDK wrapper，没有替换核心函数或 HTTP 返回值。此证据只证明错误/校验路径，**不证明真实 Search/Fetch 成功**。

任务指定的主模型 **`deepseek-flash`** 和复杂模型 **`deepseek-v4-pro`** 的“当前官方有效性”状态是 **UNVERIFIED**。本次云环境出站允许列表没有 `api-docs.deepseek.com`、`api.deepseek.com`，未绕过策略、未把第三方镜像当作最新官方文档，也未用真实 API key 调用。当前 AstrBot 源码存在 `deepseek-v4-flash` / `deepseek-v4-pro` 标记，但这不能将 `deepseek-flash` 擅自改成另一个 ID 或宣布官方核实通过。

解除条件：允许访问 [DeepSeek 官方 API 文档](https://api-docs.deepseek.com/)，核对当日模型 ID、base URL、Search/Fetch 可用范围；若随后进入真实联调，在安全设置中复用已存在的 DeepSeek key，检查模型列表和最小调用。没有 key 不阻塞本轮离线结构核查，真实成功调用仍标记 **NOT_RUN**。

## 可复核证据

以下证据文件仅保留在开发工作区，不上传 Git；上游固定链接、版本和许可结论保留在本文，运行验证可按 README 重现。

`evidence/upstream-audit.json` 保存提交、原始许可证文件与 README 的 SHA256、固定 GitHub 链接、联网限制观察。克隆时保留代理与 TLS 验证，不请求未允许域名。完整安装、插件加载、消息链路及持久化的结果以本项目相应测试报告为准，不从本文件的兼容范围推定 PASS。
