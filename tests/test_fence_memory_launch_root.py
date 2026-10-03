"""MC-1037 round 3: the fence's memory-dir root is an immutable LAUNCH fact.

Round 2 had `/api/session/trigger-type` call `load_project()` on every lookup, so
`POST /api/project/A` rewriting A's `project_path` moved what a live A session
was told its memory dir is (Fenn, 2026-10-03, A-nested-B). The route now returns
`_launch_project_path`, recorded on the session dict once at launch. These tests
pin: it is recorded on every launch path, project edits do not move it, a
session without it gets no `project_path` (fence fails closed), a launch onto
another project (the attended hand-off case) records that project's path, and
the whole chain holds through the real hook (`fence.main(['--armed'])`).
"""
import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'steward'))
import fence  # noqa: E402

import mc.memory as server_memory  # noqa: E402
from tests.test_dispatch_spawn_save_race import env  # noqa: E402,F401  (launch rig)


def _enc(p: Path) -> str:
    return str(p.resolve()).replace(':', '-').replace('\\', '-').replace('/', '-')


def _trigger_type(env, csid):
    return env['client'].get(f'/api/session/trigger-type?claude_session_id={csid}').get_json()


@pytest.mark.parametrize('streaming', [False, True], ids=['mode-a', 'mode-b'])
def test_launch_records_the_project_path_and_a_project_edit_does_not_move_it(
        env, monkeypatch, tmp_path, streaming):
    from mc import state as mc_state
    monkeypatch.setitem(mc_state.CONFIG, 'use_streaming_agent', streaming)
    ar = env['ar']
    launched_at = env['project']['project_path']
    sid = ar._dispatch_agent_internal('p1', 'do the thing')
    session = env['sessions'][sid]
    assert session['_launch_project_path'] == launched_at
    session['claude_session_id'] = 'csid-launch'

    env['project']['project_path'] = str(tmp_path / 'moved-by-project-edit')   # POST /api/project/p1
    got = _trigger_type(env, 'csid-launch')
    assert got['found'] is True and got['project_path'] == launched_at


def test_hand_off_launch_records_the_target_projects_path(env, monkeypatch, tmp_path):
    ar = env['ar']
    target = tmp_path / 'target_proj'
    target.mkdir()
    projects = {'p1': env['project'],
                'p2': {'id': 'p2', 'project_path': str(target), 'provider': 'claude'}}
    monkeypatch.setattr(ar, 'load_project', lambda pid: projects.get(pid))
    monkeypatch.setattr(ar, '_maybe_isolate_worktree', lambda *a, **k: (str(target), False))
    sid = ar._dispatch_agent_internal('p2', 'continue over there')
    assert env['sessions'][sid]['_launch_project_path'] == str(target)


def test_trigger_type_carries_no_project_path_when_the_session_has_no_launch_fact(env):
    """A revived session (rebuilt dict) or a pre-field session: absent, so the
    fence turns the exception off. Never derived from load_project()."""
    env['sessions']['old'] = {'project_id': 'p1', 'claude_session_id': 'csid-old',
                              'trigger_type': 'dispatch'}
    got = _trigger_type(env, 'csid-old')
    assert got['found'] is True and 'project_path' not in got


def test_pre_registered_dict_keeps_its_launch_path(env):
    """A Hivemind worker's dict arrives already carrying the key; a later
    launch step must not replace it with the current project record."""
    ar = env['ar']
    assert ar._launch_project_path({'project_path': 'C:\\now'},
                                   {'_launch_project_path': 'C:\\then'}) == 'C:\\then'
    assert ar._launch_project_path({'project_path': 'C:\\now'}, {}) == 'C:\\now'
    assert ar._launch_project_path({}, None) == ''


def test_a_nested_b_rewrite_of_a_cannot_unlock_bs_memory_through_the_hook(
        env, monkeypatch, tmp_path, capsys):
    """Fenn's reproducer, end to end through the real route and `main(['--armed'])`.

    Session A launched at <fx>/A. B stays registered at <fx>/A/nested-B. A's
    project_path is then rewritten to <fx>/A-nested-B, which encodes to the
    SAME Claude memory dir as B. Round 2 returned the rewritten path and the
    Write to B's topic exited 0; it must stay 2 before and after the rewrite,
    while A's own topic file stays writable (so the test is not vacuous)."""
    home = tmp_path / 'home'
    projects = home / '.claude' / 'projects'
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setattr(server_memory, 'CLAUDE_HOME', projects, raising=False)
    fx = tmp_path / 'fx'
    a = fx / 'A'
    b = a / 'nested-B'
    b.mkdir(parents=True)
    (fx / 'A-nested-B').mkdir()
    mem_a = projects / _enc(a) / 'memory'
    mem_b = projects / _enc(b) / 'memory'
    assert mem_b == projects / _enc(fx / 'A-nested-B') / 'memory'
    for d in (mem_a, mem_b):
        d.mkdir(parents=True)
        (d / 'topic.md').write_text('x', encoding='utf-8')

    ar = env['ar']
    proj_a = {'id': 'A', 'project_path': str(a), 'provider': 'claude'}
    proj_b = {'id': 'B', 'project_path': str(b), 'provider': 'claude'}
    records = {'A': proj_a, 'B': proj_b}
    monkeypatch.setattr(ar, 'load_project', lambda pid: records.get(pid))
    monkeypatch.setattr(ar, '_maybe_isolate_worktree', lambda *a_, **k: (str(a), False))
    sid = ar._dispatch_agent_internal('A', 'work in A')
    env['sessions'][sid]['claude_session_id'] = 'csid-A'
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'csid-A')
    monkeypatch.setattr(fence, '_lookup_trigger_type', lambda s: _trigger_type(env, s))

    def write(path):
        payload = {'tool_name': 'Write', 'cwd': str(a),
                   'tool_input': {'file_path': str(path), 'content': 'x'}}
        monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
        rc = fence.main(['--armed'])
        capsys.readouterr()
        return rc

    assert write(mem_a / 'topic.md') == 0
    assert write(mem_b / 'topic.md') == 2

    proj_a['project_path'] = str(fx / 'A-nested-B')          # POST /api/project/A

    assert write(mem_b / 'topic.md') == 2
    assert write(mem_a / 'topic.md') == 0
