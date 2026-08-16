"""`docs/MEMORY_SYSTEM.md`'s config table must match the code's defaults.

WHY (S12, 2026-08-16). A map that disagrees with the territory is not a
harmless inaccuracy — it is an instruction. `CLAUDE.md` went on telling every
agent to use memsearch for 80 days after memsearch was verified non-functional
and retired, because nobody had a check that the doc still described the code.

This pins the table to `server.py`'s `_load_config()` defaults dict — NOT to
`GET /api/config`. The live endpoint reflects one operator's overrides, so a test
reading it would fail on any box that had tuned anything, and would bake this
machine's state into CI. An override is not doc drift.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / 'docs' / 'MEMORY_SYSTEM.md'
SERVER = ROOT / 'server.py'

# MEMORY_SYSTEM.md is gitignored (.gitignore:194) and has never been tracked, so
# it is absent from a fresh clone and in CI. Skip rather than fail there — a test
# that fails for everyone who is not this operator is the same class of bug this
# whole stage is about. Where the doc DOES exist, it must match the code.
pytestmark = pytest.mark.skipif(
    not DOC.exists(), reason='docs/MEMORY_SYSTEM.md is operator-local (gitignored)')

def _server_defaults():
    """The literal defaults dict from server.py's _load_config, parsed with ast
    so this never imports server (which autostarts the tunnel supervisor)."""
    tree = ast.parse(SERVER.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == '_load_config':
            for stmt in ast.walk(node):
                if (isinstance(stmt, ast.Assign)
                        and any(getattr(t, 'id', '') == 'defaults' for t in stmt.targets)
                        and isinstance(stmt.value, ast.Dict)):
                    out = {}
                    for k, v in zip(stmt.value.keys, stmt.value.values):
                        if not isinstance(k, ast.Constant):
                            continue
                        try:
                            out[k.value] = ast.literal_eval(v)
                        except Exception:
                            pass          # computed default (paths) — not our concern
                    return out
    raise AssertionError('could not find _load_config defaults dict in server.py')


def _doc_rows():
    """{key: raw default cell} from the config table."""
    rows = {}
    for line in DOC.read_text(encoding='utf-8').split('\n'):
        m = re.match(r'^\|\s*`([a-z_]+)`\s*\|\s*(.+?)\s*\|', line)
        if m:
            rows[m.group(1)] = m.group(2)
        else:
            m2 = re.match(r'^\|\s*\*\*`([a-z_]+)`\*\*\s*\|\s*(.+?)\s*\|', line)
            if m2:
                rows[m2.group(1)] = m2.group(2)
    return rows


def _parse_cell(cell):
    """The documented value, or None when the cell is prose we shouldn't police."""
    lit = re.findall(r'`([^`]+)`', cell)
    if not lit:
        return None
    raw = lit[0].strip()
    if raw.lower() in ('true', 'false'):
        return raw.lower() == 'true'
    if raw == "''":
        return ''
    try:
        return int(raw)
    except ValueError:
        return raw


def test_documented_defaults_match_the_code():
    defaults = _server_defaults()
    mismatches = []
    checked = 0
    for key, cell in _doc_rows().items():
        if key not in defaults:
            continue
        want = defaults[key]
        got = _parse_cell(cell)
        if got is None:
            continue          # cell is descriptive prose, not a literal
        checked += 1
        if got != want:
            mismatches.append(f'  {key}: doc says {got!r}, server.py default is {want!r}')
    assert checked >= 10, f'only matched {checked} config rows — the table shape changed'
    assert not mismatches, (
        'docs/MEMORY_SYSTEM.md disagrees with server.py:\n' + '\n'.join(mismatches))


def test_the_byte_budget_is_documented():
    """`index_byte_budget` is the single most consequential number in this
    subsystem — it is the per-prompt token spend — and the table omitted it
    entirely until 2026-08-16."""
    assert 'index_byte_budget' in _doc_rows(), \
        'the always-loaded index byte budget must appear in the config table'


def test_the_deadband_finding_is_recorded():
    """Two reviews reached opposite conclusions here. Keep the resolution in the
    doc so it is not re-derived a third time."""
    txt = DOC.read_text(encoding='utf-8')
    assert 'deadband' in txt.lower(), 'the condense deadband finding is missing'
    assert 'index_line_hard_floor' in txt, 'the line half of the deadband is undocumented'
