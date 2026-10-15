"""G1 report-mode tap on `write_topic_note` (MC-964 step 10, mc/memory_gate_tap.py).

The tap has to (1) leave every mint byte-for-byte as it was, whatever the gate
thinks of it, (2) leave one G1 row per landed mint, (3) never block a mint when
the gate itself faults. Memory-module paths redirected to tmp_path; no server
import.
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

FROZEN = {'by': 'tester', 'at': '2026-10-15T00:00:00'}


class _NoTap:
    """Stands in for the tap module: the pre-wiring behaviour."""
    @staticmethod
    def begin(*a, **k):
        return None

    @staticmethod
    def finish(pending):
        return None


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from mc import memory as m
    from mc import state
    monkeypatch.setattr(m, 'CLAUDE_HOME', tmp_path / 'claude')
    monkeypatch.setattr(m, 'MEMORY_DIR', tmp_path / 'shared')
    monkeypatch.setattr(m, 'DATA_DIR', tmp_path / 'data' / 'projects')
    (tmp_path / 'shared').mkdir()

    def _mp(p):
        d = tmp_path / 'claude' / f"vault-{p['id']}" / 'memory'
        d.mkdir(parents=True, exist_ok=True)
        return d / 'MEMORY.md'
    monkeypatch.setattr(m, '_get_memory_path', _mp)
    monkeypatch.setattr(m, '_stamp_generated', lambda actor: dict(FROZEN))
    monkeypatch.setitem(state.CONFIG, 'memory_gate_mode', 'report')
    return m, tmp_path, monkeypatch


def _rows(tmp, pid):
    f = tmp / 'data' / 'memory_gate_log' / f'{pid}.jsonl'
    if not f.is_file():
        return []
    return [json.loads(ln) for ln in f.read_text(encoding='utf-8').splitlines() if ln.strip()]


def _read(tmp, pid, fn):
    return (tmp / 'claude' / f'vault-{pid}' / 'memory' / fn).read_bytes()


def _mint(m, pid, body, **kw):
    return m.write_topic_note({'id': pid}, 'habit_x', 'a desc', body,
                              trigger_type='manual', actor='tester', **kw)


def test_violating_mint_writes_identical_bytes_and_records_row(env):
    m, tmp, mp = env
    body = 'see [[no-such-note]] for context\n'
    # Baseline: the pre-wiring write path (tap replaced by a no-op), other vault.
    with mp.context() as inner:
        inner.setattr(m, '_gate_tap', _NoTap)
        assert _mint(m, 'base', body) == 'habit_x.md'

    assert _mint(m, 'p1', body) == 'habit_x.md'
    assert _read(tmp, 'p1', 'habit_x.md') == _read(tmp, 'base', 'habit_x.md')

    rows = _rows(tmp, 'p1')
    assert len(rows) == 1
    r = rows[0]
    assert (r['op'], r['slug'], r['mode'], r['origin'], r['result']) == \
        ('mint', 'habit_x', 'report', 'interactive', 'written')
    assert r['classes'] == ['class_d_unresolved']
    assert r['violations'][0]['target'] == 'no-such-note'
    assert _rows(tmp, 'base') == []   # the no-op baseline recorded nothing


def test_enforce_config_does_not_stop_a_mint(env):
    # The tap has no enforce posture: a violating mint still lands, and the
    # row is stamped 'report' because that is the only posture it has.
    from mc import state
    m, tmp, mp = env
    mp.setitem(state.CONFIG, 'memory_gate_mode', 'enforce')
    assert _mint(m, 'p1', 'broken [[nowhere]] link\n') == 'habit_x.md'
    assert (tmp / 'claude' / 'vault-p1' / 'memory' / 'habit_x.md').is_file()
    (r,) = _rows(tmp, 'p1')
    assert (r['mode'], r['result']) == ('report', 'written')
    assert r['classes'] == ['class_d_unresolved']


def test_clean_mint_records_row_without_violations(env):
    from mc import memory_note_gate as g
    m, tmp, _mp = env
    assert _mint(m, 'p1', 'plain body, no links\n') == 'habit_x.md'
    (r,) = _rows(tmp, 'p1')
    assert (r['op'], r['result'], r['classes'], r['violations']) == \
        ('mint', 'written', [], [])
    counts = g.gate_counts('p1')
    assert (counts['requests'], counts['clean'], counts['would_reject']) == (1, 1, 0)


def test_unattended_mint_row_carries_unattended_origin(env):
    m, tmp, _mp = env
    m.write_topic_note({'id': 'p1'}, 'sched_x', 'a desc', 'body\n',
                       trigger_type='scheduled', actor='tester')
    (r,) = _rows(tmp, 'p1')
    assert r['origin'] == 'unattended'


def test_gate_exception_does_not_block_the_write(env):
    from mc import memory_gate_tap as tap
    from mc import memory_note_gate as g
    m, tmp, mp = env
    logged = []
    mp.setattr(tap, '_log', lambda msg, *a, **k: logged.append(msg))

    def boom(*a, **k):
        raise RuntimeError('gate on fire')
    mp.setattr(g, 'evaluate', boom)
    assert _mint(m, 'p1', 'body\n') == 'habit_x.md'
    assert (tmp / 'claude' / 'vault-p1' / 'memory' / 'habit_x.md').is_file()
    assert _rows(tmp, 'p1') == []
    assert any('mint evaluate failed' in s and 'gate on fire' in s for s in logged)


def test_record_exception_does_not_block_the_write(env):
    from mc import memory_gate_tap as tap
    from mc import memory_note_gate as g
    m, tmp, mp = env
    logged = []
    mp.setattr(tap, '_log', lambda msg, *a, **k: logged.append(msg))

    def boom(*a, **k):
        raise RuntimeError('disk gone')
    mp.setattr(g, 'record', boom)
    assert _mint(m, 'p1', 'body\n') == 'habit_x.md'
    assert any('mint record failed' in s and 'disk gone' in s for s in logged)


def test_skipped_existing_mint_records_no_row(env):
    m, tmp, _mp = env
    assert _mint(m, 'p1', 'first\n') == 'habit_x.md'
    before = _read(tmp, 'p1', 'habit_x.md')
    assert _mint(m, 'p1', 'second\n') == ''          # never-clobber, unchanged
    assert _read(tmp, 'p1', 'habit_x.md') == before
    assert len(_rows(tmp, 'p1')) == 1


def test_duplicate_identity_is_recorded_not_refused(env):
    m, tmp, _mp = env
    m.write_topic_note({'id': 'p1'}, 'arch-x', 'first', 'body\n',
                       trigger_type='manual', actor='tester')
    assert m.write_topic_note({'id': 'p1'}, 'arch_x', 'second', 'body\n',
                              trigger_type='manual', actor='tester') == 'arch_x.md'
    rows = _rows(tmp, 'p1')
    assert [r['classes'] for r in rows] == [[], ['duplicate_identity']]
