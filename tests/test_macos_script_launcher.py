"""Exercise the script installer's macOS launcher without starting a server."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


INSTALLER = Path(__file__).resolve().parents[1] / "installer"
SH = shutil.which("sh")
pytestmark = pytest.mark.skipif(SH is None, reason="POSIX sh required")


def _make_install(install_dir):
    launcher = install_dir / "installer" / "start.command"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(
        (INSTALLER / launcher.name).read_text(encoding="utf-8"),
        encoding="utf-8", newline="\n",
    )
    launcher.chmod(0o755)

    # Stop at activation so the real launcher's path checks run without open or Python.
    activate = install_dir / ".venv" / "bin" / "activate"
    activate.parent.mkdir(parents=True)
    activate.write_text("printf 'Reached the install venv\\n'\nexit 0\n", newline="\n")
    return launcher


def _assert_launch(launcher, cwd):
    result = subprocess.run(
        [SH, launcher.as_posix()], cwd=cwd, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "Reached the install venv\n"


@pytest.mark.parametrize("custom_dir", [False, True], ids=["default", "custom"])
def test_applications_launcher_resolves_install(tmp_path, custom_dir):
    home = tmp_path / "home"
    setup = tmp_path / "setup"
    setup.mkdir()
    custom_path = "Clayrune's custom install"
    install = setup / custom_path if custom_dir else home / "Clayrune"
    _make_install(install)

    # Run the real installer stage in isolation from dependency setup and launch.
    source = (INSTALLER / "install.sh").read_text(encoding="utf-8")
    step = source.split('# ── [STEP 3/5] Launcher entry', 1)[1].split('# ── [STEP 4/5]', 1)[0]
    stage = setup / "create-launcher.sh"
    stage.write_text(
        'set -e\nOS=macos\nINSTALL_DIR="${CLAYRUNE_HOME:-$HOME/Clayrune}"\n#'
        + step, encoding="utf-8", newline="\n",
    )
    env = {**os.environ, "HOME": home.as_posix(), "CLAYRUNE_HOME": custom_path if custom_dir else ""}
    result = subprocess.run(
        [SH, stage.as_posix()], cwd=setup, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_launch(home / "Applications" / "Clayrune.command", tmp_path)


def test_in_install_launcher_resolves_install(tmp_path):
    launcher = _make_install(tmp_path / "Clayrune")
    _assert_launch(launcher, tmp_path)
