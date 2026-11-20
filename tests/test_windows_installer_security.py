from pathlib import Path
import shutil
import subprocess
import sys

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


def _function_source(name):
    source = INSTALL.read_text(encoding='utf-8')
    head = f'function {name} {{'
    return head + source.split(head, 1)[1].split('\n}\n', 1)[0] + '\n}\n'


def _npm_install_source():
    return _function_source('Invoke-ClaudeNpmInstall')


def test_cli_install_source_checks_process_paths_under_npm_prefix():
    install = _npm_install_source()
    assert 'Get-NpmPrefix' in install
    assert '$process.Path' in install
    assert '.StartsWith($npmRoot, [StringComparison]::OrdinalIgnoreCase)' in install
    assert "Claude CLI at '$processPath'" in install
    # Both sides of the comparison have their junctions and symlinks resolved.
    assert 'Resolve-LinkedPath (Get-NpmPrefix)' in install
    assert 'Resolve-LinkedPath $processPath' in install
    whole = INSTALL.read_text(encoding='utf-8')
    for ender in ('Stop-Process', 'taskkill', '.Kill('):
        assert ender not in whole


def _junction(link, target):
    import _winapi
    target.mkdir(parents=True, exist_ok=True)
    _winapi.CreateJunction(str(target), str(link))


# kind -> does CLI setup stop before `npm install`, and with which message.
_PATH_STOP = 'Close the running Claude CLI at'
_PID_STOP = 'A Claude process is running (PID 9999)'
_GUARD_CASES = {
    'npm': _PATH_STOP,
    'desktop': None,
    'similar-prefix': None,
    # No readable image path (elevated Claude, or a 32-bit shell): it may be
    # the npm one, so setup stops and names the PID.
    'missing-path': _PID_STOP,
    'unreadable-path': _PID_STOP,
    # ...unless the process has gone by the time it is looked at again.
    'exited-unreadable': None,
    # nvm-windows: the npm prefix is a junction, the image path is under its target.
    'junction-prefix': _PATH_STOP,
    'junction-image': _PATH_STOP,
    'junction-similar-prefix': None,
}


@pytest.mark.parametrize('kind', sorted(_GUARD_CASES))
def test_cli_install_blocks_running_claude_that_may_be_the_npm_one(tmp_path, kind):
    ps = shutil.which('powershell') or shutil.which('pwsh')
    if ps is None:
        pytest.skip('PowerShell required')
    if kind.startswith('junction') and sys.platform != 'win32':
        pytest.skip('junctions are a Windows feature')
    npm_prefix = tmp_path / 'npm'
    real = tmp_path / 'nvm' / 'v20'
    if kind in ('junction-prefix', 'junction-similar-prefix'):
        _junction(npm_prefix, real)
    elif kind == 'junction-image':
        _junction(tmp_path / 'link', npm_prefix)
    path = {
        'npm': str(tmp_path / 'NPM' / 'node_modules' / 'claude.exe'),
        'desktop': str(tmp_path / 'Claude Desktop' / 'claude.exe'),
        'similar-prefix': str(tmp_path / 'npm-desktop' / 'claude.exe'),
        'junction-prefix': str(real / 'node_modules' / 'claude.exe'),
        'junction-image': str(tmp_path / 'link' / 'node_modules' / 'claude.exe'),
        'junction-similar-prefix': str(tmp_path / 'nvm' / 'v20-desktop' / 'claude.exe'),
    }.get(kind, '')
    shown = str(npm_prefix / 'node_modules' / 'claude.exe') if kind == 'junction-image' else path
    executable_literal = "'" + path.replace("'", "''") + "'"
    script = tmp_path / 'repair-probe.ps1'
    script.write_text(
        'param($NpmPrefix, $Kind)\n'
        f'$Executable = {executable_literal}\n'
        "$ErrorActionPreference = 'Stop'\n"
        'function Get-NpmPrefix { return $NpmPrefix }\n'
        'function Get-Process {\n'
        '    param($Name, $Id)\n'
        '    if ($Id) {\n'
        '        if ($Kind -eq "exited-unreadable") { return $null }\n'
        '        return [pscustomobject]@{Id = $Id}\n'
        '    }\n'
        '    if ($Kind -in @("unreadable-path", "exited-unreadable")) {\n'
        '        $process = [pscustomobject]@{Id = 9999}\n'
        '        $process | Add-Member -MemberType ScriptProperty -Name Path -Value { throw "Access denied" }\n'
        '        return $process\n'
        '    }\n'
        '    return [pscustomobject]@{Id = 9999; Path = $Executable}\n'
        '}\n'
        'function Get-Command { param($Name) return $null }\n'
        'function Test-ClaudeWorks { return $true }\n'
        'function Install-ClaudeNpm { param([switch]$Clean) Write-Host "install called" }\n'
        + _function_source('Resolve-LinkedPath') + _npm_install_source()
        + "\nInvoke-ClaudeNpmInstall -Label 'probe'\n",
        encoding='utf-8',
    )
    result = subprocess.run(
        [ps, '-NoProfile', '-NonInteractive', '-File', str(script), str(npm_prefix), kind],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
    stop = _GUARD_CASES[kind]
    if stop is None:
        assert _PATH_STOP not in result.stdout and 'A Claude process is running' not in result.stdout
        assert 'install called' in result.stdout
        assert result.stdout.splitlines()[-1] == 'True'
        return
    assert 'install called' not in result.stdout
    assert result.stdout.splitlines()[-1] == 'False'
    if stop == _PATH_STOP:
        assert f"{_PATH_STOP} '{shown}'" in result.stdout
    else:
        assert _PID_STOP in result.stdout
        assert 'Close it, or re-run this installer from an elevated PowerShell' in result.stdout


def test_signing_workflow_requires_oidc_and_verifies_signature():
    workflow = WORKFLOW.read_text(encoding="utf-8").lower()
    assert "id-token: write" in workflow
    assert "azure/login@v3" in workflow
    assert "azure/artifact-signing-action@v2" in workflow
    assert "get-authenticodesignature" in workflow
    assert "timestamp.acs.microsoft.com" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "gh release" not in workflow
