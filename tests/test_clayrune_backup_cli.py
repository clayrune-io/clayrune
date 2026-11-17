"""tools/clayrune-backup.py exit codes (MC-1179 pass 12 TL12).

restore_backup collects per-file refusals and write failures in its report
instead of raising; the CLI used to print them and return 0.
"""
from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location('clayrune_backup_cli', ROOT / 'tools' / 'clayrune-backup.py')
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


def _archive(tmp_path, with_manifest=True):
    p = tmp_path / 'a.crbackup'
    with zipfile.ZipFile(p, 'w') as z:
        if with_manifest:
            z.writestr('manifest.json', '{"format": 1}')
        else:
            z.writestr('other.txt', 'x')
    return p


def _args(p):
    return SimpleNamespace(archive=str(p), only=None, sandbox_root=None)


def _report(errors, refused):
    return {'restored_categories': ['records'],
            'per_category': {'records': {'restored': 0, 'conflicts': 0,
                                         'refused': refused, 'errors': errors}}}


def test_restore_exits_nonzero_when_a_write_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.bk, 'announcement_for', lambda m: 'x')
    monkeypatch.setattr(cli.bk, 'restore_backup', lambda *a, **k: _report(['f: disk full'], 0))
    assert cli.cmd_restore(_args(_archive(tmp_path))) == 1


def test_restore_exits_nonzero_when_a_file_was_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.bk, 'announcement_for', lambda m: 'x')
    monkeypatch.setattr(cli.bk, 'restore_backup', lambda *a, **k: _report(['refused: y'], 1))
    assert cli.cmd_restore(_args(_archive(tmp_path))) == 1


def test_restore_clean_run_still_exits_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.bk, 'announcement_for', lambda m: 'x')
    monkeypatch.setattr(cli.bk, 'restore_backup', lambda *a, **k: _report([], 0))
    assert cli.cmd_restore(_args(_archive(tmp_path))) == 0


def test_restore_missing_manifest_is_a_refusal_not_a_traceback(tmp_path):
    assert cli.cmd_restore(_args(_archive(tmp_path, with_manifest=False))) == 1


def test_restore_not_a_zip_is_a_refusal_not_a_traceback(tmp_path):
    bad = tmp_path / 'bad.crbackup'
    bad.write_text('not a zip')
    assert cli.cmd_restore(_args(bad)) == 1


def test_create_backup_error_is_one_line_and_exit_1(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.bk, 'size_preview', lambda c: {})
    monkeypatch.setattr(cli, '_print_preview', lambda p: None)
    monkeypatch.setattr(cli.bk, '_paths', lambda: {'backup_dir': tmp_path})

    def boom(**k):
        raise cli.bk.BackupError('no space')
    monkeypatch.setattr(cli.bk, 'create_backup', boom)
    args = SimpleNamespace(exclude=None, dest=None, label=None)
    assert cli.cmd_create(args) == 1


# ── MC-1179 pass 12 second wave, TL11: vault passphrase is never an argv value ──

import pytest  # noqa: E402


def _run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, 'argv', ['clayrune-backup.py'] + argv)
    return cli.main()


@pytest.mark.parametrize('form', [['--passphrase', 'hunter2'], ['--passphrase=hunter2']])
@pytest.mark.parametrize('sub', [['export-project', 'p', '--vault', 'include'], ['import', 'a.crbackup']])
def test_old_passphrase_flag_is_refused_and_names_the_new_one(monkeypatch, capsys, sub, form):
    called = []
    monkeypatch.setattr(cli.bk, 'export_project', lambda *a, **k: called.append(k))
    monkeypatch.setattr(cli.bk, 'import_project', lambda *a, **k: called.append(k))
    with pytest.raises(SystemExit) as e:
        _run_main(monkeypatch, sub + form)
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert '--passphrase-env' in err and 'no longer accepted' in err
    assert called == []


def _export_stub(monkeypatch, seen):
    def fake(project_id, **k):
        seen.update(k)
        return {'path': 'x', 'files_written': 0, 'warnings': [], 'repo_checklist': [], 'manifest': {'vault_status': 'included'}}
    monkeypatch.setattr(cli.bk, 'export_project', fake)


def test_passphrase_env_is_read_from_the_environment(monkeypatch):
    seen = {}
    _export_stub(monkeypatch, seen)
    monkeypatch.setenv('CR_TEST_VAULT_PASS', 's3cret')
    assert _run_main(monkeypatch, ['export-project', 'p', '--vault', 'include',
                                   '--passphrase-env', 'CR_TEST_VAULT_PASS']) == 0
    assert seen['vault_passphrase'] == 's3cret'


def test_passphrase_env_unset_is_an_error_not_an_empty_passphrase(monkeypatch, capsys):
    seen = {}
    _export_stub(monkeypatch, seen)
    monkeypatch.delenv('CR_TEST_VAULT_PASS', raising=False)
    assert _run_main(monkeypatch, ['export-project', 'p', '--vault', 'include',
                                   '--passphrase-env', 'CR_TEST_VAULT_PASS']) == 1
    assert 'CR_TEST_VAULT_PASS' in capsys.readouterr().err
    assert seen == {}


def test_terminal_with_neither_given_prompts_with_getpass(monkeypatch):
    seen = {}
    _export_stub(monkeypatch, seen)
    monkeypatch.setattr(cli.sys.stdin, 'isatty', lambda: True, raising=False)
    monkeypatch.setattr(cli.getpass, 'getpass', lambda prompt='': 'typed')
    assert _run_main(monkeypatch, ['export-project', 'p', '--vault', 'include']) == 0
    assert seen['vault_passphrase'] == 'typed'


def test_no_terminal_and_no_env_passes_none_and_does_not_prompt(monkeypatch):
    seen = {}
    _export_stub(monkeypatch, seen)
    monkeypatch.setattr(cli.sys.stdin, 'isatty', lambda: False, raising=False)
    monkeypatch.setattr(cli.getpass, 'getpass', lambda prompt='': pytest.fail('prompted'))
    _run_main(monkeypatch, ['export-project', 'p', '--vault', 'include'])
    assert seen['vault_passphrase'] is None


def test_vault_omit_never_prompts(monkeypatch):
    seen = {}
    _export_stub(monkeypatch, seen)
    monkeypatch.setattr(cli.sys.stdin, 'isatty', lambda: True, raising=False)
    monkeypatch.setattr(cli.getpass, 'getpass', lambda prompt='': pytest.fail('prompted'))
    _run_main(monkeypatch, ['export-project', 'p', '--vault', 'omit'])
    assert seen['vault_passphrase'] is None
