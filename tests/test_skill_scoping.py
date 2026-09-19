"""Per-agent skill scoping (mc/skill_scoping.py, config `agent_skill_scoping_enabled`).

Ron, 2026-09-18: skills belong to the AGENT. Effective set per session =
the character's declared skills UNION the project's own skills -> full
description; every other installed skill -> `name-only` (still callable). An
agent that declares no skills is left exactly as it is today.

Never spawns a real CLI.
"""
import ast
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import skill_scoping as ss  # noqa: E402

INSTALLED = [
    {'name': 'dataviz', 'scope': 'global', 'description': 'charts'},
    {'name': 'apple-design', 'scope': 'global', 'description': 'design review'},
    {'name': 'mc-steward', 'scope': 'global', 'description': 'steward'},
    {'name': 'proj-local', 'scope': 'project', 'description': 'this repo only'},
    {'name': 'old-thing', 'scope': 'archive', 'description': 'archived'},
]


def _list(project_path, project_id):
    return list(INSTALLED)


# ── the rule ────────────────────────────────────────────────────────────────

def test_undeclared_character_is_unchanged():
    assert ss.skill_overrides('/p', 'p', [], list_skills=_list) == {}
    assert ss.skill_overrides('/p', 'p', None, list_skills=_list) == {}
    assert ss.skill_overrides('/p', 'p', ['', '  '], list_skills=_list) == {}


def test_declared_plus_project_local_stay_full_everything_else_name_only():
    out = ss.skill_overrides('/p', 'p', ['dataviz'], list_skills=_list)
    assert out == {'apple-design': 'name-only', 'mc-steward': 'name-only'}
    # never 'off' (hides the skill from the model), never demotes what is kept,
    # and never touches an archived skill (not listed by the CLI at all)
    assert 'dataviz' not in out and 'proj-local' not in out and 'old-thing' not in out
    assert set(out.values()) == {'name-only'}


def test_declared_name_match_is_case_insensitive():
    out = ss.skill_overrides('/p', 'p', ['DataViz'], list_skills=_list)
    assert 'dataviz' not in out


def test_listing_failure_demotes_nothing():
    def boom(*a):
        raise OSError('disk gone')
    assert ss.skill_overrides('/p', 'p', ['dataviz'], list_skills=boom) == {}


# ── the settings file ──────────────────────────────────────────────────────

def test_scoped_file_merges_guardrail_and_is_content_addressed(tmp_path):
    guard = tmp_path / 'claude-settings.json'
    guard.write_text(json.dumps({'hooks': {'PreToolUse': [{'matcher': 'Bash'}]}}))
    ov = {'apple-design': 'name-only', 'mc-steward': 'name-only'}
    p1 = ss.scoped_settings_path(guard, ov, tmp_path / 'scoped')
    p2 = ss.scoped_settings_path(guard, dict(reversed(list(ov.items()))), tmp_path / 'scoped')
    assert p1 == p2 and p1.is_file()
    data = json.loads(p1.read_text())
    assert data['hooks'] == {'PreToolUse': [{'matcher': 'Bash'}]}   # guard kept
    assert data['skillOverrides'] == ov
    p3 = ss.scoped_settings_path(guard, {'dataviz': 'name-only'}, tmp_path / 'scoped')
    assert p3 != p1


def test_scoped_file_without_guardrail_still_written(tmp_path):
    p = ss.scoped_settings_path(None, {'a': 'name-only'}, tmp_path / 's')
    assert json.loads(p.read_text()) == {'skillOverrides': {'a': 'name-only'}}


def test_no_overrides_writes_nothing(tmp_path):
    assert ss.scoped_settings_path(None, {}, tmp_path / 's') is None
    assert not (tmp_path / 's').exists()


# ── launch argv (Claude) ────────────────────────────────────────────────────

@pytest.fixture()
def ar(tmp_path, monkeypatch):
    from mc.blueprints import agent_routes as ar
    from mc import guardrail_hooks
    monkeypatch.setattr(guardrail_hooks, 'clayrune_home', lambda: tmp_path / 'home')
    guard = guardrail_hooks.launch_file_path('claude', tmp_path / 'home')
    guard.parent.mkdir(parents=True)
    guard.write_text(json.dumps({'hooks': {'PreToolUse': []}}))
    monkeypatch.setattr(ar._skills, 'list_skills', _list)
    monkeypatch.setitem(ar.state.CONFIG, 'agent_skill_scoping_enabled', True)
    return ar


def _settings_arg(flags):
    return flags[flags.index('--settings') + 1] if '--settings' in flags else None


P = {'id': 'p', 'project_path': '/p'}


def test_flag_off_argv_is_identical_even_with_declared_skills(ar, monkeypatch):
    monkeypatch.setitem(ar.state.CONFIG, 'agent_skill_scoping_enabled', False)
    base = ar._build_claude_flags(P)
    assert ar._build_claude_flags(P, character_skills=['dataviz']) == base


def test_undeclared_argv_is_identical_with_flag_on(ar, monkeypatch):
    with_flag = ar._build_claude_flags(P)
    monkeypatch.setitem(ar.state.CONFIG, 'agent_skill_scoping_enabled', False)
    assert with_flag == ar._build_claude_flags(P)
    monkeypatch.setitem(ar.state.CONFIG, 'agent_skill_scoping_enabled', True)
    assert ar._build_claude_flags(P, character_skills=[]) == with_flag
    assert 'skillOverrides' not in Path(_settings_arg(with_flag)).read_text()


def test_declared_argv_points_at_scoped_settings(ar):
    flags = ar._build_claude_flags(P, character_skills=['dataviz'])
    data = json.loads(Path(_settings_arg(flags)).read_text())
    assert data['skillOverrides'] == {'apple-design': 'name-only', 'mc-steward': 'name-only'}
    assert 'hooks' in data                                          # guard survives


def test_same_character_same_settings_across_launches(ar):
    """Respawn / revive / rollover each call _build_claude_flags again; the CLI
    must be handed the identical settings file every time."""
    a = _settings_arg(ar._build_claude_flags(P, streaming=True, character_skills=['dataviz']))
    b = _settings_arg(ar._build_claude_flags(P, model_override='claude-sonnet-5',
                                             character_skills=['dataviz']))
    assert a == b


# ── a session's persona resolves the same set on every respawn path ─────────

def test_session_skills_resolve_from_the_persona_on_disk(ar, tmp_path, monkeypatch):
    from mc import characters
    monkeypatch.setattr(characters, 'GLOBAL_AGENTS_DIR', tmp_path / 'agents')
    (tmp_path / 'agents').mkdir()
    characters.write_character('global', 'marketer', 'sells things', 'You sell.',
                               skills='dataviz')
    session = {'character': {'name': 'marketer', 'scope': 'global'}}
    assert ar._session_skills(P, session) == ['dataviz']
    # a rolled-over / respawned session is the same dict -> the same set
    assert ar._session_skills(P, dict(session)) == ['dataviz']
    assert ar._session_skills(P, {}) == []                          # no persona


def test_every_claude_spawn_site_passes_character_skills():
    """A site that forgets the kwarg silently reverts that path to the full
    listing — exactly how a rolled-over session would lose its set."""
    src = (PROJECT_ROOT / 'mc' / 'blueprints' / 'agent_routes.py').read_text(encoding='utf-8')
    missing = []
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ('_build_claude_flags', '_dispatch_with_routing',
                                     '_dispatch_with_routing_parallel')):
            if not any(k.arg == 'character_skills' for k in node.keywords):
                missing.append(node.lineno)
    assert missing == []


# ── non-Claude catalog ─────────────────────────────────────────────────────

def test_non_claude_catalog_is_scoped_the_same_way(ar, monkeypatch):
    proj = dict(P, provider='codex')
    skills = [dict(s, path=f"/skills/{s['name']}/SKILL.md") for s in INSTALLED]
    monkeypatch.setattr(ar._skills, 'list_skills', lambda *a, **k: list(skills))
    full = ar._skills_catalog_block(proj)
    assert 'design review' in full                                   # unscoped baseline
    scoped = ar._skills_catalog_block(proj, ['dataviz'])
    assert 'dataviz: charts' in scoped and 'proj-local: this repo only' in scoped
    assert 'design review' not in scoped and 'steward' not in scoped.replace('mc-steward', '')
    assert '- apple-design\n  SKILL.md: /skills/apple-design/SKILL.md' in scoped
    assert ar._skills_catalog_block(proj, []) == full                # undeclared = unchanged


def test_non_claude_catalog_flag_off_is_unchanged(ar, monkeypatch):
    monkeypatch.setitem(ar.state.CONFIG, 'agent_skill_scoping_enabled', False)
    proj = dict(P, provider='codex')
    skills = [dict(s, path='/x') for s in INSTALLED]
    monkeypatch.setattr(ar._skills, 'list_skills', lambda *a, **k: list(skills))
    assert ar._skills_catalog_block(proj, ['dataviz']) == ar._skills_catalog_block(proj)


def test_tampered_scoped_file_is_rewritten(tmp_path):
    """The scoped copy carries the guardrail hooks. An edit to it (hooks
    stripped) must not survive the next launch, same as the base file's
    boot-time self-heal."""
    guard = tmp_path / 'claude-settings.json'
    guard.write_text(json.dumps({'hooks': {'PreToolUse': [{'matcher': 'Bash'}]}}), encoding='utf-8')
    ov = {'dataviz': 'name-only'}
    p = ss.scoped_settings_path(guard, ov, tmp_path / 'scoped')
    good = p.read_text(encoding='utf-8')
    p.write_text(json.dumps({'skillOverrides': ov}), encoding='utf-8')
    p2 = ss.scoped_settings_path(guard, ov, tmp_path / 'scoped')
    assert p2 == p
    assert p.read_text(encoding='utf-8') == good


def test_concurrent_first_writes_use_distinct_temp_files(tmp_path, monkeypatch):
    """Two first launches of the same combination must not share one temp
    path (one would truncate the other's half-written file)."""
    import threading
    seen = []
    real = ss.Path.write_text

    def spy(self, *a, **k):
        seen.append(self.name)
        return real(self, *a, **k)

    monkeypatch.setattr(ss.Path, 'write_text', spy)
    ts = [threading.Thread(target=ss.scoped_settings_path,
                           args=(None, {'a': 'name-only'}, tmp_path / 's')) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    tmps = [n for n in seen if n.endswith('.tmp')]
    assert tmps and len(set(tmps)) == len(tmps)
