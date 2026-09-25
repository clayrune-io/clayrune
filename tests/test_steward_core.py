"""Tests for steward/core.py — config accessors, charter, cycle-task, notify
seam, fence settings, loop-health. Uses an in-memory project store injected via
configure() so nothing touches disk except the fence-settings file (tmp_path).
"""
import json

import pytest

import steward
from steward import core
from steward._config import configure


@pytest.fixture
def store(tmp_path):
    """In-memory project store wired through configure().

    configure() MUTATES the module-global steward.CFG in place, so this fixture
    must snapshot and RESTORE it — it is not a monkeypatch. Without the restore,
    CFG keeps pointing at this fixture's dead in-memory dict after the module
    finishes, and any LATER test that relies on server.py's import-time wiring
    (test_steward_routes) silently reads/writes that dead dict instead of its
    own tmp store. This was the whole cause of the 5 order-dependent
    test_steward_routes failures: they passed alone (first server import
    re-runs the wiring) and failed in the full suite (server already imported →
    wiring is a cache hit → poisoned CFG survives).
    """
    _cfg_snapshot = dict(vars(core.CFG))
    projects = {}

    def load_project(pid):
        return json.loads(json.dumps(projects[pid])) if pid in projects else None

    def save_project(pid, p):
        projects[pid] = json.loads(json.dumps(p))

    def load_projects():
        return [json.loads(json.dumps(v)) for v in projects.values()]

    def append_note(pid, item_id, text, agent_code='user'):
        p = projects.get(pid)
        if not p:
            return False
        for it in p.get('backlog', []):
            if it.get('id') == item_id:
                it.setdefault('notes', []).append(
                    {'ts': 'now', 'agent_code': agent_code, 'text': text})
                return True
        return False

    pushes = []
    configure(
        data_root=tmp_path,
        load_project_fn=load_project,
        save_project_fn=save_project,
        load_projects_fn=load_projects,
        append_note_fn=append_note,
        notify_push_fn=lambda *a, **k: pushes.append((a, k)),
        log_fn=None,
    )
    projects['p1'] = {
        'id': 'p1', 'name': 'Proj One',
        'steward_mode': 'on',
        'steward_objective': 'Keep the docs in sync with the code',
        'steward_cadence_minutes': 120,
        'backlog': [],
    }
    yield {'projects': projects, 'pushes': pushes}
    # Restore CFG exactly as we found it (wired-by-server or never-wired alike).
    vars(core.CFG).clear()
    vars(core.CFG).update(_cfg_snapshot)


def test_enabled_and_accessors(store):
    p = store['projects']['p1']
    assert core.steward_enabled(p)
    assert core.get_objective(p) == 'Keep the docs in sync with the code'
    assert core.get_cadence_minutes(p) == 120


def test_cadence_clamped(store):
    assert core.get_cadence_minutes({'steward_cadence_minutes': 5}) == core.MIN_CADENCE_MINUTES
    assert core.get_cadence_minutes({'steward_cadence_minutes': 99999}) == core.MAX_CADENCE_MINUTES
    assert core.get_cadence_minutes({}) == core.DEFAULT_CADENCE_MINUTES
    assert core.get_cadence_minutes({'steward_cadence_minutes': 'garbage'}) == core.DEFAULT_CADENCE_MINUTES


def test_disabled_default():
    assert not core.steward_enabled({'id': 'x'})
    assert not core.steward_enabled({'steward_mode': 'off'})


def test_ensure_charter_creates_once(store):
    c1 = core.ensure_charter('p1', 'Keep the docs in sync')
    assert c1 is not None
    assert c1['text'].startswith(core.CHARTER_PREFIX)
    assert c1['source'] == 'steward-charter'
    # idempotent
    c2 = core.ensure_charter('p1', 'a different objective')
    assert c2['id'] == c1['id']
    assert len(store['projects']['p1']['backlog']) == 1


def test_find_charter_by_prefix_fallback(store):
    store['projects']['p1']['backlog'] = [
        {'id': 'aa', 'text': core.CHARTER_PREFIX + 'legacy', 'source': 'dashboard'}]
    assert core.find_charter(store['projects']['p1'])['id'] == 'aa'


def test_build_cycle_task_has_marker(store):
    c = core.ensure_charter('p1', 'Keep docs synced')
    task = core.build_cycle_task(store['projects']['p1'], c)
    assert task.startswith('[Steward cycle]')
    assert c['id'] in task
    assert 'DECISION NEEDED' in task


def test_notify_appends_note_and_pushes(store):
    core.ensure_charter('p1', 'obj')
    assert core.steward_notify('p1', 'decision-needed', 'push to prod?', action='git push')
    charter = core.find_charter(store['projects']['p1'])
    last = charter['notes'][-1]['text']
    assert last.startswith('DECISION NEEDED: push to prod?')
    assert 'Action (approve to run): git push' in last
    assert len(store['pushes']) == 1  # decision-needed pushes


def test_notify_fyi_does_not_push(store):
    core.ensure_charter('p1', 'obj')
    assert core.steward_notify('p1', 'fyi', 'routine progress')
    assert len(store['pushes']) == 0  # routine FYI never pushes


def test_notify_unknown_kind_coerced_to_fyi(store):
    core.ensure_charter('p1', 'obj')
    core.steward_notify('p1', 'wat', 'body')
    charter = core.find_charter(store['projects']['p1'])
    assert charter['notes'][-1]['text'].startswith('FYI:')


def test_ensure_fence_settings_writes_hook(store):
    path = core.ensure_fence_settings()
    assert path.exists()
    content = json.loads(path.read_text(encoding='utf-8'))
    hook = content['hooks']['PreToolUse'][0]
    assert 'Bash' in hook['matcher']
    assert 'fence.py' in hook['hooks'][0]['command']
    # idempotent — second call doesn't rewrite/raise
    assert core.ensure_fence_settings() == path


def test_loop_health(store):
    core.ensure_charter('p1', 'obj')
    core.steward_notify('p1', 'decision-needed', 'approve X', action='deploy')
    core.steward_notify('p1', 'blocked', 'waiting on Y')
    h = core.loop_health()
    assert h['projects_enabled'] == 1
    assert h['decisions_pending'] == 1
    assert h['blocked'] == 1
    assert h['enabled'][0]['project_id'] == 'p1'
    assert any('decision' in a for a in h['alerts'])


# ── install_fence_to_project / remove_fence_from_project ───────────────────
# 2026-09-14, UNATTENDED_AGENT_PERMISSIONS_AUDIT §7: pinning the installer's
# existing "merge, never clobber; refuse rather than guess" contract, since
# the project-create path (mc/blueprints/project_routes.py) now calls this
# automatically for every new project and depends on both properties holding.

def test_install_fence_writes_hook_into_fresh_settings(tmp_path):
    ok = core.install_fence_to_project(str(tmp_path))
    assert ok is True
    settings_path = tmp_path / '.claude' / 'settings.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8'))
    hook = settings['hooks']['PreToolUse'][0]
    assert 'Bash' in hook['matcher']
    assert 'fence.py' in hook['hooks'][0]['command']


def test_install_fence_merges_into_existing_settings_without_clobbering(tmp_path):
    claude_dir = tmp_path / '.claude'
    claude_dir.mkdir()
    settings_path = claude_dir / 'settings.json'
    settings_path.write_text(json.dumps({
        'hooks': {'PreToolUse': [{'matcher': 'Write', 'hooks': [
            {'type': 'command', 'command': 'some-user-hook.py'}]}]},
        'permissions': {'allow': ['Bash(ls:*)']},
    }), encoding='utf-8')

    ok = core.install_fence_to_project(str(tmp_path))
    assert ok is True

    settings = json.loads(settings_path.read_text(encoding='utf-8'))
    # The user's own hook and unrelated top-level keys survive untouched.
    assert settings['permissions'] == {'allow': ['Bash(ls:*)']}
    commands = [h['command'] for e in settings['hooks']['PreToolUse'] for h in e['hooks']]
    assert 'some-user-hook.py' in commands
    assert any('fence.py' in c for c in commands)


def test_install_fence_is_idempotent_no_duplicate_entries(tmp_path):
    core.install_fence_to_project(str(tmp_path))
    core.install_fence_to_project(str(tmp_path))
    settings_path = tmp_path / '.claude' / 'settings.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8'))
    fence_entries = [e for e in settings['hooks']['PreToolUse']
                     if any('fence.py' in h['command'] for h in e['hooks'])]
    assert len(fence_entries) == 1


def test_install_fence_refuses_unparseable_settings_without_clobbering(tmp_path):
    claude_dir = tmp_path / '.claude'
    claude_dir.mkdir()
    settings_path = claude_dir / 'settings.json'
    settings_path.write_text('{not valid json', encoding='utf-8')

    ok = core.install_fence_to_project(str(tmp_path))
    assert ok is False
    # Refused, not clobbered — the broken file is untouched.
    assert settings_path.read_text(encoding='utf-8') == '{not valid json'


def test_remove_fence_leaves_other_hooks_and_settings_alone(tmp_path):
    core.install_fence_to_project(str(tmp_path))
    settings_path = tmp_path / '.claude' / 'settings.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8'))
    settings['hooks']['PreToolUse'].append(
        {'matcher': 'Write', 'hooks': [{'type': 'command', 'command': 'some-user-hook.py'}]})
    settings['permissions'] = {'allow': ['Bash(ls:*)']}
    settings_path.write_text(json.dumps(settings), encoding='utf-8')

    ok = core.remove_fence_from_project(str(tmp_path))
    assert ok is True
    after = json.loads(settings_path.read_text(encoding='utf-8'))
    commands = [h['command'] for e in after['hooks']['PreToolUse'] for h in e['hooks']]
    assert 'some-user-hook.py' in commands
    assert not any('fence.py' in c for c in commands)
    assert after['permissions'] == {'allow': ['Bash(ls:*)']}


# ── _fence_command frozen-build follow-up (MC-975, 627a4961) ────────────────
# mc/hook_entry.py + guardrail_hooks.hook_invocation_tokens() gave the Codex
# fence and process-guard a frozen-build path (f3ca2a1); the Claude-side
# steward fence built here used its own hand-rolled `"<py>" "<script>"`
# string and had the identical defect: in a PyInstaller build sys.executable
# IS the app binary, so that command would boot a second copy of the app
# instead of running fence.py.

def test_fence_command_source_install_is_byte_identical(monkeypatch):
    """Not frozen: must match the pre-fix expression exactly, unconditionally
    quoted, no --clayrune-hook token — this is the regression the brief asks
    to prove, not just "still works"."""
    monkeypatch.delattr('sys.frozen', raising=False)
    import sys as _sys
    expected = f'"{_sys.executable}" "{core.fence_script_path().as_posix()}"'
    assert core._fence_command() == expected
    assert '--clayrune-hook' not in expected


def test_fence_command_frozen_build_uses_hook_entry_flag(monkeypatch):
    monkeypatch.setattr('sys.frozen', True, raising=False)
    monkeypatch.setattr('sys.executable', 'C:/Clayrune/Clayrune.exe')
    cmd = core._fence_command()
    assert cmd == 'C:/Clayrune/Clayrune.exe --clayrune-hook fence'
    assert 'fence.py' not in cmd


def test_fence_command_frozen_build_quotes_only_paths_with_spaces(monkeypatch):
    monkeypatch.setattr('sys.frozen', True, raising=False)
    monkeypatch.setattr('sys.executable', 'C:/Program Files/Clayrune/Clayrune.exe')
    cmd = core._fence_command()
    assert cmd == '"C:/Program Files/Clayrune/Clayrune.exe" --clayrune-hook fence'


def test_is_steward_hook_entry_matches_frozen_form_too():
    """Removal must find the frozen-form command even though it carries no
    'fence.py' substring at all — the gap the brief flagged by name."""
    frozen_entry = {'hooks': [{'type': 'command',
                              'command': 'C:/Clayrune/Clayrune.exe --clayrune-hook fence'}]}
    assert core._is_steward_hook_entry(frozen_entry) is True
    source_entry = {'hooks': [{'type': 'command', 'command': '"py" "fence.py"'}]}
    assert core._is_steward_hook_entry(source_entry) is True
    other_entry = {'hooks': [{'type': 'command', 'command': 'some-user-hook.py'}]}
    assert core._is_steward_hook_entry(other_entry) is False


def test_remove_fence_leaves_other_hooks_alone_in_frozen_build(tmp_path, monkeypatch):
    """install/remove round-trip under a frozen sys.executable — the exact
    path Bram's f3ca2a1 fixed for Codex, applied to the Claude-side fence."""
    monkeypatch.setattr('sys.frozen', True, raising=False)
    monkeypatch.setattr('sys.executable', 'C:/Clayrune/Clayrune.exe')
    core.install_fence_to_project(str(tmp_path))
    settings_path = tmp_path / '.claude' / 'settings.json'
    settings = json.loads(settings_path.read_text(encoding='utf-8'))
    commands = [h['command'] for e in settings['hooks']['PreToolUse'] for h in e['hooks']]
    assert any('--clayrune-hook fence' in c for c in commands)

    ok = core.remove_fence_from_project(str(tmp_path))
    assert ok is True
    after = json.loads(settings_path.read_text(encoding='utf-8'))
    assert 'hooks' not in after


def test_public_api_exports():
    for name in ('steward_enabled', 'ensure_charter', 'build_cycle_task',
                 'steward_notify', 'ensure_fence_settings', 'loop_health',
                 'classify_bash', 'classify_action'):
        assert hasattr(steward, name)
