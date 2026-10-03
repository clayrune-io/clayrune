"""MC-1022: add-on catalogue, manifest, request store, installer and service.

Everything here runs offline against a tiny fake archive; the real catalogued
ffmpeg is exercised in tests/test_addons_ffmpeg.py. `CLAYRUNE_HOME` is a temp
dir from tests/conftest.py, and these tests point `CLAYRUNE_ADDONS_DIR` at their
own tmp_path so nothing touches ~/.clayrune.
"""
import copy
import hashlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.addons import catalogue, installer, service  # noqa: E402
from mc.addons import manifest as mf  # noqa: E402
from mc.addons import request_store as rs  # noqa: E402
from mc.addons.manifest import AddonError, AddonInUse, AddonMissing  # noqa: E402

ROOT = 'fake-1.0/'
BIN = b'#!fake binary\n'


def _zip(extra: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(ROOT + 'bin/ffmpeg', BIN + b'ffmpeg')
        z.writestr(ROOT + 'bin/ffprobe', BIN + b'ffprobe')
        z.writestr(ROOT + 'LICENSE.txt', 'GPL')
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return buf.getvalue()


def _entry(blob: bytes, key: str | None = None) -> dict:
    return {
        'id': 'ffmpeg', 'name': 'FFmpeg', 'description': 'Joins clips.', 'homepage': 'https://ffmpeg.org',
        'licence': 'GPL-3.0-or-later', 'version': '1.0', 'binaries': ['ffmpeg', 'ffprobe'],
        'probe': {'binary': 'ffmpeg', 'args': ['-version'], 'expect': 'ffmpeg version'},
        'source': 'test', 'source_host': 'example.test', 'last_verified': '2026-10-03',
        'system_binaries': ['ffmpeg', 'ffprobe'], 'commands': {'windows': 'w', 'linux': 'l', 'macos': 'brew install ffmpeg'},
        'platforms': {key or catalogue.platform_key(): {
            'url': 'https://example.test/ffmpeg.zip', 'sha256': hashlib.sha256(blob).hexdigest(), 'archive': 'zip',
            'download_bytes': len(blob), 'installed_bytes': 1000, 'archive_root': ROOT,
            'binary_paths': {'ffmpeg': 'bin/ffmpeg', 'ffprobe': 'bin/ffprobe'}, 'extra_files': ['LICENSE.txt']}},
    }


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_ADDONS_DIR', str(tmp_path / 'addons'))
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path))
    monkeypatch.setattr(mf, '_holds', {})
    monkeypatch.setattr(service, '_progress', {})
    monkeypatch.setattr(service, '_inflight', set())
    monkeypatch.setattr(service, '_resume_hooks', [])
    return tmp_path


@pytest.fixture()
def fake(home, monkeypatch):
    """A catalogue holding one fake ffmpeg whose download is an in-memory zip."""
    blob = _zip()
    entry = _entry(blob)
    cat = home / 'catalogue.json'
    cat.write_text(json.dumps({'schema': 1, 'addons': [entry]}), encoding='utf-8')
    monkeypatch.setattr(catalogue, 'CATALOGUE_PATH', cat)
    monkeypatch.setattr(installer, '_open_url', lambda url: io.BytesIO(blob))
    monkeypatch.setattr(installer, 'probe_version', lambda b, probe: 'ffmpeg version fake')
    ran = []
    monkeypatch.setattr(service, '_spawn', lambda fn: (ran.append(1), fn()))
    return entry, blob, ran


# ── catalogue ───────────────────────────────────────────────────────────────

def test_shipped_catalogue_is_valid_and_osi_only():
    cat = catalogue.load()
    assert 'ffmpeg' in cat
    for e in cat.values():
        assert e['licence'] in catalogue.OSI_LICENCES
        assert 'ffplay' not in e['binaries']
        for p in e['platforms'].values():
            assert re.fullmatch(r'[0-9a-f]{64}', p['sha256'])
            assert p['url'].startswith('https://')


def test_shipped_ffmpeg_pins_are_month_end_btbn_gpl_tags():
    e = catalogue.load()['ffmpeg']
    assert e['licence'].startswith('GPL')
    for p in e['platforms'].values():
        assert '-gpl-' in p['url'] and '/releases/download/autobuild-' in p['url']
        assert re.fullmatch(r'autobuild-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}', p['release_tag'])


@pytest.mark.parametrize('licence', ['Remotion-License', 'Proprietary', 'BUSL-1.1', ''])
def test_catalogue_refuses_non_osi_licences(licence):
    bad = _entry(_zip())
    bad['licence'] = licence
    with pytest.raises(catalogue.CatalogueError):
        catalogue.validate_entry(bad)


def test_catalogue_refuses_ffplay_and_unsafe_paths_and_bad_hash():
    for mutate in (
        lambda e: e['binaries'].append('ffplay'),
        lambda e: e['platforms'][catalogue.platform_key()]['binary_paths'].update(ffmpeg='../evil'),
        lambda e: e['platforms'][catalogue.platform_key()].update(sha256='abc'),
        lambda e: e['platforms'][catalogue.platform_key()].update(url='http://insecure.test/x.zip'),
    ):
        e = copy.deepcopy(_entry(_zip()))
        mutate(e)
        with pytest.raises(catalogue.CatalogueError):
            catalogue.validate_entry(e)


def test_agpl_is_flagged_on_the_card_view():
    assert catalogue.licence_flag('AGPL-3.0-only')
    assert not catalogue.licence_flag('GPL-3.0-or-later')


# ── install, hash verify, manifest, invocation by absolute path ─────────────

def test_install_verifies_hash_extracts_and_records_every_binary_hash(fake, home):
    entry, blob, _ = fake
    src = catalogue.static_source(entry)
    new = installer.install_from_catalogue(entry, src, request_id='r1', requested_by=None, approved_at=mf.now_iso())
    mf.put_entry(new)
    for name in ('ffmpeg', 'ffprobe'):
        p = Path(mf.resolve(name))
        assert p.is_absolute() and p.is_file()
        assert str(p).startswith(str(home / 'addons' / 'ffmpeg' / '1.0'))
        assert new['binaries'][name]['sha256'] == hashlib.sha256(p.read_bytes()).hexdigest()
    assert not list((home / 'addons' / 'staging').glob('*'))


def test_checksum_mismatch_is_a_hard_stop_with_no_fallback(fake, home, monkeypatch):
    entry, blob, _ = fake
    opened = []
    monkeypatch.setattr(installer, '_open_url', lambda url: (opened.append(url), io.BytesIO(blob + b'x'))[1])
    with pytest.raises(installer.ChecksumMismatch):
        installer.install_from_catalogue(entry, catalogue.static_source(entry), request_id='r2',
                                         requested_by=None, approved_at=mf.now_iso())
    assert opened == ['https://example.test/ffmpeg.zip']          # one source, never a mirror
    assert not (home / 'addons' / 'ffmpeg').exists()
    assert not list((home / 'addons' / 'staging').glob('*'))
    assert mf.get_entry('ffmpeg') is None


def test_404_on_the_pinned_url_flags_repinning(fake, monkeypatch):
    import urllib.error
    entry, _, _ = fake

    def gone(url):
        raise urllib.error.HTTPError(url, 404, 'nope', {}, None)
    monkeypatch.setattr(installer, '_open_url', gone)
    with pytest.raises(installer.PinnedUrlGone):
        installer.install_from_catalogue(entry, catalogue.static_source(entry), request_id='r3',
                                         requested_by=None, approved_at=mf.now_iso())


def test_archive_member_traversal_cannot_escape_staging(home, monkeypatch):
    blob = _zip({'../../escape.txt': 'x'})
    entry = _entry(blob)
    cat = home / 'c.json'
    cat.write_text(json.dumps({'schema': 1, 'addons': [entry]}), encoding='utf-8')
    monkeypatch.setattr(catalogue, 'CATALOGUE_PATH', cat)
    monkeypatch.setattr(installer, '_open_url', lambda url: io.BytesIO(blob))
    monkeypatch.setattr(installer, 'probe_version', lambda b, p: 'ffmpeg version fake')
    new = installer.install_from_catalogue(entry, catalogue.static_source(entry), request_id='r4',
                                           requested_by=None, approved_at=mf.now_iso())
    assert not (home.parent / 'escape.txt').exists() and not (home / 'escape.txt').exists()
    assert set(new['binaries']) == {'ffmpeg', 'ffprobe'}


def test_missing_wanted_member_fails_the_install(home, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr(ROOT + 'bin/ffmpeg', b'x')
    blob = buf.getvalue()
    entry = _entry(blob)
    monkeypatch.setattr(installer, '_open_url', lambda url: io.BytesIO(blob))
    with pytest.raises(AddonError):
        installer.install_from_catalogue(entry, catalogue.static_source(entry), request_id='r5',
                                         requested_by=None, approved_at=mf.now_iso())
    assert not list((home / 'addons' / 'staging').glob('*'))


def test_tampered_binary_is_marked_broken_and_never_returned(fake, home):
    entry, _, _ = fake
    mf.put_entry(installer.install_from_catalogue(entry, catalogue.static_source(entry), request_id='r6',
                                                  requested_by=None, approved_at=mf.now_iso()))
    path = Path(mf.resolve('ffmpeg'))
    path.write_bytes(b'replaced by something else')
    with pytest.raises(AddonMissing) as ei:
        mf.resolve('ffmpeg')
    assert ei.value.status == 'broken'
    assert mf.get_entry('ffmpeg')['status'] == 'broken'
    with pytest.raises(AddonMissing):                      # stays broken until approved again
        mf.resolve('ffprobe')


def test_deleted_binary_is_missing_not_broken(fake, home):
    entry, _, _ = fake
    mf.put_entry(installer.install_from_catalogue(entry, catalogue.static_source(entry), request_id='r7',
                                                  requested_by=None, approved_at=mf.now_iso()))
    Path(mf.resolve('ffmpeg')).unlink()
    with pytest.raises(AddonMissing) as ei:
        mf.resolve('ffmpeg')
    assert ei.value.status == 'missing'


def test_resolve_of_something_never_approved_is_absent(home):
    with pytest.raises(AddonMissing) as ei:
        mf.resolve('ffmpeg')
    assert ei.value.status == 'absent'
    assert not mf.is_usable('ffmpeg')


def test_remove_deletes_files_but_refuses_while_held(fake, home):
    entry, _, _ = fake
    mf.put_entry(installer.install_from_catalogue(entry, catalogue.static_source(entry), request_id='r8',
                                                  requested_by=None, approved_at=mf.now_iso()))
    with mf.hold('ffmpeg', 'Desk render abc'):
        with pytest.raises(AddonInUse) as ei:
            mf.remove('ffmpeg')
        assert 'Desk render abc' in str(ei.value)
    out = mf.remove('ffmpeg')
    assert out['deleted_files'] is True
    assert not (home / 'addons' / 'ffmpeg').exists()
    assert mf.get_entry('ffmpeg') is None
    with pytest.raises(AddonMissing):
        mf.resolve('ffmpeg')


def test_startup_sweep_clears_staging_and_marks_missing(fake, home):
    entry, _, _ = fake
    mf.put_entry(installer.install_from_catalogue(entry, catalogue.static_source(entry), request_id='r9',
                                                  requested_by=None, approved_at=mf.now_iso()))
    stale = home / 'addons' / 'staging' / 'leftover'
    stale.mkdir(parents=True)
    (stale / 'download.part').write_bytes(b'x')
    Path(mf.resolve('ffmpeg')).unlink()
    out = mf.startup_sweep()
    assert out['staging_cleared'] == 1 and not stale.exists()
    assert mf.get_entry('ffmpeg')['status'] == 'missing'


# ── adoption of a system copy ───────────────────────────────────────────────

def _fake_system_ffmpeg(tmp_path, monkeypatch, content=b'sys ffmpeg'):
    d = tmp_path / 'sysbin'
    d.mkdir(exist_ok=True)
    for n in ('ffmpeg', 'ffprobe'):
        (d / n).write_bytes(content + n.encode())
    monkeypatch.setattr(installer.shutil, 'which', lambda name, *a, **k: str(d / name) if (d / name).exists() else None)
    monkeypatch.setattr(installer, 'probe_version', lambda b, probe: 'ffmpeg version sys-9')
    return d


def test_adoption_pins_path_and_hash_and_recards_on_change(fake, home, monkeypatch):
    entry, _, _ = fake
    d = _fake_system_ffmpeg(home, monkeypatch)
    seen = installer.inspect_system(entry)
    assert seen['version'] == 'ffmpeg version sys-9'
    assert seen['binaries']['ffmpeg']['path'] == str((d / 'ffmpeg').resolve())
    new = installer.adopt_system(entry, seen, request_id='a1', requested_by=None, approved_at=mf.now_iso())
    mf.put_entry(new)
    assert new['source'] == 'system' and 'licence not checked' in new['note']
    assert mf.resolve('ffmpeg') == str((d / 'ffmpeg').resolve())
    (d / 'ffmpeg').write_bytes(b'winget upgraded me')
    with pytest.raises(AddonMissing) as ei:
        mf.resolve('ffmpeg')
    assert ei.value.status == 'broken'


def test_adoption_refuses_a_binary_that_changed_after_the_card(fake, home, monkeypatch):
    entry, _, _ = fake
    d = _fake_system_ffmpeg(home, monkeypatch)
    seen = installer.inspect_system(entry)
    (d / 'ffmpeg').write_bytes(b'swapped between card and tap')
    with pytest.raises(AddonError, match='changed after the card'):
        installer.adopt_system(entry, seen, request_id='a2', requested_by=None, approved_at=mf.now_iso())


def test_removing_an_adopted_copy_never_deletes_the_users_file(fake, home, monkeypatch):
    entry, _, _ = fake
    d = _fake_system_ffmpeg(home, monkeypatch)
    mf.put_entry(installer.adopt_system(entry, installer.inspect_system(entry), request_id='a3',
                                        requested_by=None, approved_at=mf.now_iso()))
    out = mf.remove('ffmpeg')
    assert out['deleted_files'] is False
    assert (d / 'ffmpeg').is_file()


def test_no_system_copy_means_no_adoption_card(fake, home, monkeypatch):
    monkeypatch.setattr(installer.shutil, 'which', lambda *a, **k: None)
    with pytest.raises(AddonError, match='no ffmpeg was found'):
        service.file_request('system:ffmpeg', 'x', None)
    assert rs.list_requests() == []


# ── request store ───────────────────────────────────────────────────────────

def test_filing_a_request_installs_nothing_and_leaves_the_install_tree_untouched(fake, home):
    root = home / 'addons'
    root.mkdir(parents=True, exist_ok=True)
    before = sorted(str(p) for p in root.rglob('*'))
    card, outcome = service.file_request('ffmpeg', 'render for campaign Spring', {'kind': 'agent'})
    assert outcome == 'created' and card['state'] == 'pending'
    assert sorted(str(p) for p in root.rglob('*')) == before
    assert not mf.manifest_path().exists()
    again, outcome2 = service.file_request('ffmpeg', 'reworded entirely', {'kind': 'agent'})
    assert outcome2 == 'existing' and again['id'] == card['id']
    assert len(rs.list_requests()) == 1


def test_decline_is_durable_and_keyed_on_the_addon_not_the_wording(fake, home):
    card, _ = service.file_request('ffmpeg', 'first', None)
    assert service.decline(card['id'])['state'] == 'declined'
    _again, outcome = service.file_request('ffmpeg', 'a completely different reason', None)
    assert outcome == 'declined'
    assert len(rs.list_requests(('pending',))) == 0
    assert rs.active_decline('system:ffmpeg') is not None        # adopting the same add-on is the same "no"


def test_reason_is_capped_single_line_plain_text(fake, home):
    nasty = 'line1\nline2\x00<script>alert(1)</script>' + 'A' * 900
    card, _ = service.file_request('ffmpeg', nasty, None)
    assert '\n' not in card['agent_says'] and '\x00' not in card['agent_says']
    assert len(card['agent_says']) <= rs.REASON_MAX


def test_request_is_not_in_the_catalogue_is_refused(fake):
    with pytest.raises(AddonError, match='not in the add-on catalogue'):
        service.file_request('evil-tool', 'x', None)
    with pytest.raises(AddonError):
        service.file_request('system:evil-tool', 'x', None)


def test_pending_requests_expire_after_14_days(fake, home):
    card, _ = service.file_request('ffmpeg', 'x', None)
    d = json.loads(rs.store_path().read_text(encoding='utf-8'))
    d['requests'][card['id']]['last_touched_at'] = '2020-01-01T00:00:00Z'
    rs.store_path().write_text(json.dumps(d), encoding='utf-8')
    assert rs.expire_stale() == 1
    assert rs.get(card['id'])['state'] == 'expired'
    assert service.pending_cards() == []


def test_interrupted_installs_fail_on_restart(fake, home):
    card, _ = service.file_request('ffmpeg', 'x', None)
    rs.claim(card['id'])
    assert rs.fail_interrupted() == 1
    assert rs.get(card['id'])['state'] == 'failed'


# ── approve flow ────────────────────────────────────────────────────────────

def test_approve_installs_records_manifest_and_runs_resume_hooks(fake, home):
    entry, _, _ = fake
    resumed = []
    service.register_resume_hook(resumed.append)
    card, _ = service.file_request('ffmpeg', 'x', {'kind': 'agent', 'project_id': 'p'})
    claimed = service.approve(card['id'])
    assert claimed is not None
    assert rs.get(card['id'])['state'] == 'installed'
    assert mf.get_entry('ffmpeg')['requested_by'] == {'kind': 'agent', 'project_id': 'p'}
    assert Path(mf.resolve('ffmpeg')).is_file()
    assert resumed == ['ffmpeg']


def test_second_approve_gets_nothing(fake, monkeypatch):
    monkeypatch.setattr(service, '_spawn', lambda fn: None)          # leave the first one "installing"
    card, _ = service.file_request('ffmpeg', 'x', None)
    assert service.approve(card['id']) is not None
    assert service.approve(card['id']) is None


def test_failed_install_lands_in_failed_with_a_reason_and_can_retry(fake, home, monkeypatch):
    entry, blob, _ = fake
    monkeypatch.setattr(installer, '_open_url', lambda url: io.BytesIO(blob + b'x'))
    card, _ = service.file_request('ffmpeg', 'x', None)
    service.approve(card['id'])
    r = rs.get(card['id'])
    assert r['state'] == 'failed' and 'checksum mismatch' in r['error']
    assert mf.get_entry('ffmpeg') is None
    monkeypatch.setattr(installer, '_open_url', lambda url: io.BytesIO(blob))
    assert service.approve(card['id']) is not None
    assert rs.get(card['id'])['state'] == 'installed'


def test_a_platform_with_no_static_build_gets_no_download_card(home, monkeypatch):
    blob = _zip()
    entry = _entry(blob, key='macos-arm64-not-this-host')
    cat = home / 'c.json'
    cat.write_text(json.dumps({'schema': 1, 'addons': [entry]}), encoding='utf-8')
    monkeypatch.setattr(catalogue, 'CATALOGUE_PATH', cat)
    with pytest.raises(AddonError, match='no download for this computer'):
        service.file_request('ffmpeg', 'x', None)
    assert rs.list_requests() == []
