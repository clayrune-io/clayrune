<#
.SYNOPSIS
    Make Clayrune start automatically when this PC boots (Windows).

.DESCRIPTION
    Registers a scheduled task named "Clayrune" that runs
    installer\start-boot.bat at system startup - before anyone logs in - so the
    dashboard and the remote-access tunnel are reachable from a phone or
    another machine after an unattended reboot.

    Why a scheduled task and not the Startup folder: the Startup folder only
    fires at interactive logon. A PC that reboots overnight and sits at the
    lock screen would leave Clayrune down until someone physically logged in,
    which defeats the point of remote access.

    Why LogonType S4U and not a stored password: S4U ("service for user") runs
    the task under your identity with no password stored anywhere. The catch
    to know about is that S4U logons cannot always decrypt DPAPI-protected
    data, and the remote-access device identity lives in Windows Credential
    Manager, which is DPAPI-backed. Verified on 2026-08-29 that an S4U task
    reads those entries fine (WinVaultKeyring, all six keys returned), so the
    tunnel does come up at boot. If a future Windows change breaks that, the
    symptom is Clayrune running but remote access offline, and the fix is to
    re-register the task with -Password (a batch logon, which always gets
    DPAPI) rather than S4U.

    The task is bounded, not a restart loop: it retries 3 times a minute apart
    if the server fails to launch, then stops.

.PARAMETER Remove
    Unregister the task instead of creating it.

.PARAMETER Status
    Show whether the task exists, when it last ran, and what it returned.

.PARAMETER DelaySeconds
    Seconds to wait after boot before starting (default 30) so networking and
    disks are up first.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\install-autostart.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\install-autostart.ps1 -Status
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\install-autostart.ps1 -Remove
#>
[CmdletBinding()]
param(
    [switch]$Remove,
    [switch]$Status,
    [int]$DelaySeconds = 30
)

$ErrorActionPreference = 'Stop'
$TaskName = 'Clayrune'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]::new($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-ClayruneTask {
    try { return Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop } catch { return $null }
}

# ── Status ──────────────────────────────────────────────────────────────────
if ($Status) {
    $t = Get-ClayruneTask
    if ($null -eq $t) {
        Write-Output "Clayrune autostart: NOT INSTALLED"
        exit 1
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Output "Clayrune autostart: INSTALLED"
    Write-Output "  State        : $($t.State)"
    Write-Output "  Runs as      : $($t.Principal.UserId) (LogonType $($t.Principal.LogonType))"
    Write-Output "  Action       : $($t.Actions[0].Execute) $($t.Actions[0].Arguments)"
    Write-Output "  Last run     : $($info.LastRunTime)"
    Write-Output "  Last result  : $($info.LastTaskResult)  (0 = ok, 2 = port already held by another Clayrune)"
    exit 0
}

# ── Remove ──────────────────────────────────────────────────────────────────
if ($Remove) {
    if (-not (Test-Admin)) { throw "Removing the task needs an elevated PowerShell (Run as administrator)." }
    if ($null -eq (Get-ClayruneTask)) {
        Write-Output "Clayrune autostart was not installed - nothing to remove."
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Removed the '$TaskName' scheduled task. Clayrune will no longer start at boot."
    Write-Output "A Clayrune already running right now is untouched and keeps running."
    exit 0
}

# ── Install ─────────────────────────────────────────────────────────────────
if (-not (Test-Admin)) {
    throw "Registering a boot task needs an elevated PowerShell (Run as administrator)."
}

# Repo root = parent of the directory holding this script. Derived, never
# hardcoded: this file ships to other people's machines.
$ScriptDir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$ClayruneDir  = Split-Path -Parent $ScriptDir
$BootBat      = Join-Path $ClayruneDir 'installer\start-boot.bat'

if (-not (Test-Path $BootBat)) { throw "Cannot find $BootBat - is this script still inside the Clayrune repo?" }
if (-not (Test-Path (Join-Path $ClayruneDir '.venv\Scripts\activate.bat'))) {
    Write-Warning "No .venv at $ClayruneDir\.venv - the task will register but fail until you run the installer."
}

$User = [Security.Principal.WindowsIdentity]::GetCurrent().Name

$action = New-ScheduledTaskAction -Execute 'cmd.exe' `
    -Argument ('/c "' + $BootBat + '"') -WorkingDirectory $ClayruneDir

$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT${DelaySeconds}S"

# S4U: runs as $User with no stored password. See .DESCRIPTION for why.
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType S4U -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force `
    -Description "Starts Clayrune (Mission Control) headless at boot so it is reachable remotely. Managed by tools\install-autostart.ps1." | Out-Null

Write-Output "Installed the '$TaskName' scheduled task."
Write-Output "  Starts   : at system startup, ${DelaySeconds}s delay, before login"
Write-Output "  Runs as  : $User (no password stored)"
Write-Output "  Command  : $BootBat"
Write-Output "  Logs     : $ClayruneDir\data\logs\clayrune.log (server), clayrune-boot.log (task)"
Write-Output ""
Write-Output "No browser window opens at boot - open http://localhost:5199 yourself when you want it."
Write-Output "Remove with: powershell -ExecutionPolicy Bypass -File tools\install-autostart.ps1 -Remove"
