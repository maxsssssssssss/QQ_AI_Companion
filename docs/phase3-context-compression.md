# Phase 3 会话压缩候选（2026-10-24）

## 后续统一发布状态

2026-10-24用户明确授权后，C原生Token摘要与失败回退80轮已随r4安装到运行配置及Runtime源码。旧聊天和聊天派生摘要已清理，获准L1/L2保留；未来新对话按本规则处理。固定镜像容器已重新创建，**05:10:10 +08:00启动policy/readiness及压缩配置核对PASS**；本次无真实长历史摘要测试。详见[清洁开始与r4发布](phase3-fresh-start-release.md)。下面SAVED_NOT_APPLIED及“不重启”描述保留为准备阶段历史，59项隔离证据不改写。

状态：**SAVED_NOT_APPLIED**。用户要求先保存、隔离验证，后续统一生效；本次不重启、不热重载、不写正在使用的配置或数据库、不调用真实模型或QQ。

## 配置

| 项目 | 待生效值 | 含义 |
|---|---|---|
| `agent_runner.config.compression.max_turns` | `-1` | 关闭原先20轮的日常裁剪；不是把日常上限改成80 |
| `overflow_strategy` | `llm_compress` | 使用AstrBot原生摘要压缩 |
| Flash `max_context_tokens` / `fallback_max_tokens` | `128000` | 沿用原fallback作为明确操作预算，不冒充模型官方最大窗口 |
| 原生触发比例 | `> 0.82` | 超过约104960 tokens时触发；统计含估算，不保证精确token数 |
| `keep_recent_ratio` | `0.15` | 按当前上下文token量保留最近完整轮次，当前轮不被摘要掉 |
| 压缩 `provider_id` | 空值 | 跟随当前会话主Provider；当前锁定Flash，未启用第二个模型 |
| Runtime `context_compression_fallback_turns` | `80` | 仅未生成摘要时，先保留最近最多80个完整轮次，再做原生token检查 |

检查发生在Agent每次准备请求模型之前；通常由新消息触发，同轮工具调用后的下一次模型请求也会检查。无请求时没有后台压缩任务。触发压缩的请求会等待一次摘要生成，存在额外延迟与模型费用；本次隔离验证未产生真实模型费用。主模型输出8192、请求超时120秒、每会话入站40次/分钟、Agent10步和当前Thinking覆盖均保持。

摘要指令保存在 `scripts/phase3-context-config.py`，要求保留当前话题、约定和最新纠正，区分用户自述、推测、历史状态、图片观察和送达事实，不改人格或长期事实库。合成测试只证明链路，不能证明真实长历史的摘要质量。

## 最小适配与边界

复用固定AstrBot **4.29.1**（源码commit `dffe922773725e9c178392c423676ad65044748c`）的 `LLMSummaryCompressor`、`ContextManager`、`split_into_rounds`、历史保存和SQLite。MemoryCompanion继续是派生事实主库，Guard/Recorder保留原始档案；不会自动把已被20轮旧策略裁掉的上下文补回，也不回灌旧档案。

原生没有独立的“摘要失败保留80轮”配置。第一方 `phase3_runtime/context_compression.py` 在实际 `on_agent_begin` 阶段、Token检查之前，只包装本次已核验的原生compressor实例。通过固定版本的私有 `_ACTIVE_AGENT_RUNNERS` 接口取得runner，核对event和run_context对象身份；不改上游文件、全局类、注册表或其他会话。新开关默认0；候选为80。接口不匹配时记录脱敏告警并保留原生Token保护。

正常低Token历史即使超过80轮也不被该适配裁掉。摘要成功时原样返回原生结果；原生超时、空摘要或未生成摘要时才按完整user/assistant/tool轮次保留最近最多80轮。80轮仍超Token预算时，原生继续丢弃最旧完整轮次；只有当前轮本身过大时原生保留当前轮并告警，仍可能被模型拒绝。取消直接传播，不转成失败重试。适配不追加摘要调用，原生传入`request_max_retries=1`；Provider自身仍有既存的格式兼容处理，不能把一次Provider调用等同于严格一次HTTP请求。未来新请求重新超限仍可能再次尝试摘要。

这是工作上下文的有损压缩。真实摘要细节可能丢失；失败降级会失去较旧上下文，原始档案仍存在。摘要不被作为新的原始聊天或长期用户事实导入，未送达气泡不因压缩变成已送达。

## 验证与保存

隔离证据目录：私有 `evidence/phase3/context-compression-20261024/`。测试使用合成Provider、合成消息/图片、独立AstrBot根目录及SQLite；原生摘要阈值、工具/媒体完整性、取消、失败、送达历史投影、重开持久化，以及80轮适配单独验证。未做T3真实模型摘要或T4真实QQ压缩，未改变既有体验矩阵结论。

最终联合验证 `final-isolated-r6/summary.json`：**59/59 PASS，0失败、0错误、0跳过**。其中staging 9、Runtime 24、原生上下文及实际Agent链13、失败回退13。实际原生Agent在摘要超时/空正文后正常生成final，收到最近80轮；低Token的102轮全部保留。真实Guard诊断沿用`model_request.context_compression`并保留旧元数据；合成SQLite重开、已确认气泡历史及原始入站档案核对通过。

最终VPS候选目录：`/opt/qq-ai-companion/releases/phase3-20261024-context-compression-r2/`，包含两份配置的baseline/candidate、四个Runtime文件副本及准备回执；r1保留为早期候选，r2修正诊断字段并取代r1。只读核对两容器ID/启动时间/重启计数不变，两份运行配置与Runtime源码哈希均未改变；候选通过现有生产策略校验，候选源码哈希与最终受测源码一致。证据为`production-pending-receipt-r2.json`、`final-summary.json`，真实模型调用0，新增API费用0。

`scripts/phase3-context-config.py` 只读取受保护的两份配置，在新的私有目录保存baseline、candidate和脱敏manifest，保留BOM，文件600/目录700，拒绝符号链接输入、既有输出、源码data子目录与Git工作区内的Secret候选。公开源码不包含生产配置或资料。Phase3 staging复用相同字段变换；初始Phase2模板不改。

正式生效前，等用户统一生效指令，重新读取最新配置和源码，与baseline比较并处理冲突；只合并manifest列出的8个叶子字段及已审阅的Runtime适配，不复制陈旧的完整配置。重新运行策略及受影响隔离测试，按现有发布流程停写备份、隔离恢复，再窄范围安装并重启核验。

候选尚未应用时无需回滚生产。生效后可将失败轮次开关设0关闭第一方80轮适配；如撤销C，依据当时备份仅恢复压缩相关字段，不恢复整个data。已经压缩的原生会话不会因恢复配置自动变回全量历史，不能以旧数据库覆盖新增聊天。
