#!/usr/bin/env python3
"""Full pinned host + native proactive entry + real Flash/tools + isolated OneBot.

Default: plan only. --backend synthetic validates this exact harness without
real API credentials. --backend real reads credentials on the host, in memory;
production data, login state, services and counters are never modified.
"""
import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import sqlite3
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'scripts')]


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true');p.add_argument('--host',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--backend',choices=['real','synthetic'],default='real')
    p.add_argument('--scenario',choices=['full','interest-research'],default='full',
        help='Narrow interest/research gap uses two native opportunities and a fresh host restore.')
    p.add_argument('--state-root',type=Path);p.add_argument('--output',type=Path);p.add_argument('--upstream',type=Path)
    p.add_argument('--live-config',type=Path);p.add_argument('--live-persona',type=Path)
    p.add_argument('--repetitions',type=int,default=3);p.add_argument('--max-requests',type=int,default=256)
    p.add_argument('--max-tools',type=int,default=512);return p


def plan(args):
    if not 1<=args.repetitions<=6 or not 128<=args.max_requests<=1024 or not 64<=args.max_tools<=1024:
        raise ValueError('bounded_joint_probe_required')
    opportunities=2 if args.scenario=='interest-research' else 5
    return {'backend':args.backend,'scenario':args.scenario,'repetitions':args.repetitions,'native_opportunities':opportunities*args.repetitions,
        'host_processes':2*args.repetitions,'stages':['first','resume'],'real_deepseek':args.backend=='real','real_qq':False,
        'production_changes':False,'credentials_written':False,'production_counters_used':False,
        'isolated_request_limit_per_run':args.max_requests,'isolated_tool_limit_per_run':args.max_tools,
        'model':'deepseek-flash','thinking':'disabled','stream':False,'timeout':120,'max_tokens':8192,
        'entry':'native offer -> candidate pool -> tick -> same Agent -> Guard -> isolated OneBot acknowledgement -> native persistence',
        'clock':'isolated midday clock plus next day restart; never production permission',
        'preference_coverage':'controlled native-supported explicit music expression -> confirmed receipt -> extraction -> next native opportunity; earlier unconstrained misses remain',
        'research_fixture':'PubMed 40511715 is an unverified search lead, not a preseeded fetched source; real Search/Fetch required, 3 tools per opportunity',
        'test_labels':'T2 full native pipeline + T3 real HTTP when real; T4 QQ remains NOT_TESTED'}


def validate_paths(args):
    if not args.state_root or not args.output or not args.upstream:raise ValueError('fresh_state_output_upstream_required')
    state=args.state_root.resolve();out=args.output.resolve()
    if not str(state).startswith('/tmp/') or state.exists() or args.state_root.is_symlink():raise ValueError('fresh_tmp_state_required')
    if out.exists() or args.output.is_symlink() or out==state or out in state.parents or state in out.parents:raise ValueError('separate_fresh_private_output_required')
    if args.backend=='real' and (not args.live_config or not args.live_persona):raise ValueError('readonly_live_input_paths_required')
    return state,out


async def host():
    from phase3_autonomy_joint_probe import run_joint_stage,load_real_helper
    import phase3_launch # Bootstrap only; its synthetic main is not executed.
    from configuration import validate_config
    from astrbot.core import LogBroker,LogManager,db_helper,logger
    from astrbot.core.core_lifecycle import AstrBotCoreLifecycle
    from plugin_permissions import apply_plugin_permissions
    from integrations.phase3_runtime.persona import install_persona
    state=Path(os.environ['ASTRBOT_ROOT']);spec=json.loads((state/'joint-spec.json').read_text())
    validate_config(state/'data/cmd_config.json')
    helper=load_real_helper();broker=LogBroker();LogManager.set_queue_handler(logger,broker)
    core=AstrBotCoreLifecycle(broker,db_helper);await core.initialize();await apply_plugin_permissions(core)
    stars=[p.star_cls for p in core.star_context.get_all_stars() if str(p.module_path).endswith('.phase3_runtime.main') and p.activated]
    if len(stars)!=1:raise RuntimeError('isolated_runtime_missing')
    runtime=stars[0];prompt=await runtime.prepare('10001','offline')
    await install_persona(core.persona_mgr,prompt,tools=runtime.memory_tool_names());await runtime.activate()
    helper.private_json(state/'startup.json',{'pid':os.getpid(),'ready':runtime.ready})
    stop=asyncio.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):asyncio.get_running_loop().add_signal_handler(sig,stop.set)
    task=asyncio.create_task(core.start())
    try:
        while not (state/'joint-go.flag').exists():
            if task.done():await task;raise RuntimeError('native_host_stopped')
            await asyncio.sleep(.1)
        (state/'joint-go.flag').unlink()
        try:result=await run_joint_stage(core,runtime,spec)
        except Exception as exc:result={'pass':False,'error_type':type(exc).__name__,'stage':spec['stage'],'real_qq':False}
        helper.private_json(state/('joint-'+spec['stage']+'.json'),result)
        await stop.wait()
    finally:
        await core.stop();task.cancel();await asyncio.gather(task,return_exceptions=True)


async def execute(args):
    from run_phase3_integration import Suite
    from offline_peers import OneBotPeer,TOKEN
    from aiohttp import web,ClientSession
    from phase3_autonomy_joint_probe import load_real_helper
    info=plan(args);state_root,out=validate_paths(args);helper=load_real_helper()
    # The host changes cwd to AstrBot; resolve caller-relative dependency paths
    # before exporting them to its bootstrap environment.
    args.upstream=args.upstream.resolve()
    if args.backend=='real':helper.live_provider(args.live_config,args.live_persona)
    state_root.mkdir(mode=0o700);out.mkdir(parents=True,mode=0o700);out.chmod(0o700)
    before={str(p):helper.sha(p.read_bytes()) for p in (args.live_config,args.live_persona) if p}
    helper.private_json(out/'plan.json',info);results=[]
    for repetition in range(1,args.repetitions+1):
        state=state_root/('run-'+str(repetition));evidence=out/('run-'+str(repetition));evidence.mkdir(mode=0o700)
        suite=Suite(state,evidence,args.upstream);suite.prepare()
        # Search imports this shared redactor; do not activate the production validator.
        redaction=state/'data/plugins/phase2_policy';redaction.mkdir()
        (redaction/'__init__.py').write_text('')
        shutil.copyfile(ROOT/'integrations/phase2_policy/redaction.py',redaction/'redaction.py')
        cfg=state/'data/config/phase3_runtime_config.json';data=json.loads(cfg.read_text());data['preferred_address']='畅';cfg.write_text(json.dumps(data))
        # Reserve loopback-only fixture ports independently of other suites.
        mock_app=web.Application();mock_app.router.add_post('/v1/chat/completions',suite.mock.complete)
        mock_app.router.add_get('/v1/models',suite.mock.models)
        suite.mock.runner=web.AppRunner(mock_app);await suite.mock.runner.setup()
        mock_site=web.TCPSite(suite.mock.runner,'127.0.0.1',0);await mock_site.start()
        mock_port=mock_site._server.sockets[0].getsockname()[1]
        with socket.socket() as probe_socket:
            probe_socket.bind(('127.0.0.1',0));onebot_port=probe_socket.getsockname()[1]
        main_path=state/'data/cmd_config.json';main_cfg=json.loads(main_path.read_text())
        main_cfg['provider_sources'][0]['api_base']=f'http://127.0.0.1:{mock_port}/v1'
        main_cfg['platform'][0]['ws_reverse_port']=onebot_port
        main_path.write_text(json.dumps(main_cfg))
        class JointPeer(OneBotPeer):
            async def connect(self):
                self.session=ClientSession()
                try:
                    self.ws=await self.session.ws_connect(f'http://127.0.0.1:{onebot_port}/ws',headers={
                        'Authorization':f'Bearer {TOKEN}','X-Self-ID':'90000','X-Client-Role':'Universal'})
                except BaseException:
                    await self.session.close();raise
                self.task=asyncio.create_task(self.receive())
                await self.ws.send_json({'time':int(time.time()),'self_id':90000,'post_type':'meta_event','meta_event_type':'lifecycle','sub_type':'connect'})
        try:
            for stage in ('first','resume'):
                spec={'stage':stage,'backend':args.backend,'scenario':args.scenario,'state':str(state),'max_requests':args.max_requests,'max_tools':args.max_tools,
                    'live_config':str(args.live_config) if args.live_config else '', 'live_persona':str(args.live_persona) if args.live_persona else ''}
                (state/'joint-spec.json').unlink(missing_ok=True)
                helper.private_json(state/'joint-spec.json',spec);(state/'startup.json').unlink(missing_ok=True)
                suite.log=(evidence/('host-'+stage+'.log')).open('w')
                env=dict(os.environ,ASTRBOT_ROOT=str(state),PHASE3_ASTRBOT_SOURCE=str(args.upstream/'AstrBot'),PYTHONPATH=str(ROOT),PYTHONDONTWRITEBYTECODE='1')
                suite.process=await asyncio.create_subprocess_exec(sys.executable,str(Path(__file__).resolve()),'--host',env=env,cwd=args.upstream/'AstrBot',stdout=suite.log,stderr=asyncio.subprocess.STDOUT)
                for _ in range(300):
                    if suite.process.returncode is not None:raise RuntimeError('isolated_host_start_failed')
                    if (state/'startup.json').exists():
                        try:
                            peer=JointPeer(evidence/'synthetic-transcript.jsonl');await peer.connect();suite.peer=peer;break
                        except Exception:pass
                    await asyncio.sleep(.5)
                else:raise TimeoutError('isolated_host_start_timeout')
                (state/'joint-go.flag').write_text('offline-only')
                result_path=state/('joint-'+stage+'.json')
                for _ in range(2400):
                    if result_path.exists():break
                    if suite.process.returncode is not None:raise RuntimeError('isolated_host_exited')
                    await asyncio.sleep(.5)
                else:raise TimeoutError('joint_stage_timeout')
                result=json.loads(result_path.read_text());result['repetition']=repetition
                result['offline_onebot_actions']=len(suite.peer.deliveries)
                helper.private_json(evidence/(stage+'.json'),result);results.append(result)
                print(json.dumps({'stage':stage,'repetition':repetition,'pass':result.get('pass'),
                    'error_type':result.get('error_type'),'isolated_sends':len(suite.peer.deliveries),'real_qq':False}),flush=True)
                await suite.stop()
                if result.get('error_type'):raise RuntimeError('joint_stage_failed_inspect_private_evidence')
        finally:
            await suite.stop();await suite.mock.close()
            retain_native_evidence(state,evidence)
    measured=[call for result in results for case in result.get('cases',[]) for call in case.get('calls',[])]
    usage={}
    for call in measured:
        for key,value in json.loads(call.get('usage') or '{}').items():usage[key]=usage.get(key,0)+value
    summary={**info,'completed_stages':len(results),'pass':all(r.get('pass') for r in results),
        'live_inputs_unchanged':all(helper.sha(Path(p).read_bytes())==v for p,v in before.items()),
        'results_private':True,'real_qq':False,'naturalness':'PENDING_OWNER_REVIEW',
        'actual_model_requests':sum(c['kind']=='model' for c in measured),
        'actual_tool_attempts':sum(c['kind'] in ('search','fetch') for c in measured),
        'token_usage':usage,'offline_confirmed_bubbles':sum(len([d for d in c.get('deliveries',[]) if d['state']=='sent']) for r in results for c in r.get('cases',[]))}
    helper.private_json(out/'summary.json',summary);return summary


def retain_native_evidence(state,evidence):
    """Keep only disposable native state, not production config or secrets."""
    dest=evidence/'native-persistence';dest.mkdir(mode=0o700)
    source=state/'joint-autonomy'
    if source.exists():shutil.copytree(source,dest/'autonomy')
    native_paths=(
        'data/data_v4.db',
        'data/plugin_data/phase1_guard/guard.sqlite3',
        'data/plugin_data/astrbot_plugin_private_companion/companions.json',
        'data/plugin_data/astrbot_plugin_private_companion/persona_profiles/default.db',
    )
    for relative in native_paths:
        source=state/relative
        if not source.is_file():continue
        target=dest/relative;target.parent.mkdir(parents=True,exist_ok=True)
        if source.suffix in ('.db','.sqlite3'):
            with sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True) as original, sqlite3.connect(target) as copy:
                original.backup(copy)
        else:shutil.copyfile(source,target)
        target.chmod(0o600)


def main():
    args=parser().parse_args()
    if args.host:return asyncio.run(host())
    if not args.execute:print(json.dumps(plan(args),ensure_ascii=False,indent=2));return
    result=asyncio.run(execute(args));print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
