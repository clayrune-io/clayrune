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
        rec = env['ch'].read_character('global', 'brainstorm')
        assert rec['engine'] == {'provider': 'claude', 'model': 'opus', 'effort': 'high'}


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
        assert meta['engine'] == {'provider': 'claude', 'model': 'opus', 'effort': 'high'}
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
