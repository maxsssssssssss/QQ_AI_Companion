# 三插件离线评估

项目不复制第三方插件源码到 Git，也不把它们编入运行镜像。仓库地址、提交、声明兼容范围及源目录 SHA256 位于 `config/plugins-lock.json`。执行 `python3 scripts/fetch_plugins.py` 可将原始固定提交准备到被忽略的 `.runtime/upstream`；已有目录不会被覆盖。首次下载需要 GitHub 网络，测试本身使用 `--network none`。

`prepare_plugins.py --state <ASTRBOT_ROOT> --sources <源目录>` 验证完整源目录摘要，把原样源码复制到测试临时 `data/plugins`，再由本项目生成 `data/config/*_config.json`。摘要包含相对路径、文件类型和内容哈希，排除 Git 元数据和 Python 缓存；不要求容器安装 Git。脚本拒绝已有插件或配置目录以保护用户文件。

## 实际执行路径

`tests/plugin_checks.py` 使用父验收器的真实 OneBot WebSocket 对端，发送插件命令和普通消息，由 AstrBot 原始插件加载器与事件分发执行。检查同时读取原始 SQLite/JSON 数据，并在重启后再发读取命令。没有直接代调处理函数、伪造数据库记录、替换上游模块或修改上游源码。

只有协议端和 LLM HTTP 服务是测试替身。插件命令必须返回其独有内容，而且不能落入 Mock LLM；普通对话必须产生真实 HTTP 请求和 OneBot 回复。记忆注入检查仅检查当前用户消息和 system/developer 内容，排除旧聊天历史造成的假阳性。插件加载成功不能替代数据库写入验证。

## 限制性配置

- Message Recorder：启用文本/消息链记录与管理命令，关闭媒体下载和原始消息备份；本次合成数据不启用自动清理。
- MemoryCompanion：启用本地捕获、基础检索和注入；关闭模型总结、画像调度、向量检索、跨窗口连续性、声明的 legacy 陪伴桥接与自动维护；后台启动宽限期设为 0，以便离线验收明确观测用户及 Bot 双向捕获。数据写入 `data/plugin_data/astrbot_plugin_memory_companion/memory_companion.db`。
- PrivateCompanion：从固定版本 schema 生成配置；关闭所有可选 `enable*`/`*_enabled` 功能，再保留明确列出的安全防护与本地用户资料创建。无主动目标、每日额度为零；关闭 QQ 状态、空间、天气、设备、图像、语音和外部插件动作。基础状态/昵称命令仍由原始插件执行。JSON 数据位于 `data/plugin_data/astrbot_plugin_private_companion/companions.json`。

PrivateCompanion 的内部调度循环可以正常初始化；配置和容器网络隔离共同约束它的行为。测试不证明其全部后台功能正确，也不验证真实 QQ、真实模型、拟人化日程、主动陪伴、图片/语音或跨插件长期记忆协议的完整语义。真实观测发现，两插件仍可通过独立 Bot Personal 能力做本地活动归档；不能仅根据 legacy 桥接开关宣称全部本地互操作已关闭。

## 许可证与分发边界

Message Recorder 的 `LICENSE` 为 GPLv3，README 标注 AGPL 存在不一致。MemoryCompanion 与 PrivateCompanion 未找到覆盖项目代码的许可证；字体许可证不构成插件代码授权。本次仅进行用户指定的本地原样评估。后两者不可作为已获开源授权的组件重新发布、改写或打包进本项目发行物。解除分发阻塞需要作者提供覆盖固定代码版本的有效项目许可证或明确授权。

所有测试 ID、文本、昵称和访问令牌都是固定或随机合成夹具，不使用真实 QQ 凭据或真实个人资料。第三方源目录、运行数据库、日志及全部 `evidence/` 处于 ignored 路径；Git 只保留本项目源码、配置、依赖锁、测试和必要文档。
