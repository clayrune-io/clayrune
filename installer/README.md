# Clayrune Installer

Clayrune is installed first. Provider selection belongs to the first-run UI,
which lists registered providers, offers provider-specific CLI installation,
and routes login to the provider the user chose. The normal install path does
not require Claude (or any other provider), Node, or provider authentication.

`CLAYRUNE_PROVIDER=claude|codex|gemini|qwen` remains an explicit automation
override. It is never inferred from PATH and is preserved on re-runs.

## Architecture

```
                user runs:
                curl -sSL https://clayrune.io/install.sh | sh
                       │
                       ▼
        ┌─────────────────────────────────┐
        │  install.sh / install.ps1       │  bootstrap (~100 lines)
        │  ──────────────────────────────  │  ── verifies / installs Claude CLI
        │                                  │  ── fetches install-prompt.md
        │                                  │  ── pipes it into:
        │   claude --dangerously-skip-     │
        │           permissions -p "..."   │
        └────────────────┬────────────────┘
                         │
                         ▼
        ┌─────────────────────────────────┐
        │  Claude CLI (the user's own)    │  executes the install prompt
        │                                  │
        │  STEP 1/6  detect environment   │
        │  STEP 2/6  clone or pull repo   │
        │  STEP 3/6  python venv + deps   │
        │  STEP 4/6  node.js              │
        │  STEP 5/6  desktop launcher     │
        │  STEP 6/6  start server +       │
        │            open browser         │
        └────────────────┬────────────────┘
                         │
                         ▼
        ┌─────────────────────────────────┐
        │  Clayrune running at            │
        │  http://localhost:5199          │
        │                                  │
        │  Desktop / Start Menu /         │
        │  Applications has a Clayrune    │
        │  shortcut for relaunching.      │
        └─────────────────────────────────┘
```

## Provider onboarding contract

- Unset `CLAYRUNE_PROVIDER` means no provider CLI or auth probe runs during
  installation; Clayrune opens and asks in its UI.
- An explicit provider is validated and only that CLI is provisioned. A Codex,
  Gemini, or Qwen choice never triggers a Claude-missing prompt.
- Re-running the installer never overwrites an existing `default_provider`.
- Provider login is initiated from the selected-provider card in Settings or
  the first-run walkthrough. The installer does not spend live provider quota
  merely to render onboarding.

`install-prompt.md` is retained as an audit/history pointer only. Supported
installers execute clone, Python, launcher, and server steps directly; no LLM
is handed an installation prompt.

## Files in this directory

| File | Purpose |
|---|---|
| `Clayrune-Installer.exe` | **Primary Windows path.** A native console launcher (double-click). It downloads `install.ps1` from the exact Git commit used for the build, verifies its pinned SHA-256, runs the verified local file, and offers a built-in provider login retry loop. It never pipes downloaded text into PowerShell. |
| `win-exe/ClayruneInstaller.cs` | Source for `Clayrune-Installer.exe`. Faithful port of the old `Clayrune-Setup.bat` flow. |
| `win-exe/build.ps1` | Compiles the EXE with the .NET Framework `csc.exe` already on every Windows 10/11 box and generates the commit-pinned bootstrap URL/hash. Local output is unsigned; `.github/workflows/build-windows-installer.yml` performs release signing. |
| `Clayrune-Setup.bat` | Legacy Windows double-click path, superseded by the `.exe`. Kept as a plain-text fallback for users who distrust binaries. |
| `Clayrune-Setup.command` | macOS double-click path (same role as the `.bat`). |
| `install.sh` | Bootstrap for macOS / Linux. The user runs `curl -sSL https://clayrune.io/install.sh \| sh`. |
| `install.ps1` | Bootstrap for Windows. The canonical install logic the `.exe` (and the PowerShell one-liner) hand off to. |
| `uninstall.ps1` | Safe Windows uninstaller. Default preserves Clayrune data and all provider state; `-PurgeData` removes Clayrune-owned data after a stronger confirmation. Installed as a Start Menu shortcut. |
| `install-prompt.md` | The actual installer logic, written as a prescriptive prompt for Claude. The bootstrap fetches this and pipes it into `claude --dangerously-skip-permissions`. |
| `start.sh` | Per-user launcher (Linux). Activates `.venv`, starts `python server.py`, opens the browser. The installer registers this as a `.desktop` file in `~/.local/share/applications/`. |
| `start.command` | Per-user launcher (macOS). Same role as `start.sh`. The installer copies it to `~/Applications/Clayrune.command`. |
| `uninstall-macos.command` | Safe macOS uninstaller for source installs and the signed `.app`. Default preserves Clayrune data and all provider state; `--purge-data` is separately confirmed. Installed at `~/Applications/Uninstall Clayrune.command` by the script installer and bundled into the signed app. |
| `start.bat` | Per-user launcher (Windows). Same role. Run directly for a visible dev console (live logs). The `.lnk` shortcut does **not** target it directly — see `start-hidden.vbs`. |
| `start-hidden.vbs` | Default Windows entry point. Runs `start.bat` with **no console window** (end users shouldn't see the server log console) and sets `CLAYRUNE_HIDDEN=1` so logs are written to `data\logs\clayrune.log` instead. The installer points the Desktop / Start Menu `.lnk` here (via `wscript.exe`). |

## Why this design

- **Small native wrapper**: Windows gets a reviewable C# launcher around the canonical PowerShell bootstrap. The release workflow signs and timestamps that wrapper; the install logic remains plain text in the repository.
- **Cross-platform "for free"**: Claude figures out OS, package manager, and Python/Node install paths. We don't write per-distro shell logic.
- **Self-healing**: when winget hiccups or apt is locked, Claude can diagnose and try a different approach. A scripted installer can't.
- **No bundling, no licensing review**: we never redistribute Claude CLI, Node, Python, or any other dependency.
- **On-brand**: "an AI agent installs the AI-agent operator console." It's a demo as much as an install.

## Disclosure model

The bootstrap clearly prints the exact `claude --dangerously-skip-permissions` line it's about to execute and gives the user 5 seconds to Ctrl-C out before handing off. The install prompt is publicly hosted at `https://clayrune.io/install-prompt.md` so anyone can audit what they're authorizing before running the bootstrap. The prompt is conservative: it does `git`, `pip install`, package-manager calls, and launches the app. It does NOT modify dotfiles, change the system PATH, write outside the install dir, or run `sudo` unless absolutely required (and explains why one line earlier).

## Hosting

| URL | What it serves | Source |
|---|---|---|
| `https://github.com/clayrune-io/clayrune/releases/latest/download/Clayrune-Installer.exe` | the verified-bootstrap Windows launcher linked from clayrune.io (primary) | signed workflow artifact from `.github/workflows/build-windows-installer.yml`; attach only the verified signed output to a release |
| `https://clayrune.io/install.sh` | the bootstrap (macOS/Linux) | this repo: `installer/install.sh` |
| `https://clayrune.io/install.ps1` | the bootstrap (Windows) | this repo: `installer/install.ps1` |
| `https://raw.githubusercontent.com/clayrune-io/clayrune/master/installer/uninstall-macos.command` | safe macOS uninstaller | this repo: `installer/uninstall-macos.command` |
| `https://raw.githubusercontent.com/clayrune-io/clayrune/master/installer/uninstall.ps1` | safe Windows uninstaller | this repo: `installer/uninstall.ps1` |
| `https://clayrune.io/install-prompt.md` | the install prompt | this repo: `installer/install-prompt.md` |

For testing before the domain is up, the same files can be served from
`https://raw.githubusercontent.com/clayrune-io/clayrune/master/installer/<file>`.
The bootstraps respect a `CLAYRUNE_PROMPT_URL` env var so you can point them at
any URL.

## Testing checklist

Offline provider-neutral checks are covered by
`tests/test_installer_provider_neutral.py` alongside the Qwen, auth-probe, and
Windows security tests. They do not install or authenticate a live provider.

A new install on a clean VM should:

- [ ] Download without a Microsoft Defender malware detection
- [ ] Report `Valid` from `Get-AuthenticodeSignature` for a release build
- [ ] Refuse to execute `install.ps1` when its SHA-256 differs from the build-pinned value
- [ ] Complete in under 5 minutes with no manual intervention beyond the initial `curl … | sh`
- [ ] End with the browser open at `http://localhost:5199`
- [ ] Place a clickable launcher on the Desktop and in the OS app menu
- [ ] Survive a re-run (idempotent — clone becomes pull, venv is recreated, deps re-installed)
- [ ] Leave nothing in `/etc`, `/usr`, or system-wide locations
- [ ] Not modify `.bashrc`, `.zshrc`, or system PATH

Run on at least: Windows 11, macOS 14+, Ubuntu 22.04. Each test should start from a snapshot with only Claude CLI pre-installed (or nothing pre-installed — the bootstrap handles that case too).

## Updates

The same model handles updates. After the install, the user can run:

```sh
claude "update Clayrune in ~/Clayrune by running git pull, reinstalling Python deps if requirements.txt changed, and restarting the server"
```

A future enhancement may add `clayrune.io/update.sh` that scripts this more formally.

## Uninstall safety contract

Both uninstallers have two explicit modes:

- **Default — remove app, preserve data.** Removes Clayrune, its launchers, and
  Clayrune autostart entries. Checkout-backed `config.json` and `data/` are
  copied to `~/.clayrune/uninstall-archives/<timestamp>/` first. Frozen-app
  data and `~/.clayrune` remain in place.
- **Purge data.** `-PurgeData` (Windows) / `--purge-data` (macOS) additionally
  removes Clayrune-owned runtime data, credentials, browser profiles, and local
  backups after the user types `PURGE CLAYRUNE DATA`.

Neither mode removes an AI provider CLI, Node.js, Python, Git, `~/.claude`, or
any external project directory. A server is stopped only when its executable
or command line can be tied to Clayrune; occupying port 5199 is not sufficient.
Both scripts support a dry run.
# Authentication preflight regression (2026-09-16)

Windows `install.ps1` uses `claude auth status`, not a model prompt, with a
20-second timeout and fail-closed JSON parsing. Tests:
`python -m pytest tests/test_installer_auth_probe.py tests/test_windows_installer_security.py tests/test_installer_qwen_provider.py`.
Re-test on a clean Windows VM before release. The EXE pins the bootstrap URL
and SHA-256 at build time: pushing a script fix does not repair already
downloaded EXEs; build and distribute a new installer through the existing
signed release workflow.
