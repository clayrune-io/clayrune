# fence-timeout-seconds

`docs/patches/fence-timeout-seconds.patch` fixes the unit of the fence hook
timeout in `steward/core.py::_fence_settings_content()`.

Claude Code hook timeouts are seconds. The entry had `"timeout": 10000`, written as
if milliseconds: 10000 s is 2 h 47 min. Measured on 2.1.295 (see
`docs/FENCE_FAIL_CLOSED_MEASUREMENT.md`, case T1): `timeout: 2` timed out a 5 s hook.
Ten seconds is the evident intent, so the patch names the constant
`FENCE_HOOK_TIMEOUT_SEC = 10` and uses it.

`steward/core.py` is human-owned (the fence refuses an agent edit to it), so this ships
as a patch, like the sibling `fence-*.patch` files. It touches nothing else.

## Apply

```
git apply --check docs/patches/fence-timeout-seconds.patch   # applies to master f23ee8b7
git apply docs/patches/fence-timeout-seconds.patch
```

Then `tests/test_fence_fail_closed_unattended.py::test_project_fence_entry_timeout_is_seconds_not_ms`
flips from xfail to a plain pass (it is `xfail(strict=True)` while the constant is
missing, so it will also tell you if the patch lands twice).
`test_patch_applies_to_a_throwaway_copy_and_fixes_the_timeout` applies the patch to a
copy outside the repo and runs the patched module; it skips once the fix is in.

## What it changes for attended sessions (Ron's call to apply)

Nothing the human sees. A timed-out fence hook does not block (T2), so the timeout only
bounds a HUNG fence: after the patch a hung fence stalls a tool call for 10 s instead
of ~2 h 47 min, then the call runs. That is a change to attended sessions in the
pathological case only, which is why it is a patch and not applied here.

Existing installs keep `10000` in each project's `.claude/settings.json` until
`install_fence_to_project` rewrites it (steward enable, or project path change); the
unattended per-launch entry is unaffected, because for the same command string it
replaces the project entry (measurement doc, M2/M3) and carries its own 30 s.

## Not in this patch

The unattended fail-closed entry (`onFailure: "block"`, 30 s) is plain code in
`mc/fence_fail_closed.py`; it does not need this patch.
