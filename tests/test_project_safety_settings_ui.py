"""The Safety dialog (static/js/project-safety-settings.js) copies two defaults
from server code; this fails if either drifts. It also pins that every key the
dialog sends is one the server treats as human-only, so a control can never
write a key outside the protected set by accident."""
import re
from pathlib import Path

from mc import agent_worktree, landing_gate
from mc.project_safety_settings import PROJECT_SAFETY_KEYS

JS = (Path(__file__).resolve().parent.parent / 'static' / 'js' / 'project-safety-settings.js').read_text(encoding='utf-8')


def test_default_shared_runtime_matches_server():
    m = re.search(r"const DEFAULT_SHARED_RUNTIME = \[(.*?)\];", JS, re.S)
    assert m, 'DEFAULT_SHARED_RUNTIME constant not found in the dialog'
    assert tuple(re.findall(r"'([^']+)'", m.group(1))) == agent_worktree.DEFAULT_SHARED_RUNTIME


def test_default_timeout_matches_server():
    m = re.search(r"const DEFAULT_TIMEOUT_SECONDS = (\d+);", JS)
    assert m and int(m.group(1)) == landing_gate.DEFAULT_TIMEOUT_SECONDS


def test_every_dialog_key_is_a_protected_key():
    keys = set(re.findall(r"\$\('([a-z_]+)'\)", JS))
    assert keys, 'no controls found'
    assert keys <= PROJECT_SAFETY_KEYS, keys - PROJECT_SAFETY_KEYS
    assert len(keys) == 7
