from pathlib import Path
import shutil
import subprocess

import pytest


def test_setup_batch_preserves_crlf_for_raw_download():
    setup = Path(__file__).resolve().parents[1] / 'installer' / 'Clayrune-Setup.bat'
    content = setup.read_bytes()
    assert b'\r\n' in content
    assert b'\n' not in content.replace(b'\r\n', b'')


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "installer" / "win-exe" / "ClayruneInstaller.cs"
BUILD = ROOT / "installer" / "win-exe" / "build.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "build-windows-installer.yml"


def test_launcher_does_not_pipe_downloaded_code_to_powershell():
    source = SOURCE.read_text(encoding="utf-8")
    compact = " ".join(source.lower().split())
    assert "iwr \"" not in source.lower()
    assert "| iex" not in compact
    assert "executionpolicy bypass" not in compact
    assert "client.downloadfile" in source.lower()
    assert "sha256.create" in source.lower()
    assert "security error: installer bootstrap hash mismatch" in source.lower()


def test_build_pins_bootstrap_to_commit_and_hash():
    build = BUILD.read_text(encoding="utf-8").lower()
    assert "git -c $repo rev-parse head" in build
    assert "git -c $repo diff --quiet head -- installer/install.ps1" in build
    assert "get-filehash $bootstrap -algorithm sha256" in build
    assert "raw.githubusercontent.com/clayrune-io/clayrune/$commit/installer/install.ps1" in build


def test_cli_repair_stops_before_mutation_when_a_claude_process_is_running(tmp_path):
    ps = shutil.which('powershell') or shutil.which('pwsh')
    if ps is None:
        pytest.skip('PowerShell required')
    source = (ROOT / 'installer/install.ps1').read_text(encoding='utf-8')
    name = 'function Invoke-ClaudeNpmInstall {'
    function = name + source.split(name, 1)[1].split('\n}\n', 1)[0] + '\n}\n'
    script = tmp_path / 'repair-probe.ps1'
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "function Get-Process { param($Name) [pscustomobject]@{Id = 9999} }\n"
        "function Test-ClaudeWorks { return $false }\n"
        "function Install-ClaudeNpm { throw 'unexpected attempt to reinstall' }\n"
        + function + "\nInvoke-ClaudeNpmInstall -Label 'probe'\n",
        encoding='utf-8',
    )
    result = subprocess.run(
        [ps, '-NoProfile', '-NonInteractive', '-File', str(script)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert 'Close running Claude sessions' in result.stdout
    assert result.stdout.splitlines()[-1] == 'False'


def test_signing_workflow_requires_oidc_and_verifies_signature():
    workflow = WORKFLOW.read_text(encoding="utf-8").lower()
    assert "id-token: write" in workflow
    assert "azure/login@v3" in workflow
    assert "azure/artifact-signing-action@v2" in workflow
    assert "get-authenticodesignature" in workflow
    assert "timestamp.acs.microsoft.com" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "gh release" not in workflow
