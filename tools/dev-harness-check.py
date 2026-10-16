#!/usr/bin/env python3
"""Report-only entry point; trusted execution receipts have no headless entry."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mc.dev_harness_runner import main

if __name__ == '__main__':
    raise SystemExit(main())
