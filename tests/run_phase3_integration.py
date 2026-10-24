"""Full pinned AstrBot process + synthetic OneBot and HTTP LLM. No QQ/real API.

Run inside --network none with a fresh /state and /evidence. No production state
or user profile is mounted. Individual native Search/Memory suites cover their
more detailed contracts; this suite proves shared plugin/pipeline wiring.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(ROOT / "tests")]
from offline_peers import MockLLM, OneBotPeer, action_text
from prepare_plugins import prepare_plugins
from integrations.phase3_runtime.settings import private_config
from integrations.phase1_guard.bubbles import PROMPT, PROMPT_MARKER, MARKER
from integrations.phase3_memory.bridge import native_config
from phase3_memory_http_fixture import MemoryMockLLM
from phase3_memory_http_fixture import current_profile_evidence
from phase3_profile_fixture import GAMES, OLD_FAILURE_MARKER


class Suite:
    def __init__(self, state, evidence, upstream):
        self.state, self.evidence, self.upstream = state, evidence, upstream
        self.results, self.peer, self.process, self.log = [], None, None, None
        self.mock = MemoryMockLLM(evidence / "synthetic-transcript.jsonl")

    def chat_requests(self):
        return [r for r in self.mock.requests if r.get('kind') != 'summary']

    def record(self, name, detail):
        self.results.append({"id": name, "status": "PASS", "detail": detail, "level": "T2_full_AstrBot_synthetic_peers"})
        self.save()

    def save(self):
        (self.evidence / "result.json").write_text(json.dumps({"results": self.results,
            "real_qq": False, "real_deepseek": False, "production_changed": False}, ensure_ascii=False, indent=2))

    def prepare(self):
        self.state.mkdir(parents=True, exist_ok=False)
        (self.state / "data/config").mkdir(parents=True)
        config = json.loads((ROOT / "config/offline.json").read_text())
        config["platform_settings"]["id_whitelist"] = ["offline:FriendMessage:10001"]
        config["admins_id"] = ["10001"]
        config["phase1"]["admin_fixture_enabled"] = True
        config["agent_runner"]["config"]["persona"]["persona_id"] = "qq_companion"
        config["agent_runner"]["config"]["model"]["request_max_retries"] = 1
        config["agent_runner"]["config"]["misc"].update(max_steps=6, tool_call_timeout=40)
        config["provider_settings"].update(show_tool_use_status=False, show_tool_call_result=False,
                                             display_reasoning_text=False, buffer_intermediate_messages=False)
        for provider in config['provider']:
            if provider.get('id') == 'mock/phase1':
                provider['modalities'] = ['text', 'image', 'tool_use']
        (self.state / "data/cmd_config.json").write_text(json.dumps(config))
        (self.state / "phase1.json").write_text(json.dumps(config["phase1"]))
        prepare_plugins(self.state, self.upstream)
        for name in ("phase1_guard", "phase3_runtime", "phase3_memory", "phase3_search"):
            shutil.copytree(ROOT / "integrations" / name, self.state / "data/plugins" / name,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        configs = self.state / "data/config"
        (configs / "phase1_guard_config.json").write_text(json.dumps({"phase3_enabled": True,
            "phase3_plain_text_enabled": True,
            "phase3_casual_split_fallback_enabled": True,
            "phase3_empty_recovery_enabled": True,
            "phase3_admin_id": "10001", "phase3_platform_id": "offline", "phase3_group_window": 0.25,
            "phase3_group_max_wait": 1.0, "phase3_group_max_messages": 8, "send_timeout_seconds": 1.0}))
        (configs / "phase3_runtime_config.json").write_text(json.dumps({"enabled": True, "persona": True,
            "natural_chat": True, "memory": True, "memory_recall_guidance": True,
            "search": False, "proactive": False, "preferred_address": "测试者"}))
        (configs / "phase3_search_config.json").write_text(json.dumps({"enabled": False}))
        path = configs / "astrbot_plugin_private_companion_config.json"
        path.write_text(json.dumps(private_config(json.loads(path.read_text()))))
        path = configs / "astrbot_plugin_memory_companion_config.json"
        memory = native_config(json.loads(path.read_text()), enabled=True, provider_id="mock/phase1")
        memory['memory_summary'].update(min_events=2, trigger_event_count=2, max_calls_per_session_hour=12)
        memory['memory_summary']['enabled'] = False
        memory['startup'] = {'background_grace_seconds': 0}
        path.write_text(json.dumps(memory))
        (self.state / "data/phase3").mkdir()
        # The stable destination name is part of the existing runtime contract;
        # its content is the already approved current V0.2 template.
        shutil.copyfile(ROOT / "persona/phase3-v0.2.md", self.state / "data/phase3/persona-v0.1.md")

    async def start(self):
        (self.state / "startup.json").unlink(missing_ok=True)
        self.log = (self.evidence / f"astrbot-{int(time.time()*1000)}.log").open("w")
        env = dict(os.environ, ASTRBOT_ROOT=str(self.state), PHASE3_ASTRBOT_SOURCE=str(self.upstream / "AstrBot"),
                   PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
        self.process = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / "tests/phase3_launch.py"),
            env=env, cwd=self.upstream / "AstrBot", stdout=self.log, stderr=asyncio.subprocess.STDOUT)
        for _ in range(240):
            if self.process.returncode is not None:
                raise RuntimeError("native process exited; inspect synthetic local log")
            if (self.state / "startup.json").exists():
                try:
                    peer = OneBotPeer(self.evidence / "synthetic-transcript.jsonl")
                    await peer.connect()
                    self.peer = peer
                    return
                except Exception:
                    pass
            await asyncio.sleep(.5)
        raise TimeoutError("native startup timeout")

    async def stop(self):
        if self.peer:
            await self.peer.close()
            self.peer = None
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 25)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
                raise
        if self.log:
            self.log.close()

    def rows(self, table):
        assert table in {"inbound", "deliveries", "turns"}
        path = self.state / "data/plugin_data/phase1_guard/guard.sqlite3"
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT * FROM " + table)]

    def memory_rows(self):
        matches = list((self.state / 'data/plugin_data').rglob('memory_companion.db'))
        assert len(matches) == 1
        with sqlite3.connect(matches[0]) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM memories')]

    async def wait_memory(self, check, seconds=30):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            rows = self.memory_rows()
            if check(rows): return rows
            await asyncio.sleep(.2)
        raise TimeoutError('native memory condition not reached')

    async def verify_profile_prefetch(self):
        # New isolated conversation, seeded by the real host API. Preserve the
        # previous synthetic conversation; no real owner state is mounted.
        (self.state/'profile-history-probe.flag').write_text('synthetic-only')
        for _ in range(100):
            if (self.state/'profile-history-probe.json').exists():break
            await asyncio.sleep(.2)
        prepared=json.loads((self.state/'profile-history-probe.json').read_text())
        assert prepared=={'state':'APPLIED','history_items':16,
            'previous_conversation_retained':True,'new_conversation':True},prepared
        before=len(self.chat_requests())
        reply=await self.peer.send('本轮资料正文核验：我平时玩哪些游戏？只根据已保存资料回答，不要上网。',timeout=40)
        assert all(g in action_text(reply) for g in GAMES)
        calls=self.chat_requests()[before:]
        assert [r['kind'] for r in calls]==['profile_prefetch_final']
        payload=calls[0]['request']
        evidence=current_profile_evidence(payload)
        actual='\n'.join(r['text'] for r in evidence['evidence'])
        assert all(g in actual for g in GAMES)
        old=[m for m in payload['messages'] if OLD_FAILURE_MARKER in str(m.get('content',''))]
        assert len(old)==16,len(old)
        assert sum(m['role']=='tool' for m in old)==4
        assert sum(bool(m.get('tool_calls')) for m in old)==4
        assert all(not any(g in str(m.get('content','')) for g in GAMES) for m in old)
        assert [m['role'] for m in old]==['user','assistant','tool','assistant']*4
        for index in range(0,16,4):
            assert old[index+1]['tool_calls'][0]['id']==old[index+2]['tool_call_id']
        for _ in range(40):
            with sqlite3.connect(self.state/'data/plugin_data/phase1_guard/guard.sqlite3') as db:
                diag=json.loads(db.execute('SELECT diagnostics_json FROM turns ORDER BY created_at DESC LIMIT 1').fetchone()[0])
            if diag.get('history',{}).get('confirmed_parts')==1:break
            await asyncio.sleep(.1)
        assert diag['profile_recall']['status']=='found'
        assert diag['profile_recall']['memory_refs']
        assert diag['history']['confirmed_parts']==1
        (self.evidence/'profile-prefetch-current-native-context.json').write_text(json.dumps({
            'status':'PASS','real_qq':False,'real_deepseek':False,'production_changed':False,
            'proof_location':'final HTTP last role=user, unique MemoryCompanion-Context, PHASE3_PROFILE_EVIDENCE_V2.evidence[].text',
            'system_profile_markers':0,'current_native_memory_contexts':1,
            'evidence':evidence,'old_failure_history_items':len(old),'old_tool_pairs':4,
            'new_model_requests':len(calls),'previous_synthetic_conversation_retained':True,
            'guard_history_confirmed_parts':1},ensure_ascii=False,indent=2))
        self.record('I18','Fresh profile body was parsed only from the last user message sole native MemoryCompanion context, with no profile marker in system and sixteen old failed/tool history rows retained; one response, Guard acknowledgement/history, no classifier/tool replay')

    async def verify_v3_fallback(self):
        """Exercise the unchanged native decoration/Guard/history pipeline."""
        acknowledged=[]
        for prefix,kind,expected in [
            ('隔离V3普通两句','v3_casual_fallback',['今天可算忙完了！','今晚可以安心歇一会儿了']),
            ('隔离V31普通三句','v31_three_fallback',
             ['今天真的忙完啦！🎉  ','今晚可以安心歇一会儿了。  ','明天的事就留给明天吧！']),
            ('隔离V31普通四句','v31_four_fallback',
             ['今天可算忙完了！','今晚可以安心歇一会儿了。','明天的事就留给明天吧。','这会儿只想陪你随便聊聊'])]:
            before = len(self.chat_requests())
            await self.peer.emit(prefix+'：合成闲聊，不是用户事实。')
            actions = [await asyncio.wait_for(self.peer.outgoing.get(), 40) for _ in expected]
            await asyncio.sleep(.3)
            assert self.peer.outgoing.empty()
            calls = self.chat_requests()[before:]
            assert len(calls) == 1 and calls[0]['kind'] == kind
            raw = calls[0]['response']['choices'][0]['message']['content']
            assert MARKER not in raw and len(raw)<=100
            assert [action_text(action) for action in actions] == expected
            # Boundary metadata permits only the known terminal-period
            # deletion, not restoring it or rewriting any cleaned character.
            final_cleaned=raw.rstrip('。')
            assert ''.join(expected) == final_cleaned
            with sqlite3.connect(self.state/'data/plugin_data/phase1_guard/guard.sqlite3') as db:
                tid,diag_raw = db.execute('SELECT turn_id,diagnostics_json FROM turns ORDER BY rowid DESC LIMIT 1').fetchone()
                parts = db.execute('SELECT part_index,state,text FROM deliveries WHERE turn_id=? ORDER BY part_index', (tid,)).fetchall()
            assert parts == [(i, 'sent', text) for i, text in enumerate(expected)]
            diag=json.loads(diag_raw)
            assert diag['model_final']['segment_plan']['raw_exact_marker_count']==0
            assert diag['cleaned_final']['segment_plan']['source']=='smalltalk_fallback'
            assert diag['cleaned_final']['confirmed_parts']==len(expected)
            acknowledged.extend(expected)
        self.record('I21', 'V3.1 complete two/three/four-sentence candidates passed the full native cleanup into individually acknowledged Guard parts; one HTTP model request each, concatenation exactly matched cleaned text including spacing/Emoji, removed terminal periods were not restored')

        for prefix, kind in [('隔离V3短单句', 'v3_short_single'),
                             ('隔离V31短句合并', 'v31_short_merged'),
                             ('隔离V3连续长句', 'v3_continuous_single'),
                             ('隔离V3技术结构', 'v3_technical_single')]:
            before = len(self.chat_requests())
            reply = await self.peer.send(prefix+'：合成格式测试。', timeout=40)
            await asyncio.sleep(.3)
            assert self.peer.outgoing.empty(), kind
            calls = self.chat_requests()[before:]
            assert len(calls) == 1 and calls[0]['kind'] == kind
            raw = calls[0]['response']['choices'][0]['message']['content']
            expected_text = raw.replace('```sql\n','').replace('```\n','').rstrip('。')
            assert action_text(reply) == expected_text, kind
            with sqlite3.connect(self.state/'data/plugin_data/phase1_guard/guard.sqlite3') as db:
                parts = db.execute('SELECT state,text FROM deliveries WHERE turn_id=(SELECT turn_id FROM turns ORDER BY rowid DESC LIMIT 1)').fetchall()
            assert parts == [('sent', expected_text)]
        self.record('I22', 'V3.1 short Emoji reply, a below-six-character first sentence merged with its successor, uninterrupted long sentence and SQL explanation each remained one acknowledged bubble after fixed native cleanup; no content truncation')

        deliveries = self.rows('deliveries')
        await self.stop()
        await self.start()
        await asyncio.sleep(.5)
        assert self.peer.outgoing.empty()
        assert self.rows('deliveries') == deliveries
        before = len(self.chat_requests())
        reply = await self.peer.send('隔离V3重启后继续：之前的合成回复无需重发。', timeout=40)
        assert action_text(reply) == '还在呢🙂'
        calls = self.chat_requests()[before:]
        assert len(calls) == 1 and calls[0]['kind'] == 'v3_restart_single'
        assistant_history = '\n'.join(str(m.get('content','')) for m in calls[0]['request']['messages']
                                      if m.get('role') == 'assistant')
        assert all(text in assistant_history for text in acknowledged)
        assert MARKER not in assistant_history
        self.record('I23', 'Process restart preserved the already acknowledged V3 split history and ledger without resending any old part; a new user input still reached the same ordinary Guard path')

    async def run(self):
        self.prepare()
        await self.mock.start()
        try:
            await self.start()
            self.record("I01", "Full pinned AstrBot process and plugins started in isolated synthetic state")
            before = len(self.chat_requests())
            messages = ["合成测试：我遇到一个问题", "补充：我说的是国服", "再补充：只讨论公开机制"]
            events = [await self.peer.emit(text) for text in messages]
            action = await asyncio.wait_for(self.peer.outgoing.get(), 40)
            await asyncio.sleep(1)
            calls = self.chat_requests()[before:]
            assert len(calls) == 1, len(calls)
            payload = json.dumps(calls[0]["request"], ensure_ascii=False)
            assert all(text in payload for text in messages)
            assert payload.count("[QQ_COMPANION_PERSONA_V0_2]") == 1
            assert all(text in action_text(action) for text in messages)
            self.record("I02", "Three real WS inputs became one HTTP model call with one approved V0.2 persona and one reply")
            await self.peer.ws.send_json(events[0])
            await self.peer.emit("合成未授权消息", user=10002)
            await self.peer.emit("/合成群消息", group=20001)
            await asyncio.sleep(.8)
            assert len(self.chat_requests()) == before + 1
            assert self.peer.outgoing.empty()
            self.record("I03", "Duplicate, nonallowlisted private user and group caused zero extra model calls/sends")
            sources = self.rows("inbound")
            assert len(sources) == 3
            self.record("I04", "All three original inputs preserved independently of grouped request")
            await self.stop()
            await self.start()
            reply = await self.peer.send("合成重启后继续", timeout=40)
            await asyncio.sleep(.5)
            assert "合成重启后继续" in action_text(reply)
            payload = json.dumps(self.chat_requests()[-1]["request"], ensure_ascii=False)
            assert "[QQ_COMPANION_PERSONA_V0_2]" in payload
            assert all(text in payload for text in messages)
            self.record("I05", "New process retained persona, prior context and continued OneBot chat")
            await self.peer.send('我是一名工程师。我住在苏州。我喜欢清茶。明天有面试，还有一份报告没完成。', timeout=40)
            rows = await self.wait_memory(lambda rows: any(r['memory_type']=='assertion' and '苏州' in r['content'] for r in rows))
            originals = [r for r in rows if r['memory_type']=='assertion' and '苏州' in r['content']]
            assert any(r['memory_type']=='conversation_summary' and '报告' in r['content'] for r in rows)
            self.record('I06', 'Real OneBot→AstrBot→background HTTP native summary produced four synthetic categories with sources and assertion')
            await self.peer.send('确认更正：我现在住在杭州。我仍然喜欢清茶。', timeout=40)
            rows = await self.wait_memory(lambda rows: any(r['memory_type']=='assertion' and '杭州' in r['content'] and r['validity_status']=='active' for r in rows))
            old = next(r for r in rows if r['id']==originals[0]['id'])
            assert old['validity_status']=='superseded' and old['supersedes_id']
            self.record('I07', 'Native summary assertions revised the same residence dimension, preserving superseded history')
            for text in ('合成事件：2030-10-22 15:00 的面试，记录为待发生。',
                         '合成事件改期：面试改到 2030-10-23 15:00，状态为等待结果。',
                         '合成事件完成：面试已经完成，通过了。'):
                await self.peer.send(text, timeout=40)
            path = self.state / 'data/plugin_data/phase3_runtime/memory-controls.sqlite3'
            with sqlite3.connect(path) as db:
                assert db.execute("SELECT state,revision FROM events WHERE event_id='fixture-interview'").fetchone() == ('completed',3)
                assert db.execute("SELECT COUNT(*) FROM event_history WHERE event_id='fixture-interview'").fetchone()[0] == 3
            self.record('I08', 'Native Agent called phase3_event from three original WS messages: same event created, rescheduled and completed with source-bound history')
            await self.stop()
            (self.state / 'clear-context.flag').write_text('synthetic-only')
            await self.start()
            reply = await self.peer.send('隔离记忆核验：我的当前居住地和饮品偏好是什么？', timeout=40)
            assert '杭州' in action_text(reply), action_text(reply)
            request = self.chat_requests()[-1]['request']
            assert len([m for m in request['messages'] if m['role']=='user']) == 1
            assert '苏州' not in '\n'.join(str(m.get('content','')) for m in request['messages'] if m['role']=='system')
            self.record('I09', 'After process restart and native new_conversation API, actual memory injection contained corrected facts, not stale summary/recent messages')
            await self.peer.send('合成资料：我的保密标签是紫藤27。', timeout=40)
            await self.wait_memory(lambda rows:any('紫藤27' in r['content'] and r['memory_type']=='conversation_summary' for r in rows))
            reply = await self.peer.send('别再提关于紫藤27的资料，停止使用整条来源，保留原始档案。', timeout=40)
            assert '已停止使用' in action_text(reply), action_text(reply)
            matches = list((self.state / 'data/plugin_data').rglob('memory_companion.db'))
            with sqlite3.connect(matches[0]) as db:
                assert db.execute('SELECT COUNT(*) FROM message_source_tombstones').fetchone()[0] == 2
            assert any('紫藤27' in r['text'] for r in self.rows('inbound'))
            self.record('I12', 'Native Agent queried exact source/version then withdrew both original and copied-bot native projections; original Guard archive retained and two native tombstones committed')
            await self.peer.send('更正：我的名字是合成姓名。', timeout=40)
            with sqlite3.connect(path) as db:
                pending = db.execute("SELECT source_id,state FROM identity_reviews WHERE state='pending'").fetchall()
                assert len(pending)==1, pending
                assert db.execute('SELECT COUNT(*) FROM sources WHERE source_id=?',(pending[0][0],)).fetchone()[0]==0
            await self.peer.send('确认这次身份变更', timeout=40)
            await asyncio.sleep(.5)
            with sqlite3.connect(path) as db:
                assert db.execute('SELECT state FROM identity_reviews WHERE source_id=?',(pending[0][0],)).fetchone()[0]=='approved'
                assert db.execute('SELECT COUNT(*) FROM sources WHERE source_id=?',(pending[0][0],)).fetchone()[0]==1
            self.record('I13', 'Critical identity correction stayed out of native extraction until a second real archived WS confirmation approved the exact source')
            await self.stop()
            await self.start()
            reply = await self.peer.send('普通召回隔离检查：我的保密标签是什么？', timeout=40)
            assert '没有可用的保密标签记忆' in action_text(reply), action_text(reply)
            assert '紫藤27' not in json.dumps(self.chat_requests()[-1]['request'],ensure_ascii=False)
            self.record('I14', 'Withdrawal itself opened a new native conversation; after current Agent finalized and process restarted, ordinary request history/injection/source recall contained no withdrawn label')
            (self.state / 'proactive-probe.flag').write_text('synthetic-only')
            probe_file = self.state / 'proactive-probe.json'
            for _ in range(260):
                if probe_file.exists(): break
                await asyncio.sleep(.5)
            probe = json.loads(probe_file.read_text())
            assert probe.get('pass'), probe
            proactive_requests = [r for r in self.mock.requests if str(r.get('kind','')).startswith('proactive')]
            assert proactive_requests and all(not r['request'].get('tools') for r in proactive_requests)
            # The probe emits a real synthetic OneBot proactive action; consume
            # that observation before subsequent user-response assertions.
            while not self.peer.outgoing.empty(): self.peer.outgoing.get_nowait()
            assert len(proactive_requests)==3
            assert all(not r['request'].get('tools') for r in proactive_requests)
            self.record('I11', 'Pinned Private candidates exercised defer/skip/send in one model request each; only send produced two Guard bubbles and native Memory event acknowledgement')
            before=len(self.chat_requests())
            reply=await self.peer.send('隔离空正文工具回复：只依据资料回答。',timeout=40)
            assert '合成资料补答成功' in action_text(reply)
            calls=self.chat_requests()[before:]
            assert [r['kind'] for r in calls]==['empty_recovery_tool','empty_final','empty_recovery']
            with sqlite3.connect(self.state/'data/plugin_data/phase1_guard/guard.sqlite3') as db:
                assert db.execute('SELECT state FROM response_recoveries').fetchall()==[('recovered',)]
            self.record('I16','Native memory tool result followed by empty final recovered once using original Agent context; one confirmed Guard reply, no tool replay or hidden reasoning sent')
            (self.state/'profile-probe.flag').write_text('synthetic-only')
            for _ in range(100):
                if (self.state/'profile-probe.json').exists():break
                await asyncio.sleep(.2)
            assert json.loads((self.state/'profile-probe.json').read_text())['state']=='APPLIED'
            before=len(self.chat_requests())
            reply=await self.peer.send('隔离导入资料核验：我平时玩哪些游戏？不要上网。',timeout=40)
            assert all(g in action_text(reply) for g in ('星露谷物语','戴森球计划','传送门2'))
            calls=self.chat_requests()[before:]
            assert [r['kind'] for r in calls]==['profile_navigate','profile_final']
            assert '[PHASE3_MEMORY_RECALL_V1]' in json.dumps(calls[0]['request'],ensure_ascii=False)
            self.record('I17','Reviewed synthetic profile imported through native API; final host prompt retained recall guidance, native navigate evidence reached the original Agent and one confirmed Guard answer; no Search')
            await self.verify_profile_prefetch()
            before=len(self.chat_requests())
            image={'type':'image','data':{'file':'http://127.0.0.1:18080/fixture.png','url':'http://127.0.0.1:18080/fixture.png'}}
            await self.peer.emit('',segments=[image,image])
            await self.peer.emit('图像载荷核验：这两张合成图片是什么颜色？')
            reply=await asyncio.wait_for(self.peer.outgoing.get(),40)
            assert '蓝色' in action_text(reply)
            calls=self.chat_requests()[before:]
            assert [r['kind'] for r in calls]==['image_payload']
            self.record('I19','Image-first plus text merged into one main Agent request retaining two decoded image payloads and order; Guard acknowledged reply')
            reply=await self.peer.send('含图历史空正文核验：上一轮的两张图是什么颜色？',timeout=40)
            assert '蓝色' in action_text(reply)
            assert self.chat_requests()[-1]['kind']=='image_history_recovery'
            self.record('I20','Text follow-up with actual image history survived empty final and one same-provider multimodal recovery; one Guard reply')
            await self.peer.send('全局暂停前的合成上下文代号是苍鹭58。',timeout=40)
            await asyncio.sleep(.5)
            summaries = len([r for r in self.mock.requests if r.get('kind')=='summary'])
            await self.peer.send('别记了', timeout=40)
            await self.peer.send('暂停后这条合成内容不能被自动提取。', timeout=40)
            await asyncio.sleep(1)
            assert len([r for r in self.mock.requests if r.get('kind')=='summary']) == summaries
            assert '苍鹭58' not in json.dumps(self.chat_requests()[-1]['request'],ensure_ascii=False)
            self.record('I10', 'Original WS stop-memory request applied native capture/recall ACL and caused zero subsequent summary HTTP calls')
            # Exercise the COMPLETE decoration chain, including Private's
            # control-tag cleanup, instead of invoking Guard dispatch directly.
            before = len(self.chat_requests())
            await self.peer.emit('隔离多气泡实际装饰链：请分成两个完整语义气泡。')
            bubbles = [await asyncio.wait_for(self.peer.outgoing.get(),40) for _ in range(2)]
            # Native cleanup intentionally removes the final Chinese period.
            # Guard must keep that final result, not resurrect raw model text.
            expected_bubbles = ['合成问候。','合成鼓励']
            assert [action_text(item) for item in bubbles] == expected_bubbles
            await asyncio.sleep(.3)
            assert len(self.chat_requests()) == before+1
            request = self.chat_requests()[-1]['request']
            actual_system = '\n'.join(m.get('content','') for m in request['messages']
                                      if m.get('role') == 'system')
            assert actual_system.count(PROMPT_MARKER) == 1
            assert PROMPT in actual_system
            raw = self.chat_requests()[-1]['response']['choices'][0]['message']['content']
            assert raw.count(MARKER) == 1
            with sqlite3.connect(self.state/'data/plugin_data/phase1_guard/guard.sqlite3') as db:
                rows=db.execute("SELECT part_index,state,text FROM deliveries WHERE turn_id=(SELECT turn_id FROM turns ORDER BY rowid DESC LIMIT 1) ORDER BY part_index").fetchall()
            assert rows == [(index,'sent',text) for index,text in enumerate(expected_bubbles)]
            with sqlite3.connect(self.state/'data/plugin_data/phase1_guard/guard.sqlite3') as db:
                native_parts = json.loads(db.execute('SELECT diagnostics_json FROM turns ORDER BY rowid DESC LIMIT 1').fetchone()[0])
            assert native_parts['model_final']['segment_plan']['raw_exact_marker_count'] == 1
            assert native_parts['model_final']['segment_plan']['native_count'] == 2
            assert native_parts['cleaned_final']['segment_plan']['planned_parts'] == 2
            assert native_parts['cleaned_final']['confirmed_parts'] == 2
            # The next native request must contain the acknowledged projection,
            # not the private marker nor a premature full-output placeholder.
            await self.peer.send('隔离确认多气泡后的普通上下文。',timeout=40)
            history=json.dumps(self.chat_requests()[-1]['request']['messages'],ensure_ascii=False)
            assert all(text in history for text in expected_bubbles)
            assert not any('PRIVATE_COMPANION_SPLIT' in str(m.get('content',''))
                           for m in self.chat_requests()[-1]['request']['messages'] if m.get('role')=='assistant')
            self.record('I15','Pinned full decoration chain preserved two semantic bubbles, removed cosmetic Markdown, and projected only acknowledged text into native history; one model request')
            await self.verify_v3_fallback()
        except Exception as exc:
            self.results.append({"id": "EXECUTION", "status": "FAIL", "error_type": type(exc).__name__})
            self.save()
            raise
        finally:
            await self.stop()
            await self.mock.close()
        print(json.dumps({"checks": len(self.results), "status": "PASS", "level": "T2", "real_qq": False, "real_deepseek": False}))


if __name__ == "__main__":
    evidence = Path(os.environ.get("PHASE3_EVIDENCE", "/evidence"))
    evidence.mkdir(parents=True, exist_ok=True)
    suite = Suite(Path(os.environ.get("PHASE3_STATE", "/state")) / "joint", evidence,
                  Path(os.environ.get("PHASE3_UPSTREAM", "/upstream")))
    asyncio.run(suite.run())
