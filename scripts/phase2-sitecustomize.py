"""Copied as sitecustomize.py in the production image's fixed PYTHONPATH.

Python imports this before the unchanged upstream main.py. A missing, unsafe
or unreadable production configuration stops interpreter startup. Inspection
containers without production state must explicitly clear PYTHONPATH.
"""
import json
import os
from pathlib import Path
import stat
import sys

def bootstrap(config_path=Path("/AstrBot/data/cmd_config.json")):
    try:
        from secret_redaction import install_redaction

        fd = os.open(config_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r", encoding="utf-8-sig") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or \
                    stat.S_IMODE(metadata.st_mode) != 0o600 or \
                    metadata.st_uid != os.geteuid() or metadata.st_size > 1024 * 1024:
                raise ValueError("unsafe configuration")
            config = json.load(stream)
        if not isinstance(config, dict):
            raise ValueError("invalid configuration")
        install_redaction(config)
    except BaseException:
        # site.py ignores ordinary Exception from sitecustomize. SystemExit
        # aborts startup instead, without displaying input or exception text.
        sys.stderr.write("Phase2 logging protection initialization failed; startup blocked.\n")
        raise SystemExit(78) from None


if __name__ in {"sitecustomize", "__main__"}:
    bootstrap()
