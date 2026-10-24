#!/usr/bin/env python3
"""Repair only the known empty authoritative root in a stopped data copy."""
import argparse
import json
from pathlib import Path

from phase3_fresh_chat import repair_empty_private_memory_copy


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-copy', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(repair_empty_private_memory_copy(args.data_copy), indent=2))
