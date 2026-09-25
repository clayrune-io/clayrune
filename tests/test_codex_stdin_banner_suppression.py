"""Codex's `Reading prompt from stdin...` banner must never reach the user
(Ron, 2026-09-25).

`codex exec` writes this line to stderr before it reads the prompt off stdin.
Codex is spawned with stderr=subprocess.STDOUT (both the initial dispatch via
`_mode_a_dispatch` and `CodexRuntime.write_followup`'s per-turn respawn), and
the banner has NO trailing newline of its own -- so under Python's
line-buffered `for line in proc.stdout:` iteration it is always merged onto
whatever the process writes next, byte-for-byte, with nothing to mark where
it ends. Two live examples pulled from `data/projects/find_ron_a_job_agent_log.json`
and `mission_control_agent_log.json`:

    Reading prompt from stdin...**Format approved, Ron.**

...a plain-text final answer with the banner glued to the front, and (by the
same glue mechanism, reasoned from codex's own event ordering: thread.started
fires once per thread, so every RESUMED follow-up's first line is something
else, usually turn.started) the banner can just as easily land in front of a
JSON event line instead of plain text.

Fixed at two points, both gated to `runtime.name == 'codex'` so no other
Mode-A vendor's line is touched:

1. `CodexRuntime.parse_event` strips the banner prefix before deciding
   whether the line is JSON -- this is what makes the method correct and
   testable on its own, independent of the reader loop.
2. `_mode_a_reader` (the shared Mode-A stdout reader) also strips it, ahead
   of its own `_is_protocol_json` fallback check -- that check runs against
   the SAME `line` variable `parse_event` was given, so without this a
   banner glued onto a line `parse_event` correctly suppresses (e.g.
   `turn.started`) would still fail `_is_protocol_json` (it no longer starts
   with `{`) and leak the raw banner+JSON line into the chat transcript as
   if it were stray output. `raw_record` (the full-fidelity capture) runs
   BEFORE this strip, so the untouched transport bytes are still preserved
   there.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as agent_runtime_mod  # noqa: E402
from mc.agent_runtime import EventType  # noqa: E402

BANNER = 'Reading prompt from stdin...'


def _parse_raw(raw_line: str):
    return agent_runtime_mod.CodexRuntime().parse_event(raw_line, 'sid')


# ── parse_event: the four required scenarios ─────────────────────────────────

def test_standalone_banner_line_is_suppressed():
    # Nothing after the banner at all -- must not surface as chat text.
    assert _parse_raw(BANNER) is None


def test_banner_glued_to_a_json_event_line_still_parses():
    raw = BANNER + json.dumps({'type': 'thread.started', 'thread_id': 'thr_42'})
    ev = _parse_raw(raw)
    assert ev is not None
    assert ev.type == EventType.INIT
    assert ev.session_id == 'thr_42'
    assert BANNER not in json.dumps(ev.payload)


def test_banner_glued_to_plain_text_final_message():
    # The exact live shape: a non-JSON assistant line with the banner glued
    # to the front, no newline in between.
    raw = BANNER + '**Format approved, Ron.**'
    ev = _parse_raw(raw)
    assert ev is not None
    assert ev.type == EventType.ASSISTANT_TEXT
    assert ev.payload['text'] == '**Format approved, Ron.**'
    assert BANNER not in ev.payload['text']


def test_a_real_error_line_survives_banner_stripping():
    # A genuine stray line that does not carry the banner must pass through
    # completely unchanged -- the strip is a prefix match, not a keyword scan.
    raw = 'Traceback (most recent call last): boom'
    ev = _parse_raw(raw)
    assert ev is not None
    assert ev.type == EventType.ASSISTANT_TEXT
    assert ev.payload['text'] == raw


def test_a_real_structured_error_event_survives_banner_stripping():
    ev = _parse_raw(json.dumps({'type': 'error',
                                'message': 'stream disconnected before completion'}))
    assert ev.type == EventType.ERROR
    assert ev.payload['text'] == 'stream disconnected before completion'


# ── _mode_a_reader: the follow-up-turn gap (banner glued to a SUPPRESSED
#    event, e.g. turn.started on a resumed thread) ───────────────────────────

class _FakeProc:
    """Just enough of subprocess.Popen for `_mode_a_reader` to drain."""

    def __init__(self, lines):
        self.stdout = iter(lines)

    def wait(self):
        return 0


def _run_reader_lines(lines, callbacks=None):
    proc = _FakeProc(lines)
    session_dict = {'log_lines': [], 'proc': proc}
    handle = agent_runtime_mod.SessionHandle(
        mc_session_id=f'test-{uuid.uuid4().hex[:8]}', provider='codex', mode='A',
        project_path='.', project_id='p', session_dict=session_dict,
        meta={'callbacks': callbacks or {}},
    )
    agent_runtime_mod._mode_a_reader(proc, handle, agent_runtime_mod.CodexRuntime())
    return session_dict['log_lines']


def test_banner_glued_to_a_suppressed_event_does_not_leak_into_chat():
    # Without the _mode_a_reader-level strip, this line fails parse_event's
    # JSON check post-hoc via `_is_protocol_json` (banner prefix means it no
    # longer starts with "{") and gets appended verbatim as "stray output".
    line = BANNER + json.dumps({'type': 'turn.started'})
    assert _run_reader_lines([line]) == []


def test_raw_capture_keeps_the_untouched_banner_glued_line():
    captured = []
    line = BANNER + json.dumps({'type': 'turn.started'})
    _run_reader_lines(
        [line], callbacks={'on_raw_record': lambda l, seq, m, s: captured.append(l)})
    assert captured == [line], 'full-fidelity capture must see the real transport bytes'


def test_banner_glued_to_final_text_reaches_log_lines_clean():
    line = BANNER + '**Format approved, Ron.**'
    lines = _run_reader_lines([line])
    assert lines == ['**Format approved, Ron.**']


# ── scope: no other Mode-A vendor's line is touched ──────────────────────────

class _StubGeminiRuntime:
    """Minimal stand-in -- only what `_mode_a_reader` touches."""

    name = 'gemini'

    def parse_event(self, raw_line, mc_session_id=''):
        line = raw_line.rstrip('\n\r')
        if not line:
            return None
        return agent_runtime_mod.AgentEvent(
            type=EventType.ASSISTANT_TEXT, provider='gemini',
            session_id=None, mc_session_id=mc_session_id,
            timestamp='2026-09-25T00:00:00Z', payload={'text': line},
        )


def test_other_vendors_never_get_the_codex_banner_strip():
    proc = _FakeProc([BANNER + 'hello from gemini'])
    session_dict = {'log_lines': [], 'proc': proc}
    handle = agent_runtime_mod.SessionHandle(
        mc_session_id=f'test-{uuid.uuid4().hex[:8]}', provider='gemini', mode='A',
        project_path='.', project_id='p', session_dict=session_dict, meta={},
    )
    agent_runtime_mod._mode_a_reader(proc, handle, _StubGeminiRuntime())
    assert session_dict['log_lines'] == [BANNER + 'hello from gemini'], (
        'the codex-only banner strip must not touch another vendor\'s output')
