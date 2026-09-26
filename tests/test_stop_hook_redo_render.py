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
    monkeypatch.setattr(ar, '_parse_transcript_messages', lambda f, max_messages=0, **kw: [
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


# ── REAL records (anonymized) from a live Mode B Clayrune session ────────────
# tests/fixtures/stop_hook_real_transcript.jsonl is copied from Claude Code
# 2.1.270's own transcript of a Clayrune Mode B chat: one single block
# (brevity) and one double block (brevity then permission-ask) on the SAME
# draft. Each block is: assistant draft -> isMeta user "Stop hook feedback:"
# -> attachment hook_blocking_error (hookEvent Stop) -> system
# stop_hook_summary (hookErrors) -> assistant resend. Only assistant prose,
# ids and machine paths were replaced.

_REAL_FIXTURE = Path(__file__).parent / 'fixtures' / 'stop_hook_real_transcript.jsonl'
_REAL_ROLES = ['user', 'assistant', 'stop_hook_redo', 'assistant',
               'assistant', 'stop_hook_redo', 'stop_hook_redo', 'assistant']


def test_real_transcript_marks_every_hook_turn():
    msgs = ClaudeRuntime().parse_transcript_file(_REAL_FIXTURE)
    assert [m['role'] for m in msgs if m['role'] != 'tool_call'] == _REAL_ROLES
    assert not any('Stop hook feedback' in (m.get('text') or '') for m in msgs)


def test_real_transcript_without_is_meta_uses_hook_records(tmp_path):
    """If a CLI version drops isMeta, the hook_blocking_error attachment and
    stop_hook_summary that follow the feedback turn still identify it."""
    stripped = []
    for line in _REAL_FIXTURE.read_text(encoding='utf-8').splitlines():
        rec = json.loads(line)
        rec.pop('isMeta', None)
        stripped.append(json.dumps(rec))
    msgs = ClaudeRuntime().parse_transcript_file(_write(tmp_path, stripped))
    assert [m['role'] for m in msgs if m['role'] != 'tool_call'] == _REAL_ROLES


def test_real_transcript_history_reload_never_shows_hook_as_user(monkeypatch):
    """The actual bug: a revived Mode B chat rendered '> Ron: Stop hook
    feedback: ...' above the restore marker. Real parser, real records."""
    monkeypatch.setattr(ar, '_find_transcript_file', lambda pp, cs: _REAL_FIXTURE)
    lines = ar._transcript_buffer_lines('/p', 'csid', 'Ron')
    assert not any('Stop hook feedback' in l for l in lines)
    assert [l for l in lines if l.lstrip().startswith('> ')] == ['\n> Ron: What is the status?\n']
    assert lines == ['\n> Ron: What is the status?\n',
                     'DRAFT ONE: long first reply that tripped the brevity guard.',
                     '[stop-hook-redo]', 'FINAL ONE: short resend.',
                     'DRAFT TWO: long reply ending on a permission ask.',
                     '[stop-hook-redo]', '[stop-hook-redo]',
                     'FINAL TWO: short resend after two blocks.']


def test_real_reply_text_after_double_block_is_the_resend(monkeypatch):
    monkeypatch.setattr(ar, '_find_transcript_file', lambda pp, cs: _REAL_FIXTURE)
    lines = ar._transcript_buffer_lines('/p', 'csid', 'Ron')
    assert ar._last_reply_text({'log_lines': lines}) == 'FINAL TWO: short resend after two blocks.'


# ── REVIVED session, live turn (find_ron_a_job 5edd10858aec, 2026-09-15) ────
# The live stream-json never carries the isMeta feedback turn: a revived Mode B
# chat showed draft and resend back to back with no marker, while its
# transcript held draft -> isMeta feedback -> hook_blocking_error ->
# stop_hook_summary(hookErrors=1) -> resend. tests/fixtures/
# stop_hook_revived_transcript.jsonl is those 10 real records, anonymized.
# The stream replay below is exactly what the reader receives: the assistant
# records plus the tool_result echo (same uuids), never the hook records.

from mc.agent_runtime import stop_hook_precedes  # noqa: E402

_REVIVED = Path(__file__).parent / 'fixtures' / 'stop_hook_revived_transcript.jsonl'


def _revived_records():
    return [json.loads(l) for l in _REVIVED.read_text(encoding='utf-8').splitlines() if l.strip()]


def _text_of(rec):
    return ' '.join(b.get('text', '') for b in (rec.get('message') or {}).get('content') or []
                    if isinstance(b, dict) and b.get('type') == 'text')


def _revived_stream(records):
    out = []
    for r in records:
        if r.get('type') == 'assistant' or (
                r.get('type') == 'user' and not r.get('isMeta')):
            out.append(json.dumps({'type': r['type'], 'message': r['message'],
                                   'uuid': r['uuid'], 'session_id': 'sess-revived-fixture'}))
    out.append(json.dumps({'type': 'result', 'session_id': 'sess-revived-fixture', 'num_turns': 1}))
    return out


def test_stop_hook_precedes_on_real_records():
    recs = _revived_records()
    by = {r['uuid']: r for r in recs}
    resend = next(r for r in recs if _text_of(r) == 'FINAL: compressed resend.')
    draft = next(r for r in recs if _text_of(r) == 'DRAFT: long reply the brevity guard blocked.')
    assert stop_hook_precedes(by, resend['uuid']) is True
    assert stop_hook_precedes(by, draft['uuid']) is False


def _run_revived(tmp_data_dir, monkeypatch, transcript_path, reader_name, stream=None):
    server = importlib.import_module("server")
    importlib.reload(server)
    routes = importlib.import_module('mc.blueprints.agent_routes')
    calls = []
    monkeypatch.setattr(routes, '_find_transcript_file', lambda pp, cs: transcript_path)
    real_tail = routes._transcript_tail_records
    monkeypatch.setattr(routes, '_transcript_tail_records',
                        lambda f: calls.append(str(f)) or real_tail(f))
    monkeypatch.setattr(routes, 'load_project', lambda pid: {'project_path': '/p'})
    # Reader teardown runs the Scribe, which shells out to a real `claude -p`
    # (haiku) unless stubbed; caught by the real-CLI guard in tests/conftest.py.
    monkeypatch.setattr(routes, '_write_session_memory', lambda *a, **k: True)
    session = _new_session('p-revived')
    session['claude_session_id'] = 'sess-revived-fixture'
    proc = _FakeProc(stream if stream is not None else _revived_stream(_revived_records()))
    session['proc'] = proc
    getattr(routes, reader_name)(proc, session)
    return session['log_lines'], calls


def _assert_collapsed(lines):
    d = lines.index('DRAFT: long reply the brevity guard blocked.')
    f = lines.index('FINAL: compressed resend.')
    assert lines[d + 1:f] == ['[stop-hook-redo]'], lines


def test_revived_mode_b_live_turn_emits_marker(tmp_data_dir, monkeypatch):
    lines, calls = _run_revived(tmp_data_dir, monkeypatch, _REVIVED, '_read_agent_stream_b')
    _assert_collapsed(lines)
    assert calls == [str(_REVIVED)]  # one transcript read, for the resend only


def test_revived_mode_a_live_turn_emits_marker(tmp_data_dir, monkeypatch):
    lines, _ = _run_revived(tmp_data_dir, monkeypatch, _REVIVED, '_read_agent_stream')
    _assert_collapsed(lines)


def test_no_marker_when_transcript_shows_no_hook(tmp_data_dir, monkeypatch, tmp_path):
    """Same stream, but the transcript links the second message straight to the
    first (no hook records): never collapse on the stream shape alone."""
    recs = _revived_records()
    draft = next(r for r in recs if _text_of(r) == 'DRAFT: long reply the brevity guard blocked.')
    kept = []
    for r in recs:
        if r.get('isMeta') or r.get('type') in ('attachment', 'system'):
            continue
        if _text_of(r) == 'FINAL: compressed resend.':
            r = dict(r, parentUuid=draft['uuid'])
        kept.append(json.dumps(r))
    f = tmp_path / 'nohook.jsonl'
    f.write_text('\n'.join(kept) + '\n', encoding='utf-8')
    lines, _ = _run_revived(tmp_data_dir, monkeypatch, f, '_read_agent_stream_b')
    assert '[stop-hook-redo]' not in lines


def test_ordinary_tool_turn_never_reads_the_transcript(tmp_data_dir, monkeypatch):
    """text + tool_use, tool_result, text, result: the common shape must not pay
    for a transcript read."""
    stream = [
        json.dumps({'type': 'assistant', 'uuid': 'a1', 'message': {'id': 'm1', 'content': [
            {'type': 'text', 'text': 'Let me check.'},
            {'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': {'command': 'ls'}}]}}),
        json.dumps({'type': 'user', 'uuid': 'u1', 'message': {'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': 't1', 'content': 'ok'}]}}),
        json.dumps({'type': 'assistant', 'uuid': 'a2', 'message': {'id': 'm2', 'content': [
            {'type': 'text', 'text': 'Done.'}]}}),
        json.dumps({'type': 'result', 'num_turns': 1}),
    ]
    lines, calls = _run_revived(tmp_data_dir, monkeypatch, _REVIVED, '_read_agent_stream_b', stream)
    assert calls == [] and '[stop-hook-redo]' not in lines


# ── Live stream shape of Claude Code 2.1.274 (2026-09-18 regression) ─────────
#
# Captured from a real `claude -p --input-format stream-json --output-format
# stream-json --include-partial-messages` run with a Stop hook that blocks once
# (tests/fixtures/stop_hook_live_stream.jsonl, thinking signatures redacted).
# The stream DOES carry the feedback turn, but NOT in the transcript's shape:
# `isSynthetic: true`, no `isMeta`, and `content` a list of text blocks. So
# is_stop_hook_feedback() missed it, and the transcript fallback missed it too
# because the resend's first streamed message (a thinking block) is not on disk
# until seconds later (measured >=5s), past _hook_blocked_before's 1s retry.
# Dave's chat fe34d9f18c53 showed both drafts of two blocked replies that way.

_LIVE_STREAM = Path(__file__).parent / 'fixtures' / 'stop_hook_live_stream.jsonl'


def _live_stream_lines():
    return [l for l in _LIVE_STREAM.read_text(encoding='utf-8').splitlines() if l.strip()]


def test_is_stop_hook_feedback_on_real_streamed_turn():
    user = next(json.loads(l) for l in _live_stream_lines() if json.loads(l)['type'] == 'user')
    assert user.get('isSynthetic') is True and 'isMeta' not in user
    assert is_stop_hook_feedback(user) is True


def test_synthetic_turn_without_hook_prefix_is_not_feedback():
    msg = {'type': 'user', 'isSynthetic': True,
           'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'Continue.'}]}}
    assert is_stop_hook_feedback(msg) is False


def _run_live(tmp_data_dir, monkeypatch, reader_name):
    server = importlib.import_module("server")
    importlib.reload(server)
    routes = importlib.import_module('mc.blueprints.agent_routes')
    # Live condition: the resend's record is not in the transcript yet.
    monkeypatch.setattr(routes, '_hook_blocked_before', lambda session, uuid: False)
    session = _new_session('p-live-stream')
    session['claude_session_id'] = 'sess-live-stream-fixture'
    proc = _FakeProc(_live_stream_lines())
    session['proc'] = proc
    getattr(routes, reader_name)(proc, session)
    return session['log_lines']


def _assert_live_collapsed(lines):
    d = next(i for i, l in enumerate(lines) if l.startswith('The sea has captivated'))
    f = next(i for i, l in enumerate(lines) if l.startswith('The sea is a vast'))
    assert lines[d + 1:f] == ['[stop-hook-redo]'], lines
    assert not any('Stop hook feedback' in l for l in lines)


def test_live_stream_mode_b_collapses_resend(tmp_data_dir, monkeypatch):
    _assert_live_collapsed(_run_live(tmp_data_dir, monkeypatch, '_read_agent_stream_b'))


def test_live_stream_mode_a_collapses_resend(tmp_data_dir, monkeypatch):
    _assert_live_collapsed(_run_live(tmp_data_dir, monkeypatch, '_read_agent_stream'))
