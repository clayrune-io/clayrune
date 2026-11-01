"""Composer checks plus protected-patch verification in a throwaway tree.

No test edits the repo's builtin skill, core, guard or live configuration.
"""
import ast
import copy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
from typing import Optional

import pytest

from mc import steward_role_task as task
from mc.steward_role_model import RoleError

ROOT = Path(__file__).resolve().parents[1]
SKILL = Path('data/skills/builtin/mc-steward')
PATCHES = ROOT / 'docs' / 'patches'
NOW = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """Exercise proposed protected edits only on disposable copies."""
    tree = tmp_path / 'patch-review'
    (tree / 'steward').mkdir(parents=True)
    (tree / SKILL).mkdir(parents=True)
    shutil.copyfile(ROOT / 'steward/core.py', tree / 'steward/core.py')
    shutil.copyfile(ROOT / SKILL / 'SKILL.md', tree / SKILL / 'SKILL.md')
    for patch, applied in [
        ('steward-role-core-adapter.patch', 'from mc.steward_role_task import'),
        ('steward-role-directive.patch', '# Steward role entry point'),
    ]:
        target = tree / ('steward/core.py' if 'core-adapter' in patch else SKILL / 'SKILL.md')
        if applied in target.read_text(encoding='utf-8'):
            if 'directive' in patch:
                shutil.copyfile(ROOT / SKILL / 'CYCLE_DIRECTIVE.md', tree / SKILL / 'CYCLE_DIRECTIVE.md')
            continue  # Retain the tests after the human applies the patch.
        result = subprocess.run(['git', 'apply', '--check', str(PATCHES / patch)],
                                cwd=tree, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        result = subprocess.run(['git', 'apply', str(PATCHES / patch)], cwd=tree,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    monkeypatch.setattr(task, 'DIRECTIVE_PATH', tree / SKILL / 'CYCLE_DIRECTIVE.md')
    return tree


@pytest.fixture
def project(tmp_path):
    return {'id': 'example', 'project_path': str(tmp_path / 'canonical'),
            'steward_objective': 'stale compatibility objective',
            'backlog': [{'id': 'charter', 'text': 'Keep the docs accurate', 'status': 'open'}]}


@pytest.fixture
def snapshot():
    return task.CycleSnapshot('Example Builder', 'global:example', 'wake', 'charter',
                              'docs/_journal/charter-existing.md',
                              character_body='Speak in precise short sentences.',
                              rules_refresh='Current rule: record evidence.',
                              job_instructions='Collect outstanding builder results.',
                              delivery_context='Successor of previous-session.',
                              provider='codex', model='chosen-model', effort='high')


def test_task_order_and_current_charter(staged, project, snapshot):
    before = copy.deepcopy(project)
    output = task.build_cycle_task(project, {'id': 'wrong', 'text': 'old ask'},
                                   'PERMITTED DELTAS', cycle_snapshot=snapshot, now=NOW)
    parts = [task.STEWARD_TASK_MARKER, 'Wake (local time):', 'You are Example Builder',
             'Keep the docs accurate', 'Collect outstanding builder results.',
             'Successor of previous-session.', task.load_directive(),
             snapshot.character_body, snapshot.rules_refresh, 'PERMITTED DELTAS']
    positions = [output.index(part) for part in parts]
    assert positions == sorted(positions)
    assert output.startswith('[Steward cycle]\n\nWake')
    assert output.endswith('PERMITTED DELTAS')
    assert 'stale compatibility objective' not in output
    assert 'old ask' not in output
    assert 'Schedule: wake' in output and 'Charter backlog item id: charter' in output
    assert str(Path(project['project_path']) / 'docs/_journal/charter-existing.md') in output
    assert project == before


@pytest.mark.parametrize('provider', ['claude', 'codex', 'gemini', 'qwen', 'opencode',
                                      'goose', 'aider', 'kiro'])
def test_full_directive_is_text_for_each_engine_without_skill_discovery(
        staged, project, snapshot, provider, monkeypatch):
    # Composer never calls a runtime or reads the installed skills inventory.
    monkeypatch.setattr(Path, 'home', staticmethod(lambda: Path('/no-native-skills')))
    output = task.build_cycle_task(project, None, cycle_snapshot=replace(snapshot, provider=provider))
    assert output.count(task.load_directive()) == 1
    assert f'Resolved engine: {provider};' in output
    assert snapshot.character_body in output
    assert 'You are the autonomous STEWARD' not in output
    assert '/note' not in output


def test_marker_matches_load_bearing_consumers(staged, project, snapshot):
    from mc.distiller import STEWARD_TASK_MARKER
    from steward.fence import STEWARD_MARKER
    assert task.STEWARD_TASK_MARKER == STEWARD_TASK_MARKER == STEWARD_MARKER
    assert task.build_cycle_task(project, None, cycle_snapshot=snapshot).startswith(STEWARD_MARKER)


@pytest.mark.parametrize('status', ['done', 'cancelled', 'wontdo', 'archived'])
def test_closed_role_charter_never_falls_back(staged, project, snapshot, status):
    project['backlog'][0]['status'] = status
    with pytest.raises(RoleError, match='closed'):
        task.build_cycle_task(project, None, cycle_snapshot=snapshot)


@pytest.mark.parametrize('backlog', [[], [{'id': 'charter', 'text': ' '}],
                                      [{'id': 'charter', 'text': 'a'}, {'id': 'charter', 'text': 'b'}]])
def test_missing_empty_duplicate_role_charter(staged, project, snapshot, backlog):
    project['backlog'] = backlog
    with pytest.raises(RoleError):
        task.build_cycle_task(project, None, cycle_snapshot=snapshot)


@pytest.mark.parametrize('field,value', [
    ('character_name', ''), ('character_ref', 'missing'), ('character_ref', 'global:../bad'),
    ('schedule_id', '../bad'), ('charter_item_id', ''),
    ('journal_path', 'docs/_journal/../../bad.md'), ('journal_path', '/other/project.md'),
    ('job_instructions', 'x' * (task.MAX_JOB_INSTRUCTIONS + 1)),
])
def test_invalid_snapshot_is_visible_before_delivery(staged, project, snapshot, field, value):
    with pytest.raises(RoleError):
        task.build_cycle_task(project, None, cycle_snapshot=replace(snapshot, **{field: value}))


def test_job_instruction_bound_is_not_silent_truncation(staged, project, snapshot):
    text = 'x' * task.MAX_JOB_INSTRUCTIONS
    assert text in task.build_cycle_task(project, None,
                                         cycle_snapshot=replace(snapshot, job_instructions=text))


def test_snapshot_is_immutable(snapshot):
    with pytest.raises(FrozenInstanceError):
        snapshot.model = 'silently-switched'


def test_legacy_compatibility_and_canonical_journal(staged, project, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    output = task.build_cycle_task(project, project['backlog'][0], 'LATEST', now=NOW)
    assert 'stale compatibility objective' in output
    assert 'Keep your current character name and voice' in output
    assert str(Path(project['project_path']) / 'docs/_journal/charter-steward.md') in output
    assert output.endswith('LATEST')
    project['steward_objective'] = ''
    assert 'Keep the docs accurate' in task.build_cycle_task(project, project['backlog'][0])


@pytest.mark.parametrize('charter', [None, {'id': 'c', 'status': 'done'}, {'id': '../escape'}])
def test_legacy_missing_configuration_holds_without_crashing(staged, charter):
    output = task.build_cycle_task({'steward_objective': 'objective'}, charter)
    assert output.startswith(task.STEWARD_TASK_MARKER)
    assert 'Configuration blocker:' in output and 'Hold action' in output
    assert '/note' not in output


def test_legacy_strips_only_leading_charter_prefix(staged, project):
    project['steward_objective'] = ''
    output = task.build_cycle_task(project, {'id': 'charter', 'text':
        'STEWARD CHARTER: Discuss STEWARD CHARTER: examples'})
    assert 'Discuss STEWARD CHARTER: examples' in output


@pytest.mark.parametrize('contents', [None, '', b'\xff'])
def test_missing_empty_unreadable_directive_fails_closed(tmp_path, monkeypatch, contents):
    resource = tmp_path / 'directive.md'
    if contents is not None:
        resource.write_bytes(contents if isinstance(contents, bytes) else contents.encode())
    monkeypatch.setattr(task, 'DIRECTIVE_PATH', resource)
    with pytest.raises(RoleError, match='directive'):
        task.build_cycle_task({}, None)


def test_directive_reloaded_for_next_cycle(staged, project, snapshot):
    with task.DIRECTIVE_PATH.open('a', encoding='utf-8') as stream:
        stream.write('\nCurrent directive revision.\n')
    assert 'Current directive revision.' in task.build_cycle_task(project, None, cycle_snapshot=snapshot)


def test_canonical_directive_contains_ordered_safety_contract(staged):
    directive = task.load_directive()
    assert directive.index('check replies first') < directive.index('Choose one next step')
    for text in ['ambiguous_or_mixed', 'no_reply_found', 'untrusted data',
                 'Never write unattended backlog notes', 'spawner', 'do not email',
                 'existing human proof', 'delivery_unknown', 'daily cap',
                 'human-on-one-side', 'standing scheduler', 'self-enable', 're-arm']:
        assert text in directive
    for obsolete in ['/note', 'ensure your next wake is scheduled', 'Re-arm.']:
        assert obsolete not in directive


def test_skill_is_an_entry_to_packaged_canonical_resource(staged):
    skill = (staged / SKILL / 'SKILL.md').read_text(encoding='utf-8')
    assert '[CYCLE_DIRECTIVE.md](CYCLE_DIRECTIVE.md)' in skill
    assert 'post one `blocked` note' not in skill
    spec = ast.parse((ROOT / 'installer/build-macos.spec').read_text(encoding='utf-8'))
    entries = []
    for node in ast.walk(spec):
        if isinstance(node, ast.Tuple) and len(node.elts) == 2:
            call, destination = node.elts
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == 'R':
                if all(isinstance(a, ast.Constant) for a in call.args):
                    entries.append((tuple(a.value for a in call.args), ast.literal_eval(destination)))
    assert (('data', 'skills', 'builtin', 'mc-steward'), 'data/skills/builtin/mc-steward') in entries
    assert (staged / SKILL / 'CYCLE_DIRECTIVE.md').is_file()


def test_builtin_checksum_preserves_local_edits_without_replacing_contract(staged, tmp_path, monkeypatch):
    from mc import skills
    installed = tmp_path / 'installed-skills'
    monkeypatch.setattr(skills, 'GLOBAL_SKILLS_DIR', installed)
    source = staged / 'data/skills/builtin'
    assert skills.install_builtins(source)['installed'] == ['mc-steward']
    assert (installed / 'mc-steward/CYCLE_DIRECTIVE.md').read_text(encoding='utf-8').strip() == task.load_directive()
    (installed / 'mc-steward/SKILL.md').write_text('My edited skill', encoding='utf-8')
    assert skills.install_builtins(source)['preserved'] == ['mc-steward']
    assert (installed / 'mc-steward/SKILL.md').read_text(encoding='utf-8') == 'My edited skill'
    (installed / 'mc-steward/CYCLE_DIRECTIVE.md').write_text('stale local copy', encoding='utf-8')
    assert task.load_directive() != 'stale local copy'


def test_core_patch_keeps_positional_calls_and_forwards_snapshot(staged, project, snapshot):
    parsed = ast.parse((staged / 'steward/core.py').read_text(encoding='utf-8'))
    fn = next(n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name == 'build_cycle_task')
    namespace = {'Optional': Optional}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), '<reviewed-core-adapter>', 'exec'), namespace)
    compose = namespace['build_cycle_task']
    assert compose(project, project['backlog'][0], 'REFRESH').endswith('REFRESH')
    assert 'You are Example Builder' in compose(project, None, cycle_snapshot=snapshot)
    assert '/note' not in compose(project, None, cycle_snapshot=snapshot)
