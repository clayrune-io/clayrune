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
        {'role': 'stop_hook_redo', 'text': '', 'kind': 'length'},
        {'role': 'assistant', 'text': 'Short final reply.'},
    ])
    lines = ar._transcript_buffer_lines('/p', 'csid', 'Ron')
    # No fake "> Ron: Stop hook feedback..." bubble, and the marker survives
    # so the renderer can collapse the draft that precedes it.
    assert not any('Stop hook feedback' in l for l in lines)
    assert '[stop-hook-redo:length]' in lines
    assert 'A very long draft reply.' in lines
    assert 'Short final reply.' in lines
    marker_idx = lines.index('[stop-hook-redo:length]')
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
    assert '[stop-hook-redo:length]' in lines
    # The synthetic hook turn is never rendered as its own text line.
    assert not any('Stop hook feedback' in ln for ln in lines)
    # Order: draft, marker, final.
    assert lines.index('A very long draft reply.') < lines.index('[stop-hook-redo:length]') < lines.index('Short final reply.')


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
    assert '[stop-hook-redo:length]' in lines
    assert not any('Stop hook feedback' in ln for ln in lines)
    assert lines.index('A very long draft reply.') < lines.index('[stop-hook-redo:length]') < lines.index('Short final reply.')


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
    assert not any(l.startswith('[stop-hook-redo') for l in session['log_lines'])


# ── agent_log summary: must use the FINAL text, never the retracted draft ───

def test_last_reply_text_uses_final_reply_after_stop_hook():
    session = {'log_lines': [
        '> Ron: Summarize the changes.',
        'A very long draft reply.',
        '[stop-hook-redo:length]',
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
                     '[stop-hook-redo:length]', 'FINAL ONE: short resend.',
                     'DRAFT TWO: long reply ending on a permission ask.',
                     '[stop-hook-redo:length]', '[stop-hook-redo:other]',
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
    assert lines[d + 1:f] == ['[stop-hook-redo:length]'], lines


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
    assert not any(l.startswith('[stop-hook-redo') for l in lines)


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
    assert calls == [] and not any(l.startswith('[stop-hook-redo') for l in lines)


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
    # The live fixture's hook reason is a generic test rule, not the
    # reply-length guard's, so the boundary is tagged 'other'.
    assert lines[d + 1:f] == ['[stop-hook-redo:other]'], lines
    assert not any('Stop hook feedback' in l for l in lines)


def test_live_stream_mode_b_collapses_resend(tmp_data_dir, monkeypatch):
    _assert_live_collapsed(_run_live(tmp_data_dir, monkeypatch, '_read_agent_stream_b'))


def test_live_stream_mode_a_collapses_resend(tmp_data_dir, monkeypatch):
    _assert_live_collapsed(_run_live(tmp_data_dir, monkeypatch, '_read_agent_stream'))


# ── Hook KIND on the marker (2026-10-01, backlog 31324d7b) ───────────────────
# A Stop-hook "Show earlier draft" collapse hid Dave's real answer: a length
# hook fired, Dave's follow-up was a one-line meta reply, and the collapse
# swallowed the full answer. The marker now says which hook blocked, so the
# renderer only treats the reply-length guard's block as "the follow-up
# re-sends the draft"; every other hook asks the model to CONTINUE.

from mc.agent_runtime import (  # noqa: E402
    stop_hook_block_kind_before, stop_hook_kind, stop_hook_kind_from_reasons,
    stop_hook_marker,
)

_LENGTH_START = 'BREVITY RULE VIOLATED'
_LENGTH_REASON = _LENGTH_START + ': that reply was 211 prose words against a 160-word hard ceiling.'
_PERMISSION_START = 'You ended your turn ASKING PERMISSION'
_PERMISSION_REASON = _PERMISSION_START + ' to do something reversible.'
_TURN_GUARD_REASON = 'You ended your turn by ANNOUNCING work you have not actually done yet.'


def _feedback(reason, as_blocks=False):
    text = 'Stop hook feedback:\n' + reason
    content = [{'type': 'text', 'text': text}] if as_blocks else text
    return {'type': 'user', 'isSynthetic': True, 'message': {'role': 'user', 'content': content}}


def test_kind_length_only_for_the_reply_length_guard():
    assert stop_hook_kind(_feedback(_LENGTH_REASON)) == 'length'
    assert stop_hook_kind(_feedback(_LENGTH_REASON, as_blocks=True)) == 'length'
    assert stop_hook_kind(_feedback(_PERMISSION_REASON)) == 'other'
    assert stop_hook_kind(_feedback(_TURN_GUARD_REASON)) == 'other'


def test_kind_never_guesses_length():
    assert stop_hook_kind({}) == 'other'
    assert stop_hook_kind({'type': 'user', 'message': {'content': 'hello'}}) == 'other'
    assert stop_hook_kind_from_reasons([]) == 'other'
    assert stop_hook_kind_from_reasons(['']) == 'other'
    # Two hooks blocked the same stop: the follow-up answers both, so only an
    # all-length block is a pure "re-send shorter".
    assert stop_hook_kind_from_reasons([_LENGTH_REASON, _PERMISSION_REASON]) == 'other'
    assert stop_hook_kind_from_reasons([_LENGTH_REASON, _LENGTH_REASON]) == 'length'


def test_marker_text():
    assert stop_hook_marker('length') == '[stop-hook-redo:length]'
    assert stop_hook_marker('other') == '[stop-hook-redo:other]'
    assert stop_hook_marker('') == '[stop-hook-redo:other]'
    assert stop_hook_marker('garbage') == '[stop-hook-redo:other]'


def _revived_records_with_reason(reason):
    return [json.loads(json.dumps(r).replace(_LENGTH_START, reason))
            for r in _revived_records()]


def test_block_kind_before_reads_each_record_shape():
    """The transcript carries the block three ways; each one yields the kind."""
    recs = _revived_records()
    resend = next(r for r in recs if _text_of(r) == 'FINAL: compressed resend.')
    draft = next(r for r in recs if _text_of(r) == 'DRAFT: long reply the brevity guard blocked.')
    full = {r['uuid']: r for r in recs}
    assert stop_hook_block_kind_before(full, resend['uuid']) == 'length'
    assert stop_hook_block_kind_before(full, draft['uuid']) is None

    keep = (resend['uuid'], draft['uuid'])
    shapes = {
        'feedback turn': lambda r: bool(r.get('isMeta')),
        'attachment': lambda r: (r.get('attachment') or {}).get('type') == 'hook_blocking_error',
        'summary': lambda r: r.get('subtype') == 'stop_hook_summary' and bool(r.get('hookErrors')),
    }
    for name, pred in shapes.items():
        hook = [r for r in recs if pred(r)]
        assert len(hook) == 1, name
        subset = {r['uuid']: r for r in recs if r['uuid'] in keep or r.get('type') == 'assistant'}
        subset[hook[0]['uuid']] = hook[0]
        subset[resend['uuid']] = dict(subset[resend['uuid']], parentUuid=hook[0]['uuid'])
        assert stop_hook_block_kind_before(subset, resend['uuid']) == 'length', name
        swapped = {u: json.loads(json.dumps(r).replace(_LENGTH_START, _PERMISSION_START))
                   for u, r in subset.items()}
        assert stop_hook_block_kind_before(swapped, resend['uuid']) == 'other', name


def test_stop_hook_precedes_still_true_for_any_kind():
    recs = {r['uuid']: r for r in _revived_records_with_reason(_PERMISSION_START)}
    resend = next(r for r in recs.values() if _text_of(r) == 'FINAL: compressed resend.')
    assert stop_hook_precedes(recs, resend['uuid']) is True


def _stream_with_reason(reason):
    lines = list(_STOP_HOOK_STREAM_LINES)
    lines[1] = json.dumps({'type': 'user', 'isMeta': True, 'session_id': 's1',
                           'message': {'role': 'user', 'content': 'Stop hook feedback:\n' + reason}})
    return lines


def _run_stream(tmp_data_dir, reader_name, lines):
    server = importlib.import_module("server")
    importlib.reload(server)
    session = _new_session('p-kind-' + reader_name)
    proc = _FakeProc(lines)
    session['proc'] = proc
    getattr(server, reader_name)(proc, session)
    return session['log_lines']


def test_live_readers_tag_the_marker_with_the_hook_kind(tmp_data_dir):
    for reader in ('_read_agent_stream', '_read_agent_stream_b'):
        length = _run_stream(tmp_data_dir, reader, _stream_with_reason(_LENGTH_REASON))
        assert [l for l in length if l.startswith('[stop-hook-redo')] == ['[stop-hook-redo:length]'], reader
        for reason in (_PERMISSION_REASON, _TURN_GUARD_REASON):
            other = _run_stream(tmp_data_dir, reader, _stream_with_reason(reason))
            assert [l for l in other if l.startswith('[stop-hook-redo')] == ['[stop-hook-redo:other]'], reader


def test_transcript_confirmed_marker_carries_the_kind(tmp_data_dir, monkeypatch, tmp_path):
    """The path where the live stream never carries the feedback turn (revived
    chats): the kind comes from the transcript's hook records."""
    for reason, want in ((_LENGTH_START, 'length'), (_PERMISSION_START, 'other')):
        recs = _revived_records_with_reason(reason)
        f = tmp_path / (want + '.jsonl')
        f.write_text('\n'.join(json.dumps(r) for r in recs) + '\n', encoding='utf-8')
        for reader in ('_read_agent_stream', '_read_agent_stream_b'):
            lines, _ = _run_revived(tmp_data_dir, monkeypatch, f, reader, _revived_stream(recs))
            d = lines.index('DRAFT: long reply the brevity guard blocked.')
            fi = lines.index('FINAL: compressed resend.')
            assert lines[d + 1:fi] == ['[stop-hook-redo:%s]' % want], (reader, lines)


def test_history_render_carries_the_hook_kind(tmp_path):
    """parse_transcript_file -> role 'stop_hook_redo' carries `kind` (isMeta path)."""
    for reason, want in ((_LENGTH_REASON, 'length'), (_PERMISSION_REASON, 'other')):
        lines = [ln.replace('BREVITY RULE VIOLATED: too long.', reason) for ln in _STOP_HOOK_TRANSCRIPT]
        msgs = ClaudeRuntime().parse_transcript_file(_write(tmp_path, lines))
        assert [m.get('kind') for m in msgs if m['role'] == 'stop_hook_redo'] == [want]


def test_history_render_without_is_meta_still_gets_the_kind(tmp_path):
    """A CLI version with no isMeta: the attachment/summary records convert the
    fake user turn, and they carry the reason too."""
    stripped = []
    for line in _REAL_FIXTURE.read_text(encoding='utf-8').splitlines():
        rec = json.loads(line)
        rec.pop('isMeta', None)
        stripped.append(json.dumps(rec))
    msgs = ClaudeRuntime().parse_transcript_file(_write(tmp_path, stripped))
    kinds = [m.get('kind') for m in msgs if m['role'] == 'stop_hook_redo']
    assert kinds == ['length', 'length', 'other'], kinds
