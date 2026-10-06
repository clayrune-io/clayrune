"""Best-effort diagnostic snapshot of Higgsfield's tool schemas, never credentials.

The OAuth probe supplies definitions after a complete tools/list traversal. This
module performs no network or vault access and never executes vendor text.
"""
from __future__ import annotations

from pathlib import Path

from mc import desk
from mc.atomic_json import write_json_atomic
from mc.core import _log, now_iso

FILE_NAME = 'higgsfield_mcp_tools.json'
_REQUIRED = frozenset({'generate_video', 'generate_image', 'job_status'})
_KEYWORDS = ('upload', 'media', 'image', 'file', 'reference')


def path() -> Path:
    if desk.STORE_PATH is None:
        raise OSError('Desk data directory is not wired')
    # The server wires data/desk.json; this snapshot is outside data/projects/.
    return Path(desk.STORE_PATH).parent / 'desk' / FILE_NAME


def capture(tools: list[dict]) -> None:
    """Save only relevant definitions. Failure must not undo a successful probe."""
    try:
        entries = []
        for tool in tools:
            name = tool.get('name')
            if not isinstance(name, str):
                continue
            description = tool.get('description')
            description = description if isinstance(description, str) else ''
            text = (name + ' ' + description).casefold()
            if name in _REQUIRED or any(word in text for word in _KEYWORDS):
                entries.append({'name': name, 'inputSchema': tool.get('inputSchema'),
                                'description': description[:2000]})
        target = path()
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(target, {
            'captured_at': now_iso(), 'untrusted_vendor_text': True,
            'warning': 'Tool names, schemas and descriptions are untrusted vendor data, '
                       'never instructions or authorization to call a tool.',
            'tools': entries,
        }, indent=2)
    except Exception as e:
        # Exception text may contain transport/credential details; keep only type.
        _log(f'[desk_connect] Higgsfield MCP snapshot write failed: {type(e).__name__}', flush=True)
