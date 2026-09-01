<#
  clayrune-autostart.ps1 — start Clayrune automatically when the machine comes back.

  WHY: Clayrune is the thing that runs the schedules, the stewards and the phone
  access. A Windows Update reboot at 3am silently takes all of that down, and the
  only symptom is that nothing happens and the phone stops answering. Nothing
  else notices, because the piece that would have noticed is the piece that died.

  THREE MODES
    -Install     register the logon Scheduled Task (idempotent — re-run to update)
    -Uninstall   remove it
    -Launch      what the task actually runs (also safe to run by hand)

  Defaults to -Launch with no switch, so the task definition stays short.

  SINGLE INSTANCE IS LOAD-BEARING. Clayrune assumes exactly one server per port;
  a second one racing the first for the same data dir corrupts project state.
  -Launch therefore probes the port FIRST and exits quietly if anything already
  answers. That makes the task safe to fire repeatedly, and safe to run by hand
  while the server is already up.

  LOGON BY DEFAULT, AT BOOT WITH -AtBoot. The original reasoning here was that an
  at-startup task must run as SYSTEM or store a password, and SYSTEM has a
  different profile — it would not see ~/.clayrune (the secrets vault + browser
  profiles), the Claude CLI's auth, or the user's PATH, so Clayrune would boot and
  then fail at the first agent dispatch. That is still true of SYSTEM, but it
  missed a third option: LogonType S4U runs the task AS THE REAL USER with no
  password stored anywhere.

  The risk S4U carries is DPAPI — an S4U logon cannot always decrypt
  user-protected data, and the remote-access device identity lives in Windows
  Credential Manager, which is DPAPI-backed. That failure would be the worst kind:
  Clayrune up, tunnel dead, reachable only from the machine you are not at.
  Measured 2026-08-29 before adopting it — an S4U task reads all six
  `mission-control-remote` keys (WinVaultKeyring), and a full server booted and
  served pre-login in ~4s. So -AtBoot closes the gap this header used to hand to
  Windows' "automatically sign in after an update restart".

  Use -Install for the logon task (a machine someone signs into), -Install -AtBoot
  for the boot task (a machine you reach only remotely). -AtBoot needs an elevated
  PowerShell; registering an S4U principal is an admin operation.
#>

[CmdletBinding()]
param(
  [switch]$Install,
  [switch]$Uninstall,
  [switch]$Launch,
  [switch]$AtBoot
)

$ErrorActionPreference = 'Continue'
$TaskName = 'ClayruneAutostart'
$WindowTaskName = 'ClayruneAutostartWindow'

# Derive the install dir from this script's own location (tools/ -> repo root)
# rather than hardcoding one machine's path — same rule as tools/_mc_restart.ps1.
# MC_DIR overrides it for a non-standard layout.
$dir = if ($env:MC_DIR) { $env:MC_DIR }
       else { Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$log = Join-Path $dir 'data\_mc_autostart.log'

function Log($m) {
  try { "$(Get-Date -Format o)  $m" | Out-File -FilePath $log -Append -Encoding utf8 } catch {}
}

# Prefer the venv interpreter: the server is started from .venv today, and the
# system python may not have Flask at all. pythonw keeps the console hidden —
# a stray terminal on every logon is exactly the kind of thing that gets an
# autostart deleted.
function Resolve-Python {
  $candidates = @(
    (Join-Path $dir '.venv\Scripts\pythonw.exe'),
    (Join-Path $dir '.venv\Scripts\python.exe')
  )
  foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
  if ($env:MC_PYTHON -and (Test-Path $env:MC_PYTHON)) { return $env:MC_PYTHON }
  $g = Get-Command pythonw.exe -ErrorAction SilentlyContinue
  if ($g) { return $g.Source }
  $g = Get-Command python.exe -ErrorAction SilentlyContinue
  if ($g) { return $g.Source }
  return $null
}

# Read the port from config rather than assuming 5199 — server.py resolves it as
# MC_PORT, then config.port, then 5199, and the probe has to agree or it will
# cheerfully start a second server alongside the first.
function Resolve-Port {
  if ($env:MC_PORT) { return [int]$env:MC_PORT }
  # server.py: CONFIG_PATH = _DATA_ROOT / 'config.json' — the repo ROOT, not
  # data/. This used to look in data\config.json, which never exists, so it
  # silently fell back to 5199. Harmless while the port IS 5199 and a
  # split-brain waiting to happen the day it isn't: the probe would find
  # nothing on 5199 and start a second server on the real port. data\ is kept
  # as a fallback in case an older layout ever put it there.
  foreach ($cfg in @((Join-Path $dir 'config.json'), (Join-Path $dir 'data\config.json'))) {
    if (Test-Path $cfg) {
      try {
        $j = Get-Content $cfg -Raw | ConvertFrom-Json
        if ($j.PSObject.Properties.Name -contains 'port' -and $j.port) { return [int]$j.port }
      } catch {}
    }
  }
  return 5199
}

function Test-ClayruneUp([int]$port) {
  try {
    $c = New-Object Net.Sockets.TcpClient
    $iar = $c.BeginConnect('127.0.0.1', $port, $null, $null)
    $ok = $iar.AsyncWaitHandle.WaitOne(1200)
    if ($ok -and $c.Connected) { $c.Close(); return $true }
    $c.Close()
  } catch {}
  return $false
}

# ── Install ─────────────────────────────────────────────────────────────────
if ($Install) {
  $self = $MyInvocation.MyCommand.Path
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -Launch' -f $self)
  # A minute of delay lets the network stack and any mapped drives settle; a
  # server that boots before DNS resolves just fails its first outbound calls.
  if ($AtBoot) {
    # S4U: the real user's identity, no password stored. See the header for why
    # this is safe (DPAPI/Credential Manager was measured, not assumed) and why
    # SYSTEM is not. Registering an S4U principal requires elevation.
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not ([Security.Principal.WindowsPrincipal]::new($id)).IsInRole(
          [Security.Principal.WindowsBuiltInRole]::Administrator)) {
      Write-Output "-AtBoot needs an elevated PowerShell (Run as administrator)."
      exit 1
    }
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $trigger.Delay = 'PT30S'
    $principal = New-ScheduledTaskPrincipal -UserId $id.Name -LogonType S4U -RunLevel Limited
    $desc = 'Start Clayrune at boot (before login) if it is not already running.'
    $when = 'at system startup, before login, 30s delay'

    # The boot task above starts the server BEFORE anyone logs in — S4U has no
    # desktop to show a window on. So the visible app window (the same
    # standalone Chrome/Edge --app= window the Desktop shortcut opens) has to
    # come from a second task that fires AT ACTUAL LOGON, once a desktop
    # exists. It runs start-hidden.vbs directly (not -Launch): that script
    # already does its own "server already up? just open the window" check
    # (installer\start.bat), so there's no port-race with the boot task.
    $winAction = New-ScheduledTaskAction -Execute 'wscript.exe' `
      -Argument ('"{0}"' -f (Join-Path $dir 'installer\start-hidden.vbs'))
    $winTrigger = New-ScheduledTaskTrigger -AtLogOn -User $id.Name
    $winTrigger.Delay = 'PT10S'
    $winSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    try {
      Register-ScheduledTask -TaskName $WindowTaskName -Action $winAction -Trigger $winTrigger `
        -Settings $winSettings -Description 'Open the Clayrune app window at logon.' -Force -ErrorAction Stop | Out-Null
      Log "installed task $WindowTaskName (at logon, 10s delay) -> $(Join-Path $dir 'installer\start-hidden.vbs')"
    } catch {
      Write-Output "FAILED to register window task: $_"
      Log "window task install failed: $_"
    }
  } else {
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $trigger.Delay = 'PT45S'
    $principal = $null
    $desc = 'Start Clayrune at logon if it is not already running.'
    $when = 'at logon, 45s delay'
  }
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
  try {
    $reg = @{ TaskName = $TaskName; Action = $action; Trigger = $trigger;
              Settings = $settings; Description = $desc; Force = $true;
              ErrorAction = 'Stop' }
    if ($principal) { $reg['Principal'] = $principal }
    Register-ScheduledTask @reg | Out-Null
    Log "installed task $TaskName ($when) -> $self"
    Write-Output "Installed scheduled task '$TaskName' ($when)."
    Write-Output ('Uninstall with: powershell -File "' + $self + '" -Uninstall')
  } catch {
    Write-Output "FAILED to register task: $_"
    Log "install failed: $_"
    exit 1
  }
  exit 0
}

# ── Uninstall ───────────────────────────────────────────────────────────────
if ($Uninstall) {
  try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Log "uninstalled task $TaskName"
    Write-Output "Removed scheduled task '$TaskName'."
  } catch {
    Write-Output "Task '$TaskName' was not registered (nothing to remove)."
  }
  try {
    Unregister-ScheduledTask -TaskName $WindowTaskName -Confirm:$false -ErrorAction Stop
    Log "uninstalled task $WindowTaskName"
    Write-Output "Removed scheduled task '$WindowTaskName'."
  } catch {}
  exit 0
}

# ── Launch (default) ────────────────────────────────────────────────────────
$port = Resolve-Port
Log "=== autostart launch (dir=$dir port=$port) ==="

if (Test-ClayruneUp $port) {
  Log "port $port already answering — leaving the running instance alone"
  Write-Output "Clayrune is already running on port $port."
  exit 0
}

$py = Resolve-Python
if (-not $py) {
  Log 'no python interpreter found — cannot start'
  Write-Output 'No python interpreter found.'
  exit 1
}

$serverPy = Join-Path $dir 'server.py'
if (-not (Test-Path $serverPy)) {
  Log "server.py not found at $serverPy"
  Write-Output "server.py not found at $serverPy"
  exit 1
}

Log "starting: $py server.py"
# Capture the server's own output. Without this, a server that starts and dies
# leaves only "port never answered" below and no cause — and after an unattended
# reboot that log is the only thing anyone can read remotely.
#
# Deliberately NOT data\logs\clayrune.log: start.bat / start-hidden.vbs hold
# that file open with a share mode that denies a second writer, so writing there
# fails exactly when a second launcher is involved. These two files are ours.
# They are truncated per launch, so what you read is this boot, not a year of them.
$logDir = Join-Path $dir 'data\logs'
try { if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null } } catch {}
$outLog = Join-Path $logDir 'clayrune-autostart-server.log'
$errLog = Join-Path $logDir 'clayrune-autostart-server.err.log'
try {
  Start-Process -FilePath $py -ArgumentList 'server.py' -WorkingDirectory $dir -WindowStyle Hidden `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog -ErrorAction Stop | Out-Null
} catch {
  Log "start failed: $_"
  Write-Output "Failed to start Clayrune: $_"
  exit 1
}

# Confirm it actually came up rather than reporting success on a spawn that
# died three seconds later — the failure mode that makes an autostart look
# fine while nothing is running.
$up = $false
foreach ($i in 1..20) {
  Start-Sleep -Seconds 2
  if (Test-ClayruneUp $port) { $up = $true; break }
}
if ($up) {
  Log "Clayrune is up on port $port"
  Write-Output "Clayrune started on port $port."
  exit 0
}
Log "started but port $port never answered after ~40s — server output in $outLog / $errLog"
Write-Output "Clayrune was launched but port $port never answered. See $log and $errLog"
exit 1
