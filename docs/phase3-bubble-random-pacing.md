# 气泡随机间隔：2–4秒

2026-10-29 23:44 +08:00，[首泡0.8–1.8秒等待](phase3-first-bubble-delay.md)已上线，后泡继续本报告的独立随机2–4秒。163项相关自动验证、14库隔离恢复和上线健康14/14通过，仅重启AstrBot；以下“首条立即”和195项测试是2026-10-28发布时的原始记录，不代表现行版本仍零首泡等待。

2026-10-28，按用户授权将后续气泡等待从按长度计算的1–2秒改为每次独立均匀随机2–4秒。首条生成并准备好后立即交给原发送出口，不添加拟人等待。

## 实现及范围

- `Pacing.delay()` 每次调用 `random.uniform(2.0, 4.0)`；同一轮第二、三、四条各抽取一次，文本长短不参与计算。
- 沿用原循环：先确认上一条发送，再等待下一条；等待仍按最多0.1秒片段检查取消。这是检查片段上限，不是事件循环墙钟时延保证。
- Guard逐泡ID、发送确认、失败/unknown停尾、插话及停机取消、账本和确认历史逻辑均未改。模型标记及V4兜底的分段内容和数量不变，普通/搜索最终/主动文本共用同一路径。
- 仅发布 `phase1_guard/main.py`、`bubbles.py`、`_conf_schema.json`，及现有Guard配置中 `phase3_bubble_min_interval=2.0`、`phase3_bubble_max_interval=4.0` 两项。默认配置和新候选staging同步更新；旧历史发布器的固定版本契约保留。
- V0.4、Thinking Off、记忆、搜索、自主选题及主动次数/时段/冷却均保留。QQ客户端显示间隔还受API确认、网络及事件循环调度影响，2–4秒指程序主动等待。

## 回归

| 范围 | 结果 |
|---|---:|
| 气泡组件：随机采样、首条零等待、确认顺序、取消、failed/unknown及重启 | 68/68 PASS |
| 固定AstrBot/Private/Guard原生隔离联调及确认历史 | 46/46 PASS |
| 原生主动 | 37/37 PASS |
| 自主桥与补充路径 | 19/19 PASS |
| 新候选staging | 10/10 PASS |
| 窄发布/回滚与真实SQLite/ZIP隔离恢复夹具 | 15/15 PASS |

上述共195项，无失败、错误或跳过。另有10项发布包校验通过。前五组执行期间源码/测试哈希稳定；使用合成事实、本机模拟OneBot HTTP及固定依赖，真实模型/工具/QQ实验调用均为0，不将隔离发送冒充QQ体验。

私有证据：`evidence/phase3/bubble-random-pacing-20261028/isolated-r1/`、`release-tests.json`。

## 发布及回退

**已上线。** 北京时间2026-10-28 **08:39:22 +08:00** 仅重启AstrBot，**08:40:12** 健康核验12/12 PASS。14个SQLite的一致备份和隔离恢复PASS；QQ在线、OneBot已建立连接，NapCat容器/启动时间/重启次数未变。线上三文件哈希与候选一致，Guard其余字段、完整Runtime自主配置及其源码均保持。

源清单SHA256：`24c0fc5d1bf7aa7bc802c55e25b794993ff314cdb4857d7cc2724ccbaf35d251`；包SHA256：`b2c81e8c693e6bd58f95545204f54551a4077a5e2a9d34effde56b2c3c9ea3f1`。备份位于 `/opt/qq-ai-companion/deployment/backups/phase3-20261028-bubble-pacing-r1`。启动检查未见Traceback/ERROR；本次健康检查没有发送QQ测试消息或调用模型，不把连接检查当作用户节奏体验评价。

实际执行回执见同一私有证据目录的 `release-stage.json`、`release-plan.json`、`release-apply.json`、`release-health.json`。

发布目录：`/opt/qq-ai-companion/releases/phase3-20261028-bubble-pacing-r1/source`。仅重启AstrBot；先一致备份、SQLite检查及隔离恢复成功后再替换。配置与源码之外的数据不回灌。

回退入口（默认PLAN；执行时另加 `--apply`，使用新的run-id）：

```bash
sudo python3 /opt/qq-ai-companion/releases/phase3-20261028-bubble-pacing-r1/source/scripts/phase3-deploy-bubble-pacing.py --source /opt/qq-ai-companion/releases/phase3-20261028-bubble-pacing-r1/source --run-id bubble-pacing-rollback-NEW-ID --rollback
```

仅还原本批三个文件及两项参数到旧1–2秒按长度等待；保留新增聊天、记忆、研究进度、发送账本及自主功能。若这些文件或参数发生后续修改，控制器拒绝覆盖，需要核对新的发布顺序。
