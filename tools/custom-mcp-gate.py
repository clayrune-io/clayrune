#!/usr/bin/env python3
"""Check an approved npm MCP package with dependencies, then start it (mc/desk_connect/custom_npm_gate.py).
Registered in the MCP config by Desk Connect, in front of tools/with-secret.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc.desk_connect.custom_npm_gate import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
