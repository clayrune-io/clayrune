"""First-party `clayrune` MCP server (BUILTINS-7a, backlog d37e027d).

Gives any MCP-capable session typed tools to message other sessions on its own
project. One unit per file:

  protocol.py  stdio JSON-RPC loop (initialize / tools/list / tools/call)
  tools.py     the eight tool schemas + bounded loopback HTTP forwarding
  server.py    process entry point (`python mc/clayrune_mcp/server.py ...`)
  identity.py  SERVER-side check that binds a caller to its session

The server process is stdlib-only and runs as a child of the launching agent's
CLI. It never holds a caller identity of its own: every mutating tool asks the
Clayrune server who is calling (process ancestry, `mc.caller_attribution`) and
refuses unless that matches the project the process was launched for.
"""
