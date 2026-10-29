#!/usr/bin/env python3
"""Targeted repeated real-API regressions; no QQ or production writes."""
import asyncio
import contextlib
import importlib.util
import json
import os
from pathlib import Path

path = Path(__file__).with_name('phase3-test-autonomy-real.py')
spec = importlib.util.spec_from_file_location('autonomy_real_base', path)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

MAP = '我发现游戏地图上的问号一多，探索就像在清任务表。'
CASES = (
    {'id':'fix_repeat_thesis','context':'开放机会。旧候选仍是游戏地图问号很多让探索像清任务，只有新比喻、没有新判断。可以换一个真正不同的具体观点或跳过。', 'confirmed_history':[MAP], 'tools':False},
    {'id':'fix_repeat_variant','context':'开放机会。旧候选是“读书清单越长，读书像完成指标”，没有新的判断，只有把清单类比换成游戏地图。可以选真正不同的想法。', 'confirmed_history':['我觉得把兴趣变成长长的打卡表，会把好奇变成欠债；游戏地图的问号也是这样。'], 'tools':False},
    {'id':'fix_same_domain_new','context':'开放机会。同样聊游戏，但这次想谈没有数值加成的无用小道具为什么让虚构世界更可信。这与旧地图清单观点不同，可以表达独立判断。', 'confirmed_history':[MAP], 'tools':False},
    {'id':'fix_open_empty','context':'开放机会，没有兴趣记录、外部事件或新私聊。可以从小联想、判断、假设或创作自主选一个具体话题。', 'tools':True},
    {'id':'fix_exact_arithmetic','context':'开放机会。想到一个换算：假如每天有半小时空白，一年按365天，它累计多少小时、相当于多少完整24小时的天？可以把这个线性加总与自己的想法联系起来，不是假定用户真的有这个安排。', 'tools':False},
    {'id':'fix_arithmetic_variant','context':'开放机会。一个虚构小实验：120只鸟里72只对同一个面具报警。想把它变成一个关于“小团体像流言网”的脑洞；比例与主体是合成前提，不是真实新闻，请准确保留。', 'tools':False},
    {'id':'fix_game_current','context':'开放机会。想知道无人深空官方最近的更新中一个探索设计，实际核实公开来源和发布时间，再形成自己的判断。不要把上半年旧资料说成最新或即将发生。', 'tools':True},
    {'id':'fix_old_source_allowed','context':'开放机会。可以翻看无人深空2026年Swarm旧更新里一个有意思的设计，这次就是历史回看，不要求新鲜资讯。读取真实来源、准确保留原时间背景，再说自己的判断。', 'tools':True},
    {'id':'fix_crow_subject','context':'开放机会。对乌鸦识别人脸实验中学会对面具报警的群体比例感兴趣。查公开原始研究或华盛顿大学资料，准确区分鸟与人、实验鸟与野外鸟、直接经历与旁观学习，再表达自己的想法。', 'tools':True},
    {'id':'fix_own_episode','context':'开放机会。历史中的AI曾说过看了纪录片片单，但没有来源能证明发生过这个现实经历。可以谈对片单文化的判断或自己的偏好，不能沿用那条自述为真实经历。', 'confirmed_history':['我最近列了二十部纪录片，一部都还没看。'], 'tools':False},
    {'id':'fix_qualifiers','context':'开放机会。金鱼只有七秒记忆的说法是否有依据？可以查公开研究、大学说明；保留研究实际支持的限定，不把关于谣言起源的猜测转成事实。', 'tools':True},
    {'id':'fix_hypothesis','context':'开放机会。假如人类给标点符号安排工位，括号像不像会议室里专门插话的角落？这只是创作脑洞，可以发展或换一个自己的联想，不需要编造成外部研究。', 'tools':False},
)
base.DIRECTED_CASES += CASES

def main():
    parser = base.parser()
    parser.set_defaults(scenarios=[c['id'] for c in CASES], content_review=True, baseline=False)
    args = parser.parse_args()
    if not args.execute:
        return asyncio.run(base.worker(args))
    if not args.output or args.output.exists():
        raise ValueError('fresh_private_output_required')
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(args.output.with_name(args.output.name+'.native.log'), os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            return asyncio.run(base.worker(args))

if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'status':'FAILED', 'error_type':type(exc).__name__}))
        raise SystemExit(1)
