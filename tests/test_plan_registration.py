"""Plan-file registration and the PLAN tab (mc/blueprints/agent_routes.py).

Two defects, both found 2026-08-23 when a design doc written this session did
not appear in the tab:

1. **`plan_file` was a SCALAR.** A session that wrote three plans kept only the
   third; the other two were unreachable from every UI. `plan_files` is a list
   now, with the scalar retained as "most recent" because the in-chat plan link
   and the approval banner are single-valued by nature.
2. **Only Write/Edit tool calls registered a plan.** A plan created by heredoc,
   `cp`, or `tee` produced no Write tool call, so it registered nowhere and the
   tab never mentioned it — silently, forever.

The read path also has to stay backward compatible: `data/projects/<id>_agent_log.json`
is untracked user data with years of entries carrying only the scalar, and
there is no migration.
"""
import json
import sys
import threading
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la
    from mc.blueprints import project_routes as pr

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    plans = tmp_path / 'plans'
    plans.mkdir()
    monkeypatch.setattr(ar, '_PLANS_DIR', plans)

    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(pr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(pr, '_DATA_ROOT', tmp_path)
    monkeypatch.setattr(pr, 'PROJECTS_BASE', tmp_path)
    monkeypatch.setattr(pr, 'get_manager',
                        lambda pid: types.SimpleNamespace(lock=threading.Lock()))
    (data_dir / 'p1.json').write_text(
        json.dumps({'id': 'p1', 'name': 'P1', 'backlog': []}), encoding='utf-8')

    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()

    server.app.config['TESTING'] = True
    c = types.SimpleNamespace(ar=ar, plans=plans, sessions=mc_state.agent_sessions,
                              client=server.app.test_client(), data_dir=data_dir)
    yield c

    mc_state.agent_sessions.clear()
    mc_state.agent_sessions.update(snapshot)


def _plan(env, name='design.md'):
    p = env.plans / name
    p.write_text(f'# {name}\n', encoding='utf-8')
    return str(p)


# ── the list ─────────────────────────────────────────────────────────────────

def test_a_session_keeps_every_plan_it_writes(env):
    """Defect 1. Three plans in one session used to collapse to one."""
    s = {}
    a, b, c = (_plan(env, n) for n in ('a.md', 'b.md', 'c.md'))
    for fp in (a, b, c):
        assert env.ar._register_plan_file(s, fp) is True
    assert s['plan_files'] == [a, b, c]
    # The scalar stays the MOST RECENT — the in-chat link is single-valued.
    assert s['plan_file'] == c


def test_registering_the_same_plan_twice_is_a_noop(env):
    s = {}
    a = _plan(env, 'a.md')
    env.ar._register_plan_file(s, a)
    env.ar._register_plan_file(s, a)
    assert s['plan_files'] == [a]


def test_a_path_outside_the_plans_dir_is_refused(env, tmp_path):
    """The containment rule that already governed the scalar: a plan is a .md
    under the plans dir, not any markdown the agent happened to touch."""
    s = {}
    stray = tmp_path / 'notes.md'
    stray.write_text('x', encoding='utf-8')
    assert env.ar._register_plan_file(s, str(stray)) is False
    assert env.ar._register_plan_file(s, str(env.plans / 'x.txt')) is False
    assert s == {}


def test_scalar_only_records_still_read(env):
    """Years of agent-log entries carry only `plan_file`, and the file is
    untracked user data with no migration path."""
    assert env.ar._session_plan_files({'plan_file': '/x/a.md'}) == ['/x/a.md']
    assert env.ar._session_plan_files({}) == []
    assert env.ar._session_plan_files(None) == []


def test_the_scalar_is_not_duplicated_into_the_list(env):
    rec = {'plan_files': ['/x/a.md'], 'plan_file': '/x/a.md'}
    assert env.ar._session_plan_files(rec) == ['/x/a.md']


# ── shell-written plans ──────────────────────────────────────────────────────

@pytest.mark.parametrize('tpl', [
    'cat > {p} <<EOF',
    'cp docs/design.md {p}',
    'tee "{p}"',
    "printf x >> '{p}'",
    'python x.py && cp a {p} && echo done',
])
def test_plans_written_from_the_shell_are_detected(env, tpl):
    """Defect 2. None of these produce a Write tool call."""
    target = str(env.plans / 'foo.md')
    found = env.ar._plan_paths_in_command(tpl.format(p=target))
    assert found == [target]


def test_a_tilde_path_is_expanded_before_the_containment_check(env, monkeypatch):
    """Agents write `~/.claude/plans/x.md`; unexpanded, that never resolves
    under the plans dir and the detector silently sees nothing."""
    monkeypatch.setattr(env.ar, '_PLANS_DIR',
                        Path.home() / '.claude' / 'plans')
    found = env.ar._plan_paths_in_command('cp a ~/.claude/plans/x.md')
    assert found and found[0].endswith('x.md')
    assert '~' not in found[0]


@pytest.mark.parametrize('cmd', [
    'echo hi > /tmp/other.md',
    'ls ~/.claude/plans/',
    'rm -rf /',
    '',
    'cat docs/AGENT_FLOOR_DESIGN.md',
])
def test_unrelated_commands_register_nothing(env, cmd):
    """Containment: a markdown file elsewhere on disk is not a plan, and
    listing the plans DIRECTORY is not writing one."""
    assert env.ar._plan_paths_in_command(cmd) == []


def test_existence_is_not_checked_at_detection_time(env):
    """`tool_use` is emitted BEFORE the command runs, so the file cannot exist
    yet. The read path filters — a failed command simply never produces one."""
    target = str(env.plans / 'never-created.md')
    out = env.ar._plan_paths_in_command(f'cp x {target}')
    assert out == [target] and not Path(target).exists()


# ── the PLAN tab ─────────────────────────────────────────────────────────────

def test_the_tab_lists_every_plan_from_a_live_session(env):
    a, b = _plan(env, 'a.md'), _plan(env, 'b.md')
    env.sessions['s1'] = {'session_id': 's1', 'project_id': 'p1', 'task': 't',
                          'started_at': '2026-08-23T00:00:00Z',
                          'plan_files': [a, b], 'plan_file': b}
    got = env.client.get('/api/project/p1/plans').get_json()
    assert {p['filename'] for p in got} == {'a.md', 'b.md'}


def test_the_tab_still_reads_a_scalar_only_session(env):
    a = _plan(env, 'a.md')
    env.sessions['s1'] = {'session_id': 's1', 'project_id': 'p1', 'task': 't',
                          'started_at': '2026-08-23T00:00:00Z', 'plan_file': a}
    got = env.client.get('/api/project/p1/plans').get_json()
    assert [p['filename'] for p in got] == ['a.md']


def test_a_plan_that_no_longer_exists_is_not_listed(env):
    ghost = str(env.plans / 'deleted.md')
    env.sessions['s1'] = {'session_id': 's1', 'project_id': 'p1', 'task': 't',
                          'started_at': '2026-08-23T00:00:00Z',
                          'plan_files': [ghost]}
    assert env.client.get('/api/project/p1/plans').get_json() == []


def test_another_projects_session_does_not_leak_in(env):
    a = _plan(env, 'a.md')
    env.sessions['s2'] = {'session_id': 's2', 'project_id': 'other', 'task': 't',
                          'started_at': '2026-08-23T00:00:00Z', 'plan_files': [a]}
    assert env.client.get('/api/project/p1/plans').get_json() == []


def test_one_plan_registered_by_two_sessions_is_listed_once(env):
    a = _plan(env, 'a.md')
    for sid in ('s1', 's2'):
        env.sessions[sid] = {'session_id': sid, 'project_id': 'p1', 'task': 't',
                             'started_at': '2026-08-23T00:00:00Z',
                             'plan_files': [a]}
    got = env.client.get('/api/project/p1/plans').get_json()
    assert len(got) == 1
