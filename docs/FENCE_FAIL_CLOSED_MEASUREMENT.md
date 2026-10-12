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

* The unattended launch needs no change to the project file. It hands the CLI a
  copy of the `--settings` file whose fence entry (found by `name ==
  clayrune-steward-fence`) has `onFailure: "block"` and `timeout: 30` (M3, M8).
* **Correction (follow-up, same day): "once per call" holds only when the two
  commands are byte-identical, and on this machine they are not.** The code builds
  both from `sys.executable` of the process that wrote them
  (`tests/test_install_hooks.py::test_fence_command_matches_steward_core_project_install_command`
  pins only that the two *functions* agree), but this box's project files were written
  by the system python (`...\pythoncore-3.14-64\python.exe`, `timeout` 10000) and the
  launch file by the `.venv` python. Different strings do not collapse (M1, M4), so the
  fence runs **twice per call here, as it did before this branch** (MC-1055 case). The
  first commit's message (8bae6685) says the fence "does not run twice per call"; that
  overstates it.
* If the commands differ both run, and the `onFailure` one blocks (M4) **but only
  after every hook has finished** (P5 below). Hence the shadow entry, next section.
* Do NOT put `onFailure` on the project entry alone: M3b shows the `--settings`
  entry wins the collapse, so a plain launch-file entry would erase it.
* T1/T3: a fence that crashes (exit 1) or hangs past its timeout is blocked with the
  key and passes without it. `timeout` is SECONDS (T1: `timeout:2` timed out a 5 s hook).

## P5: a hung fence when the two entries are different commands (Quill, then re-run here)

Quill's probe (`%TEMP%/quill-cw-1009/mergeprobe/hang_probe.py`): project entry sleeps
45 s (`timeout` 10000, no onFailure), the launch's own fence entry is a *different*
command that sleeps 46 s with `timeout: 5` + `onFailure: "block"`. The launch entry
timed out and blocked, but the block landed only when the project entry finished 45 s
later: Claude Code waits for **every** hook before it applies a block. With the real
project timeout of 10000 a hung fence would hold each unattended call that long.

Re-run on this branch's real mechanism, 2.1.295, stand-in fence that answers
`--self-test` and then sleeps (project 45 s `--project`, launch 46 s), project file
written like `install_fence_to_project` (`timeout: 10000`), launch file from
`install_hooks.generate('claude')`, one `claude -p` per row:

| mechanism | `--settings` fence entries | hook log | blocked? | wall for the call |
|---|---|---|---|---|
| launch file as before this branch | launch fence plain | `end PROJECT` 45 s, `end LAUNCH` 46 s | no (`probe-ok`) | 52.5 s |
| 8bae6685 (copy, no project shadow) | launch fence `onFailure`/30 | `end PROJECT` after 45 s; LAUNCH killed | yes, but only after PROJECT ended | 51.0 s (`duration_ms` 47939) |
| follow-up (copy + project shadow) | launch fence **and** a twin of the project command, both `onFailure`/30 | both started, neither ended (both killed at the 30 s timeout) | **yes, at the 30 s timeout** | **36.7 s** (`duration_ms` 33581) |

The model made one Bash attempt in each run. 36.7 s = 30 s hook timeout + about 6.7 s
of model turns, so the block lands within ~30 s of the call, not 45 s or 10000 s.

How: `launch_cmd` is handed the child's cwd; if `<cwd>/.claude/settings.json` has a
steward fence entry (command containing `fence.py`, or the frozen-build marker)
that differs from the launch file's own, and that command passes `--self-test`, the
copy gets one more entry with **that exact command**, the project entry's matcher,
`onFailure: "block"`, `timeout: 30`, `name: clayrune-steward-fence-project-copy`.
It collapses with the project entry and its keys win (M3/M3b), so the project's
`timeout: 10000` is out of play. Costs, stated plainly:

* **Two fence runs per call remain** when the commands differ (system-python fence
  and venv fence), exactly as today. The shadow replaces the project entry's run, it
  adds no third. When the commands are identical there is nothing to shadow and it is
  one run.
* One extra `--self-test` subprocess (~0.1 s) per unattended spawn when a different
  project command exists.
* The project file is read, never written. Rewriting existing project entries at
  boot (timeout 10000 -> 10, one interpreter) is a change to files the user owns and
  is left as an option for Ron.
* A project entry that fails `--self-test` (stale interpreter) is NOT shadowed: it
  already fails open without holding the call, our own entry still guards, and
  blocking every call because of a file we do not own would wedge the run.
* Only `settings.json` of the launch cwd is read. A worktree cwd has none (Claude
  Code does not walk up to the main checkout), so there is nothing to hold the call
  and nothing to shadow.

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
