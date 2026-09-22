"""Codex effort is honoured, not just "preserved" (2026-09-19).

Every non-claude dispatch logged, verbatim:

    [Requested effort 'high' is preserved, but effort control is not
     supported by this codex dispatch path.]

Nothing consumed the value, so "preserved" described a knob that did not
exist. `docs/research/CODEX_PARITY_AUDIT.md` (item 12) had already established
that `codex` accepts `-c model_reasoning_effort=<level>` as a documented
config.toml field — the flag simply was never wired. These tests pin the wiring
and the one thing that could make it WORSE than doing nothing: an unrecognized
level is a config-parse death at launch, so the two Clayrune levels above
`high` clamp instead of being passed through.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as agent_runtime_mod  # noqa: E402
from mc.characters import VALID_EFFORT  # noqa: E402


def _cmd(**kw):
    return agent_runtime_mod.CodexRuntime().build_command(**kw)


def _effort_values(cmd):
    return [cmd[i + 1] for i, a in enumerate(cmd[:-1])
            if a == '-c' and cmd[i + 1].startswith('model_reasoning_effort=')]


# ── the mapping ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize('level', ['low', 'medium', 'high'])
def test_codex_levels_pass_through_unchanged(level):
    assert agent_runtime_mod.codex_reasoning_effort(level) == level


@pytest.mark.parametrize('level', ['xhigh', 'max'])
def test_levels_above_high_clamp_rather_than_kill_the_launch(level):
    # codex's model_reasoning_effort set is minimal/low/medium/high; an
    # unrecognized value dies at config parse before reaching the API.
    assert agent_runtime_mod.codex_reasoning_effort(level) == 'high'


@pytest.mark.parametrize('level', VALID_EFFORT)
def test_every_clayrune_effort_level_maps_to_something_codex_accepts(level):
    assert agent_runtime_mod.codex_reasoning_effort(level) in (
        'minimal', 'low', 'medium', 'high')


@pytest.mark.parametrize('junk', ['', None, 'turbo', '  '])
def test_unknown_or_absent_effort_adds_no_flag(junk):
    assert agent_runtime_mod.codex_reasoning_effort(junk) == ''
    assert _effort_values(_cmd(effort=junk or '')) == []


# ── build_command ────────────────────────────────────────────────────────────

def test_requested_effort_reaches_the_command_line():
    assert _effort_values(_cmd(effort='high')) == ['model_reasoning_effort="high"']


def test_effort_is_applied_on_the_resume_branch_too():
    # A respawned turn must carry the same posture as the first one; codex
    # flags do not survive a resume, they are restated per launch.
    assert _effort_values(_cmd(effort='medium', resume_id='thr_1')) == [
        'model_reasoning_effort="medium"']


def test_effort_does_not_disturb_the_guardrail_hook_args():
    cmd = _cmd(effort='high')
    assert '--dangerously-bypass-hook-trust' in cmd
    assert any(a.startswith('hooks.PreToolUse=') for a in cmd)


# ── the log line ─────────────────────────────────────────────────────────────

def test_dispatch_no_longer_claims_an_unwired_knob_for_codex():
    import mc.blueprints.agent_routes as ar
    src = Path(ar.__file__).read_text(encoding='utf-8')
    assert 'effort control is not supported by this' not in src, (
        'the "preserved, but not supported" line is now false for codex')


# ── the hook-trust NOTICE is not an error (2026-09-19) ───────────────────────
#
# Measured live, docs/_journal/provider-live/claude-codex-handoff-verbatim/:
#
#   [codex error] `--dangerously-bypass-hook-trust` is enabled. Enabled hooks
#   may run without review for this invocation.
#
# ...twice, in a turn that then answered correctly (PINEAPPLE / CAD850). Codex
# puts this advisory on the same `{"type":"error"}` envelope it uses for real
# failures, so MC's ERROR branch labelled a healthy run as failed.

import json  # noqa: E402

from mc.agent_runtime import EventType  # noqa: E402

_NOTICE = ('`--dangerously-bypass-hook-trust` is enabled. Enabled hooks may '
           'run without review for this invocation.')


def _parse(msg):
    return agent_runtime_mod.CodexRuntime().parse_event(json.dumps(msg), 'sid')


def test_hook_trust_notice_is_not_classified_as_an_error():
    ev = _parse({'type': 'error', 'message': _NOTICE})
    assert ev.type == EventType.WARN
    assert ev.payload['severity'] == 'notice'


def test_a_real_codex_error_is_still_an_error():
    ev = _parse({'type': 'error', 'message': 'stream disconnected before completion'})
    assert ev.type == EventType.ERROR


def test_turn_failed_is_never_downgraded_even_carrying_notice_text():
    # turn.failed means the turn did NOT complete; the phrase must not rescue it.
    ev = _parse({'type': 'turn.failed', 'message': _NOTICE})
    assert ev.type == EventType.ERROR


def test_notice_matcher_is_an_exact_phrase_allowlist_not_a_keyword_guess():
    assert agent_runtime_mod.codex_error_is_notice(_NOTICE)
    for other in ('hook trust review failed', 'enabled', '', 'notice: something else'):
        assert not agent_runtime_mod.codex_error_is_notice(other), other


def test_generic_reader_has_a_warn_branch_so_a_notice_is_labelled_not_bare():
    src = Path(agent_runtime_mod.__file__).read_text(encoding='utf-8')
    reader = src[src.index('elif ev.type == EventType.WARN:'):]
    assert "{runtime.name} " in reader[:600], 'WARN must be provider-labelled, not bare text'


# ── the envelope the notice ACTUALLY uses (measured 2026-09-19) ──────────────
#
# The first fix for this bug patched the TOP-LEVEL {"type":"error"} branch and
# changed nothing live: the cross-dispatch cell still recorded `[codex error]`
# (docs/_journal/provider-live/xdispatch-claude-codex-qwen/cross-dispatch.md).
# Raw codex 0.151 stdout, captured directly:
#
#   {"type":"thread.started",...}
#   {"type":"item.completed","item":{"id":"item_0","type":"error","message":
#    "`--dangerously-bypass-hook-trust` is enabled. ..."}}
#   {"type":"item.completed","item":{"id":"item_1","type":"error", ... same}}
#   {"type":"turn.started"}
#
# ...i.e. an `item.completed` whose ITEM type is error, twice, before the turn
# even starts. The top-level shape carries the real failures that followed
# ({"type":"error","message":"Reconnecting... 401 Unauthorized..."}). Both are
# pinned so a fix to one can never again look like a fix to the other.

def test_notice_on_the_item_completed_envelope_is_a_warning():
    ev = _parse({'type': 'item.completed',
                 'item': {'id': 'item_0', 'type': 'error', 'message': _NOTICE}})
    assert ev.type == EventType.WARN, 'this is the envelope measured live'
    assert ev.payload['severity'] == 'notice'


def test_a_real_failure_on_the_item_completed_envelope_is_still_an_error():
    ev = _parse({'type': 'item.completed',
                 'item': {'id': 'item_3', 'type': 'error',
                          'message': 'Reconnecting... 2/5 (unexpected status 401 '
                                     'Unauthorized: Missing bearer or basic '
                                     'authentication in header)'}})
    assert ev.type == EventType.ERROR


# ── the notice reaches chat AT MOST ONCE per mc_session_id (2026-09-22) ──────
#
# The WARN downgrade above already stops the notice from reading as a failure.
# It still hit `session['log_lines']` (== the chat transcript) once or twice
# EVERY turn -- noise for a condition that is expected and self-inflicted
# (--dangerously-bypass-hook-trust is required, see guardrail_hooks.py:318).
# These drive `_mode_a_reader` (the shared Mode-A stdout reader) directly
# against fabricated raw codex JSONL, the same envelope pinned above, and
# assert on the resulting `log_lines` -- the actual chat surface.

import uuid  # noqa: E402


def _fresh_mc_session_id():
    return f'test-{uuid.uuid4().hex[:8]}'


class _FakeProc:
    """Just enough of subprocess.Popen for `_mode_a_reader` to drain."""

    def __init__(self, lines):
        self.stdout = iter(lines)

    def wait(self):
        return 0


def _run_reader_lines(lines, mc_session_id, callbacks=None):
    proc = _FakeProc(lines)
    session_dict = {'log_lines': [], 'proc': proc}
    handle = agent_runtime_mod.SessionHandle(
        mc_session_id=mc_session_id, provider='codex', mode='A',
        project_path='.', project_id='p', session_dict=session_dict,
        meta={'callbacks': callbacks or {}},
    )
    agent_runtime_mod._mode_a_reader(proc, handle, agent_runtime_mod.CodexRuntime())
    return session_dict['log_lines']


def _notice_line():
    return json.dumps({'type': 'item.completed',
                        'item': {'id': 'item_0', 'type': 'error', 'message': _NOTICE}})


def test_hook_trust_notice_shows_once_on_first_occurrence():
    sid = _fresh_mc_session_id()
    lines = _run_reader_lines([_notice_line()], sid)
    assert len(lines) == 1
    assert 'bypassed so Clayrune guardrail hooks can run (expected)' in lines[0]
    assert _NOTICE not in lines[0], 'chat line is reworded, not the raw vendor string'


def test_hook_trust_notice_muted_on_second_and_third_occurrence_same_session():
    sid = _fresh_mc_session_id()
    first_turn = _run_reader_lines([_notice_line(), _notice_line()], sid)
    assert len(first_turn) == 1, 'two notices in one turn still show once'
    third_turn = _run_reader_lines([_notice_line()], sid)
    assert third_turn == [], 'a later turn in the same session stays muted'


def test_hook_trust_notice_surfaces_again_in_a_different_session():
    sid_a, sid_b = _fresh_mc_session_id(), _fresh_mc_session_id()
    _run_reader_lines([_notice_line()], sid_a)
    lines_b = _run_reader_lines([_notice_line()], sid_b)
    assert len(lines_b) == 1, 'dedup key is mc_session_id, not global'


def test_real_codex_error_still_surfaces_every_time_not_deduped():
    sid = _fresh_mc_session_id()
    real_error = json.dumps({'type': 'error',
                              'message': 'stream disconnected before completion'})
    first = _run_reader_lines([real_error], sid)
    second = _run_reader_lines([real_error], sid)
    assert len(first) == 1 and '[codex error]' in first[0]
    assert len(second) == 1 and '[codex error]' in second[0], (
        'a real error is never suppressed, only the notice is')


def test_raw_capture_still_fires_every_occurrence_even_when_chat_is_muted():
    sid = _fresh_mc_session_id()
    captured = []
    lines = _run_reader_lines(
        [_notice_line(), _notice_line()], sid,
        callbacks={'on_raw_record': lambda line, seq, m, s: captured.append(line)})
    assert len(captured) == 2, 'the raw event-stream capture must not lose the signal'
    assert len(lines) == 1, 'only the chat line is deduped'
