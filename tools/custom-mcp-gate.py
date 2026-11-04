#!/usr/bin/env python3
"""Check an approved npm MCP package with dependencies, then start it (mc/desk_connect/custom_npm_gate.py).
Registered in the MCP config by Desk Connect, in front of tools/with-secret.py."""
import os
import sys
from pathlib import Path

# Started as an MCP server in the engine's working folder: keep that folder out of
# the Windows search for a bare program name before anything is spawned.
if os.name == 'nt':
    os.environ['NoDefaultCurrentDirectoryInExePath'] = '1'

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc.desk_connect.custom_npm_gate import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
