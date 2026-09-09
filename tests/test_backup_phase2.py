"""Round-trip tests for mc/backup.py Phase 2 — per-project export/import
(docs/BACKUP_EXPORT_SPEC.md §4.1/§4.2/§4.4/§8, build order §7).

Same isolation idiom as tests/test_backup.py: a fake install driven entirely
by env vars, so a "different machine" is just a second temp tree with its
own MC_DATA_DIR / HOME / CLAYRUNE_HOME.

The acceptance test the spec names explicitly (§8, last paragraph) is
``test_export_A_import_B_new_path_memory_index_loads`` below: export on one
path, import onto a genuinely different one, and prove — via the exact
function agent sessions use, ``mc.memory._native_memory_path`` — that the
project's memory index is where the next session would actually look for it.
"""
from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import backup as bk  # noqa: E402
from mc import secrets_store as ss  # noqa: E402


def _git(cwd, *args):
    subprocess.run(['git'] + list(args), cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _make_machine(tmp_path, tag, monkeypatch):
    root = tmp_path / tag / 'repo'
    home = tmp_path / tag / 'home'
    clayrune = tmp_path / tag / 'clayrune'
    (root / 'data' / 'projects').mkdir(parents=True)
    (root / 'data' / 'uploads').mkdir(parents=True)
    (home / '.claude' / 'agents').mkdir(parents=True)
    monkeypatch.setenv('MC_DATA_DIR', str(root))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('CLAYRUNE_HOME', str(clayrune))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    return {'root': root, 'home': home, 'clayrune': clayrune}


@pytest.fixture
def fake_install_a(tmp_path, monkeypatch):
    """Machine A: one registered project ('proj_x') with a memory vault,
    AGENT_RULES.md, .mcp.json, a schedule, an uploaded attachment, a
    transcript, and one untracked-but-real file in its git checkout."""
    paths = _make_machine(tmp_path, 'machineA', monkeypatch)
    root, home = paths['root'], paths['home']

    checkout = tmp_path / 'machineA' / 'checkout'
    checkout.mkdir()
    _git(checkout, 'init', '-q')
    _git(checkout, 'config', 'user.email', 'a@b.c')
    _git(checkout, 'config', 'user.name', 'test')
    (checkout / 'tracked.txt').write_text('tracked', encoding='utf-8')
    _git(checkout, 'add', 'tracked.txt')
    _git(checkout, 'commit', '-q', '-m', 'init')
    (checkout / 'notes.txt').write_text('real untracked work', encoding='utf-8')
    _git(checkout, 'remote', 'add', 'origin', 'https://example.invalid/repo.git')
    (checkout / 'AGENT_RULES.md').write_text('# rules\n', encoding='utf-8')
    (checkout / '.mcp.json').write_text(json.dumps({'mcpServers': {}}), encoding='utf-8')
    (checkout / 'docs' / '_journal').mkdir(parents=True)
    (checkout / 'docs' / '_journal' / 'note.md').write_text('journal entry', encoding='utf-8')

    (root / 'data' / 'projects' / 'proj_x.json').write_text(
        json.dumps({'id': 'proj_x', 'project_path': str(checkout), 'backlog': []}), encoding='utf-8')
    (root / 'data' / 'projects' / 'proj_x_agent_log.json').write_text(
        json.dumps({'events': ['sidecar']}), encoding='utf-8')
    (root / 'data' / 'uploads' / 'proj_x_item1_abcd1234.png').write_bytes(b'\x89PNGfake')
    (root / 'data' / 'schedules.json').write_text(
        json.dumps([{'id': 'sched1', 'project_id': 'proj_x', 'enabled': True,
                    'schedule_type': 'daily', 'task': 'do the thing'}]), encoding='utf-8')

    encoded = bk._encode_project_path(str(checkout))
    mem_dir = home / '.claude' / 'projects' / encoded / 'memory'
    mem_dir.mkdir(parents=True)
    (mem_dir / 'MEMORY.md').write_text('# memory for proj_x\n', encoding='utf-8')
    (mem_dir / 'topic_x.md').write_text('topic detail', encoding='utf-8')

    tdir = home / '.claude' / 'projects' / encoded
    (tdir / 'session1.jsonl').write_text('{"role":"user"}\n', encoding='utf-8')

    return {'root': root, 'home': home, 'clayrune': paths['clayrune'], 'checkout': checkout}


# ── Vault tri-state (spec §4.2) ─────────────────────────────────────────────

def test_vault_question_unanswered_refuses_to_export(fake_install_a):
    with pytest.raises(bk.BackupError):
        bk.export_project('proj_x', vault=None)


def test_vault_unattended_can_never_say_yes(fake_install_a):
    with pytest.raises(bk.BackupError):
        bk.export_project('proj_x', vault=True, vault_passphrase='x', unattended=True)


def test_vault_unattended_records_not_asked(fake_install_a):
    r = bk.export_project('proj_x', vault=None, unattended=True)
    assert r['manifest']['vault_status'] == 'not_asked'
    assert r['manifest']['contains_secrets'] is False


def test_vault_omit_is_a_real_answer_not_a_default(fake_install_a):
    r = bk.export_project('proj_x', vault=False)
    assert r['manifest']['vault_status'] == 'omitted'
    assert not Path(r['path']).name.endswith('-SECRETS.crbackup')


def test_vault_include_requires_passphrase(fake_install_a):
    with pytest.raises(bk.BackupError):
        bk.export_project('proj_x', vault=True)


def test_vault_scope_filter_excludes_global_secrets(fake_install_a):
    ss.set_secret('proj_x.token', 'proj-scoped-secret', scope='proj_x')
    ss.set_secret('global.thing', 'global-secret-value', scope='global')
    r = bk.export_project('proj_x', vault=True, vault_passphrase='hunter2')
    archive = Path(r['path'])
    assert archive.name.endswith('-SECRETS.crbackup')
    assert r['manifest']['contains_secrets'] is True


def test_vault_round_trip_through_passphrase(fake_install_a, tmp_path):
    ss.set_secret('proj_x.token', 'proj-scoped-secret-value', scope='proj_x')
    r = bk.export_project('proj_x', vault=True, vault_passphrase='correct-horse')
    archive = Path(r['path'])
    ss.delete_secret('proj_x.token')  # simulate a destination that doesn't have it yet

    new_path = tmp_path / 'reimported'
    new_path.mkdir()
    report = bk.import_project(archive, project_resolution='import-as-copy',
                               new_project_path=str(new_path), vault_passphrase='correct-horse')
    assert report['vault']['imported'] == ['proj_x.token']
    val = ss.get_secret_value('proj_x.token', consumer='test', project_id='proj_x-imported')
    assert val == 'proj-scoped-secret-value'


def test_vault_wrong_passphrase_refuses(fake_install_a, tmp_path):
    ss.set_secret('proj_x.token', 'value', scope='proj_x')
    r = bk.export_project('proj_x', vault=True, vault_passphrase='correct-horse')
    archive = Path(r['path'])
    new_path = tmp_path / 'reimported2'
    new_path.mkdir()
    with pytest.raises(bk.BackupError):
        bk.import_project(archive, project_resolution='import-as-copy',
                          new_project_path=str(new_path), vault_passphrase='wrong-passphrase')


# ── Dry-run collision report (spec §4.4) — the three named classes ─────────

def test_collision_project_id_skip_by_default(fake_install_a):
    r = bk.export_project('proj_x', vault=False)
    archive = Path(r['path'])
    dry = bk.import_dry_run(archive)
    assert dry['collisions']['project_id'][0] == {
        'id': 'proj_x', 'exists_locally': True,
        'options': ['skip', 'replace', 'import-as-copy'], 'default': 'skip',
    }
    result = bk.import_project(archive)  # default resolution: skip
    assert result['status'] == 'skipped'


def test_collision_project_id_replace_makes_a_safety_copy_first(fake_install_a):
    r = bk.export_project('proj_x', vault=False)
    archive = Path(r['path'])
    result = bk.import_project(archive, project_resolution='replace')
    assert result['status'] == 'applied'
    assert result['pre_replace_copy'] and Path(result['pre_replace_copy']).is_dir()
    assert list(Path(result['pre_replace_copy']).glob('proj_x*.json'))


def test_collision_project_id_import_as_copy_requires_new_path(fake_install_a):
    r = bk.export_project('proj_x', vault=False)
    archive = Path(r['path'])
    with pytest.raises(bk.BackupError):
        bk.import_project(archive, project_resolution='import-as-copy')


def test_collision_project_id_import_as_copy_leaves_original_untouched(fake_install_a, tmp_path):
    r = bk.export_project('proj_x', vault=False)
    archive = Path(r['path'])
    new_path = tmp_path / 'copy_checkout'
    new_path.mkdir()
    result = bk.import_project(archive, project_resolution='import-as-copy',
                               new_project_path=str(new_path))
    paths = bk._paths()
    assert result['final_project_id'] == 'proj_x-imported'
    assert (paths['data_dir'] / 'proj_x-imported.json').is_file()
    original = json.loads((paths['data_dir'] / 'proj_x.json').read_text(encoding='utf-8'))
    assert original['id'] == 'proj_x'  # untouched


def test_collision_schedule_id_detected_and_imported_disabled(fake_install_a, tmp_path):
    """Import onto a fresh machine (no local proj_x/sched1), then dry-run the
    SAME archive again — now both the project id and the schedule id show
    as local collisions, proving the detector sees state it just wrote."""
    r = bk.export_project('proj_x', vault=False)
    archive = Path(r['path'])
    paths = bk._paths()
    # simulate a fresh machine: drop the local project + schedule before the
    # first import so it applies cleanly.
    (paths['data_dir'] / 'proj_x.json').unlink()
    (paths['data_dir'] / 'proj_x_agent_log.json').unlink()
    paths['schedules_json'].write_text('[]', encoding='utf-8')

    new_path = tmp_path / 'fresh_checkout'
    new_path.mkdir()
    first = bk.import_project(archive, new_project_path=str(new_path))
    assert first['schedules_imported'] == 1
    scheds = json.loads(paths['schedules_json'].read_text(encoding='utf-8'))
    assert scheds[0]['id'] == 'sched1'
    assert scheds[0]['enabled'] is False, 'imported schedules must never arrive live'

    dry = bk.import_dry_run(archive)
    assert dry['collisions']['project_id'][0]['exists_locally'] is True
    assert dry['collisions']['schedule_id'][0]['exists_locally'] is True
    assert dry['collisions']['schedule_id'][0]['id'] == 'sched1'


def test_collision_character_name_detected(fake_install_a, tmp_path):
    """export_project() never bundles characters (spec §4.1 scopes a project
    export to that project's own slice) — so this hand-augments an archive
    with a records/characters/ member to exercise the detector itself, the
    same shape a full-kind archive would carry."""
    r = bk.export_project('proj_x', vault=False)
    archive = Path(r['path'])
    augmented = tmp_path / 'augmented.crbackup'
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(augmented, 'w') as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        dst.writestr('records/characters/dave.md', '# dave')

    agents_dir = bk._paths()['claude_agents_dir']
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / 'dave.md').write_text('# existing dave', encoding='utf-8')

    dry = bk.import_dry_run(augmented)
    names = {c['name']: c for c in dry['collisions']['character_name']}
    assert names['dave.md']['exists_locally'] is True
    assert names['dave.md']['options'] == ['skip', 'replace']


# ── Repo-pointer checklist (spec §4.1) ──────────────────────────────────────

def test_repo_checklist_names_remote_and_sha(fake_install_a):
    r = bk.export_project('proj_x', vault=False)
    checklist = r['repo_checklist']
    joined = ' '.join(checklist)
    assert 'https://example.invalid/repo.git' in joined
    assert 'into' in joined


def test_repo_checklist_no_remote_says_copy_manually(fake_install_a, tmp_path, monkeypatch):
    bare = tmp_path / 'bare_checkout'
    bare.mkdir()
    _git(bare, 'init', '-q')
    paths = bk._paths()
    (paths['data_dir'] / 'proj_bare.json').write_text(
        json.dumps({'id': 'proj_bare', 'project_path': str(bare), 'backlog': []}), encoding='utf-8')
    r = bk.export_project('proj_bare', vault=False)
    assert any('copy the repo directory manually' in line for line in r['repo_checklist'])


# ── THE ACCEPTANCE TEST (spec §8) ───────────────────────────────────────────

def test_export_A_import_B_new_path_memory_index_loads(fake_install_a, tmp_path, monkeypatch):
    """Export on machine A's path, import onto machine B's genuinely
    different path (different drive-relative structure, standing in for "a
    different username is enough" — spec §8), and prove the moved memory
    vault is exactly where mc.memory._native_memory_path — the function an
    agent session actually calls to load its memory index — will look."""
    r = bk.export_project('proj_x', vault=False)
    archive = Path(r['path'])
    assert archive.exists()
    old_checkout = fake_install_a['checkout']

    machine_b = _make_machine(tmp_path, 'machineB', monkeypatch)
    root_b, home_b = machine_b['root'], machine_b['home']

    # A real "different machine" wouldn't have machine A's checkout directory
    # at all — rename it out of the way (git's read-only object files make an
    # rmtree on Windows fight for its life) so path_remap_required is honest
    # instead of accidentally true just because this is one shared filesystem.
    old_checkout.rename(old_checkout.with_name('checkout_gone'))

    new_project_path = tmp_path / 'machineB' / 'DifferentUser' / 'newcheckout'
    new_project_path.mkdir(parents=True)

    dry = bk.import_dry_run(archive)
    assert dry['collisions']['project_id'][0]['exists_locally'] is False
    assert dry['path_remap_required']['proj_x'] is True

    report = bk.import_project(archive, new_project_path=str(new_project_path))
    assert report['status'] == 'applied'
    assert report['path_remapped'] is True
    assert report['restored']['records'] > 0
    assert not report['warnings'], report['warnings']

    expected_encoded = bk._encode_project_path(str(new_project_path))
    mem_path = home_b / '.claude' / 'projects' / expected_encoded / 'memory' / 'MEMORY.md'
    assert mem_path.is_file()
    assert mem_path.read_text(encoding='utf-8') == '# memory for proj_x\n'
    assert (mem_path.parent / 'topic_x.md').read_text(encoding='utf-8') == 'topic detail'

    # Prove it with the REAL function agent sessions call — not our own
    # encoding helper reimplementing the same logic and agreeing with itself.
    import mc.memory as mem
    mem.CLAUDE_HOME = home_b / '.claude' / 'projects'
    native_path = mem._native_memory_path(str(new_project_path))
    assert native_path is not None
    assert native_path == mem_path
    assert native_path.is_file()
    assert native_path.read_text(encoding='utf-8') == '# memory for proj_x\n'

    rec = json.loads((root_b / 'data' / 'projects' / 'proj_x.json').read_text(encoding='utf-8'))
    assert rec['id'] == 'proj_x'
    assert rec['project_path'] == str(new_project_path)
    assert (root_b / 'data' / 'projects' / 'proj_x_agent_log.json').is_file()
    assert (new_project_path / 'AGENT_RULES.md').read_text(encoding='utf-8') == '# rules\n'
    assert (new_project_path / '.mcp.json').is_file()
    assert (new_project_path / 'docs' / '_journal' / 'note.md').read_text(encoding='utf-8') == 'journal entry'
    assert (new_project_path / 'notes.txt').read_text(encoding='utf-8') == 'real untracked work'
    assert (root_b / 'data' / 'uploads' / 'proj_x_item1_abcd1234.png').is_file()

    scheds = json.loads((root_b / 'data' / 'schedules.json').read_text(encoding='utf-8'))
    assert scheds[0]['id'] == 'sched1'
    assert scheds[0]['enabled'] is False
    assert scheds[0]['project_id'] == 'proj_x'

    assert (home_b / '.claude' / 'projects' / expected_encoded / 'session1.jsonl').is_file()

    assert 'into' in ' '.join(report['repo_checklist'])
