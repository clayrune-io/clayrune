# MC-1148 Phase 7: standalone Node script progress

Branch candidate under the existing default-off flag. Q2 is decided by Dave:
count scripts, never infer assertions. No existing smoke helper is changed.
The launcher/transport/finalization contract in `TEST_RUN_LAUNCHER.md` still
applies; pytest children retain their separate Phase 6 adapter.

## Selection and units

`tools/test_run_node.py` recognizes direct `node script.mjs`, `node script.cjs`
and `node script.js` argv (including an absolute node/node.exe executable).
The selected runtime must support `--import` (Node 18.18+/19+); only Node
24.11.0 is exercised here. Native `--test`, options before the script, npm,
shells, aliases and wrappers keep the existing opaque-command fallback.
There is no preliminary script run, command expansion, dependency install,
interpreter replacement or script/helper migration.

One command is one `unit=script`, `runner=node-script`, labelled `Smoke script`.
Its assertion total is unknown. Console `ALL PASS`/`FAIL`, fixture text,
worker/fork exits and nested suites do not become assertion counts or extra
script units. A parent manifest counts ordered commands, separately from each
child. Existing explicit schema-1 manifests provide script selection/order and
stop/continue behavior:

```json
{"schema":1,"stop_on_failure":true,"units":[
  {"argv":["node","first.mjs"]},
  {"argv":["node","second.mjs"]},
  {"argv":["node","third.mjs"]}
]}
```

A second-script failure returns that actual exit and leaves the third script
not-run, with zero skips. `stop_on_failure:false` executes all selected scripts
and returns the first nonzero exit. Timeout/cancellation always stop the list.
The existing P4b terminal snapshot has `not_run=null`; the parent reporter
finish event contains the explicit remaining count, and selected minus completed
also expresses it. No skip is manufactured for unexecuted scripts. Missing or
wrong launcher manifests emit `invalid_invocation_or_manifest`, return 2 and
execute nothing. A direct script invocation needs no composite manifest: its
known total is one script.

## Preload and authority

The adapter waits at most eight seconds for scoped enrollment before allocating
an exclusive temporary sidecar, reusing `tools/test_run_transport.py` without
modification. Off/refused/timed-out enrollment or a missing reporter executes
the original command once with a visible capture diagnostic. Script path
resolution is deferred until enrollment; an observation-only resolution error
also runs the original command once. The adapter adds
`--import <installation-derived-file-URL>` to the selected Node executable;
original script argv, cwd, inherited environment and timeout remain selected.
`process.execArgv` necessarily includes the preload. No option is injected into
an opaque command. Unavailable old runtimes are outside this preload contract;
no fallback rerun occurs after execution begins.

`data/test_reporters/smoke-progress.mjs` imports Node built-ins only. The target
receives a sidecar path and bounded JSON metadata (`schema`, resolved selected
script, script basename unit ID) in `CLAYRUNE_SMOKE_PROGRESS_PATH` and
`CLAYRUNE_SMOKE_PROGRESS_MANIFEST`. These are local capture metadata, never
run IDs, HTTP receipts or finalize capabilities. A PID marker prevents inherited
fork preloads writing the parent's file; worker preloads also check main-thread
identity. A nested independently enrolled launcher resets its marker. No new
filesystem/security sandbox is claimed.

The preload validates metadata against the selected script, opens the existing
sidecar, emits start/one-script collection/unit start and an informational exit
checkpoint. Missing/wrong metadata prints a code-only diagnostic and runs the
script normally; the adapter also detects the missing finish. The optional
exported `checkpoint()` emits liveness only, without names, assertion counts or
outcomes. Existing scripts do not need to call it. No exception/rejection handler
changes Node's error behavior. Failure text remains solely in ordinary stderr.

Only actual child `wait()` supplies the completed script's pass/fail report and
the existing launcher queues finalization. A script throw is a failed script.
A later exit listener can change a successful reporter checkpoint to a failed
process; the waited exit wins. Timeout, abrupt kill or corrupt/missing sidecar
still preserves the observed script result, visibly marked capture partial.
Reporter checkpoints cannot finalize a run or restore earlier capture warnings.

## Bounds and distribution

Records are sequential schema-1 JSONL, limited to 16 KiB per record and 64 KiB
per sidecar. The protocol admits only fixed lifecycle fields and optional
liveness checkpoints. Writes are synchronous bounded filesystem operations,
with no network in the child. Overflow/write failures warn without changing
script exit; the adapter reports incomplete capture. Its reader polls at 20 ms
and reads at most 64 KiB per poll into the existing bounded telemetry queue.
After wait it stops the reader (one-second join), drains bounded remaining
records before finalization and removes the temporary directory. Cleanup or
incomplete drain failures warn by code. Launcher hard-kill retention/recovery
is unchanged; a hard kill can leave local sidecars.

macOS spec datas include both the reporter asset and Node adapter source.
The existing pytest copied-layout fixture now also includes the launcher's
new sibling import. The Node packaging fixture copies exactly the spec assets
into an installation path with spaces and runs a separate dependency-free
target project. Windows/Linux installers clone tracked application source,
as documented in `TEST_RUN_PYTEST.md`. This is source/spec/copied-layout
verification on Windows, not a frozen macOS artifact or fresh VM install.

## Verification

Focused tests: `tests/test_smoke_progress.py`, `tests/test_test_run_node.py`.
They run real pass/fail/throw/rejection/late-exit scripts, fail-fast/continue
manifests, worker/fork inheritance, malformed/missing metadata, spool limits,
exact arguments/cwd/environment/binary streams, timeout, registration refusal,
policy refusal and external copied installation. The HTTP fixture is an
isolated ephemeral Flask app using real socket/ancestry attribution, never
another production-port server or live configuration write.

The combined required regression command is:

```text
python -m pytest tests/test_test_run_capture.py tests/test_test_run_finalize.py tests/test_test_run_ingest.py tests/test_test_run_launcher.py tests/test_test_run_node.py tests/test_test_run_pytest.py tests/test_test_run_routes.py tests/test_test_run_store.py tests/test_test_run_transport.py tests/test_pytest_progress.py tests/test_smoke_progress.py
```

Clayrune job `025d04cfacd04daab115132afa44292b`, registered owned PID 3748,
ran that command in this worktree: **523 passed, 3 skipped in 293.48 seconds**,
rc=0, no warnings. Skips: two POSIX signal cases on Windows and unavailable sh.
This run began before the final observation-path correction; the final focused
Node rerun below is the verification of that change and its new regressions.

The first focused run passed 38 checks (rc=0; double quiet suppressed the
numeric timing summary). The final focused command, after all changes, is:

```text
python _scratch/run_phase7_checks.py tests/test_smoke_progress.py tests/test_test_run_node.py
```

The gitignored foreground wrapper starts the same `python -m pytest` argv,
immediately registers its owned PID with Process Manager and waits for exit.
Final focused result: **46 passed in 120.52 seconds**, rc=0, registered pytest
PID 3632. No skips or warnings. This covers the final path-resolution fallback,
all Node adapter/reporting code, real Node subprocesses and copied installation.
Type/syntax/whitespace commands:

```text
pyright tools/test_run_node.py tools/test_run_launcher.py tests/test_smoke_progress.py tests/test_test_run_node.py
node --check data/test_reporters/smoke-progress.mjs
git diff --check
```

All three pass, rc=0; pyright reports zero errors/warnings/informations, with
only a non-failing available-version notice. Existing `tools/smoke/*.mjs`
helpers remain unmodified and uninstrumented. No UI/static files change;
browser smokes, USER_GUIDE and README updates are inapplicable to this dark
capture module. Four-engine live adoption, restricted-mode capture, restart
survival, assertion names/skips, native framework adapters and multi-version
Node compatibility are not claimed.

Protocol references: [Node CLI preload contract](https://nodejs.org/download/release/v24.11.0/docs/api/cli.html#--importmodule),
[Node exit event](https://nodejs.org/docs/latest-v24.x/api/process.html#event-exit).
