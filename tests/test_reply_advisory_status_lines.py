"""MC-1179 A1/R34 follow-up: Clayrune's own `[label] payload` advisory lines
are status lines, so they never become (or replace) the reply a spawner gets.

Live 2026-11-18/19: `_LABELLED_RE` had no `coverage` label, so the
`[coverage] ...` advisory was read as reply text. Both backward scans collected
it, met the `[denied: Bash] ...` line above it and stopped: 7 spawner callbacks
carried the advisory and nothing else, 5 more had it glued to the end of the
reply. The `[context trim] ...` notices had the same gap.

Every line here is BUILT BY ITS REAL WRITER, never copied as a string, so a
wording or label change in the writer cannot silently reopen the gap.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mc.state as state  # noqa: E402

state.CONFIG.setdefault('port', 5199)

from mc import agent_runtime as art  # noqa: E402
from mc import artifact_coverage as ac  # noqa: E402
from mc import reply_line_kind as rlk  # noqa: E402
from mc.blueprints import agent_routes as ar  # noqa: E402
from mc.core import TimestampedLines  # noqa: E402

REPLY = '**Done.** Eight commits, not pushed.\n\n- one\n- two\n'
TASK = 'check `mc/distiller.py` and the state/IO/network paths'
BLOBS = [ac.flatten_tool_input('Bash', {'command': 'python -m pytest tests/x.py'})]
DENIALS = {'permission_denials': [
    {'tool_name': 'Bash', 'tool_input': {'command': 'git checkout -- worker.js'}},
    {'tool_name': 'Edit', 'tool_input': {'file_path': 'C:/a/[x].py'}},
]}


def _coverage_line():
    line = ac.advisory_line(TASK, BLOBS)
    assert line.startswith('['), line           # the writer still emits one
    return line


def _context_trim_lines(monkeypatch):
    """Both notices `_check_context_budget` can return, from the real function."""
    project = {'id': 'advisory-test-project', 'project_path': ''}
    big_prompt = 'x' * (41 * 1024)
    monkeypatch.setattr(ar, '_get_memory_path', lambda p: None)
    monkeypatch.setattr(ar, '_dispatch_condense', lambda p: None)
    monkeypatch.setattr(ar, '_should_condense', lambda p, **k: True)
    dispatched = ar._check_context_budget(project, big_prompt)
    monkeypatch.setattr(ar, '_should_condense', lambda p, **k: False)
    with ar._condense_lock:
        ar._condensing_projects.add(project['id'])
    try:
        in_progress = ar._check_context_budget(project, big_prompt)
    finally:
        with ar._condense_lock:
            ar._condensing_projects.discard(project['id'])
    assert dispatched and in_progress and dispatched != in_progress
    return [dispatched, in_progress]


def _session(*lines):
    label = state.CONFIG.get('user_name') or 'User'
    return {'log_lines': TimestampedLines(
        [f'\n> {label}: {TASK}\n', '[tool: Bash] python -m pytest tests/x.py', *lines])}


def _end_turn_with_advisory(session, *, denied):
    """Append what `_read_agent_stream_b.result()` appends at turn end, through
    the real writers and in the real order: denials, then the advisory."""
    if denied:
        ar._record_permission_denials(session, DENIALS)
    session['_coverage_blobs'] = list(BLOBS)
    before = len(session['log_lines'])
    ar._emit_coverage_advisory(session)
    assert len(session['log_lines']) == before + 1, 'no advisory was appended'
    return session


# ── the two advisory lines are status lines ─────────────────────────────────

def test_the_real_coverage_advisory_is_a_status_line():
    assert rlk.is_status_line(_coverage_line().strip())


def test_the_real_context_trim_notices_are_status_lines(monkeypatch):
    for line in _context_trim_lines(monkeypatch):
        assert rlk.is_status_line(line.strip()), line


# ── through the real scanners ───────────────────────────────────────────────

def test_callback_is_the_reply_when_a_denial_sits_under_the_advisory():
    """The lost-reply shape: [reply, '[denied: Bash] x', '[coverage] ...']."""
    session = _end_turn_with_advisory(_session(REPLY), denied=True)
    assert ar._last_reply_text(session) == REPLY
    assert art._collect_trailing_reply_text(list(session['log_lines'])) == REPLY


def test_callback_is_the_reply_alone_when_the_advisory_follows_it():
    """The glued-on shape: [reply, '[coverage] ...']."""
    session = _end_turn_with_advisory(_session(REPLY), denied=False)
    assert ar._last_reply_text(session) == REPLY
    assert art._collect_trailing_reply_text(list(session['log_lines'])) == REPLY


def test_a_turn_with_only_a_context_trim_notice_has_no_reply(monkeypatch):
    for notice in _context_trim_lines(monkeypatch):
        session = _session(notice)
        assert ar._last_reply_text(session) == ''
        assert art._collect_trailing_reply_text(list(session['log_lines'])) == ''
        session = _session(REPLY, notice)
        assert ar._last_reply_text(session) == REPLY
        assert art._collect_trailing_reply_text(list(session['log_lines'])) == REPLY


# ── A1 still holds: a reply line that begins with '[' stays the reply ───────

A1_REPLY_LINES = [
    '[1] first point',
    '[file:C:/a/b.md|Full report] is attached',
    '[coverage report](http://x) is here',
    '[coverage: 91%] of lines are hit',
    '[context] matters here',
    '[context trimming] was discussed',
]


@pytest.mark.parametrize('line', A1_REPLY_LINES)
def test_reply_lines_that_begin_with_a_bracket_survive_the_advisory(line):
    assert not rlk.is_status_line(line), line
    for denied in (True, False):
        session = _end_turn_with_advisory(_session('Intro.\n', line), denied=denied)
        assert ar._last_reply_text(session) == 'Intro.\n' + line
        assert art._collect_trailing_reply_text(
            list(session['log_lines'])) == 'Intro.\n' + line


def test_a_trailing_file_marker_stays_in_the_callback():
    marker = '[file:C:/a/b.md|Full report]'
    session = _end_turn_with_advisory(_session(REPLY, marker), denied=True)
    assert ar._last_reply_text(session) == REPLY + marker


# ── the class: every `[label] payload` line a helper builds ─────────────────

def _helper_built_lines(monkeypatch):
    """One real line per helper that builds a `[label] payload` status line.

    Lines appended as inline f-strings inside the stream readers (`[hint] ...`,
    `[<engine> error] ...`) have no helper to call; their literal forms are
    pinned in tests/test_reply_status_line_inventory.py."""
    lines = [_coverage_line()]
    lines += _context_trim_lines(monkeypatch)
    denied = {'log_lines': TimestampedLines()}
    ar._record_permission_denials(denied, DENIALS)
    lines += list(denied['log_lines'])
    lines += [
        art._format_tool_activity('Read', {'file_path': 'C:/a/[x].py'}),
        art._format_tool_activity('Bash', {'command': 'echo "[Errno 22]"'}),
        art._format_tool_activity('Grep', {'pattern': '[a-z]+'}),
        art._format_tool_activity('Task', {'description': 'review [the] diff'}),
        art._format_tool_activity('WebSearch', {'query': 'clayrune [docs]'}),
    ]
    mcp_sync = []
    art._log_mcp_sync_result(mcp_sync, {'error': '[Errno 2] missing'})
    lines += mcp_sync
    return lines


def test_every_helper_built_labelled_line_is_a_status_line(monkeypatch):
    lines = _helper_built_lines(monkeypatch)
    labels = set()
    for line in lines:
        stripped = line.strip()
        assert stripped.startswith('[') and '] ' in stripped, line
        labels.add(stripped[1:stripped.index(']')].split(':')[0])
        assert rlk.is_status_line(stripped), line
        assert art._collect_trailing_reply_text([REPLY, line]) == REPLY, line
        assert ar._last_reply_text(_session(REPLY, line)) == REPLY, line
    assert labels == {'coverage', 'context trim', 'denied', 'tool', 'mcp-sync'}
