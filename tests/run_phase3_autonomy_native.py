"""Run the existing complete AstrBot suite's isolated autonomy probe only."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

from run_phase3_integration import Suite


async def main(state, evidence, upstream):
    evidence.mkdir(parents=True, exist_ok=False)
    suite = Suite(state, evidence, upstream)
    suite.prepare()
    await suite.mock.start()
    try:
        await suite.start()
        (state/'autonomy-probe.flag').write_text('synthetic-only')
        output = state/'autonomy-probe.json'
        for _ in range(400):
            if output.exists(): break
            await asyncio.sleep(.5)
        if not output.exists(): raise TimeoutError('full_native_autonomy_probe_missing')
        result = json.loads(output.read_text())
        (evidence/'autonomy-native.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        summary = {k: result.get(k) for k in ('pass','level','real_deepseek','real_qq',
            'model_http_requests','tool_http_attempts','error_type','trace') if k in result}
        summary['cases'] = {name: {'pass': row['pass'], 'checks': row['checks'],
            'http_checks': row['http_checks']} for name,row in result.get('cases',{}).items()}
        print(json.dumps(summary, ensure_ascii=False))
        return bool(result.get('pass'))
    finally:
        await suite.stop()
        await suite.mock.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--upstream', type=Path, required=True)
    args = parser.parse_args()
    sys.exit(0 if asyncio.run(main(args.state,args.evidence,args.upstream)) else 1)
