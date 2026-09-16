"""Real filesystem recovery with a fake Git clone; never touches a real install."""
from pathlib import Path
import shutil
import subprocess

import pytest

PS = shutil.which('powershell') or shutil.which('pwsh')
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not PS, reason='PowerShell required')
@pytest.mark.parametrize('case', ['empty', 'partial', 'data', 'clone_failure', 'swap_failure'])
def test_recovery_preserves_original(tmp_path, case):
    target = tmp_path / 'Clayrune with spaces'
    target.mkdir()
    if case != 'empty':
        (target / 'precious.txt').write_text('keep forever')
    if case == 'data':
        (target / 'data').mkdir()
        (target / 'data' / 'projects.json').write_text('user projects')
        (target / 'config.json').write_text('{"provider":"codex"}')
    source = (ROOT / 'installer/install.ps1').read_text(encoding='utf-8')
    function = 'function Repair-NonGitInstall {' + source.split('function Repair-NonGitInstall {', 1)[1].split("\nWrite-Host '[STEP 1/5]", 1)[0]
    script = r'''
$ErrorActionPreference = 'Stop'
function git {
    param($verb, $repo, $dest)
    if ('CASE' -eq 'clone_failure') { $global:LASTEXITCODE = 1; return }
    New-Item -ItemType Directory -Path (Join-Path $dest '.git') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $dest 'server.py') -Value 'fresh code'
    $global:LASTEXITCODE = 0
}
function Move-Item {
    param($LiteralPath, $Destination, $ErrorAction)
    if ('CASE' -eq 'swap_failure' -and $LiteralPath -match '\.install-') { throw 'swap failed' }
    Microsoft.PowerShell.Management\Move-Item -LiteralPath $LiteralPath -Destination $Destination -ErrorAction Stop
}
FUNCTION
try { Repair-NonGitInstall -Destination 'TARGET' -Repository 'test-repo' }
catch { Write-Output ('EXPECTED ERROR: ' + $_); exit 9 }
'''.replace('CASE', case).replace('FUNCTION', function).replace('TARGET', str(target).replace("'", "''"))
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-Command', script],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
    if case in ('clone_failure', 'swap_failure'):
        assert result.returncode == 9, result.stdout + result.stderr
        assert (target / 'precious.txt').read_text() == 'keep forever'
        assert not (target / '.git').exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert (target / '.git').is_dir()
        backups = list(tmp_path.glob('*.backup-*'))
        assert len(backups) == 1
        if case != 'empty':
            assert (backups[0] / 'precious.txt').read_text() == 'keep forever'
        if case == 'data':
            assert (target / 'data' / 'projects.json').read_text() == 'user projects'
            assert (target / 'config.json').read_text() == '{"provider":"codex"}'
