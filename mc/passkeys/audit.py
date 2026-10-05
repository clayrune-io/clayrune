"""Append-only audit of passkey enrollment and revocation.

One JSON line per event: operation, a short credential reference, outcome, time.
Never a body, a passcode, an assertion, or a key. Best-effort: a failed audit
write is logged and never changes the outcome of the operation it describes.
Lives beside the registry under ``~/.clayrune/passkeys/``.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from mc.core import _log
from mc.passkeys import store

_LOCK = threading.Lock()


def audit_path() -> Path:
    return store.passkeys_dir() / 'audit.jsonl'


def credential_ref(credential_id: Optional[str]) -> str:
    """First 8 characters of the credential id: enough to match a list row,
    not enough to be the credential."""
    return (credential_id or '')[:8]


def record(operation: str, outcome: str, credential_id: Optional[str] = None,
           reason: str = '') -> None:
    line = json.dumps({
        'at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'operation': operation, 'outcome': outcome,
        'credential': credential_ref(credential_id), 'reason': reason[:120],
    }, sort_keys=True)
    try:
        with _LOCK:
            p = audit_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
    except Exception as e:
        _log(f'[passkeys] audit write failed: {e}', flush=True)
