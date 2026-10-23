"""Fast isolated iteration of I11; final acceptance still runs the joint suite."""
import asyncio
import json
from pathlib import Path
from run_phase3_integration import Suite


async def main():
    evidence=Path('/evidence');evidence.mkdir(exist_ok=True)
    suite=Suite(Path('/state/joint'),evidence,Path('/upstream'))
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
        (evidence/'proactive-native.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps(result,ensure_ascii=False))
        assert result.get('pass'),result
        assert calls and all(not r['request'].get('tools') for r in calls)
    finally:
        await suite.stop();await suite.mock.close()


if __name__=='__main__':asyncio.run(main())
