"""Regression: a blocked Stop hook's re-send rendered as a SECOND reply.

reply-length-guard.py / permission-ask-guard.py / turn-guard.py are global
Stop hooks. When one blocks a turn, Claude Code CLI feeds the hook's `reason`
back as a synthetic next turn (`type:"user"`, `isMeta:true`, `message.content`
a plain string starting with the CLI's own fixed "Stop hook feedback:" prefix
-- verified against a live transcript, never the guard's own wording) so the
model re-sends a compressed version in the SAME turn. Nothing treated that
boundary specially, so both the retracted draft and the resend rendered as
ordinary assistant text -- every blocked reply showed twice.

The fix keys off the STRUCTURE (isMeta + the CLI's fixed prefix), not just the
English string, and touches three places: parse_event's raw envelope (used by
both the live stream readers and parse_transcript_file), the live readers
(mc/blueprints/agent_routes.py `_read_agent_stream[_b]`), and history reload
(`_transcript_buffer_lines`). All three now emit or forward a '[stop-hook-redo]'
boundary marker instead of showing a fake user bubble; the frontend (rich-text.js
/ conversation.js) collapses the preceding draft into a closed <details> instead
of rendering it as a second answer.
"""
from __future__ import annotations

import importlib
import io
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.agent_runtime import ClaudeRuntime, is_stop_hook_feedback  # noqa: E402
from mc.blueprints import agent_routes as ar  # noqa: E402


# ── is_stop_hook_feedback: the structural detector ───────────────────────────

def test_true_for_is_meta_plus_cli_prefix():
    msg = {
        'type': 'user', 'isMeta': True,
        'message': {'role': 'user',
                    'content': 'Stop hook feedback:\nBREVITY RULE VIOLATED: too long.'},
    }
    assert is_stop_hook_feedback(msg) is True


def test_false_without_is_meta():
    """The English prefix alone (e.g. a real user pasting it) is not enough --
    isMeta is the structural half of the signal."""
    msg = {
        'type': 'user',
        'message': {'role': 'user',
                    'content': 'Stop hook feedback:\nBREVITY RULE VIOLATED: too long.'},
    }
    assert is_stop_hook_feedback(msg) is False


def test_false_for_other_meta_turns():
    """isMeta is also set on unrelated synthetic turns (a compaction 'Continue'
    nudge) -- those are ordinary continuations, not a retracted draft."""
    msg = {
        'type': 'user', 'isMeta': True,
        'message': {'role': 'user', 'content': 'Continue from where you left off.'},
    }
    assert is_stop_hook_feedback(msg) is False


def test_false_for_non_dict_or_missing_message():
    assert is_stop_hook_feedback({}) is False
    assert is_stop_hook_feedback({'type': 'user', 'isMeta': True}) is False


def test_true_with_list_content_blocks():
    """Some CLI paths carry content as a block list rather than a bare string."""
    msg = {
        'type': 'user', 'isMeta': True,
        'message': {'role': 'user', 'content': [
            {'type': 'text', 'text': 'Stop hook feedback:\nreason text'}]},
    }
    assert is_stop_hook_feedback(msg) is True


# ── parse_transcript_file: history reload / resume rendering ────────────────

def _write(tmp_path, lines):
    f = tmp_path / 'fixture.jsonl'
    f.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return f


_STOP_HOOK_TRANSCRIPT = [
    json.dumps({
        'type': 'user', 'session_id': 's1', 'timestamp': '2026-09-15T10:00:00Z',
        'message': {'role': 'user', 'content': 'Summarize the changes.'},
    }),
    json.dumps({
        'type': 'assistant', 'session_id': 's1', 'timestamp': '2026-09-15T10:00:01Z',
        'message': {'content': [{'type': 'text', 'text': 'A very long draft reply.'}]},
    }),
    json.dumps({
        'type': 'user', 'isMeta': True, 'session_id': 's1',
        'timestamp': '2026-09-15T10:00:02Z',
        'message': {'role': 'user',
                    'content': 'Stop hook feedback:\nBREVITY RULE VIOLATED: too long.'},
    }),
    json.dumps({
        'type': 'assistant', 'session_id': 's1', 'timestamp': '2026-09-15T10:00:03Z',
        'message': {'content': [{'type': 'text', 'text': 'Short final reply.'}]},
    }),
    json.dumps({'type': 'result', 'session_id': 's1', 'usage': {'input_tokens': 1}}),
]


def test_parse_transcript_file_marks_boundary_not_a_user_turn(tmp_path):
    rt = ClaudeRuntime()
    f = _write(tmp_path, _STOP_HOOK_TRANSCRIPT)
    msgs = rt.parse_transcript_file(f)
    roles = [m['role'] for m in msgs]
    assert roles == ['user', 'assistant', 'stop_hook_redo', 'assistant']
    assert not any('Stop hook feedback' in (m.get('text') or '') for m in msgs)
    assert msgs[1]['text'] == 'A very long draft reply.'
    assert msgs[3]['text'] == 'Short final reply.'


def test_parse_transcript_file_turn_without_hook_is_unaffected(tmp_path):
    """A normal turn (no isMeta anywhere) must render exactly as before."""
    rt = ClaudeRuntime()
    lines = [
        json.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'Hi'}}),
        json.dumps({'type': 'assistant',
                    'message': {'content': [{'type': 'text', 'text': 'Hello!'}]}}),
    ]
    f = _write(tmp_path, lines)
    msgs = rt.parse_transcript_file(f)
    assert [m['role'] for m in msgs] == ['user', 'assistant']


# ── _transcript_buffer_lines: what actually reaches the chat buffer ─────────

def test_transcript_buffer_lines_collapses_stop_hook_draft(monkeypatch):
    monkeypatch.setattr(ar, '_find_transcript_file', lambda pp, cs: Path('x.jsonl'))
    monkeypatch.setattr(ar, '_parse_transcript_messages', lambda f, max_messages=0: [
        {'role': 'user', 'text': 'Summarize the changes.'},
        {'role': 'assistant', 'text': 'A very long draft reply.'},
        {'role': 'stop_hook_redo', 'text': ''},
        {'role': 'assistant', 'text': 'Short final reply.'},
    ])
    lines = ar._transcript_buffer_lines('/p', 'csid', 'Ron')
    # No fake "> Ron: Stop hook feedback..." bubble, and the marker survives
    # so the renderer can collapse the draft that precedes it.
    assert not any('Stop hook feedback' in l for l in lines)
    assert '[stop-hook-redo]' in lines
    assert 'A very long draft reply.' in lines
    assert 'Short final reply.' in lines
    marker_idx = lines.index('[stop-hook-redo]')
    assert 'A very long draft reply.' in lines[marker_idx - 1]
    assert lines[-1] == 'Short final reply.'


# ── Live stream readers: Mode A and Mode B ───────────────────────────────────

class _FakeProc:
    """Minimum surface the readers touch: stdout iter, pid, wait()."""

    def __init__(self, lines):
        self.stdout = io.StringIO('\n'.join(lines) + '\n')
        self.pid = -1
        self._rc = 0

    def wait(self):
        return self._rc

    def kill(self):
        pass


def _new_session(project_id: str) -> dict:
    return {
        'project_id': project_id,
        'status': 'running',
        'log_lines': [],
        'last_output_time': 0.0,
        'last_status_change_time': 0.0,
        'provider': 'claude',
    }


_STOP_HOOK_STREAM_LINES = [
    json.dumps({'type': 'assistant', 'session_id': 's1',
                'message': {'content': [{'type': 'text', 'text': 'A very long draft reply.'}]}}),
    json.dumps({'type': 'user', 'isMeta': True, 'session_id': 's1',
                'message': {'role': 'user',
                            'content': 'Stop hook feedback:\nBREVITY RULE VIOLATED: too long.'}}),
    json.dumps({'type': 'assistant', 'session_id': 's1',
                'message': {'content': [{'type': 'text', 'text': 'Short final reply.'}]}}),
    json.dumps({'type': 'result', 'session_id': 's1', 'num_turns': 1}),
]


def test_mode_a_reader_emits_boundary_marker(tmp_data_dir):
    server = importlib.import_module("server")
    importlib.reload(server)

    session = _new_session('p-mode-a-hook')
    proc = _FakeProc(_STOP_HOOK_STREAM_LINES)
    session['proc'] = proc

    server._read_agent_stream(proc, session)

    lines = session['log_lines']
    assert 'A very long draft reply.' in lines
    assert 'Short final reply.' in lines
    assert '[stop-hook-redo]' in lines
    # The synthetic hook turn is never rendered as its own text line.
    assert not any('Stop hook feedback' in ln for ln in lines)
    # Order: draft, marker, final.
    assert lines.index('A very long draft reply.') < lines.index('[stop-hook-redo]') < lines.index('Short final reply.')


def test_mode_b_reader_emits_boundary_marker(tmp_data_dir):
    server = importlib.import_module("server")
    importlib.reload(server)

    session = _new_session('p-mode-b-hook')
    proc = _FakeProc(_STOP_HOOK_STREAM_LINES)
    session['proc'] = proc

    server._read_agent_stream_b(proc, session)

    lines = session['log_lines']
    assert 'A very long draft reply.' in lines
    assert 'Short final reply.' in lines
    assert '[stop-hook-redo]' in lines
    assert not any('Stop hook feedback' in ln for ln in lines)
    assert lines.index('A very long draft reply.') < lines.index('[stop-hook-redo]') < lines.index('Short final reply.')


def test_reader_turn_without_hook_is_unaffected(tmp_data_dir):
    """No isMeta turn anywhere -> no marker, ordinary single reply."""
    server = importlib.import_module("server")
    importlib.reload(server)

    session = _new_session('p-mode-a-plain')
    lines = [
        json.dumps({'type': 'assistant',
                    'message': {'content': [{'type': 'text', 'text': 'hello world'}]}}),
        json.dumps({'type': 'result', 'session_id': 's1', 'num_turns': 1}),
    ]
    proc = _FakeProc(lines)
    session['proc'] = proc
    server._read_agent_stream(proc, session)
    assert 'hello world' in session['log_lines']
    assert '[stop-hook-redo]' not in session['log_lines']


# ── agent_log summary: must use the FINAL text, never the retracted draft ───

def test_last_reply_text_uses_final_reply_after_stop_hook():
    session = {'log_lines': [
        '> Ron: Summarize the changes.',
        'A very long draft reply.',
        '[stop-hook-redo]',
        'Short final reply.',
    ]}
    assert ar._last_reply_text(session) == 'Short final reply.'
