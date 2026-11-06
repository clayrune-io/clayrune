# Reported test command (MC-1148 Phase 5)

This is the Phase 5 contract for the default-off test-run feature. Phase 6
adds a serial pytest adapter and source-asset packaging; its behavior overrides
the original script-only/source-only limits below for recognized direct pytest
commands. See `TEST_RUN_PYTEST.md` for the bounded enrollment wait, target
interpreter loading, actual fidelity and packaging verification limits. Passive
detection, chat cards, Floor UI and adoption enforcement remain later phases.
No config change, merge, push or restart is part of these branch candidates.

Phase 7 adds a separate direct Node script adapter and standalone preload;
see `TEST_RUN_NODE.md`. Its units are explicitly scripts, never assertions.
The existing explicit manifest supplies order and fail-fast/continue policy.

## Invocation

Use the target project's Python interpreter and the installation's absolute
`tools/test-run.py` path, from the original working directory:

```text
python <installation>/tools/test-run.py --project <project-id> --port <port> -- <original argv...>
python <installation>/tools/test-run.py --project <project-id> --shell powershell --command "<exact shell statement>"
python <installation>/tools/test-run.py --project <project-id> --manifest <explicit-manifest.json>
```

The entry imports only its sibling stdlib modules (`test_run_launcher.py` and
`test_run_transport.py`). The target project needs no `mc` package, repository
venv, Node dependencies or configuration edits. This requires Python 3.10+.
`--cwd` selects a directory (default: actual current directory);
`--timeout-seconds` is an optional positive finite limit per command. No test
selection, interpreter, environment, order, glob expansion or retries are
inferred. Direct argv uses `shell=False`. Explicit bash/sh/PowerShell/cmd mode
passes the exact string to the requested shell; unavailable shells fail before
execution rather than falling back to a different interpreter. On Windows,
cmd receives a raw `CreateProcess` string with one `/s /c` outer quote pair;
the inner statement bypasses Python's C-runtime list quoting, which would add
literal backslashes to quoted executable/script paths in cmd. Other shells
receive the statement as one argv argument. On Windows,
bash/sh reject the System32 WSL launcher and may resolve the same shell from
the existing Git installation. No shell or dependency is installed.

Long runs still use the existing server-owned job route with the launcher as
the job's command. This foreground launcher does not supply job adoption,
server-restart survival, unattended permission, automatic scheduling or a
different execution environment. Native foreground calls inherit their caller's
execution restrictions; reporting does not ask the server to execute anything.

## Explicit manifests

```json
{
  "schema": 1,
  "stop_on_failure": true,
  "units": [
    {"argv": ["python", "-m", "pytest", "tests/test_one.py"]},
    {"argv": ["node", "smoke-two.mjs"], "cwd": "tools/smoke", "timeout_seconds": 120}
  ]
}
```

Exactly the three top-level fields above are required. Each of 1..128 ordered
units specifies either `argv` (nonempty string array) or `shell` plus `command`,
with optional `cwd`, `env`, `timeout_seconds`. Unknown and duplicate keys,
non-finite values, NULs and manifests larger than 256 KiB are refused before
any command starts. Unit cwd is relative to the invocation's `--cwd`, never to
the manifest file or the preceding unit. Environment defaults to an unchanged
copy of the launcher's environment. Optional `env` is a per-unit overlay;
null removes a variable. Windows overlay/removal keys use `os.environ`'s
case-insensitive uppercase convention; POSIX keys retain their original case.
Do not place credentials in manifests or argv: retain
the vault/environment workflow. There is no CLI credential input.

`stop_on_failure:true` stops after the first nonzero exit. False runs all
requested units and returns the first nonzero exit, otherwise zero. Timeout or
cancellation always stops remaining units and returns that child's wait code.
Unexecuted units remain unexecuted, never skipped. There is no parsing or
expansion of `npm test`, `&&`, aliases or nested shell commands into a guessed
manifest. One opaque shell command is one unit, and its exit is the shell's
exit; `pytest; echo done` can still mask pytest's failure by shell semantics.

## Ownership, units and finalization

The launcher reads the existing config endpoint and reports only when
`test_run_progress_enabled` is literal true. Off/missing/nonboolean flags
produce `capture_disabled` and no test-run POST. Unavailable config/attribution,
restricted policy or telemetry/storage failures produce sanitized diagnostics;
they do not change the requested command or its exit code. Process registration
is independently attempted for every actual owned child, even with capture
off. It records only a generic command description, never arguments or env.

When reporting is available, every invocation enrolls one parent and one child
for each invoked unit. Parent/child IDs are independent UUIDs under the server's
verified project/session ownership. All enrollment, submit and finalize HTTP
requests originate from the same launcher process, including its worker thread.
The P4b socket PID/creation-time binding therefore holds across requests. The
project argument selects a scope; it cannot assert ownership. The server's
recorded cwd remains its verified session/worktree cwd; per-unit execution cwd
is not accepted as an ownership or enrollment override.

Both parent and children use **script units**. The parent has the explicit
manifest total; each child represents one invoked command, total 1. No pytest
assertion counts, parsed failure names, skip counts or ETA are claimed. The child
records `framework_reporter_unavailable`, capture partial, until the separately
built adapters exist. Generic labels (`Command 1`, etc.) keep raw arguments,
environment and runner output out of telemetry. Parent denominators count only
commands, never child assertions. Fail-fast's unexecuted count is explicit in
the parent reporter finish; P4b's exact finalize schema cannot carry `not_run`,
so the terminal snapshot's `not_run` remains null. Its selected minus completed
script count still gives the unexecuted count without inventing skips.

Each child is registered immediately after Popen, stdout/stderr are forwarded
as separate binary streams, and actual `wait()` completes before any finalization
is queued. On normal exit the child's numeric code is returned unchanged. On
POSIX, negative signal returns are propagated by terminating the launcher with
the same signal after capture closes (rather than truncating `sys.exit(-SIG)`).
On Windows, Popen's actual Windows return code remains the numeric result;
unsigned DWORD statuses above `2**31-1` use `ExitProcess` after output/capture
closes so Python's `SystemExit` conversion cannot truncate them. Platform codes
are not guessed to be POSIX signals. SIGINT or
SIGTERM received by the launcher requests cancellation. The launcher only
kills the direct child PID it started; this phase adds no tree supervisor.
Shell-spawned/detached descendants are not adopted or guaranteed terminated.
Pipe drainage is bounded to one second after direct-child exit, with an explicit
`output_drain_incomplete` diagnostic if descendants keep descriptors open or
an output sink fails. A kill failure logs a code and waits for actual exit;
no early success is manufactured.

Child unit outcomes derive from actual wait results. Missing/unstartable
executables return 127/126 with diagnostics. Parent results derive from the
specified aggregation rule. Only the launcher queues `/finalize` after these
observations; reporters never do. Finalize capabilities stay private to the
launcher's memory, distinct from producer receipts. No capabilities or receipts
are passed to child environments, argv, URLs, output or persistent files.

## Bounds and failure behavior

Telemetry uses a daemon thread in the launcher and a bounded 256-message queue;
events are buffered at up to 64 records per run/HTTP batch. Output only sets a
coalesced heartbeat flag; raw stdout/stderr never enters the queue or run store.
There is no output log/sidecar created by this phase. Loopback uses the explicit
port and `127.0.0.1`, bypasses proxy environment settings, refuses redirects,
limits responses to 1 MiB and uses a two-second socket timeout. URLs never carry
tokens. Producer receipts refresh before expiry with a new producer generation;
finalize uses the original separate launcher capability. Finished receipts are
revoked; the service's existing expiry also remains in force.

The command does not wait for enrollment or reporter submits. Child registration
is a separate bounded request immediately after spawn. Closing waits at most
30 seconds for queued telemetry, including finalization. Actual Windows socket
attribution performs multiple OS table lookups per request; the budget is not
the job-capture module's separate two-second callback budget. Queue overflow,
submit failure and unfinished final drain are visibly incomplete; they never
substitute another command, rerun a suite, or replace a real nonzero exit with
zero. A finalized exit may coexist with incomplete metrics; a failed finalization
leaves the canonical run nonterminal and emits a local gap diagnostic. No retry,
recovery or restart-survival claim is made. Existing P4b capabilities die on a
server restart. Reporter receipts cannot mint process evidence.

## Guidance and delivery limits

`mc/long_job_guidance.py` owns the optional instruction text; context wiring
passes the live flag and installation root. Off preserves the existing note.
Enabled guidance supplies a JSON argv prefix, original-selection requirements,
explicit manifest/shell syntax and the existing long-job route. Missing launcher
assets are explicitly unavailable. Incognito sessions receive no guidance;
the server still refuses sandbox/incognito enrollment. The wrapper may execute
locally in those modes subject to the caller's existing policy, with reporting
refused. This does not deliver restricted-mode coverage.

Source checkout only at this phase. Bundled launcher/reporter distribution and
clean-install validation belong to Phase 6's asset packaging unit; no frozen
macOS/Windows/Linux artifact or four-engine live adoption claim is made here.
The guidance file existence check prevents a missing bundled entry from being
advertised as available. No UI, USER_GUIDE or README feature claim is added.

## Verification

Tests use actual unrelated project directories and real child processes, an
isolated Flask HTTP app on an ephemeral port and the production socket/ancestry
resolver with the pytest PID as its controlled managed session root. No :5199
config change or second Clayrune server. They check binary streams, exact argv,
cwd/env, exits, hierarchy, stop/continue, failure modes, timeout, policy refusal
and private capability use. POSIX signal/cancellation cases are separately
skipped on Windows; this host does not establish their live behavior.

Combined regression command:

```text
python -m pytest tests/test_test_run_routes.py tests/test_test_run_ingest.py tests/test_test_run_store.py tests/test_test_run_capture.py tests/test_agent_jobs.py tests/test_background_job_endpoint.py tests/test_dev_harness_runner.py tests/test_atomic_json.py tests/test_caller_attribution.py tests/test_settings_routes.py tests/test_human_proof_guard.py tests/test_test_run_finalize.py tests/test_test_run_launcher.py tests/test_test_run_transport.py tests/test_long_job_guidance.py tests/test_context_profile_by_model_tier.py
```

Type and whitespace commands:

```text
pyright mc/long_job_guidance.py tools/test_run_launcher.py tools/test_run_transport.py tools/test-run.py
pyright mc/blueprints/agent_routes.py mc/long_job_guidance.py tools/test_run_launcher.py tools/test_run_transport.py tools/test-run.py
git diff --check
```

The first pyright command passes with zero errors, warnings or informations.
Including the existing wiring monolith yields 15 existing errors, exit 1.
Compared the baseline `cff1230f:mc/blueprints/agent_routes.py` with the candidate
using `pyright --outputjson <file>` for each; all 15 diagnostics (message, rule,
column and line after the two inserted lines) are identical. No added error
and no unrelated type repairs. The tool's available-version notice is non-failing.

The earlier 15-file combined run (without `tests/test_test_run_transport.py` or
the two late unsigned Windows exit cases) passed **713, skipped 3**, with one
existing SyntaxWarning, in 188.49s, exit 0. The late cases then passed **8** in
14.32s, exit 0:

```text
python -m pytest tests/test_test_run_launcher.py::test_windows_unsigned_exit_is_not_truncated_by_system_exit tests/test_test_run_transport.py
```

The 16-file command passed **721, skipped 3**, in 179.81s, exit 0. A later
quoted-path cmd regression caught Python argv backslash escaping being passed
literally to cmd; the raw cmd construction above fixes that path. Focused cmd,
PowerShell and environment checks include quoted executable/script paths,
literal metacharacters and case-insensitive Windows environment overlays.
The final 16-file combined rerun after these Windows corrections passed
**722, skipped 3, in 184.29s**, exit 0. No warning was emitted by that run.
The skips are two POSIX signal cases and unavailable `sh`; actual Windows
cancellation, timeout, unsigned codes, PowerShell/cmd and quoted cmd paths pass.
The last focused correction check passed **4, skipped 1, in 26.55s**, exit 0:

```text
python -m pytest tests/test_test_run_launcher.py::test_manifest_scoped_environment_removal_and_cwd tests/test_test_run_launcher.py::test_cmd_preserves_quoted_executable_script_paths_and_metacharacters tests/test_test_run_launcher.py::test_explicit_shell_preserves_command_and_exit
```

Each pytest command was invoked through a gitignored foreground wrapper
`python _scratch/run_phase5_checks.py <the same pytest arguments>` which starts
`[sys.executable, '-m', 'pytest', ...]`, immediately registers that PID with the
existing Process Manager, waits and returns its exit. The final combined PID
was 42548. No shell backgrounding or unregistered long-running command.
Working/staged whitespace checks pass. Branch-only candidate; no merge, push,
restart or config write. Main-checkout journal carries the recovery/handoff.
