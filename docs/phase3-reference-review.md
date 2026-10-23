# Phase 3 V2 参考实现核对

核对日期：2026-10-23。以下用于机制比较；没有安装 DSH/SnowLuma，没有复制第三方源码到公开仓库，也没有替换 AstrBot/NapCat/MemoryCompanion 或已确认人格。

| 来源与固定 commit | 实际阅读文件 | 采用与不采用 |
|---|---|---|
| [Derpyu520/qq-bridge](https://github.com/Derpyu520/qq-bridge/tree/a49877f4e58d04e47532d44b803360d0f001160d) `a49877f4e58d04e47532d44b803360d0f001160d` | `src/mcp-snowluma-safe.js`、`src/bridge.js`、`src/v2-wait.js`、`dsh/agent-presets/qq-chat-v2/agent.cordis.yml`、`config.example.json`、`LICENSE` | 借鉴一次模型决定多条内容、发送前再核对权限、只记录确认前缀。继续使用 Private 原分隔协议和 Guard。没有引入工具发送作为唯一回复入口、全局发送队列、长首答等待、500字截断或外部角色。 |
| [ruoqian-0205/qq-companion-bot](https://github.com/ruoqian-0205/qq-companion-bot/tree/4cdc83fc2bfd1a73a178f7d6148a6ec099f227a4) `4cdc83fc2bfd1a73a178f7d6148a6ec099f227a4` | `bot.py`、`LICENSE` | 核对确认发送后记历史，以及锁内定格资料、锁外模型调用、提交检查版本。映射到现有 Guard 和原生 MemoryService；不采用其分层库、按换行分段或撤回命令。 |
| [xinghefumeng0717/qq-companion-bot](https://github.com/xinghefumeng0717/qq-companion-bot/tree/6b33d2064e59e147d6a32715c198051d88de77dc) `6b33d2064e59e147d6a32715c198051d88de77dc` | `README.md`、`LICENSE` | 参考完整对聊仿真组织；本项目使用自己的固定依赖隔离测试与合成事实，不导入其角色卡。未声称执行过该项目测试。 |
| [DeepSeek 官方视觉协议](https://api-docs.deepseek.com/guides/vision/) | 2026-10-23 查询 | 核对 Flash 的 `image_url`/data URI 图像输入。真实合成测试检查实际图像载荷、小字、双图顺序和含图上下文；HTTP成功本身不是图片理解通过。 |

三个仓库均已读取许可证：MIT；qq-bridge 的 LICENSE 另含第三方声明。只作独立实现参考，未据此推断所有依赖均可重新分发。下载内容仅保存在忽略的 `.runtime/references-v2/`；commit、路径与逐文件 SHA256 记录于私有证据 `evidence/phase3/four-capabilities-20261023/reference-lock.json`。

参考项目的权限、频率、人格及模型配置均不构成本项目授权。本项目仍按已批准的单一私聊对象、现有搜索额度、主动窗口和 L1/L2 导入清单执行。
