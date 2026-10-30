# 首条气泡随机等待：0.8–1.8秒

2026-10-29，按用户确认新增首条气泡的随机拟真等待。**163项相关自动验证通过，已于23:44:27 +08:00仅重启AstrBot生效，23:44:58健康核验14/14 PASS。** 后续气泡继续独立随机等待2–4秒，本次不改变分段、主动选题、内容修订策略或人格。

## 实际行为与配置

| 项目 | 本次显式配置 | 含义 |
|---|---:|---|
| `phase3_first_bubble_min_delay` | `0.8` | 每个新Guard发送计划首泡等待下限，秒 |
| `phase3_first_bubble_max_delay` | `1.8` | 每个新Guard发送计划首泡等待上限，秒 |
| `phase3_bubble_min_interval` | `2.0` | 后续气泡等待下限，保持现值 |
| `phase3_bubble_max_interval` | `4.0` | 后续气泡等待上限，保持现值 |

- 首条已经生成并形成有效发送计划后，独立调用一次均匀随机采样，等待0.8–1.8秒再交给发送出口。等待不是模型生成时间的一部分，也不在生成期间提前消耗。
- 普通回复、搜索final和主动文本使用同一套发送循环；只有一条气泡时也等待。主动兴趣表达和独立补充只要走该出口，也按各自的新发送计划处理。
- 搜索progress继续立即发送，不进入这次新增的首泡等待。它与final有独立的计划身份，不能借此重复发送进度或绕过主动发送限制。
- 第二、三、四条仍在上一条得到确认之后，分别独立抽取2–4秒等待；不根据正文长度调整，不额外叠加一次首泡等待。
- 以上数值是程序主动等待范围。QQ客户端实际可见的间隔还包含发送API、网络和事件循环调度时间，不承诺墙钟间隔严格落在该范围内。

新增两项schema默认值均为`0.0`，未配置的旧实例继续零首泡等待；这保留旧配置的兼容性。`scripts/phase3-stage.py`生成的新候选和本次窄发布则明确写入`0.8/1.8`，因此不是依赖schema默认值开启。首泡配置只接受有限数值，要求`0 ≤ min ≤ max ≤ 4`；本次发布器还要求候选值严格为`0.8/1.8`。

## 取消、确认与计数

首泡等待复用既有可中断等待函数，每次最多休眠0.1秒后重新检查轮次状态。新入站、supersede、取消、停机等导致旧计划失效时，尚未发送的气泡终结为cancelled；0.1秒是轮询片段上限，不是进程繁忙时的实时响应保证。

等待位于发送调用外部，**不消耗既有2秒transport timeout**。实际网络发送仍适用原超时、确认和unknown规则。重复计划在随机采样前由既有幂等检查拦住；首泡取消、失败、unknown以及重启后均不恢复整组重发。

主动发送尝试沿用原来先预留的口径：若首泡等待时被取消，此次已经预留的主动尝试不退款，也不会把未发出的正文写成已确认。其余额度、冷却和失败处理均不改变。90分钟最短冷却、96次评估机会、12轮发送尝试及北京时间09:00–23:00保持现行配置。

实际正文、逐泡账本、发送确认及模型历史仍由原Guard路径处理，只将确认部分投影到历史。没有新增发送出口、模型调用、历史存储或调度器，也不启用AstrBot全局分段。V0.4、Thinking Off、V4分段规则、记忆和Search/Fetch保持。

## 修改范围

运行时只更新以下三个第一方Guard文件：

- `integrations/phase1_guard/main.py`：绑定两项首泡参数，并输出不含正文或凭据的实际加载范围日志。
- `integrations/phase1_guard/bubbles.py`：首泡独立采样、沿用可中断等待。
- `integrations/phase1_guard/_conf_schema.json`：新增兼容默认值与配置说明。

配套更新`phase3-stage.py`；新增`phase3-deploy-first-bubble-delay.py`、首泡组件/固定原生/窄发布测试。原`phase3-deploy-bubble-pacing.py`和其历史证据保持不变，旧报告中的“首条立即”描述属于当时的发布行为。

## 自动验证

| 范围 | 结果 |
|---|---:|
| 气泡组件、首泡行为、新候选staging、窄发布与回滚事务 | 109/109 PASS |
| 新增固定AstrBot/Private/Guard原生首泡联调 | 8/8 PASS |
| 既有固定AstrBot/Private/Guard原生回归 | 46/46 PASS |
| 合计 | **163/163 PASS，0 skip** |

覆盖随机范围与独立采样、单泡、旧配置零等待、模型标记多泡、progress立即而final延迟、主动发送、插话/停机取消、重复计划、网络failed/unknown、账本与确认历史，以及重启不重发。原生用例还实际等待1.8秒，再通过约0.4秒的本机HTTP发送确认，验证新增等待不会误触2秒transport timeout。

发布事务用真实临时SQLite/ZIP数据验证停写备份、隔离恢复、源文件及配置漂移保护、故障回退、新数据保留。固定原生联调使用锁定依赖与本机合成OneBot对端，没有真实QQ发送或额外DeepSeek调用；不能据此宣布真实QQ自然度通过。既有46项原生回归耗时53.350秒，三个Guard文件的运行前后哈希稳定。

## 发布组合与实际回执

基线为`phase3-20261029-autonomy-closeout-r2`。发布器要求线上后泡已是`2.0/4.0`，首泡字段只能同时不存在或同时为数值`0/0`；出现其他修改立即拒绝覆盖。候选必须通过完整文件清单、哈希、schema和运行时参数绑定校验。

| 发布证据 | 当前值 |
|---|---|
| 实际发布目录名 | `phase3-20261029-first-bubble-delay-r1` |
| 冻结源清单SHA256 | `df88c88cfdb3c3c75eb484d56bd0b09e771e2b260d1a3fb89d744561cf1e5dc8` |
| 冻结发布包SHA256 | `cd0b8b186276122ec1d6464ccbf74ace329becfa12d8d9ee09737b27c2116811` |
| AstrBot启动时间 | `2026-10-29 23:44:27 +08:00` |
| 健康核验时间及结果 | `2026-10-29 23:44:58 +08:00`，14/14 PASS |
| 一致性备份及隔离恢复 | 14个SQLite通过，备份目录`/opt/qq-ai-companion/deployment/backups/phase3-20261029-first-bubble-delay-r1` |
| 线上实际加载参数 | 启动日志`first=0.8..1.8s; later=2.0..4.0s; progress=immediate` |
| 线上源码与配置 | 候选Guard源核对通过，Runtime配置及自主源码hash保持 |
| QQ/OneBot状态 | QQ在线，OneBot连接数1 |
| NapCat保持 | 容器、镜像、启动时间及重启计数均未变 |
| 数据保持及错误检查 | 未迁移或覆盖聊天/记忆，首泡两字段之外的配置保持；检查窗口内Traceback/ERROR均为0 |
| 真实QQ首泡体验与用户自然度 | 本轮尚未验收 |

发布事务复用固定版本引擎：检查在途工作，停止AstrBot写入，创建一致性备份并在隔离目录验证恢复，通过后只替换三源码及两项首泡字段，再启动原AstrBot容器。NapCat未重启、未改配置，未复制登录态到另一个实例。实际健康核验同时确认加载参数、源与配置范围、QQ/OneBot在线；不只依赖容器启动状态。

冻结源码位于`/opt/qq-ai-companion/releases/phase3-20261029-first-bubble-delay-r1/source`。私有证据位于`evidence/phase3/first-bubble-delay-20261029/`：`test-summary.json`、`release-stage-1.json`、`release-plan-1.json`、`release-apply-1.json`、`release-health-1.json`；原生测试日志位于对应`.runtime/first-bubble-delay-20261029/`。私有目录不提交Git。本轮真实QQ测试消息与模型测试调用均为0，实际上线和在线健康结果不能替代用户对首泡节奏的体验评价。

## 控制器与窄回滚

入口为`scripts/phase3-deploy-first-bubble-delay.py`，默认只生成PLAN，`--apply`才执行。下例是本次发布前已执行的PLAN入口。安装完成后基线已变化，重复运行该安装PLAN会拒绝；当前状态应读取实际回执或运行只读健康检查：

```bash
sudo -n python3 /opt/qq-ai-companion/releases/phase3-20261029-first-bubble-delay-r1/source/scripts/phase3-deploy-first-bubble-delay.py \
  --source /opt/qq-ai-companion/releases/phase3-20261029-first-bubble-delay-r1/source \
  --run-id phase3-20261029-first-bubble-delay-r1
```

已安装候选需要回退时，通过同一冻结控制器恢复原三源码及两项首泡字段；此前不存在的字段恢复为不存在，此前显式`0/0`则恢复为`0/0`。先执行以下回退PLAN，核对版本与范围；确认后在相同命令末尾增加`--apply`才会真正回退：

```bash
sudo -n python3 /opt/qq-ai-companion/releases/phase3-20261029-first-bubble-delay-r1/source/scripts/phase3-deploy-first-bubble-delay.py \
  --source /opt/qq-ai-companion/releases/phase3-20261029-first-bubble-delay-r1/source \
  --run-id phase3-20261029-first-bubble-delay-r1-rollback-1 \
  --rollback
```

回滚仍只重启AstrBot，保留部署后新增聊天、发送记录、记忆、研究进度和计数，不用旧整库覆盖生产数据。若源文件或本次拥有的配置字段被其他变更改动，控制器拒绝直接覆盖，需先核对实际版本。后续气泡2–4秒以及其他配置不属于这次回滚的修改范围。

仅关闭新增等待也可将两项首泡配置设为`0/0`，但需要重载Guard才生效；本次没有执行关闭。改变这两项后若再使用窄回滚，需先核对配置漂移，不能绕过控制器保护。

工程结论限于首泡等待及其兼容性。上一轮内容可靠性的PARTIAL、原失败记录和真实体验边界继续保留，不因这次节奏改动升级为整体PASS。私有发布回执与运行审计保持在受保护目录，不纳入Git。
