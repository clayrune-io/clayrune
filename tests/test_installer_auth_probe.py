"""Exercise the actual PowerShell auth function without running the installer."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
PS = shutil.which('powershell') or shutil.which('pwsh')


@pytest.mark.skipif(not PS, reason='PowerShell required')
@pytest.mark.parametrize('output,code,timeout,expected', [
    ('{"loggedIn":true}', 0, False, 'True'),
    ('{"loggedIn":false}', 1, False, 'False'),
    ('{"loggedIn":true}', 1, False, 'ERROR:'),
    ('{"loggedIn":false}', 2, False, 'ERROR:'),
    ('{"other":true}', 0, False, 'ERROR:'),
    ('not JSON', 1, False, 'ERROR:'),
    ('', 0, True, 'ERROR:'),
])
def test_auth_status_is_bounded_and_fail_closed(output, code, timeout, expected):
    source = (ROOT / 'installer/install.ps1').read_text(encoding='utf-8')
    function = source.split('function Test-ClaudeAuth {', 1)[1].split("\nWrite-Host '======================================'", 1)[0]
    function = 'function Test-ClaudeAuth {' + function
    assert 'claude -p' not in function
    assert '& $cli.Source auth status' in function
    script = r'''
$ErrorActionPreference = 'Stop'
$script:cleaned = $false
function Start-Job { param($ScriptBlock) return 'owned-job' }
function Wait-Job { param($Job, $Timeout)
    if ($Timeout -ne 20) { throw 'wrong timeout' }
    WAIT_RESULT
}
function Receive-Job { param($Job, $ErrorAction)
    [pscustomobject]@{Output = 'OUTPUT'; ExitCode = CODE}
}
function Stop-Job { param($Job, $ErrorAction) }
function Remove-Job { param($Job, [switch]$Force, $ErrorAction) $script:cleaned = $true }
FUNCTION
try { Test-ClaudeAuth } catch { Write-Output ('ERROR:' + $_) }
if (-not $script:cleaned) { throw 'job was not cleaned up' }
'''.replace('WAIT_RESULT', '' if timeout else 'return $Job').replace('OUTPUT', output).replace('CODE', str(code)).replace('FUNCTION', function)
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-Command', script],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith(expected), result.stdout
