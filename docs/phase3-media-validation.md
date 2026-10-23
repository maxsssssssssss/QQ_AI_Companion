# Phase 3 V2 图片与含图上下文验证

**2026-10-24当前：单图及下一轮图片追问已获本人T4确认；剩余小字、多图等按用户最终“跳过全部”豁免，NOT TESTED。主动保持OFF，整体PARTIAL，本轮收尾。** 实际JPEG960×1446、185352字节，在Agent准备与保存历史中的SHA一致；两轮纯文本追问各恢复1张历史图，原deepseek-flash完成，caption替代未配置。不是现场HTTP抓包；没有输出像素或URL。详见`four-capabilities-20261023/uat-image-payload-proof-r1.json`及总矩阵。

下文保留10月23日候选工程历史。

2026-10-23，候选工程证据；不代表这次新增场景已通过真实 QQ。Phase 2 的单张计数图片成功和用户此前跳过的 Phase 3 图片测试分别保留，不合并成完整视觉验收。

## 实际缺口与最小适配

固定 AstrBot 4.29.1（源码 `dffe922773725e9c178392c423676ad65044748c`）原生 `PreProcessStage` 已负责 NapCat 路径映射和图片本地化，`ProviderRequest.assemble_context()` 已把图片保存在原对话的可携带 data URI 中。本次继续使用它们，没有新增媒体主库、caption 模型或视觉服务。

本次发现并处理六个独立问题：

1. 原 Guard 连续输入只保存文本，旧事件被覆盖后，源图片可能随事件释放。新增 `media.snapshot_components()` 在原生预处理和权限检查之后捕获本地原始字节，交回原 Turn 的组件集合；引用内图片仍在引用内，不改原入站档案。
2. 原空正文补答遇到任何 `image_url` 就报 `unsupported_media`，历史图片也会使后来的普通文本补答失败。现在保留已捕获的用户图像和原工具对，区分当前/历史图片缺失；确实不可用时明确说明，不能假称看过。当前/较新图像优先使用有限图像预算。
3. 原生 `media_utils.prepare_model_image()` 除边长设置外，还有 512 KiB 预览上限。新 `restore_current_images()` 在原 Agent 已组装完成、第一次模型调用之前，以严格对应的已批准本轮静态图片还原原始字节。原生引用重排或动画 montage 的映射不确定时保留原处理，不猜顺序。
4. 原生 OpenAI Provider 在部分图像错误后会去图并重试文本，但没有明确说明已移除图片。`media_provider.install()` 仅包装该 Provider 实例的既有去图方法，为重试加上明确缺图说明，保留原上下文，记录原 turn_id 和数量。仍由原生代码决定是否重试，不增加模型、重试次数或发送通道；插件停止可恢复实例方法。
5. 原生图片下载发生在插件入站钩子之前，长等待既会延迟文字，也可能使图片后到预处理终点而改变合批顺序。`media_download.install()` 仅包装当前原生队列实例，匹配已授权平台、主 QQ、私聊才适配。沿用原 Guard 账本/Turn 同步预登记并记录到达序号，不新增调度器；重复/拒绝项在下载前停止。原生 Image 的下载、验证共用该事件 4 秒预算，失败保留明确原因，其他文字继续；组装等待已有来源组件后按原到达顺序合并。群聊、其他联系人及平台不受包装影响。
6. 完整联调 `20261023T141539Z-7274` 中 I19 已把双图传给模型，但 I20 历史追问失败。定位到固定 PrivateCompanion 6.7.0 的 `sanitize_historical_image_blocks_before_provider` 无条件去除历史图片，并非图片首次发送成功就能证明追问成功。新增 `media_history` 在该钩子紧邻前后，仅为当前授权私聊且明确声明 image 能力的 Provider 保留用户图片。精确匹配原清理结果才恢复图片位置和清理器占位文本，不覆盖记忆、工具或其他插件修改；缺失和超额图片用明确不可用语义保留。计划只存在当前事件，不另建历史库。

稳定图片快照的项目边界为单图 8 MiB、合计 16 MiB、8 图、单边 8192 像素、3 秒快照预算；原生下载与格式验证另有单事件共用 4 秒预算。图像字节上限是进入模型的快照上限，不宣称原生下载在收到这些字节后立即截断。这些是候选的保守保护值，不是 DeepSeek 官方上限。超限/缺失/格式错误各有明确枚举，不能吞掉其余可靠文字。补答仍是同一已配置 Provider、最多一次、12 秒总墙钟、上海日 4 次持久化尝试限额，普通非空回复不会触发额外请求。

## 官方协议与采用依据

核对 [DeepSeek 官方 Vision 文档](https://api-docs.deepseek.com/guides/vision/)：当前 `deepseek-flash` 接收用户消息中的标准 `image_url` 内容块，支持内联 base64；JPEG、PNG、GIF、WebP 均支持。`high`/`original` 保留输入尺寸，模型内部仍会按官方规则处理图像。系统/助手消息不能带图。旧视觉实验别名不意味着另一个视觉模型，本次不新增该别名。

所读固定上游文件：`astrbot/core/pipeline/preprocess_stage/stage.py`、`core/provider/entities.py`、`core/utils/image_input.py`、`core/utils/media_utils.py`、`core/provider/sources/openai_source.py`、`core/agent/runners/tool_loop_agent_runner.py`、`core/event_bus.py`、`core/pipeline/scheduler.py`；PrivateCompanion 的 `main.py` 和 `tool_history_sanitizer.py`。仅第一方适配与测试进入本项目，无复制上游实现。

## 本次分层结果

| 层级 | 结果 | 范围及证据 |
|---|---|---|
| T2 媒体原生组件 | 18 PASS | `tests/test_phase3_media_native.py`；单图/双图顺序、Emoji、图后文字结构、引用、映射后本地路径、源文件删除后原请求捕获、历史追问、原始大图恢复、缺图、格式/数量/大小、当前图优先、原工具协议、实际原生 Provider 与本机 HTTP400→一次文本降级；新增原生 Private 历史清理后恢复、并行其他修改保护及缺图/超额语义 |
| T2 原生下载/入队/发送 | 8 PASS | `tests/test_phase3_media_download_native.py`；本机 HTTP 挂起、404、非法图像、正常 PNG，经真实 PreProcess 和 Guard/OneBot；慢图后文字仍按原序合批、重复入站不下载、非授权平台/用户/群不包装、失败后下一轮正常文字可处理；关键 admission 异常拒绝当前事件但不终止后续入站 |
| T2 空正文原生回归 | 12 PASS | `tests/test_phase3_empty_response_native.py`；原有 10 项加含图历史同 SDK 补答、旧图失效后普通文字仍能回复 |
| T2 完整 AstrBot/OneBot | 由总联调记录更新 | 上述原生组件不替代完整入口、合批、模型请求及 Guard 出站链 |
| T3 真实 DeepSeek | 3/3 PASS | 主线程执行 `scripts/phase3-test-media-real.py`：合成小字 BLUE-731、红色一圆/蓝色两方块的图片顺序、历史图片尚未回答过的白色背景。三次均真实 deepseek-flash、HTTP200、非空正文与 stop；输出与已知合成答案匹配，不只检查 HTTP。无私人图片或 QQ 发送 |
| T4/T6 真实 QQ/本人体验 | 待完整候选及顺序验收 | 不把 HTTP200、模型自评或 Phase 2 单图样本当作本轮通过 |
| T5 生命周期 | 原生组件通过；发布恢复另记 | 源图片删除后同一消息/原历史仍携带图像；不存在新增媒体数据库需要迁移 |

脱敏工程证据：私有 `evidence/phase3/v2-media-native-20261023-r3.json`（18 项）、`v2-media-download-native-20261023-r8.json`（8 项）、`v2-empty-native-20261023.json`（12 项），对应 `.log` 只含合成夹具。下载 r2 的两项失败完整保留：夹具错误地在 follower stop 后调用生成钩子，及用于非法格式冷启动解码的 0.5 秒测试预算先耗尽；修正为真实 Scheduler 的 stop 传播和实际 4 秒配置后 r3 通过。最终源码选第一个完成预处理的事件继续原生 pipeline，测试验证唯一执行者和源组件顺序，不强制执行者一定是先到的图片事件。r4 捕获协作修改期间 main/turns 新旧接口不匹配；接口冻结后消失。r5/r6 在业务测试开始前遭遇本地 cwd 解析异常，未改业务代码来规避；r7/r8 从稳定 `/tmp` 工作目录使用绝对源码和证据路径执行。r7 有效慢图仍用了加速的 0.5 秒预算而触发正确的缺图降级，r8 有效图片统一用实际 4 秒配置，只有专门的挂起/共享期限测试用 0.5 秒；未扩大生产预算。以上失败证据均保留。早期原生 Pydantic v1 复制不兼容及名称遮蔽也已修复并回归，未让用户承担发现这些基础错误的测试。

真实模型证据 `evidence/phase3/four-capabilities-20261023/real-media-r1.json`：输入 1889 token、输出 37 token，按未缓存峰值估算人民币 0.004074 元，不是账户账单。执行时沿用主线程预算累计预留 4.0 元（不是新额度，也不是当前全部测试最终累计）。图片诊断只记录合成来源标签、格式、尺寸、大小与摘要，不写像素或带凭据链接。

## 仍须核对的边界

- 完整候选真实请求中的 Provider/model、caption 配置、图像块数量/格式/尺寸/摘要由部署审计核对；本文件不能替代实际生产配置证据。
- 原生下载已在授权事件范围施加单事件 4 秒墙钟限制；已失败的远端 URL 不在后续快照重试。原生网络库、类和其他文件操作未全局修改；返回给适配器的临时文件在验证失败时清理，尚未返回路径时依赖原生 MediaResolver 的取消清理。
- 引用图片和动画维持原生结构，不宣称与直接静态图片有同等的原字节恢复覆盖。
- 图像快照仅供当前已有事件生命周期和原生历史使用；像素、路径、带凭据链接不写入公共诊断。
- 小字识别和图像事实准确度仍需要真实模型及本人样本，源字节完整不等于语义识别必定正确。

## 回退

随主候选发布清单一起备份和窄安装 Guard 适配文件（包括 media、media_download、media_history、media_provider 和 recovery）。回退使用该候选 installation journal 恢复旧文件并正常重启；Provider 和队列实例包装也会在插件 terminate 时恢复。保留原数据库和所有新增聊天，不覆盖 data、清理会话或回放 unknown 气泡。原版空正文补答只支持文本、历史图片被清理的限制需随回退重新标明。

### 23:05 +08:00 登录恢复与当前状态

本人扫码后已核验当前在线备用QQ身份，原生自动登录设置重新保存并验证落盘；没有额外重启或再次登录请求。23:05:16实际isLogin/coreReady=true、loginPhase=ready，内部OneBot ESTABLISHED=1，策略PASS、47源文件摘要匹配。原先waiting_qrcode及一次快捷登录未恢复的证据保留为本次发布历史。

当前为 **CANDIDATE_READY_FOR_UAT**，仅表示完整候选及自动验证/发布前置条件满足；P08真实FAIL尚未关闭，总体PARTIAL，主动OFF。已按顺序仅邀请此前失败的资料问题，未发整组作业。证据 `evidence/phase3/four-capabilities-20261023/candidate-readiness-r2.json`、`production-health-after-login-r2.json`、`napcat-save-current-session-r2.json`。
