"""MC-964 Step E, report mode (T1 + T2 + T3 of the 2026-10-15 diagnosis).

docs/_journal/b2d85e51-memory-retrieval-diagnosis.md (Bram + Dave's
"DECIDED WITHOUT RON"):
  T1  classify each human message at send time, LOG candidates, mint nothing;
      the reconciler's rebuilt session carries trigger_type from the agent_log
      row. Acceptance: replaying the 3 October messages gives 3 logged rows.
  T2  complaint shapes never mint.
  T3  the log_lines trim never drops a user line, and logs when it bites.
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The three real sentences Ron typed (transcripts, 2026-09-30 / 10-02 / 10-06)
# that Bram found matching the cue regex after Step E shipped.
OCT_MESSAGES = [
    ("a bug - when installing CR on new host and choosing to ignore the tour, "
     "both setup and tour keeps on showing every time I login to the app. Annoying..."),
    ('BTW, on PC I notice something I call "shadower" whenever I type and trigger '
     'you for example in one chat, All of other Dave\'s viewable on screen on other '
     'chats also show the "thinking" badge for couple of seconds until your actual '
     'work begins on the actual chat'),
    ("btw, one annoying item, every time I enter the passcode I get this. "
     "Anyway to skip this for passcode?"),
]
REAL_HABIT = "We have plenty of codex token allowance, I added some more"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import habit_report as hr
    from mc import memory as mem
    monkeypatch.setattr(hr, '_log_dir', lambda: tmp_path / 'habit_candidates')
    monkeypatch.setattr(hr, '_recent', {})
    monkeypatch.setitem(mem.state.CONFIG, 'user_name', 'Ron')
    monkeypatch.setitem(mem.state.CONFIG, 'habit_report_enabled', True)
    monkeypatch.setitem(mem.state.CONFIG, 'habit_complaint_exclusion_enabled', True)
    return hr, mem, tmp_path


def _rows(tmp_path, pid='p1'):
    f = tmp_path / 'habit_candidates' / f'{pid}.jsonl'
    if not f.exists():
        return []
    return [json.loads(x) for x in f.read_text(encoding='utf-8').splitlines()]


def _session(trigger_type='manual', task='chat with Ron', sid='sid-1'):
    return {'session_id': sid, 'project_id': 'p1', 'task': task,
            'trigger_type': trigger_type, 'log_lines': []}


# ── T1: acceptance — the 3 October messages give 3 logged candidates ───────

def test_three_october_messages_log_three_rows(env):
    hr, _, tmp = env
    for i, msg in enumerate(OCT_MESSAGES):
        row = hr.observe_user_message('p1', f'sid-{i}', msg, session=_session(sid=f'sid-{i}'))
        assert row is not None, f'no row for message {i}'
    rows = _rows(tmp)
    assert len(rows) == 3
    assert [r['cue'].lower() for r in rows] == ['every time i', 'whenever i', 'every time i']
    assert all(r['origin'] == 'interactive' for r in rows)


def test_observe_mints_nothing(env, monkeypatch):
    hr, mem, _ = env
    monkeypatch.setattr(mem, 'write_topic_note',
                        lambda *a, **k: pytest.fail('report mode must not mint'))
    hr.observe_user_message('p1', 'sid-1', REAL_HABIT, session=_session())


# ── T2: complaint shapes are excluded from minting ─────────────────────────

def test_the_three_october_messages_are_complaints(env):
    hr, _, tmp = env
    for i, msg in enumerate(OCT_MESSAGES):
        hr.observe_user_message('p1', f'sid-{i}', msg, session=_session(sid=f'sid-{i}'))
    rows = _rows(tmp)
    assert [r['verdict'] for r in rows] == ['excluded_complaint'] * 3
    assert [r['would_mint'] for r in rows] == [False] * 3


def test_real_habit_is_a_candidate_and_would_mint_when_attended(env):
    hr, _, tmp = env
    row = hr.observe_user_message('p1', 'sid-1', REAL_HABIT, session=_session())
    assert row['verdict'] == 'candidate' and row['would_mint'] is True
    assert row['slug'].startswith('project_habit_')


@pytest.mark.parametrize('stmt', [
    REAL_HABIT,
    "I always buy more credits before a long weekend",
    "Whenever I top up codex I forget to tell you",
    "I topped up the account this morning",
    "I always have to restart the dev server after a pull, that's just how I work",
])
def test_habit_shapes_are_not_complaints(env, stmt):
    hr, _, _ = env
    assert hr.is_complaint(stmt) is False


@pytest.mark.parametrize('stmt', OCT_MESSAGES + [
    "every time I save the file I get a weird popup",
    "whenever I open the Desk the page flickers",
    "I always get an error when I click Send",
])
def test_complaint_shapes_are_flagged(env, stmt):
    hr, _, _ = env
    assert hr.is_complaint(stmt) is True


def test_terminal_scribe_does_not_mint_a_complaint(env, monkeypatch, tmp_path):
    hr, mem, tmp = env
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp / 'MEMORY.md')
    monkeypatch.setattr(mem, '_scribe_stat', lambda *a, **k: None)
    (tmp / 'MEMORY.md').write_text('# index\n', encoding='utf-8')
    minted = []
    monkeypatch.setattr(mem, 'write_topic_note', lambda *a, **k: minted.append(a) or 'x.md')
    mem._scribe_classify_habits({'id': 'p1'}, _session(), OCT_MESSAGES)
    assert minted == []
    mem._scribe_classify_habits({'id': 'p1'}, _session(), [REAL_HABIT])
    assert len(minted) == 1


def test_exclusion_flag_off_restores_old_behaviour(env, monkeypatch):
    hr, mem, _ = env
    monkeypatch.setitem(mem.state.CONFIG, 'habit_complaint_exclusion_enabled', False)
    assert hr.is_complaint(OCT_MESSAGES[2]) is False


# ── origin / gating ────────────────────────────────────────────────────────

def test_unattended_session_logs_but_would_not_mint(env):
    hr, _, _ = env
    row = hr.observe_user_message('p1', 'sid-1', REAL_HABIT,
                                  session=_session(trigger_type='schedule'))
    assert row['origin'] == 'unattended' and row['would_mint'] is False


def test_agent_caller_message_is_skipped(env):
    hr, _, tmp = env
    assert hr.observe_user_message('p1', 'sid-1', REAL_HABIT, session=_session(),
                                   human=False) is None
    assert _rows(tmp) == []


def test_flag_off_disables_observer(env, monkeypatch):
    hr, mem, tmp = env
    monkeypatch.setitem(mem.state.CONFIG, 'habit_report_enabled', False)
    assert hr.observe_user_message('p1', 'sid-1', REAL_HABIT, session=_session()) is None
    assert _rows(tmp) == []


def test_non_cue_message_logs_nothing(env):
    hr, _, tmp = env
    assert hr.observe_user_message('p1', 'sid-1', 'fix the bug in agent_routes.py',
                                   session=_session()) is None
    assert _rows(tmp) == []


def test_same_message_via_send_and_followup_logs_once(env):
    hr, _, tmp = env
    hr.observe_user_message('p1', 'sid-1', REAL_HABIT, session=_session())
    hr.observe_user_message('p1', 'sid-1', REAL_HABIT, session=_session())
    assert len(_rows(tmp)) == 1


def test_live_session_is_resolved_when_not_passed(env, monkeypatch):
    hr, mem, tmp = env
    monkeypatch.setitem(hr.state.agent_sessions, 'sid-live', _session(sid='sid-live'))
    row = hr.observe_user_message('p1', 'sid-live', REAL_HABIT)
    assert row['origin'] == 'interactive'
    hr.state.agent_sessions.pop('sid-live', None)


def test_observe_never_raises(env, monkeypatch):
    hr, mem, _ = env
    monkeypatch.setattr(hr, '_append_row', lambda *a: 1 / 0)
    assert hr.observe_user_message('p1', 'sid-1', REAL_HABIT, session=_session()) is None
    assert hr.observe_user_message('p1', 'sid-1', None, session=None) is None


# ── T1: reconciler carries trigger_type ────────────────────────────────────

def test_reconciler_session_carries_trigger_type(monkeypatch):
    import server as srv
    seen = []
    row = {'session_id': 's1', 'claude_session_id': 'c1', 'task': 'chat',
           'status': 'completed', 'trigger_type': 'manual', 'ts': '2026-10-15T00:00:00Z'}
    # `first_encounter` is false once any row carries a 'scribed' key.
    log = [{'session_id': 'old', 'scribed': True}, row]
    monkeypatch.setattr(srv, 'load_projects', lambda: [{'id': 'pX', 'project_path': ''}])
    monkeypatch.setattr(srv, '_load_agent_log', lambda pid: log)
    monkeypatch.setattr(srv, '_save_agent_log', lambda pid, l: None)
    monkeypatch.setattr(srv, '_has_running_agent', lambda pid: False)
    monkeypatch.setattr(srv, '_write_session_memory',
                        lambda p, sess, *a, **k: seen.append(sess) or True)
    monkeypatch.setattr(srv, '_log', lambda *a, **k: None)
    srv._reconcile_unscribed_sessions()
    assert len(seen) == 1
    assert seen[0]['trigger_type'] == 'manual'
    from mc import memory as mem
    assert mem._stamp_origin(seen[0]['task'], seen[0]['trigger_type']) == 'interactive'


def test_reconciler_session_without_trigger_type_stays_unattended(monkeypatch):
    import server as srv
    seen = []
    row = {'session_id': 's1', 'claude_session_id': 'c1', 'task': 'chat',
           'status': 'completed', 'ts': '2026-10-15T00:00:00Z'}
    log = [{'session_id': 'old', 'scribed': True}, row]
    monkeypatch.setattr(srv, 'load_projects', lambda: [{'id': 'pX', 'project_path': ''}])
    monkeypatch.setattr(srv, '_load_agent_log', lambda pid: log)
    monkeypatch.setattr(srv, '_save_agent_log', lambda pid, l: None)
    monkeypatch.setattr(srv, '_has_running_agent', lambda pid: False)
    monkeypatch.setattr(srv, '_write_session_memory',
                        lambda p, sess, *a, **k: seen.append(sess) or True)
    monkeypatch.setattr(srv, '_log', lambda *a, **k: None)
    srv._reconcile_unscribed_sessions()
    from mc import memory as mem
    assert mem._stamp_origin(seen[0]['task'], seen[0]['trigger_type']) == 'unattended'


# ── T3: log_lines trim keeps user lines and says so ────────────────────────

def _trim_env(monkeypatch):
    from mc import log_lines_trim as t
    logged = []
    monkeypatch.setattr(t, '_log', lambda msg, *a, **k: logged.append(msg))
    return t, logged


def test_trim_keeps_old_user_lines_in_both_shapes(monkeypatch):
    from mc.core import TimestampedLines
    t, logged = _trim_env(monkeypatch)
    lines = TimestampedLines(['> Ron: I topped up codex', '\n> Ron: whenever I login\n'])
    lines.extend(f'stream {i}' for i in range(2001))
    out = t.trim(lines, 2000, 1500, label='abc')
    assert out[0] == '> Ron: I topped up codex'
    assert out[1] == '\n> Ron: whenever I login\n'
    assert len(out) == 1502
    assert out[-1] == 'stream 2000'
    assert len(out.ts) == len(out)
    assert out.ts[0] == lines.ts[0]


def test_trim_logs_when_it_bites(monkeypatch):
    t, logged = _trim_env(monkeypatch)
    lines = ['> Ron: hi'] + [f's{i}' for i in range(2001)]
    t.trim(lines, 2000, 1500, label='abc')
    assert len(logged) == 1 and 'trimmed 501 of 2002' in logged[0]
    assert '1 older user lines' in logged[0]


def test_trim_under_cap_is_untouched_and_silent(monkeypatch):
    t, logged = _trim_env(monkeypatch)
    lines = [f's{i}' for i in range(2000)]
    assert t.trim(lines, 2000, 1500) is lines
    assert logged == []


def test_old_slice_would_have_lost_the_user_line(monkeypatch):
    """The failure this ticket pins: the previous `[-1500:]` slice."""
    lines = ['> Ron: I topped up codex'] + [f's{i}' for i in range(2001)]
    assert not any(l.startswith('> ') for l in lines[-1500:])


def test_habit_source_statements_sees_a_trimmed_chat(env, monkeypatch):
    """End to end: Ron's line + 2001 stream lines, trimmed, still reaches the
    Scribe's statement reader (Bram's replay left 0 statements)."""
    hr, mem, _ = env
    from mc import log_lines_trim as t
    monkeypatch.setattr(t, '_log', lambda *a, **k: None)
    lines = ['> Ron: ' + REAL_HABIT] + [f's{i}' for i in range(2001)]
    out = t.trim(lines)
    assert mem._habit_source_statements({'log_lines': out}) == [REAL_HABIT]
