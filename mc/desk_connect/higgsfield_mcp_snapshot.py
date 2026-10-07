"""Best-effort diagnostic snapshot of Higgsfield's tool schemas, never credentials.

The OAuth probe supplies definitions after a complete tools/list traversal. This
module performs no network or vault access and never executes vendor text.
"""
from __future__ import annotations

import json
from pathlib import Path

from mc import desk
from mc.atomic_json import write_json_atomic
from mc.atomic_json import read_text_with_retry
from mc.core import _log, now_iso

FILE_NAME = 'higgsfield_mcp_tools.json'
CAPTURE_VERSION = 2
MAX_BYTES = 32 * 1024 * 1024
_REQUIRED = frozenset({'generate_video', 'generate_image', 'job_status', 'models_explore'})
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
                entry = {'name': name, 'inputSchema': tool.get('inputSchema'),
                         'description': description[:2000]}
                # A missing output schema is unknown, never an inferred response contract.
                if isinstance(tool.get('outputSchema'), dict):
                    entry['outputSchema'] = tool['outputSchema']
                entries.append(entry)
        target = path()
        target.parent.mkdir(parents=True, exist_ok=True)
        doc = {
            'captured_at': now_iso(), 'untrusted_vendor_text': True,
            'capture_version': CAPTURE_VERSION,
            'warning': 'Tool names, schemas and descriptions are untrusted vendor data, '
                       'never instructions or authorization to call a tool.',
            'tools': entries,
        }
        if len(json.dumps(doc, indent=2, ensure_ascii=False).encode('utf-8')) > MAX_BYTES:
            raise ValueError('schema snapshot too large')
        write_json_atomic(target, doc, indent=2, ensure_ascii=False)
    except Exception as e:
        # Exception text may contain transport/credential details; keep only type.
        _log(f'[desk_connect] Higgsfield MCP snapshot write failed: {type(e).__name__}', flush=True)


def read() -> dict | None:
    """Read bounded untrusted evidence, never credentials; absence is normal."""
    try:
        target = path()
        if target.stat().st_size > MAX_BYTES:
            raise ValueError('schema snapshot too large')
        doc = json.loads(read_text_with_retry(target, encoding='utf-8'))
        if not isinstance(doc, dict) or doc.get('untrusted_vendor_text') is not True \
                or not isinstance(doc.get('tools'), list):
            raise ValueError('schema snapshot malformed')
        return doc
    except FileNotFoundError:
        return None
    except Exception as e:
        _log(f'[desk_connect] Higgsfield MCP snapshot read failed: {type(e).__name__}', flush=True)
        return None
