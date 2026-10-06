"""MC-1055: "Allow once" never worked because the fence runs twice per tool call.

A Clayrune-launched Claude session registers steward/fence.py in two places:
the project's `.claude/settings.json` (steward.core.install_fence_to_project,
written under whatever interpreter ran at install time) and the per-launch
`--settings` file (tools/guards/install_hooks.py, written at boot under the
server's interpreter). Claude Code collapses only byte-identical commands, so
with two interpreters both run, each against the same tool call. The first
spent the human's pass and exited 0, the second found none and exited 2, and a
single exit 2 blocks the call. Live evidence 2026-10-06: two POSTs to
/api/session/attend-once/consume per tool call, call still blocked.

Fix, in two halves:
  * server (mc/attend_once_replay.py, live): consume is idempotent for ONE
    tool call, keyed on (tool_use_id, digest of what the call does).
  * fence (docs/patches/fence-attend-once-per-call.patch, a human applies it,
    agents cannot edit steward/fence.py): main() sends that identity.

The end-to-end tests run the real consume route against the PATCHED fence
(the patch applied to a temp copy, or the live file once a human lands it).
`test_live_fence_*` runs against steward/fence.py as it is now: it is an
expected failure until the patch lands, then it must pass (strict xfail).
"""
from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import steward.fence as live_fence

REPO = Path(__file__).resolve().parents[1]
PATCH = REPO / 'docs' / 'patches' / 'fence-attend-once-per-call.patch'
PASSCODE = 'one-spend-1234'
CSID = 'csid-one-spend'
PUSH = 'git push origin master'
PUSH_OTHER = 'git push origin release'


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprints)
    from mc import state as mc_state
    from mc.blueprints import local_auth as la
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    mc_state.agent_sessions['sid-1'] = {'project_id': 'proj-a', 'claude_session_id': CSID,
                                        'trigger_type': 'dispatch'}
    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client()
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


def _grant(client):
    from mc.blueprints import local_auth as la
    if not la._local_auth_is_configured():
        la._local_auth_set_passcode(PASSCODE)
    r = client.post('/api/project/proj-a/agent/sid-1/attend-once', json={'passcode': PASSCODE})
    assert r.get_json()['status'] in ('granted', 'already_open')


def _consume(client, call=None, digest=None, **extra):
    body = {'claude_session_id': CSID, **extra}
    if call is not None:
        body['tool_use_id'] = call
    if digest is not None:
        body['action_digest'] = digest
    return client.post('/api/session/attend-once/consume', json=body).get_json()


def _session():
    from mc import state as mc_state
    return mc_state.agent_sessions['sid-1']


def _patched_fence(tmp_path):
    """steward.fence with the MC-1055 patch: the live module when a human has
    already applied it, otherwise a temp copy with the patch applied."""
    if hasattr(live_fence, '_tool_call_identity'):
        return live_fence
    work = tmp_path / 'fence-patched'
    (work / 'steward').mkdir(parents=True)
    src = (REPO / 'steward' / 'fence.py').read_text(encoding='utf-8').replace('\r\n', '\n')
    (work / 'steward' / 'fence.py').write_text(src, encoding='utf-8', newline='\n')
    subprocess.run(['git', 'init', '-q'], cwd=work, check=True)
    # A CRLF checkout (core.autocrlf, text=auto) turns the patch into CRLF;
    # the copy above is LF, so normalise the patch the same way.
    patch = tmp_path / 'fence-attend-once-per-call.lf.patch'
    patch.write_text(PATCH.read_text(encoding='utf-8').replace('\r\n', '\n'),
                     encoding='utf-8', newline='\n')
    subprocess.run(['git', 'apply', '--whitespace=nowarn', str(patch)], cwd=work, check=True)
    spec = importlib.util.spec_from_file_location('fence_patched_mc1055',
                                                  work / 'steward' / 'fence.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hook_runner(monkeypatch, tmp_path, client, fence):
    """Returns run(command, tool_use_id) -> exit code of ONE hook process,
    wired to the real consume route through the test client."""
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text(json.dumps({'type': 'user', 'message': {
        'role': 'user', 'content': 'Please go implement the fix we discussed'}}) + '\n',
        encoding='utf-8')
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', CSID)
    monkeypatch.setattr(fence, '_lookup_trigger_type',
                        lambda sid: {'trigger_type': 'dispatch', 'fence_unattended_enabled': True})

    class _Resp:
        def __init__(self, data):
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self._data).encode('utf-8')

    def _urlopen(req, timeout=None):
        path = '/' + req.full_url.split('://', 1)[1].split('/', 1)[1]
        return _Resp(client.post(path, json=json.loads(req.data.decode('utf-8'))).get_json())
    monkeypatch.setattr(fence.urllib.request, 'urlopen', _urlopen)

    def run(command, tool_use_id='toolu_A'):
        payload = {'tool_name': 'Bash', 'tool_input': {'command': command},
                   'transcript_path': str(transcript)}
        if tool_use_id is not None:
            payload['tool_use_id'] = tool_use_id
        monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
        return fence.main()
    return run


# ── Step 1: the two registrations (evidence) ──────────────────────────────────

def test_a_launched_session_gets_the_fence_from_two_registrations(tmp_path, monkeypatch):
    """The cause, pinned. Source one: the entry steward.core merges into the
    project's .claude/settings.json. Source two: the group the boot step puts
    in the per-launch --settings file. Both match Bash, so both run on every
    call. If this fails the registrations were deduped, and the consume tests
    below are then belt-and-braces rather than the fix."""
    from steward import core
    from tools.guards import install_hooks

    project_entry = core._fence_settings_content()['hooks']['PreToolUse'][0]
    _before, after, _changed = install_hooks.plan_generate('claude', clayrune_home=tmp_path)
    launch_groups = [g for g in after['hooks']['PreToolUse']
                     if any(h.get('name') == install_hooks.FENCE_HOOK_NAME for h in g['hooks'])]

    assert 'Bash' in project_entry['matcher'] and 'fence.py' in project_entry['hooks'][0]['command']
    assert len(launch_groups) == 1
    assert 'Bash' in launch_groups[0]['matcher']
    assert 'fence.py' in launch_groups[0]['hooks'][0]['command']


def test_the_two_registrations_differ_when_written_under_different_interpreters(monkeypatch):
    """Claude Code runs a hook once per DISTINCT command string. The project
    file is written when a project is first fenced; the launch file at every
    boot. On the reporting machine the first used the system python and the
    second the venv python, so the strings differed and both ran."""
    from steward import core
    monkeypatch.setattr(sys, 'executable', r'C:\Python314\python.exe')
    system_python = core._fence_command()
    monkeypatch.setattr(sys, 'executable', r'C:\repo\.venv\Scripts\python.exe')
    venv_python = core._fence_command()
    assert system_python != venv_python


# ── server half: consume is idempotent for one tool call ──────────────────────

def test_repeat_of_the_same_call_is_answered_without_a_second_spend(client):
    _grant(client)
    first = _consume(client, 'toolu_A', 'dg-A')
    again = _consume(client, 'toolu_A', 'dg-A')
    assert first['consumed'] is True and not first.get('replay')
    assert again['consumed'] is True and again['replay'] is True
    assert '_attend_once_pass' not in _session()


def test_a_second_different_call_finds_no_pass(client):
    _grant(client)
    assert _consume(client, 'toolu_A', 'dg-A')['consumed'] is True
    assert _consume(client, 'toolu_B', 'dg-B')['consumed'] is False


def test_same_id_with_different_content_is_not_a_replay(client):
    _grant(client)
    assert _consume(client, 'toolu_A', 'dg-A')['consumed'] is True
    assert _consume(client, 'toolu_A', 'dg-OTHER')['consumed'] is False


def test_same_content_under_a_new_id_is_not_a_replay(client):
    _grant(client)
    assert _consume(client, 'toolu_A', 'dg-A')['consumed'] is True
    assert _consume(client, 'toolu_NEW', 'dg-A')['consumed'] is False


def test_requests_without_identity_keep_spend_once_per_request(client):
    """Older fence.py / Codex send no tool_use_id: unchanged behaviour."""
    _grant(client)
    assert _consume(client)['consumed'] is True
    assert _consume(client)['consumed'] is False
    assert 'toolu' not in json.dumps(_session().get('_attend_once_spent') or {})


@pytest.mark.parametrize('extra', [
    {'tool_use_id': 7, 'action_digest': 'dg'},
    {'tool_use_id': 'toolu_A'},
    {'action_digest': 'dg'},
    {'tool_use_id': '', 'action_digest': 'dg'},
    {'tool_use_id': 'x' * 500, 'action_digest': 'dg'},
])
def test_malformed_identity_falls_back_to_one_spend_per_request(client, extra):
    _grant(client)
    body = {'claude_session_id': CSID, **extra}
    first = client.post('/api/session/attend-once/consume', json=body).get_json()
    again = client.post('/api/session/attend-once/consume', json=body).get_json()
    assert first['consumed'] is True and again['consumed'] is False


def test_concurrent_repeats_of_one_call_both_pass_and_spend_once(client):
    _grant(client)
    results = [None, None]

    def _go(i):
        results[i] = _consume(client, 'toolu_A', 'dg-A')['consumed']
    threads = [threading.Thread(target=_go, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [True, True]
    assert '_attend_once_pass' not in _session()
    assert _consume(client, 'toolu_B', 'dg-B')['consumed'] is False


def test_a_repeat_does_not_eat_a_pass_granted_afterwards(client):
    _grant(client)
    assert _consume(client, 'toolu_A', 'dg-A')['consumed'] is True
    _grant(client)  # the human clicks again for the NEXT action
    assert _consume(client, 'toolu_A', 'dg-A')['replay'] is True
    assert '_attend_once_pass' in _session()
    assert _consume(client, 'toolu_B', 'dg-B')['consumed'] is True


def test_an_expired_record_is_not_a_replay(client):
    _grant(client)
    assert _consume(client, 'toolu_A', 'dg-A')['consumed'] is True
    _session()['_attend_once_spent']['expires_at'] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert _consume(client, 'toolu_A', 'dg-A')['consumed'] is False


# ── fence half, end to end through the real route ─────────────────────────────

def test_one_click_lets_one_call_through_both_fence_runs(monkeypatch, tmp_path, client):
    """The reported bug: two hook processes judge one call; the human's single
    click must let BOTH exit 0 (any exit 2 blocks the call)."""
    fence = _patched_fence(tmp_path)
    run = _hook_runner(monkeypatch, tmp_path, client, fence)
    _grant(client)
    assert [run(PUSH), run(PUSH)] == [0, 0]
    assert '_attend_once_pass' not in _session()


def test_the_same_click_never_covers_a_second_different_action(monkeypatch, tmp_path, client, capsys):
    fence = _patched_fence(tmp_path)
    run = _hook_runner(monkeypatch, tmp_path, client, fence)
    _grant(client)
    assert [run(PUSH, 'toolu_A'), run(PUSH, 'toolu_A')] == [0, 0]
    assert run(PUSH_OTHER, 'toolu_B') == 2          # next call, other action
    assert run(PUSH_OTHER, 'toolu_B') == 2
    assert 'STEWARD FENCE blocked' in capsys.readouterr().err


def test_a_replayed_id_carrying_a_different_command_stays_blocked(monkeypatch, tmp_path, client):
    fence = _patched_fence(tmp_path)
    run = _hook_runner(monkeypatch, tmp_path, client, fence)
    _grant(client)
    assert run(PUSH, 'toolu_A') == 0
    assert run(PUSH_OTHER, 'toolu_A') == 2


def test_no_pass_means_both_runs_block(monkeypatch, tmp_path, client):
    fence = _patched_fence(tmp_path)
    run = _hook_runner(monkeypatch, tmp_path, client, fence)
    assert [run(PUSH), run(PUSH)] == [2, 2]


def test_payload_without_a_tool_use_id_keeps_the_old_one_spend(monkeypatch, tmp_path, client):
    fence = _patched_fence(tmp_path)
    run = _hook_runner(monkeypatch, tmp_path, client, fence)
    _grant(client)
    assert [run(PUSH, None), run(PUSH, None)] == [0, 2]


def test_identity_covers_what_the_call_does(tmp_path):
    fence = _patched_fence(tmp_path)
    ident = fence._tool_call_identity
    base = ident({'tool_use_id': 't1'}, 'Bash', {'command': 'a'})
    assert base == ident({'tool_use_id': 't1'}, 'Bash', {'command': 'a'})
    assert base != ident({'tool_use_id': 't1'}, 'Bash', {'command': 'b'})
    assert base != ident({'tool_use_id': 't2'}, 'Bash', {'command': 'a'})
    assert base != ident({'tool_use_id': 't1'}, 'PowerShell', {'command': 'a'})
    assert ident({}, 'Bash', {'command': 'a'}) is None


# ── Step 1 proof: the fence as it stands today ────────────────────────────────

@pytest.mark.xfail(strict=True, condition=not hasattr(live_fence, '_tool_call_identity'),
                   reason='MC-1055: steward/fence.py has not taken '
                          'docs/patches/fence-attend-once-per-call.patch yet')
def test_live_fence_one_click_lets_one_call_through_both_runs(monkeypatch, tmp_path, client):
    """RED today: [0, 2]. The first run spends the pass, the second blocks."""
    run = _hook_runner(monkeypatch, tmp_path, client, live_fence)
    _grant(client)
    assert [run(PUSH), run(PUSH)] == [0, 0]
