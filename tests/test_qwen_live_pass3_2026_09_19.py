"""Defects from the third Qwen live pass (2026-09-19, run 0919125503,
qwen3-coder-plus; evidence docs/_journal/provider-live/qwen/, gitignored).

1. memory: a fact appended to the curated MEMORY.md never reached a new Qwen
   chat. Claude's CLI loads MEMORY.md natively; every other provider got only
   the task-scoped read floor, whose corpus EXCLUDES the curated index "because
   the agent already auto-loads it" (`_memory_search`). The new chat answered
   with a different cell's marker instead. Non-Claude context now carries the
   curated half of the index (never the managed Session Log).
2. stop-interrupt: the driver passed a backslash Windows path; qwen's shell is
   bash, which ate the backslashes, so the "slow" decoy never ran, the turn
   ended in seconds and /agent/interrupt correctly answered 400 "agent not
   active". Driver defect, not a product gap.
3. image-paste: the fixed red/green/blue PNG is guessable and passed on a model
   recorded as having no vision. The fixture is now random per run.
"""
import random
import struct
import sys
import zlib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
for p in (PROJECT_ROOT, PROJECT_ROOT / 'tools' / 'provider-live'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import codex_run as D  # noqa: E402


# ── 1. memory index reaches non-Claude agents ────────────────────────────────

@pytest.fixture()
def mem_env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import memory as mem
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    monkeypatch.setattr(ar, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    curated = '# index\n\n- The deployment codename is CAA-945.'
    entries = ['- [2026-09-19] Reply with exactly one line: CDF and 752 joined']
    (tmp_path / 'MEMORY.md').write_text(mem._mem_compose(curated, entries), encoding='utf-8')
    return tmp_path


def _ctx(tmp, provider, **kw):
    from mc.blueprints import agent_routes as ar
    return ar._build_agent_context(
        {'id': 'p1', 'name': 'P1', 'project_path': str(tmp), 'provider': provider},
        task='What is the deployment codename?', **kw)


def test_non_claude_gets_the_curated_index_but_not_the_session_log(mem_env):
    ctx = _ctx(mem_env, 'qwen')
    assert 'The deployment codename is CAA-945.' in ctx
    assert 'CDF and 752' not in ctx.split('PROJECT MEMORY INDEX', 1)[1].split('--- ', 2)[1], \
        'the managed Session Log must not ride along (Gemini read it as a task list)'


def test_claude_does_not_get_it_twice(mem_env):
    assert 'PROJECT MEMORY INDEX' not in _ctx(mem_env, 'claude')


def test_incognito_non_claude_gets_no_index(mem_env):
    assert 'CAA-945' not in _ctx(mem_env, 'qwen', incognito=True)


# ── 2. stop-interrupt decoy path is shell-safe ───────────────────────────────

def test_stop_interrupt_prompt_has_no_backslash_path():
    class Stop(Exception):
        pass

    sent = {}

    class Procs:
        def make(self, tag):
            return 'C:\\Users\\x\\decoys\\clayrune_decoy_slow.exe', 'clayrune_decoy_slow.exe'

    class Api:
        def dispatch(self, project, prompt, **kw):
            sent['prompt'] = prompt
            raise Stop

    class Ctx:
        run_id = '0919125503'
        project = 'livepass'
        model = effort = ''
        procs, api = Procs(), Api()

        def mk(self, cell, n):
            return 'AAA', '111', 'AAA111'

    with pytest.raises(Stop):
        D.run_stop_interrupt(Ctx(), D.CellRun())
    assert '\\' not in sent['prompt'], sent['prompt']
    assert 'C:/Users/x/decoys/clayrune_decoy_slow.exe' in sent['prompt']


# ── 3. image fixture is unguessable ──────────────────────────────────────────

def _decode(png):
    """(w, h, rows of RGB tuples) from the fixture's own PNG (filter 0 only)."""
    assert png[:8] == b'\x89PNG\r\n\x1a\n'
    pos, idat, w, h = 8, b'', 0, 0
    while pos < len(png):
        n, t = struct.unpack('>I4s', png[pos:pos + 8])
        d = png[pos + 8:pos + 8 + n]
        if t == b'IHDR':
            w, h = struct.unpack('>II', d[:8])
        elif t == b'IDAT':
            idat += d
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride = 1 + w * 3
    return w, h, [[tuple(raw[y * stride + 1 + x * 3: y * stride + 4 + x * 3]) for x in range(w)]
                  for y in range(h)]


def test_fixture_draws_the_stripes_it_reports():
    png, names, code = D._png_fixture(random.Random(7))
    w, h, rows = _decode(png)
    assert len(set(names)) == 4 and len(code) == 4 and code.isdigit()
    for i, n in enumerate(names):
        assert rows[10][i * (w // 4) + 5] == D._STRIPE_COLOURS[n], (i, n)
    assert any((0, 0, 0) in r for r in rows[-40:]), 'the code must be rendered below the stripes'


def test_fixture_is_not_a_constant():
    seen = {(tuple(n), c) for n, c in
            ((D._png_fixture(random.SystemRandom())[1:]) for _ in range(12))}
    assert len(seen) >= 10, 'a fixture that repeats is a guessable fixture'
