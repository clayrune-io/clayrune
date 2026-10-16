# Qwen standalone updates (MC-1102)

Implemented on a feature branch. No installed CLI was changed during validation.

## Official sources, verified 2026-10-16

- [QwenLM/qwen-code README](https://github.com/QwenLM/qwen-code#installation).
- [Installation guide](https://github.com/QwenLM/qwen-code/blob/main/scripts/installation/INSTALLATION_GUIDE.md).
- [Windows PowerShell entrypoint](https://github.com/QwenLM/qwen-code/blob/main/scripts/installation/install-qwen-standalone.ps1).
- [Windows implementation](https://github.com/QwenLM/qwen-code/blob/main/scripts/installation/install-qwen-with-source.bat).
- [macOS/Linux implementation](https://github.com/QwenLM/qwen-code/blob/main/scripts/installation/install-qwen-with-source.sh).

The README and guide identify these hosted installer URLs:

- Windows: https://qwen-code-assets.oss-cn-hangzhou.aliyuncs.com/installation/install-qwen-standalone.ps1
- macOS/Linux: https://qwen-code-assets.oss-cn-hangzhou.aliyuncs.com/installation/install-qwen-standalone.sh

These installers replace an existing managed installation and check archive
checksums. Their standalone paths use per-user directories, require no admin,
input or login, and do not launch Qwen. Windows uses `QWEN_INSTALL_METHOD=standalone`
in a fresh noninteractive PowerShell process. Unix passes `--method standalone`
to the official Bash installer and enables pipeline failure reporting. This
prevents the installer's default detect mode from falling back to npm.

## Detection and routing

`mc/qwen_cli_install.py` owns Qwen's policy. `mc/cli_install.py` delegates to
it for standalone detection and update commands. Detection checks the resolved
launcher's location and `manifest.json` with `name: @qwen-code/qwen-code`:

| OS | Public launcher | Managed runtime |
|---|---|---|
| Windows | `%LOCALAPPDATA%/qwen-code/bin/qwen.cmd` | `%LOCALAPPDATA%/qwen-code/qwen-code` |
| macOS/Linux | `~/.local/bin/qwen` | `~/.local/lib/qwen-code` |

The inner runtime's `bin/qwen.cmd` or `bin/qwen` also qualifies. Windows
comparison ignores case. Unix's public launcher is a generated shell wrapper,
not necessarily a symlink. The installer's `QWEN_INSTALL_ROOT`,
`QWEN_INSTALL_BIN_DIR`, `QWEN_INSTALL_LIB_DIR` and Unix
`QWEN_INSTALL_LIB_PARENT` overrides are honoured. Custom locations with no
matching environment configuration remain unknown: rerunning the installer
would otherwise create a second copy. Missing, malformed or wrong-package
manifests refuse standalone routing. Manifest read failures are logged.

`tools/cli-version-check.py` now includes Qwen in its CLI list and routes its
standalone update through this module. npm installations retain npm; unsupported
methods remain report-only. Other CLIs retain their existing routing. Codex's
standalone path signatures apply only to Codex.

The server's built-in daily updater uses the same detector and argv. Its
in-use check covers the managed Qwen runtime, including bundled Node, rather
than just the public launcher directory. Existing live-session and failed
process-enumeration refusal remain in force.

## In-app Update button

Qwen health checks compare the current CLI version against the last built-in
daily check's registry version. They do not fetch the network. A known newer
version supplies the existing provider-row Update reason and a method-matched
command. No registry result, unreadable version or an already-current version
shows no update action. Unknown installation methods show the newer-version
reason without a runnable button.

The existing install-launch route accepts only Qwen's fixed commands for Qwen;
npm updates retain existing prerequisite onboarding. The human passcode gate
is unchanged. The new module owns policy; the large runtime and route files
receive only hooks into their existing health/update concerns.

## Validation

All updater execution in tests is stubbed. Tests cover Windows/macOS/Linux
wrapper and inner-launcher layouts, manifests, configured custom roots, npm and
unknown routing, daily script/server command agreement, simulated version
changes, health hints, bundled-node use, fixed-command launch and passcode refusal.

Commands and results:

```text
python -m pytest tests/test_qwen_cli_install.py tests/test_cli_install.py tests/test_cli_update.py tests/test_cli_version_check.py tests/test_install_launch_gate.py tests/test_agent_routes.py tests/test_provider_runtimes.py -k "not test_compose_install_batch_posix_runs_each_vendor_independently" -q -o addopts=
509 passed, 1 skipped, 1 deselected in 27.29s

python -m pytest tests/test_agent_routes.py -k qwen_update -q -o addopts=
6 passed, 134 deselected in 1.17s (final allowlist change)

pyright mc/qwen_cli_install.py mc/cli_install.py mc/cli_update.py
0 errors, 0 warnings, 0 informations

git diff --check
exit 0
```

Read-only detection was also exercised against an installed Windows launcher.
No `--apply`, real installer, CLI update, merge, push or restart was run.
Actual installation and native macOS/Linux execution remain unverified by design.

An existing Windows environment failure is excluded from the combined check:
`test_compose_install_batch_posix_runs_each_vendor_independently`. The identical
function extracted from branch HEAD also returned 0 through
`C:/WINDOWS/system32/bash.EXE` despite failed fake segments; its AST is unchanged
by MC-1102. The original full run is recorded in the journal.

Rollback: revert the MC-1102 commit; this restores report-only standalone Qwen
updates and removes its in-app update advice. No installer state migration exists.
