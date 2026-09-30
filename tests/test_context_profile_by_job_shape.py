"""Context by JOB SHAPE — the second, orthogonal axis to model tier
(backlog 8ead5755, next to 4a11b6a5/c9cc4e5a's per-model `mc/context_profile.py`).

Model tier asks "how capable is the engine reading this"; job shape asks
"what kind of session is this" — a human's own chat ('conversation') versus
a one-off session another agent dispatched with a brief that reports home
('task'). A dispatched builder pays the rules, its brief, and task-scoped
memory; it does not need a human chat's history (recent conversations,
continuity, sibling activity, roster, the delegation-cost card) — none of
that describes ITS job, only a project's ongoing conversational state.

Shape is resolved ONCE (`context_profile.resolve_job_shape`), from `source`
at fresh dispatch, or from a session's own stored `job_shape` on revive/
resume/respawn — see the `prior_shape` short-circuit and its callers in
`mc/blueprints/agent_routes.py` (`_dispatch_agent_internal`,
`_dispatch_via_runtime`, `_revive_from_agent_log`, `_fresh_context_for`,
`_respawn_sysprompt_args`). A session's shape must never drift mid-
conversation just because a later turn's caller looks different from the
one that dispatched it — that is the entire reason `prior_shape` exists and
wins outright over a fresh `source`-based resolution.

Decision recorded here (Ron asked for it explicitly): a human opening a
dispatched child's chat and talking to it does NOT flip its shape to
'conversation'. There is no reliable per-turn signal that distinguishes "a
human is now typing" from "an automated caller sent a followup" —
`agent_followup` is answered by chat forms, plan-approval clicks, and the
email-reply relay alike — and guessing wrong would silently change what
context a running session gets mid-conversation, which is the exact
invariant `prior_shape` exists to protect. If Ron wants the full
conversation floor for a session, the lever is starting a FRESH chat, whose
source resolves to 'conversation' the normal way.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import context_profile  # noqa: E402


# ── mc/context_profile.resolve_job_shape() — pure unit tests ────────────────

def test_agent_source_resolves_task():
    assert context_profile.resolve_job_shape('agent', '') == 'task'


def test_empty_source_resolves_conversation():
    # A human's own chat: no source at all — today's unchanged default.
    assert context_profile.resolve_job_shape('', '') == 'conversation'


def test_ui_source_resolves_conversation():
    assert context_profile.resolve_job_shape('ui', '') == 'conversation'


def test_unknown_source_resolves_conversation():
    # Fail-closed to the FULLER floor, same posture as context_profile.resolve
    # failing closed to 'full' — a naming a schedule/workflow caller hasn't
    # used yet must not silently starve a session of its rules.
    assert context_profile.resolve_job_shape('some-future-caller', '') == 'conversation'


def test_source_is_case_and_whitespace_insensitive():
    assert context_profile.resolve_job_shape('  Agent  ', '') == 'task'
    assert context_profile.resolve_job_shape('AGENT', '') == 'task'


def test_prior_shape_wins_outright_over_source():
    # The whole point: a session started as 'task' (source='agent') must
    # reproduce 'task' on resume/revive even if the caller re-deriving it
    # can no longer see the original source, or sees a different one.
    assert context_profile.resolve_job_shape('', 'task') == 'task'
    assert context_profile.resolve_job_shape('agent', 'conversation') == 'conversation'


def test_unrecognized_prior_shape_falls_through_to_source():
    # A blank / garbage stored value (old session row, never set) must not
    # be trusted as a real prior — fall through to fresh resolution.
    assert context_profile.resolve_job_shape('agent', 'bogus') == 'task'
    assert context_profile.resolve_job_shape('', 'bogus') == 'conversation'


def test_never_raises_on_none_inputs():
    assert context_profile.resolve_job_shape(None, None) == 'conversation'  # type: ignore[arg-type]


# ── Integration: _build_agent_context gates sections on the resolved shape ──

@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401 — wires blueprint deps (PORT, memory helpers, …)
    from mc.blueprints import agent_routes as ar

    proj_path = tmp_path / 'proj'
    proj_path.mkdir(parents=True)
    (proj_path / 'AGENT_RULES.md').write_text(
        '# Rules\nWrite your running log to docs/_journal/<item>-<slug>.md.\n'
        'If you were dispatched by another agent, report the decision to your '
        'spawner in your final message; do not email Ron.\n',
        encoding='utf-8')

    monkeypatch.setattr(ar, '_recent_claude_transcripts',
                        lambda *a, **k: [{'session_id': 'abc123def456', 'turns': 3,
                                          'last_user': 'do the thing', 'first_user': 'hi'}])
    monkeypatch.setattr(ar, '_load_agent_log', lambda project_id: [])

    project = {
        'id': 'tc', 'name': 'TC', 'project_path': str(proj_path),
        'provider': 'claude',
        'activity_log': [{'ts': '2026-09-29T00:00:00Z', 'msg': 'Agent dispatched: did a thing'}],
    }
    return {'ar': ar, 'project': project}


def _human_chat_markers(ctx):
    """Sections that belong to a human's ongoing conversation with the
    project, not to any one task — must vanish on the task shape.

    Floor self-naming is deliberately NOT in this set: it's
    self-identification (MC-925 `TestDelegatedPersonalessIdentity` pins this
    exact text for a personaless delegated session), not "who's on the team"
    — a dispatched session gets a real Floor figure too, so it stays in
    both shapes. The roster block (who ELSE is hired) is the one that's
    conversation-only, checked separately below."""
    return {
        'recent_activity': 'Recent activity:' in ctx,
        'recent_conversations': 'Recent conversations' in ctx,
        'delegation_cost': 'WHAT A DELEGATION COSTS' in ctx,
    }


def test_conversation_shape_matches_todays_full_floor(env):
    # source='' (a human's own chat) — the default, unchanged shape.
    ctx = env['ar']._build_agent_context(
        env['project'], task='hi', session_id='sess1', source='')
    m = _human_chat_markers(ctx)
    assert m == {'recent_activity': True,
                'recent_conversations': True, 'delegation_cost': True}
    assert 'You appear on the Floor as a figure' in ctx


def test_task_shape_drops_the_human_chat_floor(env):
    # source='agent' — a one-off dispatched builder with a brief.
    ctx = env['ar']._build_agent_context(
        env['project'], task='hi', session_id='sess1', source='agent')
    m = _human_chat_markers(ctx)
    assert m == {'recent_activity': False,
                'recent_conversations': False, 'delegation_cost': False}
    # Self-identification survives — a dispatched session still gets a real
    # Floor figure and MC-925 needs it able to say it isn't the default agent.
    assert 'You appear on the Floor as a figure' in ctx


def test_explicit_job_shape_overrides_source_derived_default(env):
    # A stored 'conversation' shape wins even over source='agent' — this is
    # the revive/resume path: the ORIGINAL shape reproduces, not a fresh
    # guess from whatever `source` looks like on this call.
    ctx = env['ar']._build_agent_context(
        env['project'], task='hi', session_id='sess1', source='agent',
        job_shape='conversation')
    m = _human_chat_markers(ctx)
    assert m == {'recent_activity': True,
                'recent_conversations': True, 'delegation_cost': True}

    ctx2 = env['ar']._build_agent_context(
        env['project'], task='hi', session_id='sess1', source='',
        job_shape='task')
    m2 = _human_chat_markers(ctx2)
    assert m2 == {'recent_activity': False,
                 'recent_conversations': False, 'delegation_cost': False}


def test_unrecognized_job_shape_falls_back_to_source(env):
    # An old caller / test that passes garbage rather than '' must still get
    # a sane resolution, not a crash or a silently-wrong shape.
    ctx = env['ar']._build_agent_context(
        env['project'], task='hi', session_id='sess1', source='agent',
        job_shape='not-a-real-shape')
    assert 'Recent activity:' not in ctx


def test_task_shape_dispatched_builder_still_finds_its_journal_and_reports_home(env):
    """The ONE real check the backlog item asks for: a dispatched builder
    (source='agent', task shape) must still receive AGENT_RULES.md — the
    file that tells it where its journal lives and how to report home —
    even though every human-chat-only section above is gone.

    Real in the sense that matters here: this runs the actual
    `_build_agent_context` code path against a real file on disk (no
    mocking of the function under test), not a synthetic assertion about
    what SHOULD happen.
    """
    ctx = env['ar']._build_agent_context(
        env['project'], task='hi', session_id='sess1', source='agent')
    assert '--- AGENT_RULES.md ---' in ctx
    assert 'docs/_journal/' in ctx
    assert 'report the decision to your spawner' in ctx
    # And still confirm the human-chat floor is actually gone alongside it —
    # otherwise this would just be re-testing the unconditional rules block.
    assert 'Recent activity:' not in ctx
    # Self-naming survives (MC-925: a dispatched session gets a real Floor
    # figure and needs to say it isn't the default agent) — the roster
    # ("who else is hired") is the block that's actually conversation-only.
    assert 'You appear on the Floor as a figure' in ctx
    assert 'THE ROSTER (hired agent types' not in ctx
