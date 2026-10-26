"""Fast isolated iteration of I11; final acceptance still runs the joint suite."""
import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from run_phase3_integration import Suite


def verify_tuning_http(calls):
    """Inspect the actual loopback HTTP payload, not a pre-hook prompt copy."""
    result = {}
    for decision in ('defer', 'skip', 'send'):
        matched = []
        for call in calls:
            request = call['request']
            texts = []
            for item in request.get('messages', []):
                value = item.get('content', '')
                if isinstance(value, str):
                    texts.append(value)
                elif isinstance(value, list):
                    texts.extend(part.get('text', '') for part in value if isinstance(part, dict))
            text = '\n'.join(texts)
            if 'PROCTX_' + decision + '_' in text:
                matched.append((request, text))
        if len(matched) != 1:
            result[decision] = {'pass': False, 'matched_requests': len(matched),
                                'error': 'expected_one_main_model_http_request'}
            continue
        request, text = matched[0]
        history = re.findall(r'PROCTX_' + decision + r'_(\d{3})', text)
        topics = re.findall(r'PROTOPIC_' + decision + r'_(\d{2})', text)
        checks = {
            'exact_latest_80': history == [f'{index:03d}' for index in range(20, 100)],
            'eight_retained_topics': topics == [f'{index:02d}' for index in range(8)],
            'expired_topic_absent': 'PROTOPIC_EXPIRED' not in text,
            'has_older_and_recent_blocks': all(title in text for title in
                ('较早对话（已压缩）', '最近对话（保留原文）')),
            'decision_protocol': '[PHASE3_PROACTIVE_DECISION_V1]' in text,
            'no_tools': not request.get('tools'),
        }
        result[decision] = {'pass': all(checks.values()), 'checks': checks,
                            'history_items': len(history), 'topic_summaries': len(topics)}
    return result


async def main(tuning=False):
    evidence=Path('/evidence');evidence.mkdir(exist_ok=True)
    report=evidence/('proactive-tuning-native.json' if tuning else 'proactive-native.json')
    if report.exists():
        raise FileExistsError('use a fresh evidence directory; previous evidence must be retained')
    if tuning:
        os.environ['PHASE3_PROACTIVE_TUNING_PROBE']='1'
    suite=Suite(Path('/state/joint-tuning' if tuning else '/state/joint'),evidence,Path('/upstream'))
    suite.prepare()
    await suite.mock.start()
    try:
        await suite.start()
        flag=suite.state/'proactive-probe.flag';flag.write_text('synthetic-only')
        output=suite.state/'proactive-probe.json'
        for _ in range(260):
            if output.exists():break
            await asyncio.sleep(.5)
        if not output.exists():raise TimeoutError('probe result missing')
        result=json.loads(output.read_text())
        calls=[r for r in suite.mock.requests if str(r.get('kind','')).startswith('proactive')]
        result['actual_proactive_http_calls']=len(calls)
        result['proactive_http_tools']=[r['request'].get('tools',[]) for r in calls]
        if tuning:
            result['tuning_http']=verify_tuning_http(calls)
            result['pass']=result.get('pass',False) and all(item['pass'] for item in result['tuning_http'].values())
        report.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False))
        assert result.get('pass'),result
        assert calls and all(not r['request'].get('tools') for r in calls)
    finally:
        await suite.stop();await suite.mock.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tuning',action='store_true',help='also verify the 80-message / 8-topic / 48-evaluation candidate')
    asyncio.run(main(tuning=parser.parse_args().tuning))
