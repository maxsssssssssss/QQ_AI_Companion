# Phase 1 兼容性与验证边界

以下版本以 `config/upstream-lock.json` 和 `config/plugins-lock.json` 为准。声明兼容范围来自固定提交的 metadata；运行结论必须对应本地 `evidence/` 中的真实验收记录。证据不上传 Git，复现步骤见 README。完整提交和源目录摘要用于复现，不能以版本号字符串代替。

| 组件 | 固定版本 / 提交前缀 | 上游声明 | 本项目检验路径 | 发布授权状态 |
| --- | --- | --- | --- | --- |
| AstrBot | 4.29.1，见 upstream lock | Python >=3.12；本项目 Python 3.12 | 原始生命周期、OneBot v11、OpenAI兼容HTTP、SQLite | AGPL-3.0；保留上游源码与许可义务 |
| Message Recorder | 0.0.4 / `8cb184e2890d` | AstrBot >=4.16,<5；aiocqhttp | 原始加载、统计命令、入站消息SQLite写入、重复事件、重启/恢复 | LICENSE GPLv3 与 README AGPL 不一致，需澄清分发条件 |
| MemoryCompanion | 2.3.1 / `e40a83005b0a` | AstrBot >=4.22.0；aiocqhttp | 原始加载、status/add/search、当前轮记忆注入、用户隔离、SQLite持久化 | 未找到项目许可证；本地原样评估，阻塞再分发 |
| PrivateCompanion | 6.7.0 / `da953559f34d` | AstrBot >=4.22.0；aiocqhttp/qq_official | 原始加载、状态/昵称命令、JSON持久化；主动/外部能力关闭 | 未找到项目许可证；本地原样评估，阻塞再分发 |

## 依赖

AstrBot 与三插件声明依赖统一解析并冻结于带制品 SHA256 的 `lockfiles/astrbot-requirements.txt`。其中 PrivateCompanion 额外声明 `chinese-calendar`、`lunarcalendar`、`markdown-it-py`，Recorder 需要 `aiosqlite`、`quart`、`aiohttp`、`Pillow`；MemoryCompanion 没有独立 requirements。依赖安装在构建时完成，运行时不需要联网安装。

## 发现与范围

1. 第一次真实加载暴露本项目自定义入口缺少 `ASTRBOT_ROOT` Python 搜索路径：AstrBot 按 `data.plugins.<插件>` 导入，但进程工作目录为 `/AstrBot`。应在本项目入口补充运行根路径，保持上游源码不变。首次失败记录必须保留，不能作为插件成功证据。
2. Message Recorder 的固定入口只有入站 `event_message_type(ALL)` 钩子；未注册 LLM 回复/发送完成钩子，也未发现 PrivateCompanion 对 Recorder 的输出桥接。因此，入站记录成功不能推导普通出站或主动消息已记录。平台自回报行为不在本次离线链路中假设。
3. Recorder 数据库通过 `(platform,message_id)` 与 `(platform,content_hash)` 唯一索引和 `ON CONFLICT DO NOTHING` 防止重复入库。这不代表 AstrBot 主链具有幂等回复能力；两项必须分别验证。本项目集成的 Phase1Guard 会在 Recorder 前拒绝重复事件，因此集成重放证明最终只保留一条记录，不单独证明执行过 Recorder 的冲突插入分支。
4. Memory 的 legacy 陪伴桥接配置关闭，但真实数据中观察到独立 Bot Personal 本地活动归档；该开关不等于关闭所有本地互操作。真实加载、单插件基本状态/数据与共同运行可以验证；完整跨插件主动陪伴、模型总结等功能不在此结果里冒充通过。
5. 三个插件的管理 Web 页面未在无仪表盘的离线配置中验收；成功注册路由也不代表页面功能验证通过。

运行状态汇总由最终交付报告与对应验收 JSON 给出；本表不把静态声明或配置值当作运行 PASS。

6. Recorder 原始查询命令没有管理员装饰器，且搜索不按发起者会话过滤。项目通过 AstrBot 自带 `command_management.update_command_permission` 在启动接收消息前把 Recorder 全部命令设为管理员权限；不修改第三方代码。实际 `10002` 读取 `10001` 的负例必须得到权限拒绝。MemoryCompanion 原始 `mcomp` 管理子命令声明管理员权限；其普通对话检索的作用域隔离另有真实 Mock LLM 请求检查。


## 分层运行观测

以下插件运行观测来自整套运行 `evidence/runs/20261019T090123Z-7895/`。该运行的插件重启与恢复已完成；整个 Phase 1 的 P0 状态仍以最终报告为准，不能从本表推导。此前 `plugin-diagnostic`、`plugin-diagnostic2` 中的失败日志保留用于说明诊断依据。

| 层级 | Message Recorder | MemoryCompanion | PrivateCompanion |
| --- | --- | --- | --- |
| AstrBot 真实导入、实例化、激活 | PASS；0.0.4 | PASS；2.3.1 | PASS；6.7.0 |
| 真实 OneBot 插件命令，不落入 LLM | PASS；统计 | PASS；status/add/search | PASS；昵称/状态 |
| 管理边界负例 | PASS；10002 查询 10001 被拒绝 | PASS；非管理员 search 被拒绝 | PASS；10002 模型请求未出现 10001 的合成昵称 |
| 普通消息共存链路 | 三插件同时加载，真实 HTTP Mock LLM 调用和 OneBot 回复通过 | 当前轮注入包含已存合成事实，10002 请求不含该事实 | 昵称写入并由状态命令读回 |
| 持久化 | PASS；合成入站 SQLite 行 | PASS；手工记忆及 user_message、bot_response 双向时间线 | PASS；companions.json 昵称 |
| 普通出站消息覆盖 | FAIL；发送成功后 sender=90000 行为 0 | 可记录 bot_response，不等于发送成功回执 | 未把其私有发送账本替代核心 P0 投递账本 |
| 重启 / 恢复 | PASS；停机重启与新目录恢复后原始消息行仍在 | PASS；四类数据观测包含记忆/时间线，并经真实 search 命令读回 | PASS；昵称 JSON 保留，并经真实状态命令读回 |
| 运行后第三方源码摘要 | 与固定源码摘要相同 | 与固定源码摘要相同 | 与固定源码摘要相同 |

Memory 默认 60 秒后台启动宽限期使短时测试可能只观测到 Bot 回复，无法证明用户消息已捕获。标准离线配置通过公开 schema 将 `startup.background_grace_seconds` 设为 0；最终该轮 `plugin_memory_captured_user_message` 与 `plugin_memory_captured_bot_response` 两条断言均通过。最初手工记忆夹具含空格，AstrBot 将单个 `str` 参数截成首词；夹具已改成一个无空格参数，并通过真实写入、检索与注入重验。

重启失败根因是 AstrBot 保存自己的配置时移除了未知顶层 `phase1`，随后项目验证器找不到所需字段。修复位于本项目配置层：独立 `phase1.json` 保存元数据，验证器显式读取必需侧车；没有修改 AstrBot 或插件源码。上述整套运行已在三插件共同加载状态下重验启动、重启及恢复，相关检查均通过。
