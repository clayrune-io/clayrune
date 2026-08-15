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

  LOGON, NOT STARTUP — deliberate. An at-startup task would have to run as SYSTEM
  or store a password. SYSTEM has a different profile, so it would not see
  ~/.clayrune (the secrets vault + browser profiles), the Claude CLI's auth, or
  the user's PATH — Clayrune would boot and then fail at the first agent dispatch.
  Running at logon as the real user keeps all of that intact. The trade-off:
  after an unattended reboot this fires when the machine is signed in, so pair it
  with Windows' "automatically sign in after an update restart" if you want the
  gap closed completely.
#>

[CmdletBinding()]
param(
  [switch]$Install,
  [switch]$Uninstall,
  [switch]$Launch
)

$ErrorActionPreference = 'Continue'
$TaskName = 'ClayruneAutostart'

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
  $cfg = Join-Path $dir 'data\config.json'
  if (Test-Path $cfg) {
    try {
      $j = Get-Content $cfg -Raw | ConvertFrom-Json
      if ($j.PSObject.Properties.Name -contains 'port' -and $j.port) { return [int]$j.port }
    } catch {}
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
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
  # A minute of delay lets the network stack and any mapped drives settle; a
  # server that boots before DNS resolves just fails its first outbound calls.
  $trigger.Delay = 'PT45S'
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
  try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description 'Start Clayrune at logon if it is not already running.' -Force -ErrorAction Stop | Out-Null
    Log "installed task $TaskName -> $self"
    Write-Output "Installed scheduled task '$TaskName' (at logon, 45s delay)."
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
try {
  Start-Process -FilePath $py -ArgumentList 'server.py' -WorkingDirectory $dir -WindowStyle Hidden -ErrorAction Stop | Out-Null
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
Log "started but port $port never answered after ~40s"
Write-Output "Clayrune was launched but port $port never answered. See $log"
exit 1
