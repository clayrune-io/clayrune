"""Desk connect slice 4 follow-ups (docs/DESK_CONNECT_BY_URL_SPEC.md, "Slice 4 as built").
Fixtures and helpers come from tests/test_desk_connect_mcp.py.

Pinned:

  * a passphrase-backed vault does not block the MCP method and no longer parks the Save
    (MC-1047 shipped the server-side streaming exec the launch line relays to): the Review
    card carries one plain notice that the server starts only while Clayrune is unlocked,
    and the Save installs and registers exactly as on a plain vault;
  * the MCP status reads `registered` only when the entry file is on disk AND the config is
    exactly our line, whatever the vault mode;
  * two Saves of one package at once are serialised where the directory is replaced;
  * the download has one TOTAL deadline, not one per read;
  * the launch line strips NODE_OPTIONS and NODE_PATH (`with-secret.py --unset`), and
    `is_ours` accepts only that line.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests.test_desk_connect_mcp import (RID, _commit_req, _draft, _notion, _ours,  # noqa: E402,F401
                                         _package_files, _servers, _vault_names, _wrap_vault, _base_env, env)

from mc.desk_connect import mcp_activation, mcp_catalogue, mcp_package_store, methods  # noqa: E402
from mc.desk_connect.providers import notion as notion_provider  # noqa: E402


def _unlocked_passphrase_vault(monkeypatch):
    """A passphrase-backed vault the human has unlocked in this (server) process."""
    from mc import secrets_store
    key = secrets_store.load_master_key()[0]
    _wrap_vault()
    monkeypatch.setattr(secrets_store, '_unlocked_key', key)


# -- 1. a passphrase-backed vault: state the limit, do not block, register ------------

def test_the_review_card_states_the_limit_on_a_passphrase_vault_and_only_then(env):
    plain = methods.inspect('Notion')
    row = next(o for o in plain['options'] if o['method'] == 'mcp')
    assert row['selectable'] is True and 'notice' not in row['connector']['install']
    _wrap_vault()
    got = methods.inspect('Notion')
    row = next(o for o in got['options'] if o['method'] == 'mcp')
    assert row['selectable'] is True                                    # not blocked
    install = row['connector']['install']
    assert 'passphrase' in install['notice'] and 'only while Clayrune is unlocked' in install['notice']
    assert 'MC-1047' not in install['notice'] and 'cannot' not in install['notice']   # nothing is "waiting" any more
    assert install['pins'] == mcp_catalogue.pins(_notion())             # the notice is not part of what is approved


def test_a_passphrase_vault_saves_installs_and_registers(env, tmp_path, monkeypatch):
    client, calls, rec, cfg = env
    _unlocked_passphrase_vault(monkeypatch)
    r = _commit_req(client, _draft())
    body = r.get_json()
    assert r.status_code == 201, body
    assert calls['n'] == 1 and _vault_names() == ['notion.token']       # passcode asked once, token kept
    assert body['setup']['state'] == 'done' and body['status']['state'] == 'registered'
    assert len(rec['fetched']) == 1 and _package_files(tmp_path) != [] and 'notion' in _servers(cfg)


# -- 2. `registered` means an agent session could start it -----------------------------

def _state():
    return notion_provider.NotionProvider().credential_state('mcp')['state']


def test_registered_needs_the_file_the_config_runs_and_the_exact_line_whatever_the_vault_mode(env, monkeypatch):
    client, calls, rec, cfg = env
    assert _commit_req(client, _draft()).get_json()['status']['state'] == 'registered'
    assert _state() == 'registered' and mcp_activation.is_registered(_notion()) is True
    _unlocked_passphrase_vault(monkeypatch)                             # the vault becomes passphrase-backed afterwards
    assert 'notion' in _servers(cfg)
    assert mcp_activation.is_registered(_notion()) is True and _state() == 'registered'
    # the package directory is removed: the config line points at nothing
    mcp_package_store.entry_path(_notion()).unlink()
    assert mcp_activation.is_registered(_notion()) is False
    assert _state() == 'setup_failed'


# -- 3. serialised provisioning, one total deadline ------------------------------------

def test_two_saves_of_the_same_package_at_once_both_install(env, tmp_path, monkeypatch):
    """Without the per-package lock the second `os.replace` lands on the directory the first
    just put there and fails; with it the second waits, replaces it, and both succeed."""
    real_replace = mcp_package_store.os.replace
    first = threading.Event()

    def slow_replace(src, dst):
        if not first.is_set():
            first.set()
            time.sleep(0.4)                     # the window the other install walks into
        return real_replace(src, dst)

    monkeypatch.setattr(mcp_package_store.os, 'replace', slow_replace)
    results, errors = [], []

    def run():
        try:
            results.append(mcp_package_store.install(_notion()))
        except Exception as e:                  # noqa: BLE001 - the test reports it
            errors.append(e)

    ts = [threading.Thread(target=run) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(20)
    assert errors == [] and len(results) == 2
    assert mcp_package_store.entry_path(_notion()).is_file()
    assert _package_files(tmp_path) == ['notion', 'notion/2.5.2', 'notion/2.5.2/package', 'notion/2.5.2/package/bin',
                                        'notion/2.5.2/package/bin/cli.mjs', 'notion/2.5.2/package/package.json']


def test_a_read_that_never_returns_is_abandoned_at_the_total_deadline(env, monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(mcp_package_store, '_fetch', lambda url, limit, timeout: release.wait(10) and b'')
    monkeypatch.setattr(mcp_package_store, 'DOWNLOAD_TIMEOUT', 0.3)
    t0 = time.monotonic()
    with pytest.raises(mcp_activation.ActivationError) as e:
        mcp_package_store.download(_notion())
    release.set()
    assert e.value.code == 'download_timeout' and time.monotonic() - t0 < 3


def test_a_slow_drip_hits_the_total_deadline_not_a_per_read_timeout(monkeypatch):
    """One byte every 0.02 s never trips a per-read socket timeout; the total deadline must."""
    class Drip:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            time.sleep(0.02)
            return b'x'

    monkeypatch.setattr(mcp_package_store.urllib.request, 'urlopen', lambda req, timeout=None: Drip())
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        mcp_package_store._fetch('https://registry.npmjs.org/x.tgz', 10_000_000, 0.3)
    assert time.monotonic() - t0 < 3


# -- 4. NODE_OPTIONS / NODE_PATH are stripped from this launch ------------------------

def test_the_launch_line_strips_node_options_and_node_path(env):
    args = mcp_activation.launch_config(_notion())['args']
    assert args[1:6] == ['--raw', '--unset', 'NODE_OPTIONS', '--unset', 'NODE_PATH']
    assert args.index('--unset') < args.index('--env') < args.index('--')


def test_is_ours_refuses_a_line_without_both_unset_flags(env):
    assert mcp_activation.is_ours(_ours(), _notion()) is True
    for drop in (('--unset', 'NODE_OPTIONS'), ('--unset', 'NODE_PATH')):
        thin = _ours()
        i = next(k for k in range(len(thin['args']) - 1) if tuple(thin['args'][k:k + 2]) == drop)
        del thin['args'][i:i + 2]
        assert mcp_activation.is_ours(thin, _notion()) is False
    old = _ours()                                  # the first slice-4 shape: no --unset at all
    del old['args'][2:6]
    assert mcp_activation.is_ours(old, _notion()) is False
