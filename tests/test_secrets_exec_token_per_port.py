"""The per-boot exec token file is keyed by server port.

Incident 2026-10-15: ``~/.clayrune`` is shared by every Clayrune on the box and
the token lived in ONE file, so a second server booted on :5200 overwrote the
:5199 server's token on disk while :5199 still held its old one in memory —
every same-box ``with-secret.py`` fallback then got ``bad_exec_token``. The
earlier MC-981 fix only stopped a bare ``import server`` from minting.
"""
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    monkeypatch.delenv('MC_PORT', raising=False)
    from mc import secrets_store
    monkeypatch.setattr(secrets_store, '_exec_token', None)
    return secrets_store


def test_path_is_keyed_by_port(vault):
    assert vault.exec_token_path(5199).name == 'secrets_exec_token.5199'
    assert vault.exec_token_path(5200).name == 'secrets_exec_token.5200'
    assert vault.exec_token_path(5199) != vault.exec_token_path(5200)


def test_default_port_follows_exec_route_port(vault, monkeypatch):
    monkeypatch.setenv('MC_PORT', '5333')
    assert vault.exec_token_path().name == 'secrets_exec_token.5333'


def test_two_boots_on_different_ports_do_not_clobber(vault, tmp_path):
    """Boot A on :5199 (this process), then a second process boots on :5200.
    A's file must be untouched and each reader must get its own port's token."""
    token_a = vault.ensure_exec_token(5199)
    home = tmp_path / '.clayrune'
    file_a = vault.exec_token_path(5199)
    assert file_a.read_text(encoding='utf-8').strip() == token_a

    token_b = _boot_in_subprocess_with_repo_path(home, 5200)

    assert token_a != token_b
    assert file_a.read_text(encoding='utf-8').strip() == token_a
    assert vault.exec_token_path(5200).read_text(encoding='utf-8').strip() == token_b
    assert vault.read_exec_token(5199) == token_a
    assert vault.read_exec_token(5200) == token_b


def _boot_in_subprocess_with_repo_path(home: Path, port: int) -> str:
    """A real second process minting its token, as a second server boot would."""
    code = textwrap.dedent(f'''
        import sys
        sys.path.insert(0, {str(REPO)!r})
        from mc import secrets_store as v
        print(v.ensure_exec_token({port}))
    ''')
    env = dict(os.environ, CLAYRUNE_HOME=str(home),
               CLAYRUNE_SECRETS_KEY_BACKEND='file')
    env.pop('MC_PORT', None)
    res = subprocess.run([sys.executable, '-c', code], cwd=str(REPO), env=env,
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stdout + res.stderr
    return res.stdout.strip()


def test_readers_resolve_token_by_mc_port(vault, tmp_path, monkeypatch):
    """with-secret.py / the stream client / the notify relay resolve the port
    from MC_PORT; the token must be the one matching that port."""
    home = tmp_path / '.clayrune'
    token_a = vault.ensure_exec_token(5199)
    token_b = _boot_in_subprocess_with_repo_path(home, 5200)

    monkeypatch.setenv('MC_PORT', '5199')
    assert vault._read_exec_token_file() == token_a
    monkeypatch.setenv('MC_PORT', '5200')
    assert vault._read_exec_token_file() == token_b


def test_legacy_file_is_fallback_only_when_port_file_absent(vault):
    legacy = vault.exec_token_legacy_path()
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('legacy-token', encoding='utf-8')

    # No port file: legacy is used (a pre-upgrade server still running).
    assert vault.read_exec_token(5199) == 'legacy-token'

    # Port file exists: it wins and the legacy file is never consulted.
    vault.exec_token_path(5199).write_text('port-token', encoding='utf-8')
    assert vault.read_exec_token(5199) == 'port-token'
    # ...even when it is empty: an empty port file is not "absent".
    vault.exec_token_path(5199).write_text('', encoding='utf-8')
    assert vault.read_exec_token(5199) == ''
    # A different port with no file of its own still falls back to legacy.
    assert vault.read_exec_token(5200) == 'legacy-token'


def test_nothing_writes_the_legacy_file(vault):
    vault.ensure_exec_token(5199)
    assert not vault.exec_token_legacy_path().exists()


def test_missing_everything_reads_empty(vault):
    assert vault.read_exec_token(5199) == ''


@pytest.mark.skipif(os.name == 'nt', reason='POSIX permission bits')
def test_token_file_is_0600(vault):
    vault.ensure_exec_token(5199)
    mode = stat.S_IMODE(vault.exec_token_path(5199).stat().st_mode)
    assert mode == 0o600


def test_server_boot_passes_its_port():
    """boot() must key the token by the server's own PORT (not the default)."""
    src = (REPO / 'server.py').read_text(encoding='utf-8')
    assert '_secrets_store_boot.ensure_exec_token(PORT)' in src
