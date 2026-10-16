# Suite triage product fixes: A1, A2, A4

Base: `1b6d15b1`. Branch: `clayrune/agent/f5fe1f08cf7d`.
Source evidence: [original triage, section A](SUITE_TRIAGE_2026-10-16.md#a-product-findings-left-unchanged).

## Changes

- **A1:** `mc/dev_harness_context.py` normalizes a missing project to an empty mapping before rendering. The existing Error block and bounded diagnostic remain available; the handler no longer raises `AttributeError` while looking up the project ID. Both public function annotations accept `None`.
- **A2:** `mc/desk_connect/custom_npm_scripts.py` and `mc/desk_connect/github_start_check.py` use `mc.proc_kill.kill_tree` for POSIX cleanup. This checks group ownership and falls back to the target's subtree when a group is unsafe. Existing Windows cleanup remains in place; the start check retains direct-child cleanup if the helper signals nothing. The structural guard has no new exception.
- **A4:** both version probes in `mc/browser_default_ua.py` and Git HEAD lookup in `mc/skills.py` use `encoding='utf-8', errors='replace'` with `text=True`.
- Six new caller-level cases in `tests/test_proc_kill_guard.py` exercise the real guard against a simulated server group, another group's leader, and an owned child group. They assert server/ancestor/sibling preservation and target-subtree cleanup, without signalling real processes.

## Verification

Python: main checkout's `.venv/Scripts/python.exe`, run from this worktree.
Each command below starts `python -m pytest`; `-o addopts=` clears the repository's extra quiet flag so pass counts are printed.

Before edits, the original minimal selection reproduced all three reported findings:

```powershell
python -m pytest -q tests/test_context_freshness.py::test_it_never_raises_on_a_project_with_no_paths tests/test_proc_kill_guard.py::test_no_raw_killpg_outside_the_guard tests/test_subprocess_text_encoding.py
```

After edits:

```powershell
python -m pytest -o addopts= -q tests/test_context_freshness.py tests/test_proc_kill_guard.py tests/test_subprocess_text_encoding.py tests/test_browser_default_ua.py tests/test_skills_staging_path.py tests/test_character_skills.py tests/test_skill_import_guard.py tests/test_skill_usage_stats.py tests/test_dev_harness_vendor_delivery.py
```

**187 passed, 4 skipped in 7.50s**, exit 0. The four skips are real-POSIX process tests on Windows; the six new simulated POSIX cases passed on Windows. The vendor-delivery tests also exercise `skills.git_clone_to_staging` with mocked Git subprocesses and an immutable fixture pin.

The Desk run used the gitignored foreground helper `_scratch/run_registered_pytest.py --desk-connect`, which registers its own PID in Clayrune's Process Manager and calls `pytest.main` with the expanded arguments below. The exact invocation was:

```powershell
python _scratch/run_registered_pytest.py --desk-connect
```

Equivalent expanded pytest command (the wrapper's logged argument list):

```powershell
python -m pytest -o addopts= -q `
  tests/test_desk_connect.py `
  tests/test_desk_connect_browser_setup.py `
  tests/test_desk_connect_custom.py `
  tests/test_desk_connect_custom_audit.py `
  tests/test_desk_connect_custom_manifest.py `
  tests/test_desk_connect_discovery.py `
  tests/test_desk_connect_display_text.py `
  tests/test_desk_connect_github.py `
  tests/test_desk_connect_github_check.py `
  tests/test_desk_connect_github_install_steps.py `
  tests/test_desk_connect_github_output.py `
  tests/test_desk_connect_github_start.py `
  tests/test_desk_connect_mcp.py `
  tests/test_desk_connect_mcp_followups.py `
  tests/test_desk_connect_names.py `
  tests/test_desk_connect_npm_closure.py `
  tests/test_desk_connect_npm_launch_gate.py `
  tests/test_desk_connect_npm_node_paths.py `
  tests/test_desk_connect_npm_scripts.py `
  tests/test_desk_connect_npm_semver.py `
  tests/test_desk_connect_package_help.py `
  tests/test_desk_connect_parameters.py `
  tests/test_desk_connect_permission_execution.py `
  tests/test_desk_connect_permission_policy.py `
  tests/test_desk_connect_providers.py `
  tests/test_desk_connect_purpose_verification_consent.py `
  tests/test_desk_connect_real_chromium.py `
  tests/test_desk_connect_reference_draft.py `
  tests/test_desk_connect_registry_bounds.py `
  tests/test_desk_connect_remote.py `
  tests/test_desk_connect_secret_placeholders.py `
  tests/test_desk_connect_signin_login_save.py `
  tests/test_desk_connect_types.py `
  tests/test_desk_connect_vault_picker.py `
  tests/test_desk_connect_verify_consent.py `
  tests/test_desk_connect_x_account_attach.py
```

**1,530 passed in 313.16s**, exit 0; no skips or failures. This includes every `tests/test_desk_connect_*.py` file plus `tests/test_desk_connect.py`. A read-only stack sample during slow progress showed the existing `_wait_x_port_free` fixture teardown in `tests/test_desk_oauth_hold.py`; no test or timeout was changed.

`git diff --check` also passed. The two successful selections are disjoint; the original failing selection overlaps the focused run and must not be added to its counts.

## Scope and delivery

- A3 capture navigation flake and scheduler Pool hang are untouched and excluded from this targeted validation. No full-suite or release-clearance claim.
- No real POSIX cleanup was exercised on this Windows host. Browser version commands for Linux/macOS use the existing mocked module tests.
- Internal fixes only: no new UI or user actions, so `docs/USER_GUIDE.md`, README and agent instructions need no changes.
- Local branch commit only; no merge, push, server restart, or live configuration changes.
