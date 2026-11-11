"""Operator keygen: throwaway keys only, no network or live configuration."""
from __future__ import annotations

import base64
import importlib.util
import io
import os
from pathlib import Path
import re
import stat

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('agent_mail_keygen', REPO / 'tools/agent-mail-keygen.py')
keygen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(keygen)
from agent_mail_key_files import key_path_in_checkout, write_new_private_file  # noqa: E402

sign_spec = importlib.util.spec_from_file_location('keygen_signer', REPO / 'tools/agent-mail-manifest-sign.py')
signer = importlib.util.module_from_spec(sign_spec)
sign_spec.loader.exec_module(signer)


def run(folder):
    out, err = io.StringIO(), io.StringIO()
    code = keygen.run(['--directory', str(folder)], stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def public(key):
    return base64.urlsafe_b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).rstrip(b'=').decode()


def test_keys_match_public_files_and_service_pkcs8_contract(tmp_path):
    folder = tmp_path / 'keys with spaces'
    code, out, err = run(folder)
    assert code == 0 and not err
    assert set(p.name for p in folder.iterdir()) == set(keygen.FILENAMES)
    keys = [signer.load_root_key(str(folder / (name + '.key'))) for name in ('root', 'service')]
    assert all(isinstance(key, Ed25519PrivateKey) for key in keys)
    assert public(keys[0]) != public(keys[1])
    for name, key in zip(('root', 'service'), keys):
        encoded = (folder / (name + '.key')).read_text().strip()
        raw = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))
        assert len(raw) != 32  # service import requires PKCS#8 DER, not a seed
        assert isinstance(serialization.load_der_private_key(raw, None), Ed25519PrivateKey)
        assert (folder / (name + '.pub')).read_text().strip() == public(key)
        assert public(key) in out
        assert encoded not in out + err
        seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                 serialization.NoEncryption())
        assert base64.urlsafe_b64encode(seed).rstrip(b'=').decode() not in out + err
    assert "< '" in out and 'keys with spaces/service.key' in out
    assert 'secret put MAIL_SERVICE_PRIVATE_KEY --config' in out
    assert 'MANIFEST_FILE' not in out
    assert f'--service-public-key={public(keys[1])}' in out
    document = signer.sign_manifest(signer.build_payload({
        'origin': 'https://mail.example.net', 'sequence': '1',
        'issued': '2026-11-11T00:00:00Z', 'activate': '2026-11-11T00:00:00Z',
        'expires': '2026-12-11T00:00:00Z', 'retire': '2027-01-11T00:00:00Z',
        'key_id': 'mail-service-v1', 'service_public_key': public(keys[1]),
    }), keys[0])
    assert document['manifest'][7] == public(keys[1])


@pytest.mark.parametrize('name', keygen.FILENAMES)
def test_existing_output_refuses_entire_operation_without_changing_it(tmp_path, name):
    folder = tmp_path / 'keys'
    folder.mkdir()
    (folder / name).write_bytes(b'do not touch')
    code, out, err = run(folder)
    assert code == 2 and not out and 'no files overwritten' in err
    assert list(folder.iterdir()) == [folder / name]
    assert (folder / name).read_bytes() == b'do not touch'


@pytest.mark.parametrize('git_entry', ['file', 'directory'])
def test_other_checkout_and_worktree_refused_before_mkdir(tmp_path, git_entry):
    repo = tmp_path / 'repo'
    repo.mkdir()
    if git_entry == 'file':
        (repo / '.git').write_text('gitdir: elsewhere')
    else:
        (repo / '.git').mkdir()
    code, out, err = run(repo / 'nested/keys')
    assert code == 2 and not out and 'git checkout' in err
    assert not (repo / 'nested').exists()


def test_own_checkout_refused_and_guard_reused():
    assert signer._key_path_in_checkout is keygen.key_path_in_checkout is key_path_in_checkout
    code, out, err = run(REPO / '_scratch/never-create-operator-keys')
    assert code == 2 and not out and 'git checkout' in err
    assert not (REPO / '_scratch/never-create-operator-keys').exists()


def test_default_is_human_home_not_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(keygen.Path, 'home', lambda: tmp_path)
    args = keygen._parser().parse_args([])
    assert Path(args.directory) == tmp_path / '.clayrune/agent-mail-keys'


def test_second_run_keeps_all_key_bytes(tmp_path):
    folder = tmp_path / 'keys'
    assert run(folder)[0] == 0
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    assert run(folder)[0] == 2
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


def test_write_failure_stops_and_reports_partial_files_without_secrets(tmp_path, monkeypatch):
    def refuse(path, data):
        raise OSError('private bytes must never escape')
    monkeypatch.setattr(keygen, 'write_new_private_file', refuse)
    code, out, err = run(tmp_path / 'keys')
    assert code == 2 and not out and 'partial files may remain' in err
    assert 'private bytes must never escape' not in err


def test_exclusive_writer_never_truncates(tmp_path):
    path = tmp_path / 'key'
    write_new_private_file(path, b'first')
    with pytest.raises(FileExistsError):
        write_new_private_file(path, b'second')
    assert path.read_bytes() == b'first'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX mode bits; Windows DACL tested separately')
def test_posix_files_owner_only_at_creation(tmp_path):
    folder = tmp_path / 'keys'
    assert run(folder)[0] == 0
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in folder.iterdir())


@pytest.mark.skipif(os.name != 'nt', reason='native Windows ACL check')
def test_windows_files_have_protected_owner_only_dacl(tmp_path):
    import ctypes
    from ctypes import wintypes
    folder = tmp_path / 'keys'
    assert run(folder)[0] == 0
    advapi, kernel = ctypes.WinDLL('advapi32'), ctypes.WinDLL('kernel32')
    get = advapi.GetNamedSecurityInfoW
    get.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
                    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                    ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    get.restype = wintypes.DWORD
    convert = advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                        ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
    convert.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    for path in folder.iterdir():
        descriptor, text = ctypes.c_void_p(), wintypes.LPWSTR()
        assert get(str(path), 1, 4, None, None, None, None, ctypes.byref(descriptor)) == 0
        try:
            assert convert(descriptor, 1, 4, ctypes.byref(text), None)
            assert text.value == 'D:P(A;;FA;;;OW)'
        finally:
            kernel.LocalFree(text)
            kernel.LocalFree(descriptor)


def test_symlink_escape_and_broken_output_are_refused(tmp_path):
    repo = tmp_path / 'repo'
    (repo / '.git').mkdir(parents=True)
    alias = tmp_path / 'alias'
    try:
        alias.symlink_to(repo, target_is_directory=True)
    except OSError:
        pytest.skip('symlink privilege unavailable')
    assert run(alias / 'keys')[0] == 2
    assert not (repo / 'keys').exists()
    folder = tmp_path / 'keys'
    folder.mkdir()
    (folder / 'service.key').symlink_to(tmp_path / 'missing')
    assert run(folder)[0] == 2
    assert not (folder / 'root.key').exists()


def _runbook_python():
    doc = (REPO / 'docs/AGENT_MAIL_OPERATOR_SETUP.md').read_text(encoding='utf-8')
    # Strip Markdown's list indentation, preserving the Python body's own indentation.
    return re.findall(r"<<'PY'\n(.*?)\n    PY", doc, flags=re.S)


def test_runbook_timestamp_command_matches_signer_parser(monkeypatch, capsys):
    snippets = _runbook_python()
    assert len(snippets) == 2
    monkeypatch.setattr('sys.argv', ['-', '30', '60'])
    exec(compile('\n'.join(line[4:] for line in snippets[0].splitlines()), '<runbook-times>', 'exec'), {})
    values = dict(zip(('ISSUED', 'EXPIRES', 'RETIRE'), capsys.readouterr().out.split()))
    times = {key: int(signer.parse_time_ms(value, key.lower())) for key, value in values.items()}
    assert times['EXPIRES'] - times['ISSUED'] == 30 * 86400000
    assert times['RETIRE'] - times['ISSUED'] == 60 * 86400000


def test_runbook_var_command_preserves_toml_sections_flags_and_exact_document(tmp_path, monkeypatch):
    import json
    tomllib = pytest.importorskip('tomllib')
    path = tmp_path / 'wrangler.toml'
    path.write_bytes((REPO / 'workers/agent-mail/wrangler.toml.example').read_bytes())
    before = tomllib.loads(path.read_text())
    manifest = tmp_path / 'manifest.json'
    # Public fixture only; this test checks transport encoding, not signature validity.
    document = {'manifest': ['clayrune-mail-service-manifest', '1', 'https://mail.example.net'],
                'signature': 'public-fixture'}
    manifest.write_text(json.dumps(document, separators=(',', ':')) + '\n')
    code = compile('\n'.join(line[4:] for line in _runbook_python()[1].splitlines()), '<runbook-var>', 'exec')
    monkeypatch.setattr('sys.argv', ['-', str(manifest), str(path)])
    for _ in range(2):  # Renewal updates in place without duplicating or moving the var.
        exec(code, {})
        after = tomllib.loads(path.read_text())
        assert json.loads(after['vars'].pop('MAIL_SERVICE_MANIFEST')) == document
        assert after == before
