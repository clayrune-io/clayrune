# Fail-closed fence for unattended Claude launches: measurements

2026-10-09. Code: `mc/fence_fail_closed.py`, called through
`mc/launch_marker.launch_cmd` at every Claude spawn site. Tests:
`tests/test_fence_fail_closed_unattended.py`, `tests/test_launch_marker.py`.

## Question

Claude Code 2.1.295 added `onFailure: "block"` on command hooks (Quill,
2026-10-09: a hook that cannot start, times out or exits with an unexpected code
blocks the call; without the key the call runs). Our fence entry has no such key,
and a Clayrune launch registers the fence twice: once in the project's
`.claude/settings.json` (`steward.core.install_fence_to_project`) and once in the
per-launch `--settings` file (`tools/guards/install_hooks.py`). Does a per-launch
`--settings` hook MERGE with the project entry or REPLACE it, and what happens to
two entries carrying the same command?

## Method

Real Claude Code 2.1.295 (`cc295/` npm prefix copy Quill installed under
`%TEMP%\quill-cw-1009`, `DISABLE_AUTOUPDATER=1`), `--model haiku`, `--max-turns 3`,
`--dangerously-skip-permissions`, cwd = a probe project holding
`.claude/settings.json`, plus `--settings <launch.json>`. Prompt: `Use the Bash
tool to run exactly: echo probe-ok .` Each case is one `claude -p` call. A passing
call returns `probe-ok`; a blocked one has `permission_denials` of length 1 and the
hook error text. The hook is either `hook.py` (appends `<tag> <tool>` to a log, exit
0) or a missing interpreter `"C:/nonexistent-probe/python.exe" "C:/nonexistent-probe/fence.py"`
(the "hook cannot start" case).

```
claude.cmd -p "<prompt>" --output-format json --model haiku --max-turns 3 \
  --dangerously-skip-permissions --settings <case>/launch.json     # cwd <case>/proj
```

## Results (2.1.295)

| case | project `.claude/settings.json` | `--settings` file | hook log / result |
|---|---|---|---|
| M1 merge | cmd A (tag PROJECT) | cmd B (tag LAUNCH) | `['PROJECT Bash', 'LAUNCH Bash']`, `probe-ok`: both ran, **MERGE** |
| M2 | cmd A `timeout:10000` | byte-identical cmd A, `timeout:10, onFailure:block` | `['SAME Bash']`: **one run**, identical commands collapse |
| M7 | as M2 | as M2 plus `name:"clayrune-steward-fence"` | `['SAME Bash']`: `name` does not break the collapse |
| M3 | missing interpreter, `timeout:10000`, no onFailure | same command, `timeout:10, onFailure:block` | **blocked**: `failed; blocking because onFailure is "block"` |
| M8 | as M3 | as M3 plus `name` (the real shape) | **blocked** |
| M3b | same command, `onFailure:block` | same command, **no** onFailure | `probe-ok`: **not blocked**, the `--settings` entry replaced the project one |
| M4 | missing interpreter, no onFailure | command + extra arg (`--fail-closed`), `onFailure:block` | **blocked** (different commands both run; the blocking one wins) |
| M5 control | missing, no onFailure | missing, no onFailure | `probe-ok` (fails open, the bug) |
| M6 | none (worktree shape) | missing, `onFailure:block` | **blocked** |
| T1 | none | `sleep 5`, `timeout:2, onFailure:block` | blocked: `timed out; blocking because onFailure is "block"` |
| T2 | none | `sleep 5`, `timeout:2`, no onFailure | `probe-ok` (a timed-out hook does not block) |
| T3 | none | `exit 1`, `onFailure:block` | blocked: `failed; blocking ...` |
| T4 | none | `exit 1`, no onFailure | `probe-ok` (exit 1 is non-blocking without the key) |
| T5 control | none | `exit 0`, `onFailure:block` | `probe-ok` |

M3 and M3b were run twice with the same outcome.

## Decision

**Merge, with last-wins collapse of identical commands.** So:

* The unattended launch needs no new file in the project and no change to the
  project entry. It hands the CLI a copy of the `--settings` file whose fence entry
  (found by `name == clayrune-steward-fence`) has `onFailure: "block"` and
  `timeout: 30` (M3, M8). The command string is left byte-identical to the project
  entry's (`tests/test_install_hooks.py::test_fence_command_matches_steward_core_project_install_command`),
  so Claude Code collapses the two into one run carrying the launch entry's keys
  (M2, M3, M3b): **the fence does not run twice per call**, cost unchanged.
* If the commands ever differ (different interpreter after a venv move), both run,
  and the `onFailure` one still blocks (M4). Cost then is the two runs the launch
  file already pays today (MC-1055).
* Do NOT put `onFailure` on the project entry alone: M3b shows the `--settings`
  entry wins the collapse, so a plain launch-file entry would erase it.
* T1/T3: a fence that crashes (exit 1) or hangs past its timeout is blocked with the
  key and passes without it. `timeout` is SECONDS (T1: `timeout:2` timed out a 5 s hook).

## End to end through our code (real 2.1.295, 2.1.293)

`launch_marker.launch_cmd(cmd, trigger_type)` then `claude` on the returned list, with a
stand-in fence that passes `--self-test` (so the launch is not refused) and then exits 1
on a real call (the crash-after-self-test case); project entry written by
`steward.core._fence_settings_content()`, launch file by `install_hooks.generate('claude')`:

```
=== cc295 trigger=dispatch: cli=(2, 1, 295) settings_swapped=True  denials=1 fence_runs=1
    result='PreToolUse:Bash hook error: [...crashy_fence.py"]: failed; blocking because onFailure is "block"'
=== cc295 trigger=manual:   cli=(2, 1, 295) settings_swapped=False denials=0 fence_runs=1
    result='probe-ok'
=== cc293 trigger=dispatch: cli=(2, 1, 293) settings_swapped=False denials=0 fence_runs=1
    result='probe-ok'
=== cc295 trigger=dispatch, real fence.py: settings_swapped=True denials=0
    result='probe-ok'
```

Line 2: attended is unchanged (same list, plain file, nothing blocked). Line 3: on this
machine's 2.1.293 the key does nothing, so the only protection there is the pre-spawn
self-test; a fence that passes it and then crashes still fails open on that CLI. Line 4: a
healthy real fence is not wedged by the key.

## Cost

Measured on this box, real fence, 15 runs: `launch_cmd` for an unattended launch (self-test
subprocess + cached version) min 0.095 s, median 0.097 s, max 0.102 s; first `claude
--version` probe 0.041 s, once per installed binary identity (path, mtime/size, and the
npm package.json beside the shim); cached lookup 0.27 ms; attended `launch_cmd` 1.6 us.
