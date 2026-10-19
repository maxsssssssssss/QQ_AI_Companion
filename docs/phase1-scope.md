# Phase 1 实施边界与验收索引

权威来源：用户在当前开发会话补充的完整《QQ AI Companion — Phase 1 完整开发任务书》。本文件是执行索引，完整用户任务书优先。

使用唯一主框架 AstrBot；普通 QQ 的未来接入优先 NapCat，SnowLuma 仅备选。本阶段只模拟 OneBot v11 和 LLM，不登录 QQ、不部署 VPS、不使用真实个人数据、不公开端口、不未经授权推送远程、不改第三方源码。使用现成能力和公开接口，只有确认缺口后添加最小补充。

| P0 | 必须验证的行为 |
|---|---|
| T01 | 独立项目可重建，结构/依赖/文档/配置/Git 状态可核查 |
| T02 | 模型/模拟身份/白名单/存储/时区/插件配置能加载；错误明确 |
| T03 | 无真实 API Key 的 Mock 环境启动和测试 |
| T04 | 真正启动固定版本 AstrBot 进程 |
| T05 | 真正连接 AstrBot aiocqhttp OneBot v11 适配器 |
| T06 | 私聊 sender/session/text/message_id 正确进入框架 |
| T07 | 框架通过真实 HTTP 调用 Mock LLM |
| T08 | 生成与发送分别观测，回复到模拟 OneBot 接收端 |
| T09 | 连续中文/Emoji无遗漏/重复，明确当前逐条/合并行为 |
| T10 | 非白名单不能触发私人模型或敏感工具；不是 Prompt 限权 |
| T11 | 重复消息 ID 不重复记录或处理；检查归档真实行为 |
| T12 | 持久卷数据重启后可读 |
| T13 | 真备份恢复到隔离目录，验证内容 |
| T14 | HTTP错误、超时、无效模型响应明确失败并可恢复 |
| T15 | 发送失败/超时不误记成功，保留可诊断状态 |
| T16 | 插件文档/版本/安装/加载/基础功能/未验证/许可等分级真实记录 |
| T17 | 外置合成人格能加载和更换 |
| T18 | Asia/Shanghai配置及真实日期/存储/转换行为 |
| T19 | Git忽略、样例、数据、日志、Docker及快照不泄露真实秘密 |
| T20 | doctor/test-phase1等诊断入口实际执行、正确退出码 |

P0 状态只能 PASS / FAIL / SKIPPED / BLOCKED / NOT IMPLEMENTED；只有全部 PASS 才能宣称 Phase 1 PASS。

P1 尽可能完成：Recorder 入站/出站/主动消息覆盖；Memory 基础保存检索和抽取；Private 基础陪伴并关闭虚构日程；两者桥接且不重复上下文；原生合并能力；Future Tasks 一次性任务/时区/失败保留（特别检查失败被删除）；DeepSeek 原生 Search/Fetch；数据导出/WebUI管理。P1 问题不撤销 P0 已验证成果。

主模型需求 deepseek-flash，复杂任务 deepseek-v4-pro，须以执行时官方资料为准；无真实 Key 时仅 Mock 运行，不捏造官方有效性或定价。未来时区固定北京时间。初始人格60%亲密女性朋友+40%恋人式陪伴，明确AI、不虚构现实活动；不命名、不决定外貌或完整背景。

交付 README、architecture/dependencies/compatibility/phase1-validation/security/known-issues/phase2-deployment 文档、固定依赖、配置、模拟器、真实运行证据。Phase2仅部署准备文档（30–150元/月预算、VPS资源、未来扫码/监控/真实API步骤）；本轮不执行。
