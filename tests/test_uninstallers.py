"""Safety and integration coverage for the Windows/macOS uninstall flows."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
WINDOWS_UNINSTALLER = ROOT / "installer" / "uninstall.ps1"
MAC_UNINSTALLER = ROOT / "installer" / "uninstall-macos.command"


def _make_source_install(home: Path) -> Path:
    install = home / "Clayrune"
    (install / "installer").mkdir(parents=True)
    (install / "data" / "projects").mkdir(parents=True)
    (install / "server.py").write_text("# marker\n", encoding="utf-8")
    (install / "config.json").write_text('{"port": 55199}\n', encoding="utf-8")
    (install / "data" / "projects" / "example.json").write_text(
        '{"id":"example"}\n', encoding="utf-8"
    )
    return install


def _assert_preserved_archive(home: Path) -> None:
    archives = list((home / ".clayrune" / "uninstall-archives").glob("*"))
    assert len(archives) == 1
    assert (archives[0] / "config.json").exists()
    assert (archives[0] / "data" / "projects" / "example.json").exists()
    assert (archives[0] / "README.txt").exists()


def test_uninstallers_never_remove_provider_state_or_clis() -> None:
    windows = WINDOWS_UNINSTALLER.read_text(encoding="utf-8")
    mac = MAC_UNINSTALLER.read_text(encoding="utf-8")

    forbidden_windows = (
        "npm uninstall",
        "winget uninstall",
        "Remove-Item $HomeDir\\.claude",
        "Remove-Item \"$env:USERPROFILE\\.claude",
    )
    forbidden_mac = ("npm uninstall", "brew uninstall", 'rm -rf "$user_home/.claude"')
    assert not any(token in windows for token in forbidden_windows)
    assert not any(token in mac for token in forbidden_mac)

    assert "PURGE CLAYRUNE DATA" in windows
    assert "PURGE CLAYRUNE DATA" in mac
    assert "not identifiable as Clayrune; leaving it running" in windows
    assert "uninstall-archives" in windows
    assert "uninstall-archives" in mac


def test_installers_create_discoverable_uninstall_entries() -> None:
    ps1 = (ROOT / "installer" / "install.ps1").read_text(encoding="utf-8")
    sh = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    spec = (ROOT / "installer" / "build-macos.spec").read_text(encoding="utf-8")

    assert "Uninstall Clayrune.lnk" in ps1
    assert "uninstall.ps1" in ps1
    assert "Uninstall Clayrune.command" in sh
    assert "uninstall-macos.command" in sh
    assert "uninstall-macos.command" in spec
    assert "--app-path" in MAC_UNINSTALLER.read_text(encoding="utf-8")

    notarizer = (ROOT / "tools" / "notarize-macos.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "build-macos.yml").read_text(
        encoding="utf-8"
    )
    assert "Uninstall Clayrune.command" in notarizer
    assert "Uninstall Clayrune.command" in workflow


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell flow runs on Windows")
def test_windows_uninstall_preserves_data_by_default(tmp_path: Path) -> None:
    home = tmp_path / "home"
    install = _make_source_install(home)
    provider_state = home / ".claude" / "auth.json"
    provider_state.parent.mkdir(parents=True)
    provider_state.write_text("keep", encoding="utf-8")
    external_project = home / "Projects" / "real-work.txt"
    external_project.parent.mkdir(parents=True)
    external_project.write_text("keep", encoding="utf-8")

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WINDOWS_UNINSTALLER),
            "-InstallDir",
            str(install),
            "-UserHome",
            str(home),
            "-Yes",
        ],
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not install.exists()
    assert provider_state.read_text(encoding="utf-8") == "keep"
    assert external_project.read_text(encoding="utf-8") == "keep"
    _assert_preserved_archive(home)


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell flow runs on Windows")
def test_windows_purge_is_clayrune_scoped(tmp_path: Path) -> None:
    home = tmp_path / "home"
    install = _make_source_install(home)
    (home / ".clayrune").mkdir(parents=True)
    (home / ".clayrune" / "secrets.json").write_text("remove", encoding="utf-8")
    frozen = home / "AppData" / "Roaming" / "MissionControl"
    frozen.mkdir(parents=True)
    (frozen / "config.json").write_text("{}", encoding="utf-8")
    provider_state = home / ".claude" / "auth.json"
    provider_state.parent.mkdir(parents=True)
    provider_state.write_text("keep", encoding="utf-8")

    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WINDOWS_UNINSTALLER),
            "-InstallDir",
            str(install),
            "-UserHome",
            str(home),
            "-PurgeData",
            "-Yes",
        ],
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not install.exists()
    assert not (home / ".clayrune").exists()
    assert not frozen.exists()
    assert provider_state.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS flow runs on macOS")
@pytest.mark.parametrize("purge", [False, True])
def test_macos_uninstall_is_scoped(tmp_path: Path, purge: bool) -> None:
    home = tmp_path / "home"
    install = _make_source_install(home)
    provider_state = home / ".claude" / "auth.json"
    provider_state.parent.mkdir(parents=True)
    provider_state.write_text("keep", encoding="utf-8")
    (home / ".clayrune").mkdir(parents=True)
    frozen = home / "MissionControl"
    frozen.mkdir(parents=True)

    command = [
        "/bin/sh",
        str(MAC_UNINSTALLER),
        "--install-dir",
        str(install),
        "--home",
        str(home),
        "--yes",
    ]
    if purge:
        command.append("--purge-data")
    result = subprocess.run(command, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not install.exists()
    assert provider_state.read_text(encoding="utf-8") == "keep"
    if purge:
        assert not (home / ".clayrune").exists()
        assert not frozen.exists()
    else:
        assert frozen.exists()
        _assert_preserved_archive(home)


def test_macos_script_has_valid_shell_syntax_when_shell_available() -> None:
    shell = "/bin/sh" if Path("/bin/sh").exists() else shutil.which("sh")
    if not shell:
        pytest.skip("no POSIX shell available")
    result = subprocess.run(
        [shell, "-n", str(MAC_UNINSTALLER)],
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
