"""Targeted fixed AstrBot HTTP/OneBot proof, synthetic data and no real model.

Run under the same network-none test image/mounts as the full integration suite.
The seeded old conversation is new isolated state, never a production snapshot.
"""
import asyncio
import json
import os
from pathlib import Path

from run_phase3_integration import Suite


class ProfilePipelineSuite(Suite):
    async def run(self):
        self.prepare()
        await self.mock.start()
        try:
            await self.start()
            self.record('PROFILE_START','Pinned full AstrBot and fixed plugins in fresh synthetic state')
            # Establish a native conversation before the fixture creates a new
            # one, so preservation is tested instead of silently overwriting it.
            await self.peer.send('合成资料验收前的普通问候。',timeout=40)
            await asyncio.sleep(.3)
            (self.state/'profile-probe.flag').write_text('synthetic-only')
            for _ in range(100):
                if (self.state/'profile-probe.json').exists():break
                await asyncio.sleep(.2)
            assert json.loads((self.state/'profile-probe.json').read_text())['state']=='APPLIED'
            await self.verify_profile_prefetch()
        except Exception as exc:
            self.results.append({'id':'EXECUTION','status':'FAIL','error_type':type(exc).__name__})
            self.save()
            raise
        finally:
            await self.stop()
            await self.mock.close()
        print(json.dumps({'status':'PASS','checks':len(self.results),
            'real_qq':False,'real_deepseek':False,'production_changed':False}))


if __name__=='__main__':
    evidence=Path(os.environ.get('PHASE3_EVIDENCE','/evidence'))
    evidence.mkdir(parents=True,exist_ok=True)
    asyncio.run(ProfilePipelineSuite(Path(os.environ.get('PHASE3_STATE','/state'))/'joint',
        evidence,Path(os.environ.get('PHASE3_UPSTREAM','/upstream'))).run())
