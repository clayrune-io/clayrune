<#
  Safe Clayrune uninstaller for Windows.

  Default: remove the application, shortcuts, and Clayrune autostart tasks;
  preserve provider CLIs, ~/.claude, ~/.clayrune, external projects, and a copy
  of checkout-backed Clayrune data under ~/.clayrune/uninstall-archives/.

  -PurgeData: also remove Clayrune-owned runtime data after a stronger prompt.
  Provider CLIs, Node.js, Python, Git, ~/.claude, and project directories are
  never removed by this script.
#>

[CmdletBinding()]
param(
    [string]$InstallDir = '',
    [switch]$PurgeData,
    [switch]$Yes,
    [switch]$DryRun,
    # Public mainly so support/tests can isolate the operation. It does not
    # broaden deletion: every recursive target still passes the path guards.
    [string]$UserHome = ''
)

$ErrorActionPreference = 'Stop'

function Resolve-FullPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    return [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($Path))
}

function Test-SamePath([string]$A, [string]$B) {
    if (-not $A -or -not $B) { return $false }
    return ([string]::Equals(
        (Resolve-FullPath $A).TrimEnd('\'),
        (Resolve-FullPath $B).TrimEnd('\'),
        [StringComparison]::OrdinalIgnoreCase
    ))
}

function Assert-SafeRecursiveTarget([string]$Path, [string]$Label) {
    $full = Resolve-FullPath $Path
    if (-not $full) { throw "Refusing empty $Label path." }
    $root = [IO.Path]::GetPathRoot($full)
    if (Test-SamePath $full $root) { throw "Refusing to remove drive root: $full" }
    if (Test-SamePath $full $script:HomeDir) { throw "Refusing to remove the user home: $full" }
    return $full
}

function Write-Action([string]$Message) {
    $prefix = if ($DryRun) { '[dry run] ' } else { '' }
    Write-Host "$prefix$Message"
}

function Remove-SafePath([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $safe = Assert-SafeRecursiveTarget $Path $Label
    Write-Action "Remove $Label`: $safe"
    if (-not $DryRun) {
        Remove-Item -LiteralPath $safe -Recurse -Force
    }
}

function Remove-SafeFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Write-Action "Remove $Label`: $Path"
    if (-not $DryRun) { Remove-Item -LiteralPath $Path -Force }
}

$HomeDir = if ($UserHome) { Resolve-FullPath $UserHome } else { Resolve-FullPath $env:USERPROFILE }
if (-not $HomeDir) { throw 'Could not resolve the user home directory.' }

$InstallDir = if ($InstallDir) {
    Resolve-FullPath $InstallDir
} elseif ($env:CLAYRUNE_HOME) {
    Resolve-FullPath $env:CLAYRUNE_HOME
} else {
    Join-Path $HomeDir 'Clayrune'
}
$InstallDir = Assert-SafeRecursiveTarget $InstallDir 'install directory'

# A custom UserHome is used by the isolated regression test. Never let such a
# run inherit the real account's APPDATA and accidentally cross that boundary.
$realHome = Resolve-FullPath $env:USERPROFILE
$isCurrentHome = Test-SamePath $HomeDir $realHome
$appDataBase = if ((Test-SamePath $HomeDir $realHome) -and $env:APPDATA) {
    Resolve-FullPath $env:APPDATA
} else {
    Join-Path $HomeDir 'AppData\Roaming'
}
$frozenDataDir = if ($env:MC_DATA_DIR -and (Test-SamePath $HomeDir $realHome)) {
    Resolve-FullPath $env:MC_DATA_DIR
} else {
    Join-Path $appDataBase 'MissionControl'
}
$stateDir = Join-Path $HomeDir '.clayrune'

$installExists = Test-Path -LiteralPath $InstallDir
if ($installExists) {
    $serverMarker = Join-Path $InstallDir 'server.py'
    $installerMarker = Join-Path $InstallDir 'installer'
    if (-not (Test-Path -LiteralPath $serverMarker) -or
        -not (Test-Path -LiteralPath $installerMarker -PathType Container)) {
        throw "Refusing to remove '$InstallDir': it is not recognizably a Clayrune checkout."
    }
}

Write-Host ''
Write-Host 'Clayrune Uninstaller' -ForegroundColor Cyan
Write-Host "  Application: $InstallDir"
Write-Host "  Mode:        $(if ($PurgeData) { 'remove app + Clayrune-owned data' } else { 'remove app, preserve data' })"
Write-Host ''
Write-Host 'Always preserved:'
Write-Host '  - AI provider CLIs, Node.js, Python, and Git'
Write-Host '  - ~/.claude (provider authentication, transcripts, and user customizations)'
Write-Host '  - project directories referenced by Clayrune'
if (-not $PurgeData) {
    Write-Host '  - ~/.clayrune and frozen-app data'
    Write-Host '  - checkout data/config copied to ~/.clayrune/uninstall-archives/'
} else {
    Write-Host ''
    Write-Host 'PURGE also removes:' -ForegroundColor Yellow
    Write-Host "  - $stateDir"
    Write-Host "  - $frozenDataDir"
    Write-Host '  - checkout-backed Clayrune data/config (no uninstall archive)'
}
Write-Host ''

if (-not $DryRun -and -not $Yes) {
    $expected = if ($PurgeData) { 'PURGE CLAYRUNE DATA' } else { 'UNINSTALL' }
    $answer = Read-Host "Type $expected to continue"
    if ($answer -cne $expected) {
        Write-Host 'Cancelled. Nothing was changed.'
        exit 0
    }
}

# Stop only listeners whose executable or command line proves they belong to
# this install. Merely owning Clayrune's usual port is never enough evidence.
$ports = New-Object 'System.Collections.Generic.HashSet[int]'
[void]$ports.Add(5199)
foreach ($cfgPath in @((Join-Path $InstallDir 'config.json'), (Join-Path $frozenDataDir 'config.json'))) {
    if (Test-Path -LiteralPath $cfgPath) {
        try {
            $cfg = Get-Content -LiteralPath $cfgPath -Raw | ConvertFrom-Json
            if ($cfg.port) { [void]$ports.Add([int]$cfg.port) }
        } catch {
            Write-Warning "Could not read port from $cfgPath; continuing with known ports."
        }
    }
}

if ($isCurrentHome) {
    foreach ($port in $ports) {
        $connections = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
        foreach ($connection in $connections) {
            $pidNumber = [int]$connection.OwningProcess
            try {
                $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pidNumber" -ErrorAction Stop
                $identity = "$($proc.ExecutablePath) $($proc.CommandLine)"
                $belongsToInstall = $identity.IndexOf($InstallDir, [StringComparison]::OrdinalIgnoreCase) -ge 0
                $belongsToFrozen = $identity -match '(?i)Clayrune(?:\.exe)?'
                if ($belongsToInstall -or $belongsToFrozen) {
                    Write-Action "Stop Clayrune process PID $pidNumber on port $port"
                    if (-not $DryRun) { Stop-Process -Id $pidNumber -Force -ErrorAction Stop }
                } else {
                    Write-Warning "Port $port is owned by PID $pidNumber, but it is not identifiable as Clayrune; leaving it running."
                }
            } catch {
                Write-Warning "Could not verify PID $pidNumber on port $port; leaving it running."
            }
        }
    }
}

# Remove only Clayrune's fixed-name scheduled tasks. Failure (commonly a boot
# task requiring elevation) is surfaced and does not make other cleanup unsafe.
if ($isCurrentHome) {
    foreach ($taskName in @('ClayruneAutostart', 'ClayruneAutostartWindow')) {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($task) {
            Write-Action "Remove scheduled task: $taskName"
            if (-not $DryRun) {
                try {
                    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
                } catch {
                    Write-Warning "Could not remove scheduled task '$taskName': $_"
                }
            }
        }
    }
}

$shortcutPaths = @(
    (Join-Path $HomeDir 'Desktop\Clayrune.lnk'),
    (Join-Path $appDataBase 'Microsoft\Windows\Start Menu\Programs\Clayrune.lnk'),
    (Join-Path $appDataBase 'Microsoft\Windows\Start Menu\Programs\Uninstall Clayrune.lnk')
)
foreach ($shortcut in $shortcutPaths) { Remove-SafeFile $shortcut 'launcher' }

if ($installExists -and -not $PurgeData) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $archiveDir = Join-Path $stateDir "uninstall-archives\$stamp"
    $archiveItems = @('config.json', 'data') | Where-Object {
        Test-Path -LiteralPath (Join-Path $InstallDir $_)
    }
    if ($archiveItems.Count -gt 0) {
        Write-Action "Preserve checkout data in: $archiveDir"
        if (-not $DryRun) {
            New-Item -ItemType Directory -Path $archiveDir -Force | Out-Null
            foreach ($item in $archiveItems) {
                Copy-Item -LiteralPath (Join-Path $InstallDir $item) -Destination $archiveDir -Recurse -Force
            }
            @(
                'Clayrune uninstall archive',
                "Created: $(Get-Date -Format o)",
                "Original install: $InstallDir",
                'Restore by reinstalling Clayrune, closing it, then copying config.json and data/ back.'
            ) | Set-Content -LiteralPath (Join-Path $archiveDir 'README.txt') -Encoding UTF8
        }
    }
}

# Leave the directory before removing a checkout that may contain this script.
$currentPath = Resolve-FullPath (Get-Location).Path
if ((Test-SamePath $currentPath $InstallDir) -or
    $currentPath.StartsWith($InstallDir.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    Set-Location ([IO.Path]::GetTempPath())
}
if ($installExists) { Remove-SafePath $InstallDir 'Clayrune application directory' }

if ($PurgeData) {
    if (-not (Test-SamePath $frozenDataDir $InstallDir)) {
        Remove-SafePath $frozenDataDir 'Clayrune frozen-app data'
    }
    if (-not (Test-SamePath $stateDir $InstallDir)) {
        Remove-SafePath $stateDir 'Clayrune private state'
    }
}

Write-Host ''
if ($DryRun) {
    Write-Host 'Dry run complete. Nothing was changed.' -ForegroundColor Green
} else {
    Write-Host 'Clayrune was uninstalled.' -ForegroundColor Green
    if (-not $PurgeData) {
        Write-Host "Preserved data is under $stateDir"
    }
}
Write-Host 'Provider CLIs, ~/.claude, and project directories were not changed.'
