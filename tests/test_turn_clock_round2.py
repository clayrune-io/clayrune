"""Turn clock, review round 2 (Fenn on b949168a): three findings, one pin each.

 1. `note_reply` fired at process exit unconditionally -- an empty turn, or a
    reader that no longer owns the session (codex retry swap), reset a 9h gap
    to 0.
 2. Revive paths other than Claude Mode B (Claude Mode A, native engines
    re-attached through `_dispatch_agent_internal`) got no clock and no gap.
 3. Initial dispatch, live steering and Claude Mode A queued follow-ups
    bypassed `_clock_wrap`.

Every test drives the shipped code path (real readers / real routes / real
revive), and fails on b949168a.
"""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as agent_runtime_mod  # noqa: E402
from mc import turn_clock  # noqa: E402
from tests.test_runtime_completion_log import (  # noqa: E402,F401
    _FakeProc, _FakeRuntime, env, _dispatch)
from tests.test_revive_notify_carry import ar, _project, CSID  # noqa: E402,F401
from tests.test_mid_turn_send import (  # noqa: E402,F401
    client, _sess, _route_setup, _post)

NINE_H = 9 * 3600 + 20 * 60
OLD = lambda: time.time() - NINE_H  # noqa: E731


def _iso(epoch):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(epoch))


def _handle(session, provider='fakeprov'):
    return agent_runtime_mod.SessionHandle(
        mc_session_id='s1', provider=provider, mode='A', project_path='/p',
        project_id='p1', session_dict=session, meta={'callbacks': {}})


# ── finding 1: stamp only a real reply from the owning reader ───────────────

def test_shared_reader_empty_turn_does_not_reset_the_gap(env):
    sid, handle = _dispatch(env)
    sess = env['sessions'][sid]
    old = OLD()
    sess[turn_clock._REPLY_KEY] = old
    env['runtime'].run_turn(handle, [], rc=0)
    assert sess[turn_clock._REPLY_KEY] == old


def test_shared_reader_superseded_proc_does_not_stamp(env):
    """codex retry_without_model: a new proc owns the session while the old
    reader is still draining. The old reader's exit is not the agent speaking."""
    sid, handle = _dispatch(env)
    sess = env['sessions'][sid]
    old = OLD()
    sess[turn_clock._REPLY_KEY] = old
    msg = '__MSG__:partial words'

    def _lines():
        yield msg
        sess['proc'] = object()      # respawn took over mid-stream

    proc = _FakeProc(_lines())
    sess['proc'] = proc
    sess['status'] = 'running'
    agent_runtime_mod._mode_a_reader(proc, handle, env['runtime'])
    assert sess[turn_clock._REPLY_KEY] == old


def test_shared_reader_real_reply_still_stamps(env):
    sid, handle = _dispatch(env)
    sess = env['sessions'][sid]
    sess[turn_clock._REPLY_KEY] = OLD()
    before = time.time()
    env['runtime'].run_turn(handle, ['__MSG__:an actual answer'], rc=0)
    assert sess[turn_clock._REPLY_KEY] >= before


def test_gemini_reader_empty_turn_does_not_reset_the_gap():
    old = OLD()
    proc = _FakeProc([], rc=0)
    session = {'log_lines': [], 'proc': proc, turn_clock._REPLY_KEY: old}
    agent_runtime_mod.GeminiRuntime()._read_stream(proc, _handle(session, 'gemini'))
    assert session[turn_clock._REPLY_KEY] == old


def test_gemini_reader_superseded_proc_does_not_stamp():
    old = OLD()
    fixture = (PROJECT_ROOT / 'tests' / 'fixtures'
               / 'gemini_live_stream_2026-09-18.jsonl')
    raw = fixture.read_text(encoding='utf-8').splitlines(keepends=True)
    session = {'log_lines': [], turn_clock._REPLY_KEY: old}

    def _lines():
        yield from raw
        session['proc'] = object()

    proc = _FakeProc(_lines(), rc=0)
    session['proc'] = proc
    agent_runtime_mod.GeminiRuntime()._read_stream(proc, _handle(session, 'gemini'))
    assert session[turn_clock._REPLY_KEY] == old


def test_gemini_reader_real_reply_still_stamps():
    fixture = (PROJECT_ROOT / 'tests' / 'fixtures'
               / 'gemini_live_stream_2026-09-18.jsonl')
    raw = fixture.read_text(encoding='utf-8').splitlines(keepends=True)
    proc = _FakeProc(raw, rc=0)
    session = {'log_lines': [], 'proc': proc, turn_clock._REPLY_KEY: OLD()}
    before = time.time()
    agent_runtime_mod.GeminiRuntime()._read_stream(proc, _handle(session, 'gemini'))
    assert session[turn_clock._REPLY_KEY] >= before


# ── finding 2: every revive carries the gap ─────────────────────────────────

class _RecStdin:
    def __init__(self):
        self.buf = []

    def write(self, s):
        self.buf.append(s)

    def flush(self):
        pass

    def close(self):
        pass


def _capture_popen(ar_, monkeypatch):
    seen = {'cmd': None, 'stdin': _RecStdin()}

    def _popen(cmd, *a, **k):
        seen['cmd'] = cmd
        return SimpleNamespace(pid=555, stdin=seen['stdin'], stdout=iter([]),
                               poll=lambda: None)
    monkeypatch.setattr(ar_.subprocess, 'Popen', _popen)
    monkeypatch.setattr(ar_._turn_clock, 'gap_briefing', lambda *a, **k: '')
    return seen


def _row_nine_hours_ago():
    return [{'session_id': 's1', 'claude_session_id': CSID, 'status': 'completed',
             'ts': _iso(OLD())}]


@pytest.mark.parametrize('streaming', [False, True])
def test_claude_revive_with_no_transcript_uses_agent_log_gap(
        ar, tmp_path, monkeypatch, streaming):
    """Mode A (-p) and Mode B (stdin) both: with no transcript to read the
    last-reply time comes from the durable agent_log row."""
    monkeypatch.setitem(ar.state.CONFIG, 'use_streaming_agent', streaming)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid, **_kw: _row_nine_hours_ago())
    seen = _capture_popen(ar, monkeypatch)

    session = ar._revive_from_agent_log('p1', 's1', 'still there?', _project(tmp_path))

    assert session is not None
    if streaming:
        out = json.loads(''.join(seen['stdin'].buf))['message']['content']
    else:
        out = seen['cmd'][seen['cmd'].index('-p') + 1]
    assert out.startswith('[Now '), out[:120]
    assert '9h 20m since your last reply' in out.split('\n', 1)[0], out[:160]
    assert out.count('[Now ') == 1
    # the revived session keeps the (old) stamp so the NEXT turn is measured
    # from the real last reply, not from the revive.
    assert turn_clock.last_reply_epoch(session) == pytest.approx(
        turn_clock._parse_iso(_row_nine_hours_ago()[0]['ts']), abs=1)


def test_dispatch_clock_task_fresh_session_is_stamp_only(ar, tmp_path):
    out = ar._dispatch_clock_task(_project(tmp_path), 'p1', 'do the thing')
    head, _, body = out.partition('\n\n')
    assert head.startswith('[Now ') and 'since your last reply' not in head
    assert 'Since your last reply:' not in out
    assert body == 'do the thing'


def test_dispatch_clock_task_reattached_conversation_gets_the_gap(
        ar, tmp_path, monkeypatch):
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid, **_kw: _row_nine_hours_ago())
    monkeypatch.setattr(ar._turn_clock, 'gap_briefing', lambda *a, **k: '')
    out = ar._dispatch_clock_task(_project(tmp_path), 'p1', 'carry on',
                                  reuse_session_id='s1')
    assert '9h 20m since your last reply' in out.split('\n', 1)[0], out[:160]
    assert out.endswith('carry on')


def test_dispatch_clock_task_never_raises_and_never_stacks(ar, tmp_path, monkeypatch):
    p = _project(tmp_path)
    once = ar._dispatch_clock_task(p, 'p1', 'x')
    assert ar._dispatch_clock_task(p, 'p1', once) == once      # no double wrap
    monkeypatch.setattr(ar, '_clock_wrap',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
    assert ar._dispatch_clock_task(p, 'p1', 'plain') == 'plain'


def test_native_engine_dispatch_sends_the_stamped_task_to_the_runtime(env, monkeypatch):
    """`_dispatch_via_runtime(outgoing_task=...)` is what reaches the CLI;
    `task` stays clean for context building and the session record."""
    seen = {}
    real = env['runtime'].dispatch

    def _rec(**kw):
        seen['task'] = kw['task']
        return real(**kw)
    monkeypatch.setattr(env['runtime'], 'dispatch', _rec)
    sid, _h = _dispatch(env, task='plain task',
                        outgoing_task='[Now 2026-11-04 07:10 PT]\n\nplain task')
    assert seen['task'].startswith('[Now ')
    assert env['sessions'][sid]['task'] == 'plain task'


# ── finding 3: the remaining bypasses ───────────────────────────────────────

def test_live_steer_carries_the_clock(client, tmp_path, monkeypatch):
    s = _sess(provider='claude', mode='B')
    calls = _route_setup(monkeypatch, tmp_path, s)
    r = _post(client, {'message': 'new direction'})
    assert r.get_json()['route'] == 'steer'
    content = json.loads(calls['writes'][0])['message']['content']
    assert content.startswith('[Now '), content[:80]
    assert content.endswith('new direction')
    assert content.count('[Now ') == 1


def test_live_steer_slash_command_stays_first_bytes(client, tmp_path, monkeypatch):
    s = _sess(provider='claude', mode='B')
    calls = _route_setup(monkeypatch, tmp_path, s)
    _post(client, {'message': '/compact'})
    assert json.loads(calls['writes'][0])['message']['content'].startswith('/compact')


def test_queued_claude_mode_a_followup_carries_the_clock(ar, tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(ar, 'load_project', lambda _: project)
    monkeypatch.setattr(ar, '_respawn_sysprompt_args', lambda *a: ([], None))
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'claude')
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **k: [])
    seen = _capture_popen(ar, monkeypatch)
    session = dict(project_id='p1', session_id='s1', model='sonnet', log_lines=[],
                   claude_session_id=CSID, **{turn_clock._REPLY_KEY: OLD()})
    ar._auto_dispatch_followup(session, 'continue')
    sent = seen['cmd'][seen['cmd'].index('-p') + 1]
    assert sent.startswith('[Now '), sent[:80]
    assert '9h' in sent.split('\n', 1)[0]
    assert sent.endswith('continue')


def test_has_header_blocks_double_wrap_and_strip_peels_every_variant():
    stamped = '[Now 2026-11-04 07:10 PT · 9h 20m since your last reply]\n\nhello'
    assert turn_clock.has_header(stamped)
    assert not turn_clock.has_header('hello [Now 2026-11-04 07:10 PT]')
    for variant in ('[Now 2026-11-04 07:10 PT]\n\nhello', stamped):
        assert agent_runtime_mod.strip_injected_preamble(variant) == 'hello', variant


@pytest.mark.parametrize('streaming', [False, True])
def test_initial_claude_dispatch_is_stamped(ar, tmp_path, monkeypatch, streaming):
    """The message that STARTS a process (Mode A `-p`, Mode B first stdin
    write) carries the stamp, with no gap briefing -- there is no prior reply."""
    project = _project(tmp_path)
    project['use_streaming_agent'] = streaming
    monkeypatch.setitem(ar.state.CONFIG, 'use_streaming_agent', streaming)
    monkeypatch.setattr(ar, 'load_project', lambda _: project)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid, **_kw: [])
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'claude')
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **k: [])
    seen = _capture_popen(ar, monkeypatch)

    ar._dispatch_agent_internal('p1', 'build the thing')

    if streaming:
        deadline = time.time() + 5          # the first write is a daemon thread
        while not seen['stdin'].buf and time.time() < deadline:
            time.sleep(0.01)
        out = json.loads(''.join(seen['stdin'].buf).splitlines()[0])['message']['content']
    else:
        out = seen['cmd'][seen['cmd'].index('-p') + 1]
    head, _, body = out.partition('\n\n')
    assert head.startswith('[Now ') and 'since your last reply' not in head, out[:160]
    assert body == 'build the thing'
