# Clayrune installer bootstrap (Windows)
#
# Usage (in PowerShell):
#   iwr https://clayrune.io/install.ps1 -useb | iex
#
# What this script does:
#   1. Installs Clayrune itself (without requiring a provider CLI).
#   2. Opens Clayrune's first-run provider picker.
#   3. Installs/logs in to the provider the user selects, when needed.
#
# The bootstrap performs the deterministic install directly, creates a Desktop
# / Start Menu shortcut, and opens the app in the user's browser. Provider
# setup is intentionally deferred to the first-run UI unless explicitly pinned.
#
# Read the install prompt before running:
#   iwr https://clayrune.io/install-prompt.md -useb | Select-Object -ExpandProperty Content
#
# Override URLs (for testing):
#   $env:CLAYRUNE_PROMPT_URL = '...'
#   $env:CLAYRUNE_NO_CONFIRM = '1'   # skip the 5-second abort window
#   $env:CLAYRUNE_PROVIDER = '...'  # optional explicit provider override:
#                                    # claude|codex|gemini|qwen
#
# EXIT CODES — a contract, not an accident. installer/win-exe/ClayruneInstaller.cs
# maps these to the remediation menu it shows the user, so DO NOT reuse or
# renumber them without updating that file too. (Before 2026-07-23 everything
# non-zero was reported to the user as "you probably aren't logged in", which
# sent someone through a pointless OAuth login when the real failure was git.)
#
#   0  success
#   1  an explicitly requested provider prerequisite could not be installed
#   2  a deterministic install step failed — see the red "[STEP n/5] FAIL" line
#   3  an explicitly requested Claude CLI is installed but NOT AUTHENTICATED

$ErrorActionPreference = 'Stop'

$PromptUrl = if ($env:CLAYRUNE_PROMPT_URL) { $env:CLAYRUNE_PROMPT_URL } `
             else { 'https://clayrune.io/install-prompt.md' }

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

# LOAD-BEARING. In PowerShell a native command's stdout goes to the PIPELINE,
# so calling `winget ...` inside a function silently appends winget's output
# lines to that function's RETURN VALUE. `return $false` then yields
# @('...winget line...', $false) - an Object[] - and `if (-not (Setup-Node))`
# evaluates `-not <non-empty array>` = $false, so the failure guard NEVER FIRES.
# That is exactly how a fresh-VM install fell through a failed Node install and
# looped forever. Route every native call through here: Write-Host goes to the
# console only, never the pipeline, and we return the real exit code (a native
# non-zero exit is NOT a terminating error, so try/catch cannot see it).
#
# ALSO LOAD-BEARING: the first parameter is named $Command, not $Exe.
# PowerShell binds single-dash arguments to parameter names by PREFIX, so with
# `[string]$Exe` the call
#     Invoke-Native winget install --id Git.Git -e --silent --source winget
# bound winget's `-e` (--exact) to `-Exe` and swallowed `--silent` as its
# value. The function then tried to launch `--silent`, caught the
# CommandNotFoundException, and returned -1. Measured on a clean Windows 11 VM
# 2026-09-20: EVERY winget call in this file (Node, Python, Git x2) had been
# dead since the helper was introduced, always silently taking the
# direct-download fallback — including the `--source winget` pin added in
# 6a9da6e, which never once executed. Double-dash arguments are never bound as
# parameter names, so `--silent` / `--source` are safe; short flags are not.
# Keep the name free of any single-dash winget/npm flag prefix, and pass
# -Command/-Arguments explicitly at winget call sites.
function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'   # stderr must not become a terminating error
    try {
        & $Command @Arguments 2>&1 | ForEach-Object { Write-Host $_ }
        return $LASTEXITCODE
    } catch {
        Write-Host "  ($Command could not be launched: $_)" -ForegroundColor DarkGray
        return -1
    } finally {
        $ErrorActionPreference = $prev
    }
}

# Belt-and-braces at every call site: take the LAST value a function emitted.
# Even if some future edit re-introduces pipeline pollution, the `return $true`
# / `return $false` is always the final element, so the guard still works.
function Get-BoolResult {
    param($Value)
    $items = @($Value)
    if ($items.Count -eq 0) { return $false }
    return [bool]$items[-1]
}

# Node dirs Node can legitimately land in (winget MSI, non-admin installer, our
# own zip drop). winget writes the MSI's dir to the *Machine* PATH, which a
# non-elevated Refresh-Path may not see until the next logon - so probe the
# filesystem directly rather than trusting PATH.
function Add-NodeToPathIfPresent {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Clayrune\node'),
        (Join-Path $env:ProgramFiles 'nodejs'),
        (Join-Path $env:LOCALAPPDATA 'Programs\nodejs')
    )
    if (${env:ProgramFiles(x86)}) {
        $candidates += (Join-Path ${env:ProgramFiles(x86)} 'nodejs')
    }
    foreach ($dir in $candidates) {
        if (-not $dir) { continue }
        if (Test-Path (Join-Path $dir 'node.exe')) {
            if (-not (";$env:Path;".ToLower().Contains(";$($dir.ToLower());"))) {
                $env:Path = "$dir;$env:Path"
            }
            return $true
        }
    }
    return $false
}

# winget-free, admin-free Node install: the official Windows .zip from
# nodejs.org (which bundles npm) unpacked into %LOCALAPPDATA%\Clayrune\node.
# The winget package installs an MSI that wants elevation and writes a Machine
# PATH entry - both of which fail or go unseen on a locked-down / fresh box.
# This path needs neither.
function Install-NodeZip {
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x64' }
    Write-Host "  Fetching Node.js LTS ($arch) direct from nodejs.org (no admin needed)..."

    try {
        $index = Invoke-RestMethod -Uri 'https://nodejs.org/dist/index.json' -UseBasicParsing -TimeoutSec 60
    } catch {
        Write-Host "  Could not reach nodejs.org: $_" -ForegroundColor Red
        return $false
    }
    $lts = $index | Where-Object { $_.lts -is [string] } | Select-Object -First 1
    if (-not $lts) {
        Write-Host '  nodejs.org returned no LTS release.' -ForegroundColor Red
        return $false
    }

    $ver     = $lts.version                       # e.g. v22.20.0
    $name    = "node-$ver-win-$arch"
    $url     = "https://nodejs.org/dist/$ver/$name.zip"
    $zipPath = Join-Path $env:TEMP "clayrune-$name.zip"
    $root    = Join-Path $env:LOCALAPPDATA 'Clayrune'
    $target  = Join-Path $root 'node'

    Write-Host "  Downloading $name.zip ..."
    try {
        $progressPreference = 'SilentlyContinue'   # the progress UI makes iwr ~10x slower
        Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing -TimeoutSec 600
    } catch {
        Write-Host "  Download failed: $_" -ForegroundColor Red
        return $false
    }

    try {
        New-Item -ItemType Directory -Force -Path $root | Out-Null
        if (Test-Path $target) { Remove-Item -Recurse -Force $target }
        $staging = Join-Path $root $name
        if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
        Expand-Archive -Path $zipPath -DestinationPath $root -Force
        Rename-Item -Path $staging -NewName 'node'
    } catch {
        Write-Host "  Unpack failed: $_" -ForegroundColor Red
        return $false
    } finally {
        Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
    }

    if (-not (Test-Path (Join-Path $target 'node.exe'))) {
        Write-Host '  Unpacked archive has no node.exe.' -ForegroundColor Red
        return $false
    }

    # Persist to the USER PATH so the Clayrune app (and every later shell) sees
    # node/npm too - not just this process.
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not $userPath) { $userPath = '' }
    if (-not (";$userPath;".ToLower().Contains(";$($target.ToLower());"))) {
        [Environment]::SetEnvironmentVariable('Path', "$target;$userPath", 'User')
    }
    $env:Path = "$target;$env:Path"

    Write-Host "  Node installed to $target" -ForegroundColor Green
    return $true
}

# Returns Node major version on PATH, or 0 if missing/invalid.
function Get-NodeMajor {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { return 0 }
    try {
        $v = (& node --version 2>$null).Trim().TrimStart('v')
        $major = [int]($v -split '\.')[0]
        return $major
    } catch {
        return 0
    }
}

# Ensure Node 18+ is on PATH. Already-good Node -> no-op. Old or missing ->
# install Node LTS via winget. Must run BEFORE any Claude CLI install attempt
# because npm-installed Claude CLI requires Node 18+ to even parse its own
# source.
function Setup-Node {
    $major = Get-NodeMajor
    if ($major -ge 18) {
        return $true
    }

    # Node may be installed already but invisible to us: winget's MSI writes its
    # dir to the *Machine* PATH, which this process won't pick up until the next
    # logon. Look on disk before concluding it's missing.
    if (Add-NodeToPathIfPresent) {
        $major = Get-NodeMajor
        if ($major -ge 18) {
            Write-Host "OK Node $(& node --version 2>&1) (found on disk, added to PATH)" -ForegroundColor Green
            Write-Host ''
            return $true
        }
    }

    if ($major -eq 0) {
        Write-Host 'Node.js not found. Need 18+ for Claude CLI.' -ForegroundColor Yellow
    } else {
        Write-Host "Node.js v$major found - too old for Claude CLI (need 18+)." -ForegroundColor Yellow
    }

    # Attempt 1: winget. Best-effort only - on a fresh box it commonly fails
    # (needs elevation for the machine-wide MSI, or its sources aren't
    # initialised yet) and it fails by EXIT CODE, not by throwing.
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host 'Installing Node.js LTS via winget...'
        $rc = Invoke-Native -Command winget -Arguments @(
            'install', '--id', 'OpenJS.NodeJS.LTS', '-e', '--silent',
            '--source', 'winget',
            '--accept-source-agreements', '--accept-package-agreements')
        if ($rc -ne 0) {
            Write-Host "  winget exited $rc - falling back to a direct download." -ForegroundColor Yellow
        }
        Refresh-Path
        [void](Add-NodeToPathIfPresent)
        $major = Get-NodeMajor
        if ($major -ge 18) {
            Write-Host "OK Node $(& node --version 2>&1)" -ForegroundColor Green
            Write-Host ''
            return $true
        }
        Write-Host '  winget did not yield a usable Node. Falling back.' -ForegroundColor Yellow
    } else {
        Write-Host 'winget not available - using a direct download instead.' -ForegroundColor Yellow
    }

    # Attempt 2: the official nodejs.org zip. No winget, no admin, no reboot.
    if (Install-NodeZip) {
        $major = Get-NodeMajor
        if ($major -ge 18) {
            Write-Host "OK Node $(& node --version 2>&1)" -ForegroundColor Green
            Write-Host ''
            return $true
        }
        Write-Host "Node unpacked but 'node --version' still reports v$major." -ForegroundColor Red
    }

    Write-Host 'Could not get a working Node 18+ runtime.' -ForegroundColor Red
    Write-Host 'Install Node 20+ manually from https://nodejs.org/ and re-run.'
    return $false
}

# Returns $true iff bash.exe is reachable OR PowerShell 7+ is the host. Claude
# Code on Windows shells out to bash for its scripting and refuses to run
# without one. Git for Windows ships bash.exe; PowerShell 7+ also satisfies.
function Test-ClaudeRuntimeShell {
    if (Get-Command bash.exe -ErrorAction SilentlyContinue) { return $true }
    if ($PSVersionTable.PSVersion.Major -ge 7) { return $true }
    foreach ($p in @(
        "$env:ProgramFiles\Git\bin\bash.exe",
        "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
        "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
    )) {
        if (Test-Path $p) {
            # Git is installed but its bin dir isn't on PATH - add it for this
            # session so subsequent `bash` lookups succeed.
            $bin = Split-Path $p
            if (-not (";${env:Path};".ToLower().Contains((";$bin;").ToLower()))) {
                $env:Path = "$bin;$env:Path"
            }
            return $true
        }
    }
    return $false
}

# Ensure Claude Code can run on Windows: install Git for Windows (provides
# bash.exe) if missing. Claude shells out to bash internally; without it the
# CLI errors with "Claude Code on Windows requires either Git for Windows or
# PowerShell" the moment we hand off - this preflight catches that BEFORE we
# spawn the install-prompt subprocess.
# winget-free, admin-free Git: the official Git for Windows installer is an Inno
# Setup .exe, and when it is NOT elevated it installs per-user into
# %LOCALAPPDATA%\Programs\Git. Same reasoning as Install-NodeZip - a box where
# winget can't land the Node MSI can't land the Git one either.
function Install-GitForWindows {
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { '64-bit' }
    Write-Host '  Fetching Git for Windows direct from git-scm.com (no admin needed)...'

    try {
        $rel = Invoke-RestMethod -UseBasicParsing -TimeoutSec 60 `
            -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest' `
            -Headers @{ 'User-Agent' = 'clayrune-installer' }
    } catch {
        Write-Host "  Could not reach the Git for Windows release feed: $_" -ForegroundColor Red
        return $false
    }

    $asset = $rel.assets | Where-Object { $_.name -like "Git-*-$arch.exe" } | Select-Object -First 1
    if (-not $asset) {
        Write-Host "  No Git installer asset for $arch in the latest release." -ForegroundColor Red
        return $false
    }

    $exePath = Join-Path $env:TEMP $asset.name
    Write-Host "  Downloading $($asset.name) ..."
    try {
        $progressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $exePath -UseBasicParsing -TimeoutSec 600
    } catch {
        Write-Host "  Download failed: $_" -ForegroundColor Red
        return $false
    }

    Write-Host '  Installing (silent, per-user)...'
    $rc = Invoke-Native $exePath /VERYSILENT /NORESTART /SUPPRESSMSGBOXES /NOCANCEL `
        /SP- /COMPONENTS="gitlfs" /o:PathOption=Cmd
    Remove-Item $exePath -Force -ErrorAction SilentlyContinue
    if ($rc -ne 0) {
        Write-Host "  Git installer exited $rc." -ForegroundColor Red
        return $false
    }
    Refresh-Path
    return $true
}

function Setup-ClaudeRuntimeShell {
    if (Test-ClaudeRuntimeShell) { return $true }

    Write-Host 'Claude Code needs bash.exe (Git for Windows) or PowerShell 7+ to run.' -ForegroundColor Yellow

    # Attempt 1: winget. Fails by exit code, not by throwing - so check it.
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host 'Installing Git for Windows via winget (also gives Claude its bash runtime)...'
        $rc = Invoke-Native -Command winget -Arguments @(
            'install', '--id', 'Git.Git', '-e', '--silent',
            '--source', 'winget',
            '--accept-source-agreements', '--accept-package-agreements')
        if ($rc -ne 0) {
            Write-Host "  winget exited $rc - falling back to a direct download." -ForegroundColor Yellow
        }
        Refresh-Path
        if (Test-ClaudeRuntimeShell) {
            Write-Host 'OK Git for Windows / bash available' -ForegroundColor Green
            Write-Host ''
            return $true
        }
    } else {
        Write-Host 'winget not available - using a direct download instead.' -ForegroundColor Yellow
    }

    # Attempt 2: the official installer, per-user.
    if (Install-GitForWindows) {
        if (Test-ClaudeRuntimeShell) {
            Write-Host 'OK Git for Windows / bash available' -ForegroundColor Green
            Write-Host ''
            return $true
        }
    }

    Write-Host 'Could not provide bash.exe for Claude Code.' -ForegroundColor Red
    Write-Host 'Install Git for Windows manually from https://git-scm.com/downloads/win and re-run.'
    return $false
}

# Returns $true iff `claude --version` runs cleanly with non-empty output.
# This is the *real* working-state check - Get-Command alone only proves a
# binary is on PATH, not that it actually runs (the same trap that bit us on
# WSL where npm completed "successfully" but produced a broken CLI).
function Test-ClaudeWorks {
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { return $false }
    try {
        $out = & claude --version 2>$null
        return ($LASTEXITCODE -eq 0) -and ($out) -and ($out.ToString().Trim() -ne '')
    } catch {
        return $false
    }
}

# The npm global prefix (where the `claude` bin shims + node_modules live).
function Get-NpmPrefix {
    $prefix = ''
    try { $prefix = (& npm config get prefix 2>$null | Select-Object -First 1).ToString().Trim() } catch {}
    if (-not $prefix) { $prefix = Join-Path $env:APPDATA 'npm' }
    return $prefix
}

# THE root cause of the fresh-PC failures: the package ships a native
# claude.exe. If any `claude` process is alive (commonly one Clayrune itself
# spawned), Windows locks the .exe, so `npm install -g`'s atomic
# extract-then-rename can't replace it -> EPERM, npm aborts the finalize and
# leaves the bin shims but an incomplete package. The leftover
# `@anthropic-ai/.claude-code-*` staging dir then trips the NEXT install too.
# So before any (re)install we must (1) kill claude processes and (2) purge
# the leftover @anthropic-ai dir. Do NOT gate health on a hardcoded internal
# file path - the package layout changes between versions (older builds had
# node_modules/@anthropic-ai/claude-code/cli.js; current ones do not). The
# only version-stable health check is `claude --version` (Test-ClaudeWorks).

function Stop-ClaudeProcesses {
    # Safe here: this runs in the PowerShell bootstrap, BEFORE we ever spawn
    # the `claude -p` install prompt - so we're not killing our own installer.
    try {
        Get-Process claude -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    } catch {}
}

# Clear npm leftovers that break a fresh install:
#  - the @anthropic-ai dir npm's EPERM leaves half-written (staging
#    `.claude-code-*` + a partial `claude-code`);
#  - a top-level `claude.exe` at the npm prefix root. npm only ever
#    generates claude / claude.cmd / claude.ps1 - it NEVER creates a
#    top-level claude.exe. One sitting there is a STALE ORPHAN from an
#    older package version that runs `node ...\cli.js` (a path the
#    current package no longer ships). Because PATHEXT/shutil.which
#    prefer .exe, it shadows the correct .cmd and makes Clayrune crash
#    with MODULE_NOT_FOUND even though `claude` itself is fine. npm won't
#    remove a file it didn't create, so we must. Best-effort.
function Clear-ClaudeNpmLeftovers {
    $prefix = Get-NpmPrefix
    $dir = Join-Path $prefix 'node_modules\@anthropic-ai'
    if (Test-Path $dir) {
        try { Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue } catch {}
    }
    $orphanExe = Join-Path $prefix 'claude.exe'
    if (Test-Path $orphanExe) {
        try { Remove-Item -Force $orphanExe -ErrorAction SilentlyContinue } catch {}
    }
}

# Install (or, with -Clean, hard-repair) the global Claude CLI via npm.
function Install-ClaudeNpm {
    param([switch]$Clean)
    Stop-ClaudeProcesses          # always: a live claude.exe is what causes EPERM
    if ($Clean) {
        Write-Host '  Clearing the locked/partial global package first...'
        [void](Invoke-Native npm uninstall -g '@anthropic-ai/claude-code')
        Clear-ClaudeNpmLeftovers
        [void](Invoke-Native npm cache clean --force)
    }
    # Invoke-Native, not a bare `npm ...`: npm's stdout would otherwise land in
    # this function's return value and then in Invoke-ClaudeNpmInstall's, making
    # `if (Invoke-ClaudeNpmInstall ...)` truthy even when the install failed.
    [void](Invoke-Native npm install -g '@anthropic-ai/claude-code')
    Refresh-Path
}

# npm install with an automatic one-shot clean-reinstall fallback. Health is
# judged ONLY by `claude --version` (Test-ClaudeWorks) - layout-agnostic.
function Invoke-ClaudeNpmInstall {
    param([string]$Label)
    # A present-but-non-working `claude` => go straight to a clean repair.
    $broken = (Get-Command claude -ErrorAction SilentlyContinue) -and -not (Test-ClaudeWorks)
    Install-ClaudeNpm -Clean:$broken
    if (Test-ClaudeWorks) {
        Write-Host "+ $Label" -ForegroundColor Green
        Write-Host ''
        return $true
    }
    Write-Host '- install did not yield a working `claude` - forcing a clean reinstall...' -ForegroundColor Yellow
    Install-ClaudeNpm -Clean
    if (Test-ClaudeWorks) {
        Write-Host "+ $Label (after clean reinstall)" -ForegroundColor Green
        Write-Host ''
        return $true
    }
    Write-Host '- still not working after a clean reinstall' -ForegroundColor Yellow
    Write-Host ''
    return $false
}

# Check local login state, never a model prompt. A broken CLI must not hang
# installation indefinitely or be mistaken for successful authentication.
function Test-ClaudeAuth {
    $authJob = $null
    try {
        $authJob = Start-Job -ScriptBlock {
            $ErrorActionPreference = 'Continue'
            # Prefer native/npm cmd entrypoints: a vanilla PowerShell policy
            # can block npm's claude.ps1 shim before the CLI even starts.
            $cli = Get-Command claude.exe, claude.cmd -ErrorAction SilentlyContinue | Select-Object -First 1
            if (-not $cli) { throw 'Claude executable or cmd entrypoint not found.' }
            $out = (& $cli.Source auth status 2>$null | Out-String)
            [pscustomobject]@{ Output = $out; ExitCode = $LASTEXITCODE }
        }
        if (-not (Wait-Job -Job $authJob -Timeout 20)) {
            throw 'Claude authentication status timed out after 20 seconds.'
        }
        $result = Receive-Job -Job $authJob -ErrorAction Stop
        if (-not $result) { throw 'Claude authentication status returned no result.' }
        $status = $result.Output | ConvertFrom-Json -ErrorAction Stop
        if ($status.loggedIn -isnot [bool]) {
            throw 'Claude authentication status did not return a loggedIn boolean. Update the Claude CLI and retry.'
        }
        if ($result.ExitCode -notin @(0, 1)) {
            throw 'Claude authentication status exited unexpectedly.'
        }
        if ($status.loggedIn -and $result.ExitCode -ne 0) {
            throw 'Claude authentication status failed despite reporting a login.'
        }
        return $status.loggedIn
    } finally {
        if ($authJob) {
            Stop-Job -Job $authJob -ErrorAction SilentlyContinue
            Remove-Job -Job $authJob -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host '======================================' -ForegroundColor Cyan
Write-Host '  Clayrune Installer' -ForegroundColor White
Write-Host '======================================'
Write-Host ''

# -- Contact on failure ----------------------------------------------------
# Every non-zero exit is a person who bounced before ever opening Clayrune.
# They are the only ones who can tell us whether install is the gate, and
# until now every one of these paths exited in silence.
#
# NOTE: [Environment]::Exit() terminates the process immediately and does NOT
# run trap/finally blocks, so unlike install.sh this cannot be a single trap.
# Every non-zero exit must call Exit-WithContact instead. Exit code is passed
# through unchanged -- ClayruneInstaller.exe keys its remediation menu off
# code 3, so the contract at the top of this file still holds.
$ClayruneContact = 'hello@clayrune.io'

function Exit-WithContact {
    param([Parameter(Mandatory = $true)][int]$Code)
    Write-Host ''
    Write-Host '------------------------------------------------------------' -ForegroundColor Yellow
    Write-Host '  This did not work, and we would like to know why.'
    Write-Host "  Email $ClayruneContact and paste the output above." -ForegroundColor Cyan
    Write-Host '  A one-line "it failed here" is plenty. No account needed.'
    Write-Host '------------------------------------------------------------' -ForegroundColor Yellow
    [Environment]::Exit($Code)
}

# Optional provider override
# Provider selection, CLI installation, and login belong to the first-run UI.
$ProviderChoices = @('claude', 'codex', 'gemini', 'qwen')

$ChosenProvider = $env:CLAYRUNE_PROVIDER
if ($ChosenProvider) { $ChosenProvider = $ChosenProvider.Trim().ToLower() }
if ($ChosenProvider -and ($ProviderChoices -notcontains $ChosenProvider)) {
    Write-Host "CLAYRUNE_PROVIDER=$ChosenProvider is not one of: $($ProviderChoices -join ', ')" -ForegroundColor Red
    Exit-WithContact 1
}
if ($ChosenProvider) {
    Write-Host "OK Explicit provider: $ChosenProvider" -ForegroundColor Green
} else {
    Write-Host 'No provider selected yet. Clayrune will ask on first launch.' -ForegroundColor Cyan
}
Write-Host ''

# -- Step 0: Ensure Node 18+ is available -----------------------------------

if ($ChosenProvider) {
if (-not (Get-BoolResult (Setup-Node))) {
    Write-Host ''
    Write-Host 'Could not set up a working Node 18+ runtime automatically.' -ForegroundColor Red
    Write-Host 'Please install Node 20+ from https://nodejs.org/ and re-run.'
    Write-Host ''
    Write-Host 'Retrying the installer will NOT help until Node is present -' -ForegroundColor Yellow
    Write-Host 'install it first, then re-run.' -ForegroundColor Yellow
    exit 1
}

# -- Step 1/1.4/1.5: ensure + auth-check ONLY the chosen provider's CLI ----
#
# Used to unconditionally run the full Claude-specific dance (npm install,
# bash.exe/PowerShell-7 runtime shell, `claude -p` auth probe) regardless of
# what the user actually picked. Codex/Gemini share Step 0's Node, but have
# no Claude-specific runtime-shell or auth-probe equivalent here.
if ($ChosenProvider -ne 'claude') {
    $provPkg = switch ($ChosenProvider) {
        'codex' { '@openai/codex' }
        'qwen'  { '@qwen-code/qwen-code' }
        default { '@google/gemini-cli' }
    }
    Write-Host "Checking $ChosenProvider CLI..."
    if (Get-Command $ChosenProvider -ErrorAction SilentlyContinue) {
        Write-Host "OK $ChosenProvider CLI already installed." -ForegroundColor Green
        Write-Host ''
    } else {
        if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
            [void](Add-NodeToPathIfPresent)
        }
        if (Get-Command npm -ErrorAction SilentlyContinue) {
            Write-Host "Installing via npm install -g $provPkg..."
            $rc = Invoke-Native npm install -g $provPkg
            if ($rc -eq 0 -and (Get-Command $ChosenProvider -ErrorAction SilentlyContinue)) {
                Write-Host "OK $ChosenProvider CLI installed." -ForegroundColor Green
                Write-Host ''
            } else {
                Write-Host ''
                Write-Host "Could not install $ChosenProvider CLI automatically." -ForegroundColor Red
                Write-Host "Install manually:  npm install -g $provPkg" -ForegroundColor Cyan
                Write-Host 'Then re-run:       iwr https://clayrune.io/install.ps1 -useb | iex' -ForegroundColor Cyan
                exit 1
            }
        } else {
            Write-Host ''
            Write-Host "$ChosenProvider CLI not found and npm is not on PATH even though Node is." -ForegroundColor Red
            exit 1
        }
    }
    # No auth-check here — unlike Claude, there is no verified "is this CLI
    # logged in" probe for codex/gemini to run from a script. Clayrune's own
    # in-app auth banner (provider-auth.js) surfaces sign-in state for
    # whichever provider is in_use, the first time it's actually needed.
    Write-Host "Sign in when Clayrune opens if it prompts you - it only asks once $ChosenProvider actually needs it."
    Write-Host ''
} else {

if (Test-ClaudeWorks) {
    $claudeVersion = (& claude --version 2>&1 | Select-Object -First 1)
    Write-Host "OK Claude CLI already installed: $claudeVersion" -ForegroundColor Green
    Write-Host ''
} else {
    if (Get-Command claude -ErrorAction SilentlyContinue) {
        Write-Host "Found 'claude' on PATH but it doesn't run cleanly." -ForegroundColor Yellow
        Write-Host 'Will attempt a clean reinstall.'
        Write-Host ''
    } else {
        Write-Host 'Claude CLI not found. Attempting to install...' -ForegroundColor Yellow
        Write-Host ''
    }

    $installed = $false

    # Method 1: npm (preferred on Windows - ships natively with Node). Step 0
    # already guarantees Node 18+ and therefore npm; if npm is still missing,
    # Node came from somewhere unusual, so re-probe the PATH before giving up.
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        [void](Add-NodeToPathIfPresent)
    }
    if (-not $installed -and (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Host 'Trying npm install -g @anthropic-ai/claude-code...'
        try {
            if (Get-BoolResult (Invoke-ClaudeNpmInstall -Label 'npm install succeeded')) {
                $installed = $true
            } else {
                Write-Host '  npm install did not produce a working claude.' -ForegroundColor Yellow
                Write-Host ''
            }
        } catch {
            Write-Host "- npm install failed: $_" -ForegroundColor Yellow
            Write-Host ''
        }
    } elseif (-not $installed) {
        Write-Host 'npm is not on PATH even though Node is - cannot install the Claude CLI.' -ForegroundColor Red
        Write-Host ''
    }

    if (-not $installed) {
        $anthDir = Join-Path (Get-NpmPrefix) 'node_modules\@anthropic-ai'
        Write-Host ''
        Write-Host 'Could not install a working Claude CLI automatically.' -ForegroundColor Red
        Write-Host ''
        Write-Host 'Most common cause: the Claude CLI ships a native claude.exe, and' -ForegroundColor Yellow
        Write-Host "npm can't replace it while a claude process is running (EPERM /"  -ForegroundColor Yellow
        Write-Host 'operation not permitted). That usually means Clayrune is open and' -ForegroundColor Yellow
        Write-Host 'holding claude.exe. Fix by hand:' -ForegroundColor Yellow
        Write-Host ''
        Write-Host '  1. Close Clayrune completely, then in PowerShell:'
        Write-Host '  Get-Process claude -ErrorAction SilentlyContinue | Stop-Process -Force' -ForegroundColor Cyan
        Write-Host "  Remove-Item -Recurse -Force '$anthDir'" -ForegroundColor Cyan
        Write-Host '  npm install -g @anthropic-ai/claude-code' -ForegroundColor Cyan
        Write-Host '  claude --version' -ForegroundColor Cyan
        Write-Host ''
        Write-Host '  If it still EPERMs, the locker is antivirus: add an exclusion'
        Write-Host "  for $anthDir, or reboot (clears all file locks) and run the"
        Write-Host '  three commands above BEFORE opening Clayrune.'
        Write-Host ''
        Write-Host 'Docs: https://docs.anthropic.com/claude-code'
        Write-Host 'Then re-run this installer in a NEW PowerShell window:'
        Write-Host '  iwr https://clayrune.io/install.ps1 -useb | iex' -ForegroundColor Cyan
        exit 1
    }

    $claudeVersion = (& claude --version 2>&1 | Select-Object -First 1)
    Write-Host "OK Claude CLI: $claudeVersion" -ForegroundColor Green
    Write-Host ''
}

# -- Step 1.4: Verify Claude Code can run (bash.exe / PowerShell 7) ---------

# Skip on non-Windows (the .ps1 only runs on Windows but be defensive).
if (-not (Get-BoolResult (Setup-ClaudeRuntimeShell))) {
    Write-Host ''
    Write-Host 'Could not provide a runtime shell for Claude Code. Aborting.' -ForegroundColor Red
    exit 1
}

# -- Step 1.5: Verify Claude CLI is authenticated ---------------------------

Write-Host 'Checking Claude CLI authentication...'
try {
    $claudeAuthenticated = Test-ClaudeAuth
} catch {
    Write-Host "Could not check Claude authentication: $_" -ForegroundColor Yellow
    Write-Host 'Run claude auth status in Command Prompt to diagnose, then retry the installer.'
    Exit-WithContact 1
}
if (-not $claudeAuthenticated) {
    Write-Host ''
    Write-Host 'Claude CLI is installed but not authenticated.' -ForegroundColor Yellow
    Write-Host ''
    Write-Host 'Easiest path: re-run this installer via the double-click setup' -ForegroundColor White
    Write-Host '(Clayrune-Setup.bat) and pick the [L] option - it logs you in'
    Write-Host 'and continues the install automatically.'
    Write-Host ''
    Write-Host 'Otherwise, do it manually:' -ForegroundColor White
    Write-Host ''
    Write-Host 'Step 1.' -ForegroundColor White -NoNewline; Write-Host ' Open Command Prompt (cmd.exe, NOT PowerShell) and run:'
    Write-Host '         claude /login' -ForegroundColor Cyan
    Write-Host '         (PowerShell users: this fails on default Windows due to ExecutionPolicy.'
    Write-Host '          Use ' -NoNewline; Write-Host 'cmd.exe' -ForegroundColor Cyan -NoNewline; Write-Host ' instead, or run:'
    Write-Host '          ' -NoNewline; Write-Host 'powershell -ExecutionPolicy Bypass -Command "claude /login"' -ForegroundColor Cyan -NoNewline; Write-Host ')'
    Write-Host '         Follow the OAuth prompts (or paste an Anthropic API key).'
    Write-Host '         When you see "' -NoNewline; Write-Host 'Logged in' -ForegroundColor Cyan -NoNewline; Write-Host '", type ' -NoNewline; Write-Host 'exit' -ForegroundColor Cyan -NoNewline; Write-Host ' to leave the Claude REPL.'
    Write-Host ''
    Write-Host 'Step 2.' -ForegroundColor White -NoNewline; Write-Host ' Re-run this installer in a NEW PowerShell window:'
    Write-Host '         $env:CLAYRUNE_PROMPT_URL = ''https://raw.githubusercontent.com/clayrune-io/clayrune/master/installer/install-prompt.md''' -ForegroundColor Cyan
    Write-Host '         iwr https://raw.githubusercontent.com/clayrune-io/clayrune/master/installer/install.ps1 -useb | iex' -ForegroundColor Cyan
    Write-Host ''
    # Exit code 3 = "not authenticated", specifically. See the EXIT CODES
    # contract at the top of this file: ClayruneInstaller.exe keys its
    # remediation menu off this, so only THIS path may return 3.
    Exit-WithContact 3
}
Write-Host 'OK Authenticated' -ForegroundColor Green
Write-Host ''
} # ChosenProvider -ne 'claude' / -eq 'claude'
} # explicit CLAYRUNE_PROVIDER preflight

# -- Direct deterministic install (no Claude handoff) ----------------------
#
# We previously fetched install-prompt.md and asked Claude to run the install
# steps via `claude --dangerously-skip-permissions -p "<24KB markdown>"`.
# That broke for two reasons:
#   1. The 24 KB user-message-styled "you are an automated installer, do not
#      ask for confirmation" prompt is the textbook shape of a prompt-injection
#      attack. Newer Claude models flag it and refuse, then exit 0 - leaving
#      the wrapper to mistakenly declare success.
#   2. None of the steps actually need an LLM. `git clone`, venv setup,
#      pip install, shortcut creation, and `start` are all deterministic
#      shell commands. PowerShell is already running on the user's machine
#      with full privileges; we don't need to ask Claude permission to run
#      what we wrote ourselves.
# So we skip Claude entirely from here. Clayrune still uses Claude AT RUNTIME
# (that's the product), but installing Clayrune doesn't.

$installDir = if ($env:CLAYRUNE_HOME) { $env:CLAYRUNE_HOME } else { "$env:USERPROFILE\Clayrune" }
$repoUrl = 'https://github.com/clayrune-io/clayrune.git'

Write-Host '--------------------------------------' -ForegroundColor Yellow
Write-Host 'About to install Clayrune to:' -ForegroundColor White
Write-Host "  $installDir" -ForegroundColor Cyan
Write-Host 'Steps: clone repo, set up Python venv, create Desktop shortcut, launch dashboard.'
Write-Host '--------------------------------------' -ForegroundColor Yellow
Write-Host ''
if (-not $env:CLAYRUNE_NO_CONFIRM) {
    Write-Host 'Press Ctrl+C in the next 5 seconds to abort, or wait...'
    Start-Sleep -Seconds 5
}
Write-Host ''

# -- [STEP 1/5] Clone or update the repository -----------------------------
#
# LOAD-BEARING: `git pull --ff-only` alone is NOT enough. When the release
# branch is force-pushed upstream, ff-only aborts outright
# ("fatal: Not possible to fast-forward, aborting") and every EXISTING install
# is bricked - it can never update again, silently. Observed on a clean VM
# 2026-07-23. So: try ff-only first (cheapest, preserves any local commits);
# if that fails, re-sync hard to the remote tip.
#
# `git reset --hard` only rewrites TRACKED files. Every piece of user data in
# this checkout is untracked or gitignored - data\, data\projects\, config.json,
# data\settings.json, data\logs\, .venv\ - so it all survives untouched.
# NEVER add `git clean` here: that WOULD delete it.
$env:GIT_TERMINAL_PROMPT = '0'   # fail fast instead of popping a credential dialog

# LOAD-BEARING: git is a CORE prerequisite, not a provider one.
#
# Until 2026-09-20 the only thing that installed Git for Windows was
# Setup-ClaudeRuntimeShell, and that lives inside the `if ($ChosenProvider)`
# preflight. Once provider choice moved to the first-run UI (e939ebd) the
# default install picks no provider, skips that whole block, and then runs
# `git clone` below against a box that has never had git. Measured on a clean
# Windows 11 VM 2026-09-20: STEP 1/5 died with
# "The term 'git' is not recognized", before the step's own FAIL message, so
# the user got a CommandNotFoundException instead of the exit-code contract.
# Every step from here needs git; ensure it BEFORE the first call, always.
function Test-GitOnPath {
    if (Get-Command git -ErrorAction SilentlyContinue) { return $true }
    # Installed but not on PATH (per-user Inno install, or a winget MSI whose
    # PATH edit this process never picked up) - add it for this session.
    foreach ($cmd in @(
            "$env:ProgramFiles\Git\cmd",
            "${env:ProgramFiles(x86)}\Git\cmd",
            "$env:LOCALAPPDATA\Programs\Git\cmd")) {
        if (Test-Path (Join-Path $cmd 'git.exe')) {
            $env:Path = "$cmd;$env:Path"
            return $true
        }
    }
    return $false
}

function Setup-Git {
    if (Test-GitOnPath) { return $true }

    Write-Host 'Clayrune needs git to install itself. Setting it up...' -ForegroundColor Yellow

    # Attempt 1: winget. Fails by exit code, not by throwing - so check it.
    # --source winget is required: without it a machine with a configured
    # msstore source can resolve Git.Git to a Store listing and hang.
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $rc = Invoke-Native -Command winget -Arguments @(
            'install', '--id', 'Git.Git', '-e', '--silent',
            '--source', 'winget',
            '--accept-source-agreements', '--accept-package-agreements')
        if ($rc -ne 0) {
            Write-Host "  winget exited $rc - falling back to a direct download." -ForegroundColor Yellow
        }
        Refresh-Path
        if (Test-GitOnPath) {
            Write-Host 'OK git available' -ForegroundColor Green
            Write-Host ''
            return $true
        }
    } else {
        Write-Host 'winget not available - using a direct download instead.' -ForegroundColor Yellow
    }

    # Attempt 2: the official installer, per-user, no admin needed.
    if (Install-GitForWindows) {
        if (Test-GitOnPath) {
            Write-Host 'OK git available' -ForegroundColor Green
            Write-Host ''
            return $true
        }
    }
    return $false
}

if (-not (Get-BoolResult (Setup-Git))) {
    Write-Host ''
    Write-Host '[STEP 1/5] FAIL could not install git' -ForegroundColor Red
    Write-Host 'Install Git for Windows manually from https://git-scm.com/downloads/win'
    Write-Host 'and re-run the installer.'
    Exit-WithContact 2
}

function Repair-NonGitInstall {
    param([string]$Destination, [string]$Repository)
    # Clone first: network failure must leave the original folder untouched.
    $target = [IO.Path]::GetFullPath($Destination).TrimEnd('\', '/')
    $parent = Split-Path -Parent $target
    if (-not $parent -or $target -eq [IO.Path]::GetPathRoot($target).TrimEnd('\', '/') -or
        $target -eq [IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\', '/')) {
        throw 'Refusing to relocate a drive root or user profile as an install directory.'
    }
    $original = Get-Item -LiteralPath $target -Force -ErrorAction Stop
    if (-not $original.PSIsContainer -or ($original.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Install destination must be a normal directory, not a file or junction.'
    }
    $suffix = [guid]::NewGuid().ToString('N')
    $stage = Join-Path $parent ((Split-Path -Leaf $target) + '.install-' + $suffix)
    $backup = Join-Path $parent ((Split-Path -Leaf $target) + '.backup-' + $suffix)
    Write-Host "  Existing non-Git folder detected. Preparing a fresh checkout; backup: $backup"
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue' # git progress on stderr is not failure
        & git clone $Repository $stage 2>&1 | ForEach-Object { Write-Host $_ }
        $cloneExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $savedPreference }
    if ($cloneExit -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $stage '.git'))) {
        throw "Fresh clone failed. Original folder unchanged; partial download retained at $stage"
    }
    # Preserve the entire original as a sibling backup. Carry the known user
    # state into the fresh install, not old application code or broken venvs.
    foreach ($name in @('data', 'config.json', '.env', '.claude', '.agents')) {
        $source = Join-Path $target $name
        if (Test-Path -LiteralPath $source) {
            $items = @(Get-Item -LiteralPath $source -Force)
            if ($items[0].PSIsContainer) {
                $items += @(Get-ChildItem -LiteralPath $source -Recurse -Force -ErrorAction Stop)
            }
            if ($items | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) {
                throw "Cannot safely copy linked user state at $source. Original folder unchanged."
            }
            if ($items[0].PSIsContainer) {
                $copyTarget = Join-Path $stage $name
                New-Item -ItemType Directory -Path $copyTarget -Force | Out-Null
                Get-ChildItem -LiteralPath $source -Force | ForEach-Object {
                    $copySource = $_.FullName
                    try {
                        Copy-Item -LiteralPath $copySource -Destination $copyTarget -Recurse -Force -ErrorAction Stop
                    } catch { throw "Could not preserve user data from $copySource : $($_.Exception.Message). Original folder unchanged." }
                }
            } else {
                try {
                    Copy-Item -LiteralPath $source -Destination (Join-Path $stage $name) -Force -ErrorAction Stop
                } catch { throw "Could not preserve $source : $($_.Exception.Message). Original folder unchanged." }
            }
        }
    }
    # Both destinations are explicit siblings of the validated target.
    try {
        Move-Item -LiteralPath $target -Destination $backup -ErrorAction Stop
    } catch {
        # A running installer or terminal inside the old folder may hold it
        # open. Do not terminate anything or overwrite locked files. The
        # prepared checkout already contains the copied user state.
        $cause = $_.Exception
        $sharingViolation = $false
        while ($cause) {
            if (($cause.HResult -band 0xffff) -in @(32, 33)) { $sharingViolation = $true }
            $cause = $cause.InnerException
        }
        if (-not $sharingViolation) { throw "Could not preserve original folder $target : $($_.Exception.Message)" }
        Write-Host "  Original folder is in use and remains untouched: $target" -ForegroundColor Yellow
        Write-Host "  Continuing installation at $stage; shortcuts will use this location." -ForegroundColor Yellow
        return $stage
    }
    try {
        Move-Item -LiteralPath $stage -Destination $target -ErrorAction Stop
    } catch {
        Move-Item -LiteralPath $backup -Destination $target -ErrorAction Stop
        throw
    }
    Write-Host "  Recovered automatically. Original files preserved at $backup" -ForegroundColor Green
    return $target
}

Write-Host '[STEP 1/5] Cloning repository...' -ForegroundColor White
if (Test-Path $installDir) {
    if (Test-Path (Join-Path $installDir '.git')) {
        Write-Host "  Existing checkout at $installDir - pulling latest."
        & git -C $installDir pull --ff-only
        if ($LASTEXITCODE -ne 0) {
            Write-Host ''
            Write-Host '  Fast-forward pull failed (upstream history was rewritten).' -ForegroundColor Yellow
            Write-Host '  Re-syncing this checkout to the remote tip instead.' -ForegroundColor Yellow
            Write-Host '  Your data is untouched: data\, config.json, .venv\ are not tracked by git.' -ForegroundColor Yellow

            & git -C $installDir fetch --prune origin
            if ($LASTEXITCODE -ne 0) {
                Write-Host '[STEP 1/5] FAIL git fetch failed - check your network connection.' -ForegroundColor Red
                Exit-WithContact 2
            }

            # Which remote branch to land on. Detached HEAD (or an unknown
            # local branch) falls back to master, the release channel.
            $branch = (& git -C $installDir rev-parse --abbrev-ref HEAD 2>$null | Out-String).Trim()
            if (-not $branch -or $branch -eq 'HEAD') { $branch = 'master' }
            & git -C $installDir rev-parse --verify --quiet "refs/remotes/origin/$branch" *> $null
            if ($LASTEXITCODE -ne 0) { $branch = 'master' }

            $oldSha = (& git -C $installDir rev-parse --short HEAD 2>$null | Out-String).Trim()
            & git -C $installDir reset --hard "origin/$branch"
            if ($LASTEXITCODE -ne 0) {
                Write-Host "[STEP 1/5] FAIL could not re-sync $installDir to origin/$branch." -ForegroundColor Red
                Write-Host '          The checkout looks unusable. Move it aside and re-run this' -ForegroundColor Red
                Write-Host '          installer to get a fresh clone:' -ForegroundColor Red
                Write-Host "          Rename-Item '$installDir' '$installDir.old'" -ForegroundColor Cyan
                Write-Host '          (your projects live in the .old copy under data\ - copy them back after)' -ForegroundColor Red
                Exit-WithContact 2
            }
            if ($oldSha) {
                Write-Host "  Re-synced to origin/$branch. Previous commit was $oldSha" -ForegroundColor DarkGray
                Write-Host "  (recover it with: git -C '$installDir' reset --hard $oldSha)" -ForegroundColor DarkGray
            }
        }
    } else {
        try {
            $installDir = Repair-NonGitInstall -Destination $installDir -Repository $repoUrl
        } catch {
            Write-Host "[STEP 1/5] FAIL automatic folder recovery: $_" -ForegroundColor Red
            Exit-WithContact 2
        }
    }
} else {
    & git clone $repoUrl $installDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[STEP 1/5] FAIL git clone failed' -ForegroundColor Red
        Exit-WithContact 2
    }
}
Write-Host '[STEP 1/5] OK' -ForegroundColor Green
Write-Host ''

# Switch to 'Continue' for the deterministic install phase. Native commands
# (winget, git, python, pip) frequently write to stderr even on success-equivalent
# outcomes ("warnings", "deprecation notices", App Execution Alias stubs). Under
# 'Stop' that becomes a NativeCommandError -> terminating error -> script halts
# halfway through. We do our own exit-code + Test-Path checks per step, so we
# don't need PowerShell's auto-stop here.
$ErrorActionPreference = 'Continue'

# -- [STEP 2/5] Python 3.11+ -----------------------------------------------
Write-Host '[STEP 2/5] Setting up Python 3.11+...' -ForegroundColor White
function Find-Python311 {
    # Probe `py -3.12 --version` / `py -3.11 --version` first via the Python
    # launcher (Windows convention; preferred when present because it skips
    # the App Execution Alias trap entirely).
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($vTag in @('-3.12', '-3.11')) {
            try {
                $out = & $pyLauncher.Source $vTag --version 2>$null | Out-String
                if ($LASTEXITCODE -eq 0 -and $out -match 'Python\s+3\.(\d+)') {
                    $min = [int]$Matches[1]
                    if ($min -ge 11) {
                        $exe = (& $pyLauncher.Source $vTag -c "import sys; print(sys.executable)" 2>$null | Out-String).Trim()
                        if ($exe -and (Test-Path $exe)) { return $exe }
                    }
                }
            } catch {}
        }
    }

    foreach ($cmd in @('python3.12', 'python3.11', 'python3', 'python')) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if (-not $found) { continue }
        # Skip Windows 11 / 10 App Execution Alias stubs at
        # %LOCALAPPDATA%\Microsoft\WindowsApps. They are NOT Python -- they
        # redirect to the Microsoft Store. Running --version against them
        # prints "Python was not found..." to stderr; in PowerShell 5.1 that
        # surfaces as a NativeCommandError and (depending on ErrorActionPref
        # inherited from iex) halts the whole script before we can reach
        # the winget install fallback. Recognizing the path sidesteps it.
        if ($found.Source -match '\\WindowsApps\\') { continue }
        try {
            # Stderr -> $null so a misbehaving binary's error text doesn't
            # become a PowerShell error record.
            $verOut = & $found.Source --version 2>$null | Out-String
            if ($LASTEXITCODE -ne 0) { continue }
        } catch { continue }
        if ($verOut -match 'Python\s+(\d+)\.(\d+)') {
            $maj = [int]$Matches[1]
            $min = [int]$Matches[2]
            if (($maj -eq 3 -and $min -ge 11) -or $maj -gt 3) {
                return $found.Source
            }
        }
    }
    return $null
}
# Official python.org installer, per-user (InstallAllUsers=0) so it needs no
# admin - the winget-free path, mirroring Install-NodeZip / Install-GitForWindows.
function Install-PythonOrg {
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
    $ver  = '3.12.8'
    $url  = "https://www.python.org/ftp/python/$ver/python-$ver-$arch.exe"
    $exe  = Join-Path $env:TEMP "python-$ver-$arch.exe"

    Write-Host "  Downloading Python $ver ($arch) from python.org (no admin needed)..."
    try {
        $progressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing -TimeoutSec 600
    } catch {
        Write-Host "  Download failed: $_" -ForegroundColor Red
        return $false
    }

    Write-Host '  Installing (silent, per-user)...'
    $rc = Invoke-Native $exe /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1
    Remove-Item $exe -Force -ErrorAction SilentlyContinue
    if ($rc -ne 0) {
        Write-Host "  Python installer exited $rc." -ForegroundColor Red
        return $false
    }
    Refresh-Path
    return $true
}

$pythonExe = Find-Python311
if (-not $pythonExe) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host '  Python 3.11+ not found. Installing via winget...' -ForegroundColor Yellow
        $rc = Invoke-Native -Command winget -Arguments @(
            'install', '--id', 'Python.Python.3.12', '-e', '--silent',
            '--source', 'winget',
            '--accept-source-agreements', '--accept-package-agreements')
        if ($rc -ne 0) {
            Write-Host "  winget exited $rc - falling back to a direct download." -ForegroundColor Yellow
        }
        Refresh-Path
        $pythonExe = Find-Python311
    }
}
if (-not $pythonExe) {
    Write-Host '  Python 3.11+ still not found. Installing from python.org...' -ForegroundColor Yellow
    if (Get-BoolResult (Install-PythonOrg)) {
        $pythonExe = Find-Python311
    }
}
if (-not $pythonExe) {
    Write-Host '[STEP 2/5] FAIL could not find or install Python 3.11+' -ForegroundColor Red
    Write-Host '          Install manually from https://python.org/downloads, then re-run.' -ForegroundColor Red
    Exit-WithContact 2
}
Write-Host "  Using: $pythonExe"

$venvPath = Join-Path $installDir '.venv'
if (-not (Test-Path (Join-Path $venvPath 'Scripts\python.exe'))) {
    & $pythonExe -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[STEP 2/5] FAIL venv creation failed' -ForegroundColor Red
        Exit-WithContact 2
    }
}
$venvPip = Join-Path $venvPath 'Scripts\pip.exe'
$reqPath = Join-Path $installDir 'requirements.txt'
if (Test-Path $reqPath) {
    & $venvPip install --quiet -r $reqPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[STEP 2/5] FAIL pip install failed' -ForegroundColor Red
        Exit-WithContact 2
    }
}
Write-Host '[STEP 2/5] OK' -ForegroundColor Green
Write-Host ''

# -- Write the provider choice into config.json (before first launch) ------
#
# Must happen before Step 4 starts the server, and needs the venv's python
# (just built above) to merge safely rather than clobber. A re-run of this
# installer, or an upgrade, must never overwrite a choice already on disk -
# Settings -> Default provider is the only place to change it after this.
$venvPython = Join-Path $venvPath 'Scripts\python.exe'
$configPath = Join-Path $installDir 'config.json'
$mergeScript = @'
import json, sys
path, provider = sys.argv[1], sys.argv[2]
try:
    with open(path) as f:
        cfg = json.load(f)
except (FileNotFoundError, ValueError):
    cfg = {}
if not cfg.get('default_provider'):
    cfg['default_provider'] = provider
    with open(path, 'w') as f:
        json.dump(cfg, f, indent=2)
'@
try {
if ($ChosenProvider) {
    & $venvPython -c $mergeScript $configPath $ChosenProvider
}
} catch {
    Write-Host "  (could not write default_provider into config.json: $_)" -ForegroundColor DarkGray
}

# -- [STEP 3/5] Desktop + Start Menu shortcuts -----------------------------
Write-Host '[STEP 3/5] Creating Desktop + Start Menu shortcuts...' -ForegroundColor White
$startBat  = Join-Path $installDir 'installer\start.bat'
$hiddenVbs = Join-Path $installDir 'installer\start-hidden.vbs'
$uninstallPs1 = Join-Path $installDir 'installer\uninstall.ps1'
# Full path to the Windows Script Host. A bare 'wscript.exe' usually resolves,
# but a shortcut stored with the absolute System32 path is more reliable.
$wscriptExe = Join-Path $env:WINDIR 'System32\wscript.exe'
if (-not (Test-Path $startBat)) {
    Write-Host "[STEP 3/5] FAIL $startBat not found in checkout" -ForegroundColor Red
    Exit-WithContact 2
}
$iconPath = Join-Path $installDir 'assets\clayrune.ico'
$wsh = New-Object -ComObject WScript.Shell
$lnks = @(
    "$env:USERPROFILE\Desktop\Clayrune.lnk",
    "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Clayrune.lnk"
)
foreach ($lnk in $lnks) {
    try {
        $sc = $wsh.CreateShortcut($lnk)
        if (Test-Path $hiddenVbs) {
            # Launch windowless so end users never see the server's log console.
            # wscript runs the .vbs, which starts start.bat with a hidden window.
            # IconLocation is set explicitly below so the shortcut still shows
            # the Clayrune icon rather than wscript.exe's.
            $sc.TargetPath = $wscriptExe
            $sc.Arguments  = "`"$hiddenVbs`""
        } else {
            $sc.TargetPath = $startBat
        }
        $sc.WorkingDirectory = $installDir
        if (Test-Path $iconPath) { $sc.IconLocation = $iconPath }
        $sc.Description = 'Clayrune'
        $sc.Save()
        Write-Host "  Created $lnk"
    } catch {
        # `${lnk}` braces required: `$lnk:` would be parsed as a drive-
        # qualified variable (like $env: / $function:) and crash the script
        # with "InvalidVariableReferenceWithDrive".
        Write-Host "  WARN could not create ${lnk}: $_" -ForegroundColor Yellow
    }
}

# A separate Start Menu entry gives the installation a discoverable, safe
# removal path. It never shares Clayrune's working directory, so the
# uninstaller can delete the checkout after it has stopped the verified server.
if (Test-Path $uninstallPs1) {
    try {
        $uninstallLnk = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Uninstall Clayrune.lnk"
        $powershellExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $sc = $wsh.CreateShortcut($uninstallLnk)
        $sc.TargetPath = $powershellExe
        $sc.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$uninstallPs1`" -InstallDir `"$installDir`""
        $sc.WorkingDirectory = $env:TEMP
        if (Test-Path $iconPath) { $sc.IconLocation = $iconPath }
        $sc.Description = 'Safely uninstall Clayrune'
        $sc.Save()
        Write-Host "  Created $uninstallLnk"
    } catch {
        Write-Host "  WARN could not create uninstall shortcut: $_" -ForegroundColor Yellow
    }
}
Write-Host '[STEP 3/5] OK' -ForegroundColor Green
Write-Host ''

# -- [STEP 4/5] Launch the server (windowless) ----------------------------
Write-Host '[STEP 4/5] Launching server (windowless)...' -ForegroundColor White
if (Test-Path $hiddenVbs) {
    # Match the shortcut: no visible console for end users. Server logs are
    # written to data\logs\clayrune.log.
    Start-Process -FilePath $wscriptExe -ArgumentList "`"$hiddenVbs`"" -WorkingDirectory $installDir
} else {
    Start-Process -WindowStyle Minimized -FilePath $startBat -WorkingDirectory $installDir
}
Write-Host '  Polling http://localhost:5199/ for up to 30s...'
$serverUp = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $resp = Invoke-WebRequest -Uri 'http://localhost:5199/' -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        $serverUp = $true
        break
    } catch {
        # Connection refused = server not up yet - keep polling
    }
}
if ($serverUp) {
    Write-Host '[STEP 4/5] OK' -ForegroundColor Green
} else {
    Write-Host '[STEP 4/5] WARN server did not respond within 30s.' -ForegroundColor Yellow
    Write-Host '          The install completed; you can launch manually via the' -ForegroundColor Yellow
    Write-Host '          Clayrune shortcut on your Desktop.' -ForegroundColor Yellow
}
Write-Host ''

# -- [STEP 5/5] Dashboard window --------------------------------------------
# start.bat (launched in Step 4) already opens Clayrune as a standalone app
# window with its own taskbar icon once the server is up (via
# installer\launch-app-window.ps1). Opening another tab here would put a
# duplicate, browser-iconed window on top of it — so don't.
Write-Host '[STEP 5/5] Clayrune window opens automatically once the server is ready.' -ForegroundColor White
Write-Host '          If nothing appears, open http://localhost:5199 manually.'
Write-Host '[STEP 5/5] OK' -ForegroundColor Green
Write-Host ''

# -- Final verification -----------------------------------------------------
Write-Host "[install] Verifying install at: $installDir" -ForegroundColor Cyan
$mustExist = @(
    (Join-Path $installDir 'server.py'),
    (Join-Path $installDir 'installer\start.bat')
)
$missing = @($mustExist | Where-Object { -not (Test-Path $_) })
if ($missing.Count -gt 0) {
    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Red
    Write-Host '  Install verification FAILED after deterministic install' -ForegroundColor Red
    Write-Host '============================================================' -ForegroundColor Red
    Write-Host '  Missing:'
    foreach ($m in $missing) { Write-Host "    - $m" -ForegroundColor Red }
    Write-Host '  This should not happen - please report this output as an issue.'
    Exit-WithContact 2
}

Write-Host ''
Write-Host '============================================================' -ForegroundColor Green
Write-Host '  Clayrune is installed and running.' -ForegroundColor Green
Write-Host '============================================================' -ForegroundColor Green
Write-Host "  Open:     http://localhost:5199"
Write-Host "  Location: $installDir"
if ($ChosenProvider) {
    Write-Host "  Provider: $ChosenProvider (change any time in Settings)"
} else {
    Write-Host '  Provider: choose one in Clayrune on first launch'
}
Write-Host '  Relaunch: double-click the Clayrune shortcut on your Desktop'
Write-Host '            (also available in your Start Menu).'
Write-Host '  Uninstall: choose Uninstall Clayrune from your Start Menu.'
Write-Host '============================================================' -ForegroundColor Green
[Environment]::Exit(0)
