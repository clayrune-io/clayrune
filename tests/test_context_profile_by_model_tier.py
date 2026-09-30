"""Per-MODEL context-profile gating (backlog 4a11b6a5).

`_build_agent_context` used to slim its floor (Recent activity, Recent
conversations, plus a lean-only curated-memory bridge and a background-job
notice) by VENDOR alone (`_is_claude`): every Claude model, including Haiku,
got the full floor; every non-Claude model got the lean one regardless of
capability.

That gate is now `mc/context_profile.py` (mirrors
`AgentRuntime.image_input_for`): each runtime declares which of its own
models are 'lean' (`CONTEXT_PROFILE_PATTERNS`/`CONTEXT_PROFILE_DEFAULT`), a
`config.json` `context_profile_overrides` escape hatch can reclassify any
model without a code change, and `_build_agent_context` reads only the
resolved profile for the session's EFFECTIVE model (character pin / router
choice / project default) — never a vendor name directly.

Day-one behavior must match today for Opus/Sonnet/unset (full) and for
current non-Claude defaults (lean); Haiku is newly lean.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import context_profile  # noqa: E402
from mc import agent_runtime  # noqa: E402


# ── mc/context_profile.resolve() — pure unit tests, no Flask/project needed ──

def test_resolve_claude_haiku_is_lean():
    assert context_profile.resolve('claude', 'claude-haiku-4-5-20251001') == 'lean'


def test_resolve_claude_haiku_case_insensitive_substring_match():
    # CONTEXT_PROFILE_PATTERNS does a substring/regex search, not an exact
    # match — any id containing "haiku" anywhere buckets lean, matching how
    # _router_model_tier's own haiku bucketing already worked.
    assert context_profile.resolve('claude', 'HAIKU-4-5') == 'lean'


def test_resolve_claude_opus_is_full():
    assert context_profile.resolve('claude', 'claude-opus-5-5') == 'full'


def test_resolve_claude_sonnet_is_full():
    # Ron's brief: measure Sonnet, keep it full unless the numbers argue
    # otherwise — day one, Sonnet stays on the 'full' default.
    assert context_profile.resolve('claude', 'claude-sonnet-5-5') == 'full'


def test_resolve_claude_unknown_model_is_full():
    # model='' (unresolved at context-build time, e.g. the auto-router
    # branch) must fall back to the conservative default, not 'lean'.
    assert context_profile.resolve('claude', '') == 'full'


def test_resolve_non_claude_provider_is_lean_by_default():
    for provider, model in (
        ('gemini', 'gemini-2.5-pro'),
        ('qwen', 'qwen3-coder-plus'),
        ('codex', 'gpt-5-codex'),
        ('opencode', 'anthropic/claude-opus-5'),
        ('goose', 'anthropic/claude-sonnet-5'),
        ('aider', 'sonnet'),
        ('kiro', ''),
    ):
        assert context_profile.resolve(provider, model) == 'lean', provider


def test_resolve_unknown_provider_fails_closed_to_full():
    # An unregistered provider name must never block dispatch by raising —
    # and 'full' (not 'lean') is the conservative side of the fail-closed
    # default, matching today's behavior for anything _is_claude couldn't
    # classify either.
    assert context_profile.resolve('not-a-real-provider', 'whatever-model') == 'full'


def test_resolve_override_wins_over_runtime_default():
    overrides = {'claude-opus-*': 'lean'}
    assert context_profile.resolve('claude', 'claude-opus-5-5', overrides) == 'lean'


def test_resolve_override_reclassifies_non_claude_to_full():
    overrides = {'gemini-3-*': 'full'}
    assert context_profile.resolve('gemini', 'gemini-3-pro', overrides) == 'full'
    # A non-matching model on the same provider is untouched by the override.
    assert context_profile.resolve('gemini', 'gemini-2.5-flash', overrides) == 'lean'


def test_resolve_override_bad_value_falls_through_to_runtime_default():
    overrides = {'claude-haiku-*': 'not-a-real-profile'}
    assert context_profile.resolve('claude', 'claude-haiku-4-5', overrides) == 'lean'


def test_resolve_never_raises_on_garbage_overrides():
    assert context_profile.resolve('claude', 'claude-opus-5-5', {123: None}) == 'full'


# ── AgentRuntime.context_profile_for — runtime-level declaration ────────────

def test_claude_runtime_context_profile_for_haiku():
    rt = agent_runtime.get_runtime('claude')
    assert rt.context_profile_for('claude-haiku-4-5-20251001') == 'lean'


def test_claude_runtime_context_profile_for_opus_and_sonnet():
    rt = agent_runtime.get_runtime('claude')
    assert rt.context_profile_for('claude-opus-5-5') == 'full'
    assert rt.context_profile_for('claude-sonnet-5-5') == 'full'


def test_claude_runtime_context_profile_for_unknown_defaults_full():
    rt = agent_runtime.get_runtime('claude')
    assert rt.context_profile_for('') == 'full'
    assert rt.context_profile_for('some-future-claude-model') == 'full'


def test_gemini_runtime_context_profile_default_is_lean():
    rt = agent_runtime.get_runtime('gemini')
    assert rt.context_profile_for('gemini-2.5-pro') == 'lean'
    assert rt.context_profile_for('') == 'lean'


# ── Integration: _build_agent_context actually gates on the resolved profile ─

@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401 — wires blueprint deps (PORT, memory helpers, …)
    from mc.blueprints import agent_routes as ar

    proj_path = tmp_path / 'proj'
    proj_path.mkdir(parents=True)

    mem_path = tmp_path / 'MEMORY.md'
    mem_path.write_text('# Test project memory\nA curated fact.\n', encoding='utf-8')
    monkeypatch.setattr(ar, '_get_memory_path', lambda project: mem_path)
    monkeypatch.setattr(
        ar, '_recent_claude_transcripts',
        lambda *a, **k: [{'session_id': 'abc123def456', 'turns': 3,
                          'last_user': 'do the thing', 'first_user': 'hi'}])
    monkeypatch.setattr(ar, '_load_agent_log', lambda project_id: [])

    project = {
        'id': 'tc', 'name': 'TC', 'project_path': str(proj_path),
        'provider': 'claude',
        'activity_log': [{'ts': '2026-09-29T00:00:00Z', 'msg': 'Agent dispatched: did a thing'}],
    }
    return {'ar': ar, 'project': project}


def _markers(ctx):
    return {
        'recent_activity': 'Recent activity:' in ctx,
        'recent_conversations': 'Recent conversations' in ctx,
        'memory_index': 'PROJECT MEMORY INDEX' in ctx,
        'no_bg_job': 'no background-job facility' in ctx,
    }


def test_haiku_takes_the_slim_path(env):
    # Haiku drops the two phantom-task-list sections (Recent activity, Recent
    # conversations) — that's the model-tier gate this backlog item adds.
    # It does NOT pick up the curated-memory bridge or the no-background-job
    # notice: those compensate for a gap in the CLAUDE CLI specifically
    # (native MEMORY.md auto-load, `run_in_background`), and Haiku still runs
    # on that same CLI. See test_haiku_stays_vendor_gated_for_claude_only_sections.
    ctx = env['ar']._build_agent_context(
        env['project'], task='hi', session_id='sess1',
        provider='claude', model='claude-haiku-4-5-20251001')
    m = _markers(ctx)
    assert m == {'recent_activity': False, 'recent_conversations': False,
                'memory_index': False, 'no_bg_job': False}


def test_opus_keeps_the_full_floor(env):
    ctx = env['ar']._build_agent_context(
        env['project'], task='hi', session_id='sess1',
        provider='claude', model='claude-opus-5-5')
    m = _markers(ctx)
    assert m == {'recent_activity': True, 'recent_conversations': True,
                'memory_index': False, 'no_bg_job': False}


def test_sonnet_keeps_the_full_floor_day_one(env):
    ctx = env['ar']._build_agent_context(
        env['project'], task='hi', session_id='sess1',
        provider='claude', model='claude-sonnet-5-5')
    m = _markers(ctx)
    assert m == {'recent_activity': True, 'recent_conversations': True,
                'memory_index': False, 'no_bg_job': False}


def test_unset_model_on_claude_project_matches_todays_full_default(env):
    # No explicit provider/model passed at all (project-only default) — the
    # ORIGINAL call shape every existing caller used before this backlog
    # item. Must still resolve to 'full', unchanged from today's _is_claude
    # behavior for a plain claude project.
    ctx = env['ar']._build_agent_context(env['project'], task='hi', session_id='sess1')
    m = _markers(ctx)
    assert m == {'recent_activity': True, 'recent_conversations': True,
                'memory_index': False, 'no_bg_job': False}


def test_non_claude_provider_unchanged_lean(env):
    project = dict(env['project'], provider='gemini')
    ctx = env['ar']._build_agent_context(
        project, task='hi', session_id='sess1', provider='gemini', model='gemini-2.5-pro')
    m = _markers(ctx)
    assert m == {'recent_activity': False, 'recent_conversations': False,
                'memory_index': True, 'no_bg_job': True}


def test_character_pinned_haiku_model_is_honored_over_claude_project_default(env):
    # The project itself defaults to 'claude' with no model pin — this
    # proves the per-call `model` kwarg (how a character's pinned model, or
    # the composer's explicit picker choice, reaches this function) actually
    # drives the gate, not just the project's own provider field.
    ctx = env['ar']._build_agent_context(
        env['project'], task='hi', session_id='sess1',
        model='claude-haiku-4-5-20251001')
    m = _markers(ctx)
    assert m == {'recent_activity': False, 'recent_conversations': False,
                'memory_index': False, 'no_bg_job': False}


def test_haiku_stays_vendor_gated_for_claude_only_sections(env):
    # Regression guard: an earlier pass gated ALL FOUR sections on the new
    # model-tier flag by mechanically swapping `_is_claude` -> `_full_context`
    # everywhere. That was wrong for two of them — the curated-memory bridge
    # and the no-background-job notice exist only to compensate for what the
    # Claude CLI itself provides natively; a Claude Haiku session still runs
    # on that CLI and still has both, so injecting them anyway would
    # duplicate the real MEMORY.md auto-load (measured +18.6KB on a real
    # project) and tell Haiku it has no backgrounding facility when it does.
    ctx_claude = env['ar']._build_agent_context(
        env['project'], task='hi', session_id='sess1',
        provider='claude', model='claude-haiku-4-5-20251001')
    assert 'PROJECT MEMORY INDEX' not in ctx_claude
    assert 'no background-job facility' not in ctx_claude

    gemini_project = dict(env['project'], provider='gemini')
    ctx_gemini = env['ar']._build_agent_context(
        gemini_project, task='hi', session_id='sess1',
        provider='gemini', model='gemini-2.5-pro')
    assert 'PROJECT MEMORY INDEX' in ctx_gemini
    assert 'no background-job facility' in ctx_gemini


def test_context_profile_override_reclassifies_haiku_to_full(env, monkeypatch):
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'context_profile_overrides',
                        {'claude-haiku-*': 'full'})
    ctx = env['ar']._build_agent_context(
        env['project'], task='hi', session_id='sess1',
        provider='claude', model='claude-haiku-4-5-20251001')
    m = _markers(ctx)
    assert m == {'recent_activity': True, 'recent_conversations': True,
                'memory_index': False, 'no_bg_job': False}
