"""The steward health panel reads the journal and the role ledger (review P65).

`steward.core.loop_health()` counted `DECISION NEEDED` / `BLOCKED` backlog notes
and took "last activity" from the newest note. A steward that obeys the binding
rule (no unattended backlog notes) therefore looked dead. The reading logic is
`mc/steward_health_journal.py` (agent-editable, tested directly and always on);
the call into it from `loop_health()` is the human-owned part, shipped as
docs/patches/steward-loop-health-journal.patch.

Same shape as tests/test_fence_hivemind_reviewer_armed.py: the tests that need
the patch are xfail(strict) until `steward/core.py` carries the call, and a
second group applies the patch to a throwaway copy of core.py and runs the
patched `loop_health()`.
"""
import importlib.util
import inspect
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from steward import core
from steward._config import configure
from mc import steward_health_journal as shj
from mc.steward_role_state import RoleStateStore

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PATCH = PROJECT_ROOT / 'docs' / 'patches' / 'steward-loop-health-journal.patch'

_HAS_FIX = 'steward_health_journal' in inspect.getsource(core.loop_health)
_needs_patch = pytest.mark.xfail(
    not _HAS_FIX, strict=True,
    reason='steward/core.py is human-owned: apply docs/patches/steward-loop-health-journal.patch')

CID = 'ch1'
# Relative to now: with no completed cycle in the journal only the last 7 days count
# as blocked, so fixed dates would age the tests out.
_NOW = datetime.now(timezone.utc).replace(microsecond=0)
T0, T1, T2, T3 = ((_NOW - timedelta(hours=h)).isoformat() for h in (3, 2, 1, 0))
NOTE_TS = '2026-11-16T08:00:00.000000Z'


def _journal(*events, name=f'{CID}-steward.md'):
    return ''.join(f'\n## {ts} | {ev}\nbody\n' for ts, ev in events)


def _project(tmp_path, notes=(), **extra):
    root = tmp_path / 'proj'
    root.mkdir(exist_ok=True)
    p = {'id': 'p1', 'name': 'P1', 'project_path': str(root), 'steward_mode': 'on',
         'steward_objective': 'obj',
         'backlog': [{'id': CID, 'source': 'steward-charter', 'text': 'STEWARD CHARTER: obj',
                      'notes': list(notes)}]}
    p.update(extra)
    return p


def _write_journal(project, text, name=f'{CID}-steward.md'):
    d = Path(project['project_path']) / 'docs' / '_journal'
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding='utf-8')


def _health(project, data_root):
    charter = core.find_charter(project)
    return shj.project_health(project, charter, (charter or {}).get('notes') or [], data_root)


def _note(text, ts=NOTE_TS):
    return {'text': text, 'ts': ts}


def _decision(did, status):
    return {'id': did, 'key': did, 'route': 'email', 'status': status, 'reason': '',
            'subject': 's', 'created_at': T0, 'email_attempted_at': None,
            'cap_counted_at': None, 'sent_at': None, 'attempts': 0, 'reply_decision': None,
            'reply_checked_at': None, 'last_reported_status': ''}


def _role(tmp_path, project, *statuses, journal='docs/_journal/ch1-role.md'):
    """Make `project` role-bound: pointer, schedules.json row, state ledger."""
    data = tmp_path / 'data'
    data.mkdir(exist_ok=True)
    project['steward_assignment'] = {'schedule_id': 's1'}
    row = {'id': 's1', 'project_id': 'p1', 'enabled': True,
           'steward_role': {'version': 1, 'charter_item_id': CID, 'journal_path': journal}}
    (data / 'schedules.json').write_text(json.dumps([row]), encoding='utf-8')
    RoleStateStore(data).update('p1', 's1', lambda st: st['decisions'].extend(
        _decision(f'd{i}', s) for i, s in enumerate(statuses)))
    return data


# ── the reading module (always on) ──────────────────────────────────────────

def test_notes_only_keeps_todays_numbers(tmp_path):
    p = _project(tmp_path, [_note('DECISION NEEDED: a'), _note('BLOCKED: b'), _note('FYI: c')])
    h = _health(p, tmp_path)
    assert (h['decisions_pending'], h['blocked']) == (1, 1)
    assert h['last_note_ts'] == NOTE_TS and h['activity_source'] == 'notes'
    assert h['unknown'] == []


def test_no_journal_and_no_notes_is_nothing_not_unknown(tmp_path):
    h = _health(_project(tmp_path), tmp_path)
    assert h == {'decisions_pending': 0, 'blocked': 0, 'last_note_ts': None,
                 'activity_source': 'none', 'unknown': []}


def test_journal_only_gives_activity_and_blocked_events(tmp_path):
    p = _project(tmp_path)
    _write_journal(p, _journal((T0, 'started'), (T1, 'blocked_guard'), (T2, 'decision_blocked'),
                               (T3, 'skipped_busy')))
    h = _health(p, tmp_path)
    assert h['last_note_ts'] == T3 and h['activity_source'] == 'journal'
    assert h['blocked'] == 2
    # a steward with no role is a normal configuration, not a read failure
    assert h['unknown'] == []


def test_an_old_block_followed_by_a_completed_cycle_shows_zero(tmp_path):
    p = _project(tmp_path)
    old = (_NOW - timedelta(days=40)).isoformat()
    _write_journal(p, _journal((old, 'started'), (old, 'blocked_guard'),
                               ((_NOW - timedelta(days=39)).isoformat(), 'completed'),
                               ((_NOW - timedelta(days=39)).isoformat(), 'started')))
    assert _health(p, tmp_path)['blocked'] == 0


def test_two_blocks_after_the_last_completed_cycle_show_two(tmp_path):
    p = _project(tmp_path)
    # the completed cycle is older than the 7-day window and the blocks are newer, so
    # this also proves 'completed' bounds the count rather than the window
    long_ago = (_NOW - timedelta(days=30)).isoformat()
    _write_journal(p, _journal((long_ago, 'blocked_charter'), (long_ago, 'completed'),
                               (T0, 'blocked_guard'), (T1, 'decision_blocked')))
    assert _health(p, tmp_path)['blocked'] == 2
    q = _project(tmp_path)
    _write_journal(q, _journal((long_ago, 'completed'), (long_ago, 'blocked_guard'),
                               (T0, 'blocked_channel'), (T1, 'blocked_channel')))
    assert _health(q, tmp_path)['blocked'] == 3


def test_with_no_completed_cycle_only_the_last_seven_days_count(tmp_path):
    p = _project(tmp_path)
    old = (_NOW - timedelta(days=8)).isoformat()
    _write_journal(p, _journal((old, 'blocked_guard'), (T0, 'blocked_guard')))
    assert _health(p, tmp_path)['blocked'] == 1


def test_journal_heading_forged_inside_a_body_is_not_counted(tmp_path):
    p = _project(tmp_path)
    # append_journal escapes a body line that starts with '#'; the reader must
    # agree: only a line that STARTS with '## ' counts.
    _write_journal(p, f'\n## {T0} | started\ntext \\## {T2} | blocked_guard\n')
    h = _health(p, tmp_path)
    assert h['blocked'] == 0 and h['last_note_ts'] == T0


def test_both_journal_and_notes_take_the_newer_time_and_sum_blocked(tmp_path):
    newer = (_NOW + timedelta(hours=1)).isoformat()
    p = _project(tmp_path, [_note('DECISION NEEDED: a'), _note('BLOCKED: b', ts=newer)])
    _write_journal(p, _journal((T1, 'started'), (T2, 'blocked_charter')))
    h = _health(p, tmp_path)
    assert h['last_note_ts'] == newer and h['activity_source'] == 'notes'
    assert (h['decisions_pending'], h['blocked']) == (1, 2)
    p2 = _project(tmp_path, [_note('FYI: old')])
    _write_journal(p2, _journal((T2, 'completed')))
    assert _health(p2, tmp_path)['activity_source'] == 'journal'


def test_role_ledger_counts_only_open_decisions_so_an_answered_one_is_retired(tmp_path):
    p = _project(tmp_path)
    data = _role(tmp_path, p, 'sent', 'delivery_unknown', 'answered', 'resolved', 'reported', 'held')
    _write_journal(p, _journal((T1, 'completed')), name='ch1-role.md')
    h = _health(p, data)
    assert h['decisions_pending'] == 2
    assert h['last_note_ts'] == T1 and h['unknown'] == []


def test_role_ledger_wins_over_stale_decision_notes(tmp_path):
    p = _project(tmp_path, [_note('DECISION NEEDED: answered long ago')])
    data = _role(tmp_path, p, 'answered')
    assert _health(p, data)['decisions_pending'] == 0


def test_role_with_no_journal_yet_and_a_readable_ledger_is_known(tmp_path):
    p = _project(tmp_path)
    data = _role(tmp_path, p, 'sent')
    h = _health(p, data)
    assert h['decisions_pending'] == 1 and h['unknown'] == [] and h['activity_source'] == 'none'


@pytest.mark.parametrize('damage', ['not json', '{"version": 99}', 'NUL'])
def test_unreadable_ledger_is_unknown_never_a_healthy_zero(tmp_path, damage):
    p = _project(tmp_path)
    data = _role(tmp_path, p, 'sent')
    path = RoleStateStore(data).path('p1', 's1')
    path.write_bytes(b'{"a":"\x00"}' if damage == 'NUL' else damage.encode())
    h = _health(p, data)
    assert h['unknown'] == ['decision ledger unreadable']
    # the number is a floor from the notes, and the flag says it may be low
    assert h['decisions_pending'] == 0


def test_unreadable_journal_is_unknown_and_falls_back_to_notes(tmp_path):
    p = _project(tmp_path, [_note('BLOCKED: b')])
    d = Path(p['project_path']) / 'docs' / '_journal'
    d.mkdir(parents=True)
    (d / f'{CID}-steward.md').write_bytes(b'## 2026-11-17T09:00:00+00:00 | started\n\xff\xfe\n')
    h = _health(p, tmp_path)
    assert h['unknown'] == ['journal unreadable (UnicodeDecodeError)']
    assert h['blocked'] == 1 and h['activity_source'] == 'notes'


@pytest.mark.parametrize('what', ['missing', 'corrupt', 'wrong_shape'])
def test_role_bound_but_schedules_unreadable_is_unknown(tmp_path, what):
    p = _project(tmp_path, steward_assignment={'schedule_id': 's1'})
    if what != 'missing':
        (tmp_path / 'schedules.json').write_text('{oops' if what == 'corrupt' else '{}', encoding='utf-8')
    h = _health(p, tmp_path)
    assert len(h['unknown']) == 1 and h['unknown'][0].startswith('role assignment unreadable')


def test_role_journal_path_that_escapes_is_unknown(tmp_path):
    p = _project(tmp_path)
    data = _role(tmp_path, p, journal='docs/_journal/../../x.md')
    assert _health(p, data)['unknown'] == ['role journal path invalid']


def test_project_without_a_registered_path_is_not_flagged(tmp_path):
    p = _project(tmp_path, [_note('FYI: x')])
    p['project_path'] = ''
    assert _health(p, tmp_path)['unknown'] == []


def test_never_raises_on_garbage(tmp_path):
    for project, charter, notes in [({}, None, None), ({'id': 'x'}, {'id': 7}, [None, 3, 'x']),
                                    ({'id': 'x', 'steward_assignment': 5}, {'id': 'c'}, [])]:
        h = shj.project_health(project, charter, notes, None)
        assert set(h) == {'decisions_pending', 'blocked', 'last_note_ts', 'activity_source', 'unknown'}


# ── loop_health() itself (xfail until the patch is applied) ─────────────────

@pytest.fixture
def wired(tmp_path):
    snapshot = dict(vars(core.CFG))
    projects: dict[str, dict] = {}
    configure(data_root=tmp_path, load_project_fn=lambda pid: projects.get(pid),
              save_project_fn=lambda pid, p: projects.__setitem__(pid, p),
              load_projects_fn=lambda: list(projects.values()),
              append_note_fn=lambda *a, **k: True)
    yield projects
    for k, v in snapshot.items():
        setattr(core.CFG, k, v)


@_needs_patch
def test_loop_health_reads_the_journal_for_a_steward_with_no_notes(tmp_path, wired):
    p = _project(tmp_path)
    wired['p1'] = p
    _write_journal(p, _journal((T1, 'started'), (T2, 'blocked_guard')))
    h = core.loop_health()
    e = h['enabled'][0]
    assert (e['last_note_ts'], e['blocked'], e['activity_source']) == (T2, 1, 'journal')
    assert h['blocked'] == 1 and e['unknown'] == []
    assert not any('health partly unknown' in a for a in h['alerts'])   # no role is not a failure


@_needs_patch
def test_loop_health_notes_only_is_unchanged(tmp_path, wired):
    wired['p1'] = _project(tmp_path, [_note('DECISION NEEDED: a'), _note('BLOCKED: b')])
    h = core.loop_health()
    e = h['enabled'][0]
    assert (e['decisions_pending'], e['blocked'], e['unknown']) == (1, 1, [])
    assert h['decisions_pending'] == 1 and any('decision' in a for a in h['alerts'])


@_needs_patch
def test_loop_health_role_ledger_retires_an_answered_decision(tmp_path, wired):
    p = _project(tmp_path)
    wired['p1'] = p
    _role(tmp_path, p, 'sent', 'answered')       # data root = tmp_path/'data'
    configure(data_root=tmp_path / 'data', load_project_fn=lambda pid: wired.get(pid),
              save_project_fn=lambda *a: None, load_projects_fn=lambda: list(wired.values()),
              append_note_fn=lambda *a, **k: True)
    assert core.loop_health()['enabled'][0]['decisions_pending'] == 1


@_needs_patch
def test_loop_health_unreadable_ledger_is_reported_unknown(tmp_path, wired):
    p = _project(tmp_path)
    wired['p1'] = p
    data = _role(tmp_path, p, 'sent')
    RoleStateStore(data).path('p1', 's1').write_text('not json', encoding='utf-8')
    configure(data_root=data, load_project_fn=lambda pid: wired.get(pid),
              save_project_fn=lambda *a: None, load_projects_fn=lambda: list(wired.values()),
              append_note_fn=lambda *a, **k: True)
    h = core.loop_health()
    assert h['enabled'][0]['unknown'] == ['decision ledger unreadable']
    assert any('health partly unknown' in a for a in h['alerts'])


# ── the patch itself, on a throwaway copy of core.py ────────────────────────

@pytest.fixture
def patched_core(tmp_path):
    if _HAS_FIX:
        pytest.skip('steward/core.py already carries the fix')
    root = tmp_path / 'copy'
    (root / 'steward').mkdir(parents=True)
    (root / 'steward' / 'core.py').write_bytes(
        (PROJECT_ROOT / 'steward' / 'core.py').read_bytes().replace(b'\r\n', b'\n'))
    r = subprocess.run(['git', 'apply', '--unsafe-paths', str(PATCH)], cwd=root,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    spec = importlib.util.spec_from_file_location('steward.core_health_probe',
                                                  root / 'steward' / 'core.py')
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_patched_copy_reads_the_journal_where_master_shows_nothing(tmp_path, wired, patched_core):
    p = _project(tmp_path)
    wired['p1'] = p
    _write_journal(p, _journal((T1, 'started'), (T2, 'blocked_guard')))
    before = core.loop_health()['enabled'][0]
    after = patched_core.loop_health()['enabled'][0]
    # the defect: a rule-following steward looks dead on master
    assert (before['last_note_ts'], before['blocked']) == (None, 0)
    assert (after['last_note_ts'], after['blocked'], after['activity_source']) == (T2, 1, 'journal')


def test_patched_copy_keeps_note_numbers_and_flags_an_unreadable_ledger(tmp_path, wired, patched_core):
    p = _project(tmp_path, [_note('DECISION NEEDED: a')])
    wired['p1'] = p
    assert patched_core.loop_health()['enabled'][0]['decisions_pending'] == 1
    data = _role(tmp_path, p, 'sent')
    RoleStateStore(data).path('p1', 's1').write_text('not json', encoding='utf-8')
    configure(data_root=data, load_project_fn=lambda pid: wired.get(pid),
              save_project_fn=lambda *a: None, load_projects_fn=lambda: list(wired.values()),
              append_note_fn=lambda *a, **k: True)
    e = patched_core.loop_health()['enabled'][0]
    assert e['unknown'] == ['decision ledger unreadable']
