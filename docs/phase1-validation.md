# Phase 1 真实验收报告

Phase 1 Status: **PASS**

本表严格对应用户 T01–T20。测试运行未登录真实 QQ、未调用真实 DeepSeek、未部署 VPS；AstrBot、三个插件、SQLite 与消息协议传输运行真实上游代码。仅 QQ 对端和模型为模拟服务。

| 验收 | 状态 | 证据与范围 |
|---|---|---|
| T01 项目初始化与重建 | PASS | doctor/files+dependency_hashes; evidence/repro.json（当前源码hash与干净副本一致） |
| T02 配置加载及错误 | PASS | unit/configuration; doctor/config; B08（AstrBot重写配置后重新校验） |
| T03 无真实密钥启动 | PASS | 仅固定offline-fixture凭据，Mock HTTP；B01+B02 |
| T04 AstrBot真实进程 | PASS | startup-*.json; astrbot-*.log; B01 |
| T05 OneBot真实连接 | PASS | 真实WS鉴权、重连与请求；B04+B06 |
| T06 私聊字段进入框架 | PASS | 真实hook持久化sender/session/text/message_id并与OneBot事件对照；B02 |
| T07 框架调用Mock模型 | PASS | transcript.jsonl 中HTTP请求和随机标识响应；B02 |
| T08 生成与发送分开验证 | PASS | 独立LLM response、OneBot action/ack及guard状态；B02+B15 |
| T09 连续中文与Emoji | PASS | 三条突发消息各一次、处理顺序上下文累积、原文及最终历史完整；不保证入站FIFO/合并；B12 |
| T10 未授权用户拒绝 | PASS | 无效连接token和非白名单私聊/群聊无模型调用；B04+B05 |
| T11 重复消息去重 | PASS | 一条原文/一次模型/一次发送；重启重传仍拒绝；B13+B08 |
| T12 持久化与重启 | PASS | 持久卷SQLite integrity_check，换PID后历史重用和guard去重；B07+B08 |
| T13 备份恢复 | PASS | 停写全状态ZIP、hash核验、新目录真实启动与导出；B09+B17+backup单测 |
| T14 模型三类故障 | PASS | 503、HTTP超时、无效200响应；明确错误回复且正常请求恢复；B10 |
| T15 发送故障状态 | PASS | OneBot retcode失败→failed，丢ack→unknown，正常ack→sent；B15 |
| T16 插件兼容性分级 | PASS | docs/compatibility.md + plugins-lock.json + 每项真实插件结果（不要求P1全通过） |
| T17 外置人格更换 | PASS | ALPHA/BETA外置文件，真实请求system prompt变化；B16 |
| T18 北京时间 | PASS | zoneinfo跨日、guard SQLite+08:00、配置Asia/Shanghai；UTC字段另行标注 |
| T19 敏感信息安全 | PASS | 已纳入Git的文件扫描+忽略规则+无真实秘密fixture+无外网/端口容器；启发式扫描范围已注明 |
| T20 可重复诊断入口 | PASS | scripts/test-phase1.sh 实际执行doctor、非零单测、真实上游进程套件并保留退出码 |

## 实际执行

```bash
./scripts/bootstrap.sh
python3 scripts/fetch_plugins.py
./scripts/doctor.sh
./scripts/test-phase1.sh
```

本次单元测试：38，失败 0，错误 0，跳过 0。
原生 Future Tasks：5 项；成功标志 True。该套件证明一次性任务**失败后被删除**的限制，不代表生产提醒可靠。

本次来源：`evidence/runs/phase1-20261019T090920Z-9687` 与 `evidence/runs/20261019T090933Z-10004`。最终选定证据另保存于 `evidence/phase1/latest/`。

## P1 真实结果

| 检查 | 状态 |
|---|---|
| plugin_message_recorder_loaded | PASS |
| plugin_memory_companion_loaded | PASS |
| plugin_private_companion_loaded | PASS |
| plugin_recorder_command | PASS |
| plugin_memory_command | PASS |
| plugin_memory_write | PASS |
| plugin_memory_read | PASS |
| plugin_recorder_non_admin_denied | PASS |
| plugin_memory_non_admin_denied | PASS |
| plugin_private_write | PASS |
| plugin_private_read | PASS |
| plugin_combined_llm_chain | PASS |
| plugin_memory_actual_prompt_injection | PASS |
| plugin_memory_other_user_isolation | PASS |
| plugin_private_other_user_isolation | PASS |
| plugin_recorder_replay_deduplicated | PASS |
| plugin_recorder_outbound_coverage | FAIL |
| plugin_recorder_durable_data | PASS |
| plugin_memory_durable_data | PASS |
| plugin_timeline_durable_data | PASS |
| plugin_private_durable_data | PASS |
| plugin_memory_captured_user_message | PASS |
| plugin_memory_captured_bot_response | PASS |
| plugin_message_recorder_source_unchanged | PASS |
| plugin_memory_companion_source_unchanged | PASS |
| plugin_private_companion_source_unchanged | PASS |
| plugin_restart_recorder_persisted | PASS |
| plugin_restart_memory_persisted | PASS |
| plugin_restart_timeline_persisted | PASS |
| plugin_restart_private_persisted | PASS |
| plugin_restart_memory_readable | PASS |
| plugin_restart_private_readable | PASS |
| plugin_restore_recorder_persisted | PASS |
| plugin_restore_memory_persisted | PASS |
| plugin_restore_timeline_persisted | PASS |
| plugin_restore_private_persisted | PASS |
| plugin_restore_memory_readable | PASS |
| plugin_restore_private_readable | PASS |

Recorder 出站覆盖失败是已确认上游限制；本项目 Phase1Guard 仅为受限非流式私聊文本提供双向归档和发送状态。Memory 的模型抽取、完整主动陪伴、所有 WebUI 页面和真实网络服务均不得由这些测试推导通过。

完整观察、故障详情和原始协议记录见机器报告；未解决限制见 `known-issues.md`。
