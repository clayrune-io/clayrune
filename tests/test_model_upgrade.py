"""Unit tests for mc/model_upgrade.py — Piece 2 of the 2026-09-23
model-auto-upgrade spec.

Determinism: `~/.clayrune` (prices file + audit log) is already isolated for
the whole suite by tests/conftest.py's session-wide CLAYRUNE_HOME override
(MC-965); nothing here needs its own fixture for that. Character-pin tests
additionally repoint mc.characters.GLOBAL_AGENTS_DIR at tmp_path, the
tests/test_character_persona.py precedent — no real ~/.claude is touched.
project/global pin tests use plain in-memory load/save closures (the module's
own dependency-injection surface), so they never touch data/projects/ at all.
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.conftest import stub_codex_models_cache  # noqa: E402

from mc import model_upgrade as mu  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_global_agents_dir(tmp_path, monkeypatch):
    """mc.characters.GLOBAL_AGENTS_DIR defaults to the operator's REAL
    ~/.claude/agents (mc/skills.py's _home() -- unrelated to the CLAYRUNE_HOME
    override conftest already applies for ~/.clayrune). run_upgrade_gate's
    character-pin scan reads it unconditionally, so without this every test
    in this file would evaluate-and-possibly-upgrade the box's REAL hired
    characters (AGENT_RULES.md: tests must not touch live operator state).
    Individual tests that want a populated dir still monkeypatch it to their
    own tmp_path via the `chars` fixture below -- same target, so no conflict."""
    from mc import characters as ch
    monkeypatch.setattr(ch, 'GLOBAL_AGENTS_DIR', tmp_path / 'agents-global')


# ── evaluate_pin: the spec's own decision-matrix ─────────────────────────────

class TestEvaluatePin:
    def test_not_a_pin_tier_tracking_untouched(self):
        decision = mu.evaluate_pin('codex', 'tier:balanced')
        assert decision.reason == 'not_a_pin'
        assert decision.action == 'no_change'

    def test_inherit_empty_untouched(self):
        decision = mu.evaluate_pin('codex', '')
        assert decision.reason == 'not_a_pin'

    def test_cross_family_no_family_signal(self):
        # 'gpt-5.5' carries no -astra/-sol/-luna suffix, so tier_family()
        # returns '' — never guessed into some other family's comparison.
        decision = mu.evaluate_pin('codex', 'gpt-5.5')
        assert decision.reason == 'no_family'
        assert decision.action == 'no_change'

    def test_unknown_provider(self):
        decision = mu.evaluate_pin('not-a-real-provider', 'gpt-5.6-sol')
        assert decision.reason == 'unknown_provider'

    def test_already_head_no_change(self, monkeypatch):
        from mc.agent_runtime import CodexRuntime
        monkeypatch.setattr(CodexRuntime, 'catalog_head_for',
                            lambda self, tier: 'gpt-6-sol' if tier == 'balanced' else '',
                            raising=False)
        decision = mu.evaluate_pin('codex', 'gpt-6-sol')
        assert decision.reason == 'already_head'

    def _head_stub(self, monkeypatch, head='gpt-6-sol'):
        from mc.agent_runtime import CodexRuntime
        monkeypatch.setattr(CodexRuntime, 'catalog_head_for',
                            lambda self, tier: head if tier == 'balanced' else '',
                            raising=False)

    def test_unknown_price_old_blocks_upgrade(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path)
        self._head_stub(monkeypatch)
        mu.set_price('codex', 'gpt-6-sol', 2.0, 10.0)
        decision = mu.evaluate_pin('codex', 'gpt-5.6-sol')
        assert decision.reason == 'unknown_price_old'
        assert decision.action == 'no_change'

    def test_unknown_price_new_blocks_upgrade(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path)
        self._head_stub(monkeypatch)
        mu.set_price('codex', 'gpt-5.6-sol', 4.0, 20.0)
        decision = mu.evaluate_pin('codex', 'gpt-5.6-sol')
        assert decision.reason == 'unknown_price_new'
        assert decision.action == 'no_change'

    def test_equal_price_upgrades(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path)
        self._head_stub(monkeypatch)
        mu.set_price('codex', 'gpt-5.6-sol', 4.0, 20.0)
        mu.set_price('codex', 'gpt-6-sol', 4.0, 20.0)
        decision = mu.evaluate_pin('codex', 'gpt-5.6-sol')
        assert decision.action == 'upgrade'
        assert decision.reason == 'ok'
        assert decision.new_model == 'gpt-6-sol'

    def test_cheaper_upgrades(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path)
        self._head_stub(monkeypatch)
        mu.set_price('codex', 'gpt-5.6-sol', 4.0, 20.0)
        mu.set_price('codex', 'gpt-6-sol', 2.0, 10.0)
        decision = mu.evaluate_pin('codex', 'gpt-5.6-sol')
        assert decision.action == 'upgrade'
        assert decision.old_price == {'input': 4.0, 'output': 20.0,
                                      'source_url': '', 'verified_at': decision.old_price['verified_at']}
        assert decision.new_price['input'] == 2.0

    def test_more_expensive_blocks_upgrade(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path)
        self._head_stub(monkeypatch)
        mu.set_price('codex', 'gpt-5.6-sol', 4.0, 20.0)
        mu.set_price('codex', 'gpt-6-sol', 5.0, 20.0)
        decision = mu.evaluate_pin('codex', 'gpt-5.6-sol')
        assert decision.action == 'no_change'
        assert decision.reason == 'more_expensive'

    def test_input_cheaper_output_pricier_blocks(self, monkeypatch, tmp_path):
        # Both must hold -- one side improving isn't enough.
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path)
        self._head_stub(monkeypatch)
        mu.set_price('codex', 'gpt-5.6-sol', 4.0, 20.0)
        mu.set_price('codex', 'gpt-6-sol', 2.0, 25.0)
        decision = mu.evaluate_pin('codex', 'gpt-5.6-sol')
        assert decision.action == 'no_change'
        assert decision.reason == 'more_expensive'


# ── prices store ──────────────────────────────────────────────────────────────

class TestPricesStore:
    def test_set_price_roundtrips(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path)
        mu.set_price('codex', 'gpt-6-sol', 2.0, 10.0, source_url='https://example.test')
        got = mu.get_price('codex', 'gpt-6-sol')
        assert got['input'] == 2.0 and got['output'] == 10.0
        assert got['source_url'] == 'https://example.test'
        assert 'verified_at' in got

    def test_set_price_rejects_negative(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path)
        with pytest.raises(ValueError):
            mu.set_price('codex', 'gpt-6-sol', -1.0, 10.0)

    def test_get_price_missing_is_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path)
        assert mu.get_price('codex', 'no-such-model') is None

    def test_prices_file_never_written_under_repo(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path)
        mu.set_price('codex', 'gpt-6-sol', 2.0, 10.0)
        assert mu.prices_path() == tmp_path / mu.PRICES_FILENAME
        assert not (PROJECT_ROOT / mu.PRICES_FILENAME).exists()


# ── discovery_report ──────────────────────────────────────────────────────────

class TestDiscoveryReport:
    def test_codex_live_cache_reported(self, monkeypatch, tmp_path):
        stub_codex_models_cache(monkeypatch, tmp_path)
        report = mu.discovery_report()
        assert report['codex'] is not None
        assert 'gpt-6-sol' in report['codex']
        assert 'gpt-5.6-sol' in report['codex']
        # 'hide' entries never reach discovery either.
        assert 'gpt-reserve' not in report['codex']

    def test_codex_missing_cache_is_none_not_guessed(self, monkeypatch, tmp_path):
        stub_codex_models_cache(monkeypatch, tmp_path, missing=True)
        report = mu.discovery_report()
        assert report['codex'] is None

    def test_runtime_with_no_discovery_source_is_none(self):
        # Base AgentRuntime.discover_models() default — any runtime that
        # hasn't overridden it (e.g. ClaudeRuntime) reports None.
        report = mu.discovery_report()
        assert report.get('claude') is None


# ── price_gaps ────────────────────────────────────────────────────────────────

class TestPriceGaps:
    def test_finds_pin_and_head_with_no_price(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path)
        stub_codex_models_cache(monkeypatch, tmp_path)
        config = {'default_provider': 'codex'}
        project = {'id': 'p1', 'agent_model': 'gpt-5.6-sol'}

        gaps = mu.price_gaps(
            load_projects_fn=lambda: [project],
            load_project_fn=lambda pid: project if pid == 'p1' else None,
            save_project_fn=lambda pid, data: None,
            config=config, config_path=tmp_path / 'config.json',
        )
        pairs = {(g['provider'], g['model']) for g in gaps}
        assert ('codex', 'gpt-5.6-sol') in pairs
        assert ('codex', 'gpt-6-sol') in pairs  # the head it would upgrade to

    def test_known_price_is_not_a_gap(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path)
        stub_codex_models_cache(monkeypatch, tmp_path)
        mu.set_price('codex', 'gpt-5.6-sol', 4.0, 20.0)
        mu.set_price('codex', 'gpt-6-sol', 2.0, 10.0)
        config = {'default_provider': 'codex'}
        project = {'id': 'p1', 'agent_model': 'gpt-5.6-sol'}

        gaps = mu.price_gaps(
            load_projects_fn=lambda: [project],
            load_project_fn=lambda pid: project,
            save_project_fn=lambda pid, data: None,
            config=config, config_path=tmp_path / 'config.json',
        )
        assert gaps == []


# ── run_upgrade_gate: project + global pins via in-memory DI ─────────────────

class TestRunUpgradeGateProjectAndGlobal:
    def test_project_agent_model_upgrades(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path)
        stub_codex_models_cache(monkeypatch, tmp_path)
        mu.set_price('codex', 'gpt-5.6-sol', 4.0, 20.0)
        mu.set_price('codex', 'gpt-6-sol', 2.0, 10.0)

        projects = {'p1': {'id': 'p1', 'agent_model': 'gpt-5.6-sol'}}
        config = {'default_provider': 'codex'}
        config_path = tmp_path / 'config.json'
        config_path.write_text(json.dumps(config), encoding='utf-8')

        saved = {}
        report = mu.run_upgrade_gate(
            load_projects_fn=lambda: list(projects.values()),
            load_project_fn=lambda pid: projects.get(pid),
            save_project_fn=lambda pid, data: (projects.__setitem__(pid, data), saved.__setitem__(pid, data)),
            config=config, config_path=config_path, apply=True,
        )
        assert report['changed'] is True
        assert len(report['upgraded']) == 1
        assert saved['p1']['agent_model'] == 'gpt-6-sol'
        # Audit log line written.
        log_lines = (tmp_path / mu.UPGRADE_LOG_FILENAME).read_text(encoding='utf-8').strip().splitlines()
        assert len(log_lines) == 1
        entry = json.loads(log_lines[0])
        assert entry['old_model'] == 'gpt-5.6-sol' and entry['new_model'] == 'gpt-6-sol'

    def test_global_agent_model_upgrades(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path)
        stub_codex_models_cache(monkeypatch, tmp_path)
        mu.set_price('codex', 'gpt-5.6-sol', 4.0, 20.0)
        mu.set_price('codex', 'gpt-6-sol', 2.0, 10.0)

        config = {'default_provider': 'codex', 'agent_model': 'gpt-5.6-sol'}
        config_path = tmp_path / 'config.json'
        config_path.write_text(json.dumps(config), encoding='utf-8')

        report = mu.run_upgrade_gate(
            load_projects_fn=lambda: [],
            load_project_fn=lambda pid: None,
            save_project_fn=lambda pid, data: None,
            config=config, config_path=config_path, apply=True,
        )
        assert report['changed'] is True
        assert config['agent_model'] == 'gpt-6-sol'
        persisted = json.loads(config_path.read_text(encoding='utf-8'))
        assert persisted['agent_model'] == 'gpt-6-sol'

    def test_apply_false_previews_without_writing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path)
        stub_codex_models_cache(monkeypatch, tmp_path)
        mu.set_price('codex', 'gpt-5.6-sol', 4.0, 20.0)
        mu.set_price('codex', 'gpt-6-sol', 2.0, 10.0)

        config = {'default_provider': 'codex', 'agent_model': 'gpt-5.6-sol'}
        config_path = tmp_path / 'config.json'
        config_path.write_text(json.dumps(config), encoding='utf-8')

        report = mu.run_upgrade_gate(
            load_projects_fn=lambda: [],
            load_project_fn=lambda pid: None,
            save_project_fn=lambda pid, data: None,
            config=config, config_path=config_path, apply=False,
        )
        assert report['applied'] is False
        assert report['changed'] is False  # changed requires apply=True too
        assert len(report['upgraded']) == 1
        assert report['upgraded'][0]['would_upgrade'] is True
        # Nothing actually written.
        assert config['agent_model'] == 'gpt-5.6-sol'
        assert not (tmp_path / mu.UPGRADE_LOG_FILENAME).exists()

    def test_unknown_price_reported_not_applied(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path)
        stub_codex_models_cache(monkeypatch, tmp_path)
        config = {'default_provider': 'codex', 'agent_model': 'gpt-5.6-sol'}
        config_path = tmp_path / 'config.json'
        config_path.write_text(json.dumps(config), encoding='utf-8')

        report = mu.run_upgrade_gate(
            load_projects_fn=lambda: [],
            load_project_fn=lambda pid: None,
            save_project_fn=lambda pid, data: None,
            config=config, config_path=config_path, apply=True,
        )
        assert report['upgraded'] == []
        assert len(report['unknown_price']) == 1
        assert config['agent_model'] == 'gpt-5.6-sol'


# ── run_upgrade_gate: character pins against a TEMP characters dir ───────────

class TestRunUpgradeGateCharacters:
    """The spec's own end-to-end check: Marlow-shaped character pinned to
    gpt-5.6-sol ($4/$20) upgrades to gpt-6-sol ($2/$10) once both prices are
    on file — against a throwaway GLOBAL_AGENTS_DIR, never the real one."""

    @pytest.fixture()
    def chars(self, tmp_path, monkeypatch):
        from mc import characters as ch
        monkeypatch.setattr(ch, 'GLOBAL_AGENTS_DIR', tmp_path / 'agents-global')
        ch.write_character(
            'global', 'prd-writer', 'Use when the user wants a PRD.',
            'You write product requirement docs.',
            engine={'provider': 'codex', 'model': 'gpt-5.6-sol'},
        )
        return ch

    def test_character_pin_upgrades_end_to_end(self, monkeypatch, tmp_path, chars):
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path / 'clayrune-home')
        stub_codex_models_cache(monkeypatch, tmp_path)
        mu.set_price('codex', 'gpt-5.6-sol', 4.0, 20.0)
        mu.set_price('codex', 'gpt-6-sol', 2.0, 10.0)
        config = {}
        config_path = tmp_path / 'config.json'

        report = mu.run_upgrade_gate(
            load_projects_fn=lambda: [],
            load_project_fn=lambda pid: None,
            save_project_fn=lambda pid, data: None,
            config=config, config_path=config_path, apply=True,
        )
        assert report['changed'] is True
        upgraded = [r for r in report['upgraded'] if r['kind'] == 'character']
        assert len(upgraded) == 1
        assert upgraded[0]['old_model'] == 'gpt-5.6-sol'
        assert upgraded[0]['new_model'] == 'gpt-6-sol'

        rec = chars.read_character('global', 'prd-writer')
        assert rec['engine']['model'] == 'gpt-6-sol'
        # Provider + everything else on the character survives untouched.
        assert rec['engine']['provider'] == 'codex'
        assert rec['description'] == 'Use when the user wants a PRD.'
        assert rec['body'].strip() == 'You write product requirement docs.'

    def test_character_pin_not_upgraded_when_price_unknown(self, monkeypatch, tmp_path, chars):
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path / 'clayrune-home')
        stub_codex_models_cache(monkeypatch, tmp_path)
        config = {}
        config_path = tmp_path / 'config.json'

        report = mu.run_upgrade_gate(
            load_projects_fn=lambda: [],
            load_project_fn=lambda pid: None,
            save_project_fn=lambda pid, data: None,
            config=config, config_path=config_path, apply=True,
        )
        assert report['changed'] is False
        rec = chars.read_character('global', 'prd-writer')
        assert rec['engine']['model'] == 'gpt-5.6-sol'  # untouched

    def test_tier_tracking_character_pin_never_touched(self, monkeypatch, tmp_path):
        from mc import characters as ch
        monkeypatch.setattr(ch, 'GLOBAL_AGENTS_DIR', tmp_path / 'agents-global')
        ch.write_character(
            'global', 'tier-tracker', 'Tracks the best tier.',
            'You are always on the newest model.',
            engine={'provider': 'codex', 'model': 'tier:best'},
        )
        monkeypatch.setattr(mu, 'clayrune_home', lambda: tmp_path / 'clayrune-home')
        stub_codex_models_cache(monkeypatch, tmp_path)
        mu.set_price('codex', 'gpt-6-astra', 15.0, 75.0)
        config = {}
        config_path = tmp_path / 'config.json'

        report = mu.run_upgrade_gate(
            load_projects_fn=lambda: [],
            load_project_fn=lambda pid: None,
            save_project_fn=lambda pid, data: None,
            config=config, config_path=config_path, apply=True,
        )
        assert report['upgraded'] == []
        assert report['unknown_price'] == []
        assert report['more_expensive'] == []
        assert report['skipped'] == []  # not even reported -- non_candidate
        rec = ch.read_character('global', 'tier-tracker')
        assert rec['engine']['model'] == 'tier:best'
