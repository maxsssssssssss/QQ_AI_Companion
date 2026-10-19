#!/usr/bin/env python3
"""Map executed evidence to the user's twenty mandatory acceptance criteria."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from provenance import source_manifest

ROOT = Path(__file__).resolve().parents[1]
suite, integration = (Path(x).resolve() for x in sys.argv[1:3])
exits = dict(zip(("doctor", "unit", "future_tasks", "integration"), map(int, sys.argv[3:7])))


def read(path):
    return json.loads(path.read_text()) if path.is_file() else {}


doctor = read(suite / "doctor.json")
unit = read(suite / "unit.json")
runtime = read(integration / "results.json")
repro = read(ROOT / "evidence/repro.json")
repro_current = repro.get("status") == "PASS" and repro.get("source_manifest") == source_manifest(ROOT)
checks = {c["id"]: c for c in runtime.get("results", [])}
doctor_checks = {c["id"]: c for c in doctor.get("checks", [])}


def good(*names):
    return all(checks.get(n, {}).get("status") == "PASS" for n in names)


def dg(*names):
    return all(doctor_checks.get(n, {}).get("status") == "PASS" for n in names)


unit_good = exits["unit"] == 0 and unit.get("passed") is True and unit.get("tests_run", 0) > 0 and not unit.get("skipped")
tracked_count = len(subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines())
definitions = [
    ("T01", "项目初始化与重建", dg("files", "dependency_hashes") and repro_current, "doctor/files+dependency_hashes; evidence/repro.json（当前源码hash与干净副本一致）"),
    ("T02", "配置加载及错误", unit_good and dg("config") and good("B08_RESTART"), "unit/configuration; doctor/config; B08（AstrBot重写配置后重新校验）"),
    ("T03", "无真实密钥启动", good("B01_REAL_START", "B02_PRIVATE_E2E") and dg("config"), "仅固定offline-fixture凭据，Mock HTTP；B01+B02"),
    ("T04", "AstrBot真实进程", good("B01_REAL_START"), "startup-*.json; astrbot-*.log; B01"),
    ("T05", "OneBot真实连接", good("B04_ONEBOT_AUTH", "B06_RECONNECT"), "真实WS鉴权、重连与请求；B04+B06"),
    ("T06", "私聊字段进入框架", good("B02_PRIVATE_E2E"), "真实hook持久化sender/session/text/message_id并与OneBot事件对照；B02"),
    ("T07", "框架调用Mock模型", good("B02_PRIVATE_E2E"), "transcript.jsonl 中HTTP请求和随机标识响应；B02"),
    ("T08", "生成与发送分开验证", good("B02_PRIVATE_E2E", "B15_SEND_FAILURES"), "独立LLM response、OneBot action/ack及guard状态；B02+B15"),
    ("T09", "连续中文与Emoji", good("B12_CHINESE_BURST"), "三条突发消息各一次、处理顺序上下文累积、原文及最终历史完整；不保证入站FIFO/合并；B12"),
    ("T10", "未授权用户拒绝", good("B05_ALLOWLIST", "B04_ONEBOT_AUTH"), "无效连接token和非白名单私聊/群聊无模型调用；B04+B05"),
    ("T11", "重复消息去重", good("B13_DEDUP", "B08_RESTART"), "一条原文/一次模型/一次发送；重启重传仍拒绝；B13+B08"),
    ("T12", "持久化与重启", good("B07_PERSISTENCE", "B08_RESTART"), "持久卷SQLite integrity_check，换PID后历史重用和guard去重；B07+B08"),
    ("T13", "备份恢复", good("B09_BACKUP_RESTORE", "B17_EXPORT") and unit_good, "停写全状态ZIP、hash核验、新目录真实启动与导出；B09+B17+backup单测"),
    ("T14", "模型三类故障", good("B10_LLM_FAILURE_RECOVERY"), "503、HTTP超时、无效200响应；明确错误回复且正常请求恢复；B10"),
    ("T15", "发送故障状态", good("B15_SEND_FAILURES"), "OneBot retcode失败→failed，丢ack→unknown，正常ack→sent；B15"),
    ("T16", "插件兼容性分级", all("plugin_" + n + "_loaded" in checks for n in ("message_recorder", "memory_companion", "private_companion")) and (ROOT / "docs/compatibility.md").is_file(), "docs/compatibility.md + plugins-lock.json + 每项真实插件结果（不要求P1全通过）"),
    ("T17", "外置人格更换", good("B16_PERSONA_SWITCH"), "ALPHA/BETA外置文件，真实请求system prompt变化；B16"),
    ("T18", "北京时间", dg("timezone") and unit_good and good("B07_PERSISTENCE"), "zoneinfo跨日、guard SQLite+08:00、配置Asia/Shanghai；UTC字段另行标注"),
    ("T19", "敏感信息安全", tracked_count > 10 and dg("tracked_safety", "gitignore", "no_public_ports") and good("B11_RUNTIME_ISOLATION"), "已纳入Git的文件扫描+忽略规则+无真实秘密fixture+无外网/端口容器；启发式扫描范围已注明"),
    ("T20", "可重复诊断入口", exits["doctor"] == 0 and exits["unit"] == 0 and exits["integration"] == 0 and bool(checks), "scripts/test-phase1.sh 实际执行doctor、非零单测、真实上游进程套件并保留退出码"),
]
results = [{"id": i, "name": name, "status": "PASS" if passed else ("BLOCKED" if i == "T01" and not repro else "FAIL"), "evidence": evidence}
           for i, name, passed, evidence in definitions]
status = "PASS" if all(r["status"] == "PASS" for r in results) else "PARTIAL"
report = {"phase1_status": status, "p0": results, "p1": [r for r in checks.values() if r["id"].startswith("plugin_")], "exit_codes": exits,
          "unit": unit, "future_tasks": read(suite / "future-tasks.json"), "integration_evidence": str(integration.relative_to(ROOT)),
          "tracked_files_scanned": tracked_count, "real_qq_login": "NOT ATTEMPTED", "vps_deployment": "NOT ATTEMPTED", "real_deepseek_api": "NOT TESTED"}
(suite / "phase1-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
(ROOT / ".runtime/latest-suite").write_text(str(suite) + "\n")
lines = ["# Phase 1 真实验收报告", "", f"Phase 1 Status: **{status}**", "", "本表严格对应用户 T01–T20。测试运行未登录真实 QQ、未调用真实 DeepSeek、未部署 VPS；AstrBot、三个插件、SQLite 与消息协议传输运行真实上游代码。仅 QQ 对端和模型为模拟服务。", "", "| 验收 | 状态 | 证据与范围 |", "|---|---|---|"]
for r in results:
    lines.append(f"| {r['id']} {r['name']} | {r['status']} | {r['evidence']} |")
lines.extend(["", "## 实际执行", "", "```bash", "./scripts/bootstrap.sh", "python3 scripts/fetch_plugins.py", "./scripts/doctor.sh", "./scripts/test-phase1.sh", "```", "", f"本次单元测试：{unit.get('tests_run', 0)}，失败 {unit.get('failures', '?')}，错误 {unit.get('errors', '?')}，跳过 {unit.get('skipped', '?')}。", f"原生 Future Tasks：{report['future_tasks'].get('tests_run', 0)} 项；成功标志 {report['future_tasks'].get('tests_passed', False)}。该套件证明一次性任务**失败后被删除**的限制，不代表生产提醒可靠。", "", f"本次来源：`{suite.relative_to(ROOT)}` 与 `{integration.relative_to(ROOT)}`。最终选定证据另保存于 `evidence/phase1/latest/`。", "", "## P1 真实结果", "", "| 检查 | 状态 |", "|---|---|"])
for r in report["p1"]:
    lines.append(f"| {r['id']} | {r['status']} |")
lines.extend(["", "Recorder 出站覆盖失败是已确认上游限制；本项目 Phase1Guard 仅为受限非流式私聊文本提供双向归档和发送状态。Memory 的模型抽取、完整主动陪伴、所有 WebUI 页面和真实网络服务均不得由这些测试推导通过。", "", "所有机器报告、日志和协议记录仅保存在本地 `evidence/`，不上传 Git；新克隆需按 README 重新生成。未解决限制见 `known-issues.md`。"])
(ROOT / "docs/phase1-validation.md").write_text("\n".join(lines) + "\n")
print(f"Phase 1 Status: {status}; P0 {sum(r['status']=='PASS' for r in results)}/20 PASS; unit {unit.get('tests_run',0)}; report {suite/'phase1-report.json'}")
raise SystemExit(0 if status == "PASS" else 1)
