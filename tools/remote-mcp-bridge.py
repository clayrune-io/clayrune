#!/usr/bin/env python3
"""Start the stdio bridge to one approved remote MCP server (mc/desk_connect/remote_mcp_bridge.py).
Registered in the MCP config by Desk Connect, behind tools/with-secret.py when it needs a token."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc.desk_connect.remote_mcp_bridge import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
