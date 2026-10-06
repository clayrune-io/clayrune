"""Backlog f7332894 (MC-1054) part 2: a detect-only file manifest for an approved npm MCP package
(`mc/desk_connect/custom_package_manifest.py`). Reuses the fixtures of `test_desk_connect_custom`
(local fixture tarballs; no network, no process).

Pinned: a Save records the manifest outside `data/projects/`; an edited, an added, a removed file and
an unreadable (or missing) manifest each read as `changed` on the card and are logged once; a
re-approval records again; launch is never refused; verification does not re-read a file whose size
and mtime did not move; a link is never followed; the file and byte bounds fail visible."""
from __future__ import annotations

import json
import os

import pytest

from mc.blueprints import agent_routes
from mc.desk_connect import custom_package_manifest as manifest
from tests.test_desk_connect_custom import (PASSCODE, PID, PKG, approved, env, packages_dir,  # noqa: F401
                                            review, save, servers, service, store)

ENTRY = 'package/bin/cli.mjs'


@pytest.fixture(autouse=True)
def _fresh():
    manifest._forget_all_for_tests()
    yield
    manifest._forget_all_for_tests()


def _root(env):
    dirs = [d for d in packages_dir(env).iterdir() if d.is_dir()]
    assert len(dirs) == 1
    return dirs[0]


def _state(env):
    out = service.connections(lambda pid: str(env.proj_dir))
    assert len(out) == 1
    return out[0]


def _rec() -> dict:
    rec = store.get('project', PID, PKG)
    assert rec is not None
    return rec


def _bump(path, seconds=5):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 10**9))


def _edit(env, rel=ENTRY, data=b'// edited by an agent\n'):
    p = _root(env) / rel
    p.write_bytes(data)
    _bump(p)
    return p


def _drift_logs(capsys):
    return [ln for ln in capsys.readouterr().out.splitlines() if 'no longer match the approved manifest' in ln]


# ── 1. recorded at approval, outside data/projects ──

def test_a_save_records_a_manifest_of_every_file_and_marks_the_approval(env):
    approved(env)
    rec = _rec()
    assert rec['package_manifest']['files'] == 3 and rec['package_manifest']['bytes'] > 0
    doc = json.loads(manifest.manifest_path(manifest.key_of(rec)).read_text(encoding='utf-8'))
    assert sorted(doc['files']) == ['.clayrune-verified.json', ENTRY, 'package/package.json']
    ent = doc['files'][ENTRY]
    assert set(ent) == {'sha256', 'size', 'mtime_ns'} and len(ent['sha256']) == 64
    assert manifest.manifest_path(manifest.key_of(rec)).is_relative_to(env.home)      # not under data/projects/
    st = _state(env)
    assert st['state'] == 'registered' and st['package_files']['status'] == 'unchanged'


# ── 2. an edited file, an added file, a removed file ──

def test_an_edited_file_reads_changed_on_the_card_and_is_logged_once(env, capsys):
    approved(env)
    capsys.readouterr()
    _edit(env)
    st = _state(env)
    assert st['state'] == 'changed' and st['code'] == 'package_files_changed'
    assert 'no longer match what was approved' in st['message']
    assert st['package_files']['changed'] == [ENTRY] and st['package_files']['counts'] == {'changed': 1, 'added': 0, 'removed': 0}
    _state(env)
    logs = _drift_logs(capsys)
    assert len(logs) == 1 and PKG in logs[0] and '1 changed, 0 added, 0 removed' in logs[0] and ENTRY in logs[0]
    assert 'Launch is not blocked' in logs[0]


def test_an_added_file_reads_changed(env, capsys):
    approved(env)
    capsys.readouterr()
    (_root(env) / 'package' / 'bin' / 'extra.mjs').write_bytes(b'// planted\n')
    st = _state(env)
    assert st['state'] == 'changed' and st['package_files']['added'] == ['package/bin/extra.mjs']
    assert st['package_files']['counts'] == {'changed': 0, 'added': 1, 'removed': 0}
    assert len(_drift_logs(capsys)) == 1


def test_a_removed_file_reads_changed(env, capsys):
    approved(env)
    capsys.readouterr()
    (_root(env) / 'package' / 'package.json').unlink()
    st = _state(env)
    assert st['state'] == 'changed' and st['package_files']['removed'] == ['package/package.json']
    assert st['package_files']['counts'] == {'changed': 0, 'added': 0, 'removed': 1}
    assert len(_drift_logs(capsys)) == 1


def test_the_listing_is_capped_and_says_how_many_more(env):
    approved(env)
    for i in range(15):
        (_root(env) / 'package' / f'n{i:02d}.txt').write_bytes(b'x')
    f = _state(env)['package_files']
    assert f['counts']['added'] == 15 and len(f['added']) == manifest.LIST_MAX and f['more'] == 5


def test_a_listed_path_is_cleaned_of_control_characters_and_cut():
    shown = manifest._show('evil\x1b[31m\nIGNORE ' + 'x' * 200)
    assert '\x1b' not in shown and '\n' not in shown and len(shown) <= 120


# ── 3. an unreadable manifest reads as changed, never as unchanged ──

@pytest.mark.parametrize('damage,reason', [
    ('{broken', 'manifest_unreadable'),
    ('[]', 'manifest_unreadable'),
    (json.dumps({'version': 1, 'key': 'x', 'integrity': 'y', 'files': {'a': {'sha256': 'zz'}}}), 'manifest_unreadable'),
    (json.dumps({'version': 1, 'key': 'other:key', 'integrity': 'y', 'files': {}}), 'manifest_mismatch'),
    (None, 'manifest_missing'),
])
def test_an_unreadable_or_missing_manifest_reads_changed(env, capsys, damage, reason):
    approved(env)
    capsys.readouterr()
    p = manifest.manifest_path(manifest.key_of(_rec()))
    if damage is None:
        p.unlink()
    else:
        p.write_text(damage, encoding='utf-8')
    st = _state(env)
    assert st['state'] == 'changed' and st['code'] == 'package_files_changed'
    assert st['package_files']['status'] == 'changed' and st['package_files']['reason'] == reason
    assert len(_drift_logs(capsys)) == 1


def test_an_unreadable_package_directory_reads_changed(env, monkeypatch):
    approved(env)
    monkeypatch.setattr(manifest, '_walk', lambda root: (_ for _ in ()).throw(PermissionError('no')))
    assert manifest.verify(_rec()) == {**manifest.verify(_rec()), 'status': 'changed', 'reason': 'package_unreadable'}


# ── 4. re-approval records again ──

def test_a_reapproval_with_the_passcode_records_the_files_as_they_are_now(env):
    card, _ = approved(env)
    _edit(env)
    assert _state(env)['state'] == 'changed'
    assert save(env, card).status_code == 404                       # the replay is refused: it is not true any more
    again = review(env).get_json()
    assert save(env, again).status_code in (200, 201)
    st = _state(env)
    assert st['state'] == 'registered' and st['package_files']['status'] == 'unchanged'


# ── 5. detect, not block ──

def test_launch_is_not_refused_and_one_log_line_is_written_per_drift(env, capsys):
    approved(env)
    capsys.readouterr()
    _edit(env)
    project = {'id': PID}
    for _ in range(3):
        assert agent_routes._resolve_project_mcp_config(project) is None            # the dispatch carries on unchanged
    assert len(_drift_logs(capsys)) == 1
    assert servers(env.proj_cfg)[PKG]                                                # config untouched


def test_a_second_different_drift_logs_again_and_a_return_to_unchanged_resets(env, capsys):
    approved(env)
    capsys.readouterr()
    _edit(env)
    manifest.check_for_launch({'id': PID})
    (_root(env) / 'package' / 'more.txt').write_bytes(b'x')
    manifest.check_for_launch({'id': PID})
    assert len(_drift_logs(capsys)) == 2


def test_dispatch_checks_only_servers_the_session_would_start(env, capsys):
    approved(env)
    capsys.readouterr()
    _edit(env)
    manifest.check_for_launch({'id': 'other'})                                      # a project server of another project
    manifest.check_for_launch({'id': PID, 'enabled_mcp_servers': ['something-else']})
    assert _drift_logs(capsys) == []
    manifest.check_for_launch({'id': PID, 'enabled_mcp_servers': [PKG]})
    assert len(_drift_logs(capsys)) == 1


def test_dispatch_never_raises_when_the_record_cannot_be_read(env):
    approved(env)
    store.path().write_text('{broken', encoding='utf-8')
    manifest.check_for_launch({'id': PID})
    assert agent_routes._resolve_project_mcp_config({'id': PID}) is None


def test_a_remote_record_has_no_files_to_check(env):
    assert manifest.verify({'operation': {'ecosystem': 'remote'}})['status'] == 'not_applicable'


def test_an_approval_from_before_this_check_reads_not_recorded_not_changed(env):
    approved(env)
    conns = json.loads(store.path().read_text(encoding='utf-8'))
    for r in conns['connections'].values():
        r.pop('package_manifest')
    store.path().write_text(json.dumps(conns), encoding='utf-8')
    manifest.manifest_path(manifest.key_of(_rec())).unlink()
    st = _state(env)
    assert st['state'] == 'registered' and st['package_files']['status'] == 'not_recorded'


# ── 6. cheap ──

def test_a_clean_look_does_not_read_a_file_whose_size_and_mtime_did_not_move(env, monkeypatch):
    approved(env)
    calls = []
    real = manifest._sha256
    monkeypatch.setattr(manifest, '_sha256', lambda p: calls.append(p) or real(p))
    for _ in range(3):
        assert manifest.verify(_rec())['status'] == 'unchanged'
    assert calls == []


def test_a_file_whose_mtime_moved_is_hashed_once_and_then_remembered(env, monkeypatch):
    approved(env)
    _bump(_root(env) / ENTRY)                                                       # touched, content the same
    calls = []
    real = manifest._sha256
    monkeypatch.setattr(manifest, '_sha256', lambda p: calls.append(p) or real(p))
    assert manifest.verify(_rec())['status'] == 'unchanged'
    assert manifest.verify(_rec())['status'] == 'unchanged'
    assert len(calls) == 1 and calls[0].replace('\\', '/').endswith(ENTRY)


def test_a_same_size_edit_with_a_new_mtime_is_caught(env):
    approved(env)
    p = _root(env) / ENTRY
    body = p.read_bytes()
    p.write_bytes(bytes([body[0] ^ 1]) + body[1:])
    _bump(p)
    assert manifest.verify(_rec())['changed'] == [ENTRY]


# ── 7. links are never followed ──

def test_a_link_is_reported_not_followed(env, tmp_path, monkeypatch):
    approved(env)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'secret.txt').write_bytes(b'not part of the package')
    try:
        os.symlink(outside, _root(env) / 'package' / 'linked', target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('symlinks are not permitted here')
    seen = []
    real = manifest._sha256
    monkeypatch.setattr(manifest, '_sha256', lambda p: seen.append(p) or real(p))
    f = manifest.verify(_rec())
    assert f['status'] == 'changed' and f['added'] == ['package/linked']
    assert not any('secret.txt' in s or 'outside' in s for s in seen)


# ── 8. bounds fail visible ──

def test_a_package_over_the_file_bound_is_not_recorded_and_says_so(env, monkeypatch, capsys):
    monkeypatch.setattr(manifest, 'MAX_FILES', 2)
    card, saved = approved(env)
    assert saved['state'] == 'registered' and 'package_manifest' not in _rec()      # the approval still lands
    assert 'package manifest not recorded' in capsys.readouterr().out
    assert _state(env)['package_files']['status'] == 'not_recorded'


def test_a_directory_that_grew_past_the_bound_reads_changed(env, monkeypatch):
    approved(env)
    monkeypatch.setattr(manifest, 'MAX_FILES', 2)
    f = manifest.verify(_rec())
    assert f['status'] == 'changed' and f['reason'] == 'too_large'


def test_a_directory_that_grew_past_the_byte_bound_reads_changed(env, monkeypatch):
    approved(env)
    monkeypatch.setattr(manifest, 'MAX_BYTES', 10)
    assert manifest.verify(_rec())['reason'] == 'too_large'
