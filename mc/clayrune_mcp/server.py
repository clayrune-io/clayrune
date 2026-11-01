"""Entry point for the `clayrune` MCP server (stdio).

Launched by an agent CLI from the per-launch MCP selection:

    python <repo>/mc/clayrune_mcp/server.py --project <id> --base-url http://127.0.0.1:<port>

Only the project is passed. The calling session is established by the Clayrune
server on each mutating call (see `identity.py`), never taken from here.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

if __package__ in (None, ''):  # run by path: put the repo root on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mc.clayrune_mcp import protocol, tools  # noqa: E402

SERVER_INFO = {'name': 'clayrune', 'version': '1.0.0'}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description='Clayrune cross-session MCP server')
    ap.add_argument('--project', required=True)
    ap.add_argument('--base-url', required=True)
    ns = ap.parse_args(argv)
    try:
        gateway = tools.Gateway(ns.base_url, ns.project)
    except ValueError as e:
        print(f'[clayrune-mcp] {e}', file=sys.stderr, flush=True)
        return 2
    protocol.serve(tools.ClayruneToolHost(gateway), SERVER_INFO)
    return 0


if __name__ == '__main__':
    sys.exit(main())
