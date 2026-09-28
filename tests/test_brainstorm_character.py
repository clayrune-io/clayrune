"""MC-957: the built-in Brainstorm character installs and resolves like any
other persona — no bespoke dispatch path, just the existing character +
engine-pin machinery pointed at a real file.

Determinism: mc.characters.GLOBAL_AGENTS_DIR is repointed at tmp_path; no real
~/.claude is touched. Unlike test_characters_builtin.py this DOES read the
real data/agents/builtin/brainstorm.md — these tests are the regression net
for its frontmatter and required content sections, not just the install
mechanics (already covered generically for Claydo).
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BUILTIN_ROOT = PROJECT_ROOT / 'data' / 'agents' / 'builtin'


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401 — wires agent_routes' blueprint deps
    from mc import characters as ch

    monkeypatch.setattr(ch, 'GLOBAL_AGENTS_DIR', tmp_path / 'agents-global')
    ch.install_builtin_characters(BUILTIN_ROOT)
    return {'ch': ch}


class TestInstall:
    def test_installs_from_the_real_builtin_dir(self, env):
        rec = env['ch'].read_character('global', 'brainstorm')
        assert rec is not None
        assert rec['agent_name'] == 'Brainstorm'
        assert rec['avatar'] == 'fig:alchemist'

    def test_appears_in_roster(self, env):
        names = [r['name'] for r in env['ch'].list_characters()]
        assert 'brainstorm' in names

    def test_engine_pin_present(self, env):
        # MC-957 review (Dave): a shipped built-in must not pin a vendor — a
        # Codex- or Gemini-only install would land it on a CLI that isn't
        # there. 'tier:best' tracks whatever the ACTIVE provider's own top
        # tier is (mc/engine_selection.py); provider itself is left unset so
        # it inherits the project/global default like any other persona.
        rec = env['ch'].read_character('global', 'brainstorm')
        assert rec['engine'] == {'model': 'tier:best', 'effort': 'high'}
        assert 'provider' not in rec['engine']


class TestPersonaContent:
    """Regression anchors for the foundation/spec contract — not a re-check of
    prose wording, just that the required sections survive an edit."""

    @pytest.fixture()
    def body(self, env):
        return env['ch'].read_character('global', 'brainstorm', include_body=True)['body']

    def test_covers_all_five_phases(self, body):
        for phase in ('Find the pull', 'Map the frontier', 'Probe gaps',
                      'Pressure-test', 'Choose the next probe'):
            assert phase in body

    def test_swot_is_mandatory_for_business_ideas(self, body):
        assert 'SWOT' in body
        assert 'may not waive it' in body

    def test_research_evidence_rules_present(self, body):
        assert 'retrieval date' in body
        assert 'sparse search' in body

    def test_exploration_brief_contents_present(self, body):
        assert 'Exploration brief' in body
        assert 'cheap experiment' in body

    def test_grants_no_extra_permissions(self, body):
        assert 'no extra' in body and 'permissions or tools' in body

    def test_out_of_scope_list_present(self, body):
        assert 'multi-month roadmap' in body
        assert 'automatic backlog' in body


class TestDispatchResolution:
    """The new-chat picker sends `character: 'global:brainstorm'`; dispatch
    resolves it via _resolve_character exactly like any other persona — this
    proves that path works end to end for the real file, not a synthetic one."""

    def test_resolves_via_global_scope_reference(self, env, tmp_path):
        from mc.blueprints import agent_routes as ar
        proj_path = tmp_path / 'proj'
        proj_path.mkdir()
        meta, body = ar._resolve_character(str(proj_path), 'global:brainstorm')
        assert meta is not None
        assert meta['name'] == 'brainstorm'
        assert meta['scope'] == 'global'
        assert meta['agent_name'] == 'Brainstorm'
        assert meta['engine'] == {'model': 'tier:best', 'effort': 'high'}
        assert 'Find the pull' in body

    def test_character_block_injects_into_agent_context(self, env, tmp_path):
        from mc.blueprints import agent_routes as ar
        proj_path = tmp_path / 'proj'
        proj_path.mkdir()
        _meta, body = ar._resolve_character(str(proj_path), 'global:brainstorm')
        ctx = ar._build_agent_context(
            {'id': 'tc', 'name': 'TC', 'project_path': str(proj_path), 'provider': 'claude'},
            character_body=body)
        assert '--- CHARACTER (active persona for this chat) ---' in ctx
        assert 'Find the pull' in ctx


class TestEngineResolvesTierBestPerVendor:
    """MC-957 review (Dave): the built-in's 'tier:best' pin must resolve to
    EACH provider's own current top handle through the one real choke point
    (mc/engine_selection.py resolve_model, called by _build_claude_flags for
    claude and _resolve_runtime_model for every other runtime) — not pass the
    literal 'tier:best' string to a CLI's --model flag — and an explicit
    per-chat model pick must still beat it, exactly like any other tier
    tracking value or character pin.
    """

    def test_claude_resolves_tier_best_to_its_own_handle(self, env):
        from mc import engine_selection as es
        meta = env['ch'].read_character('global', 'brainstorm')
        char_model = meta['engine']['model']
        assert char_model == 'tier:best'
        model, source = es.resolve_model('claude', {}, None, override=char_model)
        # ClaudeRuntime.TIER_ALIASES — the CLI's own always-resolves-to-newest
        # alias, not a concrete id MC would have to keep updated by hand.
        assert model == 'opus'
        assert source == 'explicit'

    def test_codex_resolves_tier_best_to_its_own_handle(self, env, monkeypatch, tmp_path):
        from mc import engine_selection as es
        from mc.agent_runtime import CodexRuntime
        # Force the static-catalog fallback so this doesn't depend on whether
        # this box happens to have a real ~/.codex/models_cache.json.
        monkeypatch.setattr(CodexRuntime, '_model_cache_path',
                            staticmethod(lambda: tmp_path / 'no-cache.json'))
        meta = env['ch'].read_character('global', 'brainstorm')
        char_model = meta['engine']['model']
        model, source = es.resolve_model('codex', {}, None, override=char_model)
        assert model == 'gpt-6-astra'
        assert source == 'explicit'

    def test_tier_best_is_never_a_cross_vendor_mismatch(self, env):
        # A tracking token, not a concrete id — must never be flagged as
        # belonging to some other vendor's catalog (which would refuse the
        # dispatch outright instead of resolving the tier).
        from mc.blueprints import agent_routes as ar
        assert ar._model_provider_mismatch('claude', 'tier:best') == ''
        assert ar._model_provider_mismatch('codex', 'tier:best') == ''

    def test_resolve_runtime_model_translates_for_a_non_claude_dispatch(
            self, env, monkeypatch, tmp_path):
        # The exact function _dispatch_via_runtime calls to turn the raw
        # model_override it receives into what actually reaches the CLI.
        from mc.blueprints import agent_routes as ar
        from mc.agent_runtime import CodexRuntime
        monkeypatch.setattr(CodexRuntime, '_model_cache_path',
                            staticmethod(lambda: tmp_path / 'no-cache.json'))
        meta = env['ch'].read_character('global', 'brainstorm')
        resolved = ar._resolve_runtime_model(
            CodexRuntime(), None, model_override=meta['engine']['model'],
            provider_name='codex')
        assert resolved == 'gpt-6-astra'

    def test_explicit_per_chat_model_beats_the_tier_pin(self, env, monkeypatch, tmp_path):
        from mc.blueprints import agent_routes as ar
        proj_path = tmp_path / 'proj-explicit'
        (proj_path / '.claude' / 'agents').mkdir(parents=True)
        project = {'id': 'tc-explicit', 'name': 'TCX', 'project_path': str(proj_path),
                   'provider': 'codex'}
        monkeypatch.setattr(ar, 'load_project', lambda pid: project)
        captured = {}

        def _fake_runtime_dispatch(p, task, *, provider_name, model_override, **kw):
            captured['provider_name'] = provider_name
            captured['model_override'] = model_override
            return 'sid-explicit'
        monkeypatch.setattr(ar, '_dispatch_via_runtime', _fake_runtime_dispatch)

        sid = ar._dispatch_agent_internal(
            'tc-explicit', 'brainstorm this', character='global:brainstorm',
            provider_override='codex', model_override='gpt-6-sol')
        assert sid == 'sid-explicit'
        # The user's own composer pick, not the character's 'tier:best' pin.
        assert captured['model_override'] == 'gpt-6-sol'

    def test_character_pin_applies_when_nothing_else_is_picked(self, env, monkeypatch, tmp_path):
        # Mirror of the previous test with no explicit override — proves the
        # 'tier:best' pin (not the literal absence of a pin) is what reaches
        # the runtime dispatch call when the user picks nothing.
        from mc.blueprints import agent_routes as ar
        proj_path = tmp_path / 'proj-inherit'
        (proj_path / '.claude' / 'agents').mkdir(parents=True)
        project = {'id': 'tc-inherit', 'name': 'TCI', 'project_path': str(proj_path),
                   'provider': 'codex'}
        monkeypatch.setattr(ar, 'load_project', lambda pid: project)
        captured = {}

        def _fake_runtime_dispatch(p, task, *, provider_name, model_override, **kw):
            captured['provider_name'] = provider_name
            captured['model_override'] = model_override
            return 'sid-inherit'
        monkeypatch.setattr(ar, '_dispatch_via_runtime', _fake_runtime_dispatch)

        sid = ar._dispatch_agent_internal(
            'tc-inherit', 'brainstorm this', character='global:brainstorm',
            provider_override='codex')
        assert sid == 'sid-inherit'
        assert captured['model_override'] == 'tier:best'
