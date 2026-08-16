"""The S4 ranker constants must be wired in BOTH places, and default to no-ops.

THE TRAP THIS PINS (verified live, 2026-08-16). `update_config` filters on
`if k in _CONFIG_EDITABLE_KEYS`. PUT a key that is missing from that list and the
API returns:

    {"ok": true, "respawn_flagged": 0, "updated": []}

200. Success-shaped. Nothing happened. There is no error, no warning, and the
value is not persisted — so a flag can look wired, read as working in a smoke
test, and silently do nothing forever. Observed exactly this while landing S4.

The mirror failure is just as quiet: a key in `_CONFIG_EDITABLE_KEYS` but absent
from `server.py`'s defaults dict is settable but has no documented default, so
its behaviour on a fresh install depends on a `.get()` fallback buried in the
ranker rather than on anything a reader can find.

So both lists are asserted together, and the defaults are asserted to equal the
module constants — landing this code must be a measurable no-op, which is what
makes each constant judgeable on its own evidence afterwards.
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / 'server.py'
SETTINGS = ROOT / 'mc' / 'blueprints' / 'settings_routes.py'
MEMORY = ROOT / 'mc' / 'memory.py'

KEYS = ('bm25_b', 'bm25_title_boost', 'read_floor_archive_quota')


def _server_defaults():
    """Parsed with ast so this never imports server — importing it autostarts
    the tunnel supervisor, whose orphan reaper kills the operator's cloudflared."""
    tree = ast.parse(SERVER.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == '_load_config':
            for stmt in ast.walk(node):
                if (isinstance(stmt, ast.Assign)
                        and any(getattr(t, 'id', '') == 'defaults' for t in stmt.targets)
                        and isinstance(stmt.value, ast.Dict)):
                    out = {}
                    for k, v in zip(stmt.value.keys, stmt.value.values):
                        if isinstance(k, ast.Constant):
                            try:
                                out[k.value] = ast.literal_eval(v)
                            except Exception:
                                pass
                    return out
    raise AssertionError('could not find _load_config defaults in server.py')


@pytest.mark.parametrize('key', KEYS)
def test_key_is_settable(key):
    """In _CONFIG_EDITABLE_KEYS, or every PUT is a 200-shaped no-op."""
    assert f"'{key}'" in SETTINGS.read_text(encoding='utf-8'), (
        f'{key} is missing from _CONFIG_EDITABLE_KEYS — PUTs will return '
        f'{{"ok": true, "updated": []}} and silently change nothing')


@pytest.mark.parametrize('key', KEYS)
def test_key_has_a_default(key):
    assert key in _server_defaults(), \
        f'{key} is missing from server.py _load_config defaults'


def test_defaults_are_todays_behaviour():
    """Shipping S4 must change nothing until a constant is deliberately flipped."""
    d = _server_defaults()
    src = MEMORY.read_text(encoding='utf-8')
    assert d['bm25_b'] == 0.75, 'bm25_b default must match the shipped _BM25_B'
    assert d['bm25_title_boost'] == 3, 'title boost default must match _TITLE_BOOST'
    assert d['read_floor_archive_quota'] == 0, 'the archive quota must default OFF'
    assert '_BM25_B = 0.75' in src and '_TITLE_BOOST = 3' in src, \
        'the module constants moved — the defaults above are now lying'


def test_ranker_reads_the_config_not_the_constant():
    """A getter that ignores config is the same failure as an unlisted key:
    the flag exists, reads as wired, and does nothing."""
    src = MEMORY.read_text(encoding='utf-8')
    for key, fn in (('bm25_b', '_bm25_b'),
                    ('bm25_title_boost', '_title_boost'),
                    ('read_floor_archive_quota', '_archive_quota')):
        assert f"def {fn}(" in src, f'{fn}() is missing'
        assert f"'{key}'" in src, f'{fn}() does not read config key {key}'
    # and the hot paths must call the getters, not the bare constants
    assert '_title_boost()' in src, 'the corpus builder still uses the raw _TITLE_BOOST'
    assert '_bm25_b()' in src, 'the scorer still uses the raw _BM25_B'
    assert '_archive_quota()' in src, 'the archive quota is never consulted'
