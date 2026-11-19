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


INSTALL = ROOT / 'installer/install.ps1'


def _npm_install_source():
    source = INSTALL.read_text(encoding='utf-8')
    name = 'function Invoke-ClaudeNpmInstall {'
    return name + source.split(name, 1)[1].split('\n}\n', 1)[0] + '\n}\n'


def test_cli_install_source_checks_process_paths_under_npm_prefix():
    install = _npm_install_source()
    assert 'Get-NpmPrefix' in install
    assert '$process.Path' in install
    assert '.StartsWith($npmRoot, [StringComparison]::OrdinalIgnoreCase)' in install
    assert "Claude CLI at '$processPath'" in install
    assert 'Stop-Process' not in INSTALL.read_text(encoding='utf-8')


@pytest.mark.parametrize('kind', ['npm', 'desktop', 'similar-prefix', 'missing-path', 'unreadable-path'])
def test_cli_install_blocks_only_running_claude_under_npm_prefix(tmp_path, kind):
    ps = shutil.which('powershell') or shutil.which('pwsh')
    if ps is None:
        pytest.skip('PowerShell required')
    npm_prefix = tmp_path / 'npm'
    path = {
        'npm': str(tmp_path / 'NPM' / 'node_modules' / 'claude.exe'),
        'desktop': str(tmp_path / 'Claude Desktop' / 'claude.exe'),
        'similar-prefix': str(tmp_path / 'npm-desktop' / 'claude.exe'),
    }.get(kind, '')
    executable_literal = "'" + path.replace("'", "''") + "'"
    script = tmp_path / 'repair-probe.ps1'
    script.write_text(
        'param($NpmPrefix, $Kind)\n'
        f'$Executable = {executable_literal}\n'
        "$ErrorActionPreference = 'Stop'\n"
        'function Get-NpmPrefix { return $NpmPrefix }\n'
        'function Get-Process {\n'
        '    param($Name)\n'
        '    if ($Kind -eq "unreadable-path") {\n'
        '        $process = [pscustomobject]@{Id = 9999}\n'
        '        $process | Add-Member -MemberType ScriptProperty -Name Path -Value { throw "Access denied" }\n'
        '        return $process\n'
        '    }\n'
        '    return [pscustomobject]@{Path = $Executable}\n'
        '}\n'
        'function Get-Command { param($Name) return $null }\n'
        'function Test-ClaudeWorks { return $true }\n'
        'function Install-ClaudeNpm { param([switch]$Clean) Write-Host "install called" }\n'
        + _npm_install_source() + "\nInvoke-ClaudeNpmInstall -Label 'probe'\n",
        encoding='utf-8',
    )
    result = subprocess.run(
        [ps, '-NoProfile', '-NonInteractive', '-File', str(script), str(npm_prefix), kind],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
    if kind == 'npm':
        assert f"Close the running Claude CLI at '{path}'" in result.stdout
        assert 'install called' not in result.stdout
        assert result.stdout.splitlines()[-1] == 'False'
    else:
        assert 'Close the running Claude CLI' not in result.stdout
        assert 'install called' in result.stdout
        assert result.stdout.splitlines()[-1] == 'True'


def test_signing_workflow_requires_oidc_and_verifies_signature():
    workflow = WORKFLOW.read_text(encoding="utf-8").lower()
    assert "id-token: write" in workflow
    assert "azure/login@v3" in workflow
    assert "azure/artifact-signing-action@v2" in workflow
    assert "get-authenticodesignature" in workflow
    assert "timestamp.acs.microsoft.com" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "gh release" not in workflow
