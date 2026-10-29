"""No network, real connector, operator binary, ledger or home writes."""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
import threading
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from mc_remote import cloudflared as cf
from mc_remote import cloudflared_update as cu

ARTIFACT = ("https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64", False)
NEW = b"cloudflared version 2026.10.6 (built 2026-10-29-12:18 UTC)"
OLD = b"cloudflared version 2026.3.0 (built 2026-03-01-12:00 UTC)"
REAL_READ_VERSION = cu.read_version


class Today(date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 29)


@pytest.fixture
def updater(tmp_path, monkeypatch):
    manager = cu.CloudflaredUpdater(tmp_path / "outside-repo" / "rollback")
    monkeypatch.setattr(cu, "updater", manager)
    monkeypatch.setattr(cf, "updater", manager)
    monkeypatch.setattr(cu, "date", Today)
    monkeypatch.setattr(cu, "read_version", lambda path: cu.parse_version(path.read_text()))

    def no_network(*args, **kwargs):
        raise AssertionError("test attempted network access")

    monkeypatch.setattr(cu.urllib.request, "urlopen", no_network)
    monkeypatch.setattr(cu, "fetch_release", lambda artifact: cu.Release(
        (2026, 10, 6), "https://example.invalid/cloudflared", hashlib.sha256(NEW).hexdigest()))
    monkeypatch.setattr(cu, "download", lambda url, target: target.write_bytes(NEW))
    return manager


@pytest.fixture
def binary(tmp_path):
    target = tmp_path / "repo" / "bin" / "cloudflared"
    target.parent.mkdir(parents=True)
    target.write_bytes(OLD)
    return target


@pytest.mark.parametrize("text,expected", [
    ("2026.10.2", (2026, 10, 2)), ("v2026.10.6", (2026, 10, 6)),
    ("cloudflared version 2026.10.6 (built 2026-10-29-12:18 UTC)\n", (2026, 10, 6)),
    ("2026.3.10", (2026, 3, 10)), ("2027.1.0", (2027, 1, 0)),
])
def test_version_parsing(text, expected):
    assert cu.parse_version(text) == expected


@pytest.mark.parametrize("text", ["", "2026.13.1", "2026.0.1", "junk 2026.10.2", "2026.10.2-rc1", "2026.10.2/other"])
def test_invalid_version_rejected(text):
    with pytest.raises(ValueError):
        cu.parse_version(text)


@pytest.mark.parametrize("version,today,expected", [
    ((2026, 10, 1), date(2026, 10, 29), True),
    ((2026, 10, 2), date(2026, 10, 29), False),
    ((2026, 10, 6), date(2026, 10, 29), False),
    ((2026, 10, 10), date(2026, 10, 29), False),
    ((2026, 10, 6), date(2026, 12, 29), False),  # 89 days
    ((2026, 10, 6), date(2026, 12, 30), True),   # 90 days
    ((2027, 1, 0), date(2026, 10, 29), False),
])
def test_minimum_and_staleness(version, today, expected):
    assert cu.update_due(version, today) is expected


def test_version_subprocess_is_bounded_hidden_and_checks_exit(monkeypatch, tmp_path):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=NEW.decode())

    monkeypatch.setattr(cu.subprocess, "run", run)
    target = tmp_path / "cloudflared.exe"
    assert REAL_READ_VERSION(target) == (2026, 10, 6)
    assert calls[0][0] == [str(target), "--version"]
    assert calls[0][1]["check"] is True
    assert calls[0][1]["timeout"] == 5


def test_release_body_checksum_exact_filename_and_crlf():
    digest = hashlib.sha256(NEW).hexdigest()
    body = f"### SHA256 Checksums:\r\n```\r\ncloudflared-linux-amd64: {digest}\r\n```"
    assert cu.release_checksum(body, "cloudflared-linux-amd64") == digest
    with pytest.raises(ValueError, match="missing"):
        cu.release_checksum(body, "cloudflared-linux-arm64")
    with pytest.raises(ValueError, match="ambiguous"):
        cu.release_checksum(body + "\r\n" + body, "cloudflared-linux-amd64")


def test_fetch_pins_tag_asset_and_checksum(monkeypatch):
    artifact = "cloudflared-linux-amd64"
    url = f"{cu.DOWNLOAD_BASE}/2026.10.6/{artifact}"
    payload = {"tag_name": "2026.10.6", "body": f"{artifact}: {hashlib.sha256(NEW).hexdigest()}",
               "assets": [{"name": artifact, "browser_download_url": url}]}
    calls = []

    def urlopen(request, **kwargs):
        calls.append((request.full_url, kwargs))
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(cu.urllib.request, "urlopen", urlopen)
    release = cu.fetch_release(artifact)
    assert release.url == url and release.version == (2026, 10, 6)
    assert release.checksum == hashlib.sha256(NEW).hexdigest()
    assert calls == [(cu.RELEASE_API, {"timeout": 15})]
    payload["body"] = "missing checksum"
    with pytest.raises(ValueError, match="checksum"):
        cu.fetch_release(artifact)
    payload["tag_name"] = "2026.10.6/other"
    with pytest.raises(ValueError, match="tag"):
        cu.fetch_release(artifact)


def test_verified_swap_has_no_gap_and_external_rollback(updater, binary, monkeypatch):
    replace = cu.os.replace
    swaps = []

    def observed_replace(source, target):
        if target == binary.resolve():
            swaps.append(binary.read_bytes())
        return replace(source, target)

    monkeypatch.setattr(cu.os, "replace", observed_replace)
    assert updater.ensure(binary, ARTIFACT) == str(binary)
    assert swaps == [OLD]  # live path still held OLD immediately before replace
    assert binary.read_bytes() == NEW
    backup = updater.rollback_path(binary)
    assert backup.read_bytes() == OLD
    assert not backup.is_relative_to(binary.parent.parent)
    assert updater.status()["installed_version"] == "2026.10.6"
    updater.rollback(binary)
    assert binary.read_bytes() == OLD
    assert updater.status()["out_of_date"] is True
    assert list(binary.parent.iterdir()) == [binary]


@pytest.mark.parametrize("kind", ["mismatch", "missing", "offline"])
def test_failed_verification_or_network_keeps_old(updater, binary, monkeypatch, caplog, kind):
    if kind == "mismatch":
        monkeypatch.setattr(cu, "download", lambda url, target: target.write_bytes(b"corrupt"))
    else:
        def fail(artifact):
            raise ValueError("missing checksum") if kind == "missing" else OSError("offline")
        monkeypatch.setattr(cu, "fetch_release", fail)
    assert updater.ensure(binary, ARTIFACT) == str(binary)
    assert binary.read_bytes() == OLD
    assert updater.status()["last_update_error"]
    assert updater.status()["out_of_date"] is True
    assert "cloudflared update failed" in caplog.text
    assert not updater.rollback_path(binary).exists()
    assert list(binary.parent.iterdir()) == [binary]


def test_candidate_wrong_version_never_swapped(updater, binary, monkeypatch):
    monkeypatch.setattr(cu, "download", lambda url, target: target.write_bytes(OLD))
    monkeypatch.setattr(cu, "fetch_release", lambda artifact: cu.Release(
        (2026, 10, 6), "https://example.invalid", hashlib.sha256(OLD).hexdigest()))
    updater.ensure(binary, ARTIFACT)
    assert binary.read_bytes() == OLD
    assert "differs from release tag" in updater.status()["last_update_error"]


def test_post_swap_validation_failure_restores_previous(updater, binary, monkeypatch):
    def version(path):
        if path == binary.resolve() and path.read_bytes() == NEW:
            raise OSError("post-swap execution failed")
        return cu.parse_version(path.read_text())

    monkeypatch.setattr(cu, "read_version", version)
    updater.ensure(binary, ARTIFACT)
    assert binary.read_bytes() == OLD
    assert updater.rollback_path(binary).read_bytes() == OLD
    assert "post-swap execution failed" in updater.status()["last_update_error"]
    assert updater.status()["installed_version"] == "2026.3.0"


def test_swap_failure_keeps_previous_binary(updater, binary, monkeypatch):
    replace = cu.os.replace

    def fail(source, target):
        if target == binary.resolve():
            raise PermissionError("binary locked")
        return replace(source, target)

    monkeypatch.setattr(cu.os, "replace", fail)
    assert updater.ensure(binary, ARTIFACT) == str(binary)
    assert binary.read_bytes() == OLD
    assert "binary locked" in updater.status()["last_update_error"]


def test_current_version_queries_metadata_without_download(updater, binary, monkeypatch):
    binary.write_bytes(NEW)
    downloads = []
    monkeypatch.setattr(cu, "download", lambda *args: downloads.append(args))
    updater.ensure(binary, ARTIFACT)
    assert downloads == []
    assert updater.status()["latest_version"] == "2026.10.6"
    assert updater.status()["out_of_date"] is False


def test_supported_but_newer_release_available_is_reported_not_forced(updater, binary, monkeypatch):
    binary.write_bytes(b"2026.10.2")
    monkeypatch.setattr(cu, "download", lambda *args: pytest.fail("not stale or below minimum"))
    updater.ensure(binary, ARTIFACT)
    assert binary.read_bytes() == b"2026.10.2"
    assert updater.status()["out_of_date"] is True
    assert updater.status()["update_pending"] is False


def test_latest_release_never_downgrades_installed(updater, binary):
    binary.write_bytes(b"2027.1.0")
    updater.ensure(binary, ARTIFACT)
    assert binary.read_bytes() == b"2027.1.0"
    assert updater.status()["out_of_date"] is False


def test_backup_failure_prevents_swap(updater, binary, monkeypatch):
    def fail(source, target):
        raise PermissionError("rollback directory is not writable")
    monkeypatch.setattr(cu, "_copy_atomic", fail)
    updater.ensure(binary, ARTIFACT)
    assert binary.read_bytes() == OLD
    assert "rollback directory is not writable" in updater.status()["last_update_error"]


def test_failed_restore_blocks_launch_until_explicit_recovery(updater, binary, monkeypatch):
    copy = cu._copy_atomic
    def fail_restore(source, target):
        if target == binary.resolve():
            raise PermissionError("restore denied")
        return copy(source, target)
    def version(path):
        if path == binary.resolve() and path.read_bytes() == NEW:
            raise OSError("new executable cannot run")
        return cu.parse_version(path.read_text())
    monkeypatch.setattr(cu, "_copy_atomic", fail_restore)
    monkeypatch.setattr(cu, "read_version", version)
    assert updater.ensure(binary, ARTIFACT) is None
    assert updater.ensure(binary, ARTIFACT) is None
    assert updater.status()["installed_version"] is None
    assert "rollback failed (restore denied)" in updater.status()["last_update_error"]
    assert updater.rollback_path(binary).read_bytes() == OLD
    monkeypatch.setattr(cu, "_copy_atomic", copy)
    updater.rollback(binary)
    assert binary.read_bytes() == OLD


def test_constructor_does_no_update_io(updater, binary, monkeypatch):
    monkeypatch.setattr(cf, "find_binary", lambda: str(binary))
    monkeypatch.setattr(updater, "ensure", lambda *a: pytest.fail("constructor attempted update"))
    cf.CloudflaredProcess()


def test_offline_start_still_launches_old_binary(updater, binary, monkeypatch):
    calls = []
    def offline(artifact):
        raise OSError("metadata offline")
    def popen(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(pid=123456, poll=lambda: None, stdout=None)
    monkeypatch.setattr(cu, "fetch_release", offline)
    monkeypatch.setattr(cf.subprocess, "Popen", popen)
    monkeypatch.setattr(cf, "reap_orphans", lambda: 0)
    monkeypatch.setattr(cf, "_ledger_add", lambda pid: None)
    manager = cf.CloudflaredProcess(binary_path=str(binary))
    manager.start("test-token")
    manager.start("test-token")
    assert len(calls) == 1 and calls[0][0] == str(binary)
    assert "--no-autoupdate" in calls[0]
    assert binary.read_bytes() == OLD and manager.is_alive()


def test_streamed_download_preserves_bytes(monkeypatch, tmp_path):
    calls = []
    def urlopen(url, **kwargs):
        calls.append((url, kwargs))
        return io.BytesIO(NEW)
    monkeypatch.setattr(cu.urllib.request, "urlopen", urlopen)
    target = tmp_path / "stage"
    cu.download("https://example.invalid/download", target)
    assert target.read_bytes() == NEW
    assert calls == [("https://example.invalid/download", {"timeout": 60})]


def test_newer_than_minimum_but_stale_updates(updater, binary, monkeypatch):
    class Later(Today):
        @classmethod
        def today(cls):
            return cls(2027, 1, 29)
    binary.write_bytes(b"2026.10.2")
    monkeypatch.setattr(cu, "date", Later)
    newer = b"2027.1.1"
    monkeypatch.setattr(cu, "fetch_release", lambda artifact: cu.Release(
        (2027, 1, 1), "https://example.invalid", hashlib.sha256(newer).hexdigest()))
    monkeypatch.setattr(cu, "download", lambda url, target: target.write_bytes(newer))
    updater.ensure(binary, ARTIFACT)
    assert binary.read_bytes() == newer


def test_missing_binary_uses_verified_pipeline(updater, binary):
    binary.unlink()
    assert updater.ensure(binary, ARTIFACT) == str(binary)
    assert binary.read_bytes() == NEW
    assert not updater.rollback_path(binary).exists()


def test_offline_missing_binary_returns_none(updater, binary, monkeypatch):
    binary.unlink()
    def fail(artifact):
        raise OSError("offline")
    monkeypatch.setattr(cu, "fetch_release", fail)
    assert updater.ensure(binary, ARTIFACT) is None
    assert not binary.exists()


def test_live_connector_defers_swap_then_updates_after_exit(updater, binary):
    proc = SimpleNamespace(code=None)
    proc.poll = lambda: proc.code
    updater.track_running(binary, proc)
    updater.ensure(binary, ARTIFACT)
    assert binary.read_bytes() == OLD
    assert updater.status()["update_pending"] is True
    with pytest.raises(RuntimeError, match="running"):
        updater.rollback(binary)
    proc.code = 0
    updater.ensure(binary, ARTIFACT)
    assert binary.read_bytes() == NEW
    assert updater.status()["update_pending"] is False


def test_check_and_failed_download_retry_are_daily(updater, binary, monkeypatch):
    clock = [100.0]
    checks, downloads = [], []
    monkeypatch.setattr(cu.time, "monotonic", lambda: clock[0])
    release = cu.fetch_release("ignored")
    monkeypatch.setattr(cu, "fetch_release", lambda artifact: (checks.append(artifact), release)[1])
    def fail(url, target):
        downloads.append(url)
        raise OSError("download offline")
    monkeypatch.setattr(cu, "download", fail)
    for _ in range(3):
        updater.ensure(binary, ARTIFACT)
    assert len(checks) == len(downloads) == 1
    clock[0] += cu.CHECK_INTERVAL_SECONDS
    updater.ensure(binary, ARTIFACT)
    assert len(checks) == len(downloads) == 2


def test_tarball_verifies_archive_and_extracts_only_binary(updater, binary, monkeypatch):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tf:
        for name, data in [("../../unrelated", b"do not extract"), ("cloudflared", NEW)]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    payload = archive.getvalue()
    monkeypatch.setattr(cu, "fetch_release", lambda artifact: cu.Release(
        (2026, 10, 6), "https://example.invalid", hashlib.sha256(payload).hexdigest()))
    monkeypatch.setattr(cu, "download", lambda url, target: target.write_bytes(payload))
    updater.ensure(binary, ("https://example.invalid/cloudflared-darwin-arm64.tgz", True))
    assert binary.read_bytes() == NEW
    assert not (binary.parent.parent.parent / "unrelated").exists()


def test_tarball_symlink_binary_is_refused(updater, binary, monkeypatch):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tf:
        info = tarfile.TarInfo("cloudflared")
        info.type, info.linkname = tarfile.SYMTYPE, "../../unrelated"
        tf.addfile(info)
    payload = archive.getvalue()
    monkeypatch.setattr(cu, "fetch_release", lambda artifact: cu.Release(
        (2026, 10, 6), "https://example.invalid", hashlib.sha256(payload).hexdigest()))
    monkeypatch.setattr(cu, "download", lambda url, target: target.write_bytes(payload))
    updater.ensure(binary, ("https://example.invalid/cloudflared-darwin-arm64.tgz", True))
    assert binary.read_bytes() == OLD
    assert "exactly one cloudflared binary" in updater.status()["last_update_error"]


def test_status_does_not_wait_for_update_lock(updater):
    done = threading.Event()
    with updater.lifecycle_lock:
        reader = threading.Thread(target=lambda: (updater.status(), done.set()))
        reader.start()
        assert done.wait(1)
    reader.join(1)


def test_start_holds_update_lock_until_process_is_tracked(updater, binary, monkeypatch):
    entered, release_start, ensure_finished = (threading.Event() for _ in range(3))
    errors = []
    proc = SimpleNamespace(pid=123456, poll=lambda: None, stdout=None)
    def popen(*args, **kwargs):
        assert "--no-autoupdate" in args[0]
        entered.set()
        assert release_start.wait(3)
        return proc
    monkeypatch.setattr(cf.subprocess, "Popen", popen)
    monkeypatch.setattr(cf, "reap_orphans", lambda: 0)
    monkeypatch.setattr(cf, "_ledger_add", lambda pid: None)
    manager = cf.CloudflaredProcess(binary_path=str(binary))
    def start():
        try:
            manager.start("test-token")
        except Exception as exc:
            errors.append(exc)
    launcher = threading.Thread(target=start)
    launcher.start()
    assert entered.wait(3)
    def concurrent_ensure():
        updater.ensure(binary, ARTIFACT)
        ensure_finished.set()
    checker = threading.Thread(target=concurrent_ensure)
    checker.start()
    try:
        assert not ensure_finished.wait(0.1)
    finally:
        release_start.set()
        launcher.join(3)
        checker.join(3)
    assert not launcher.is_alive() and not checker.is_alive()
    assert errors == [] and ensure_finished.is_set()
    assert updater._is_running(binary.resolve())


def test_ensure_binary_wiring_isolated_from_real_binary(updater, binary, monkeypatch):
    monkeypatch.setattr(cf, "find_binary", lambda: str(binary))
    assert cf.ensure_binary() == str(binary)
    assert binary.read_bytes() == NEW
    assert cf.download_state() == {"in_progress": False, "last_error": None}


def test_provider_status_passes_cached_fields_through_route(updater, monkeypatch):
    from flask import Flask
    from mc.blueprints import remote_routes as rr
    from mc_remote import provider_impl as pi

    updater._publish(installed_version="2026.3.0", latest_version="2026.10.6",
                     last_update_error="offline", out_of_date=True)
    monkeypatch.setattr(pi.device_keys, "load_identity", lambda: None)
    monkeypatch.setattr(pi.tunnel_supervisor, "get", lambda: pytest.fail("status touched supervisor"))
    provider = pi.ClayruneProvider()
    monkeypatch.setattr(provider, "get_caps", lambda: None)
    monkeypatch.setattr(rr, "_get_remote_provider", lambda: provider)
    app = Flask(__name__)
    app.register_blueprint(rr.bp)
    response = app.test_client().get("/api/remote/status")
    assert response.status_code == 200
    payload = response.get_json()["cloudflared"]
    assert payload["installed_version"] == "2026.3.0"
    assert payload["latest_version"] == "2026.10.6"
    assert payload["last_update_error"] == "offline"
    assert payload["out_of_date"] is True
