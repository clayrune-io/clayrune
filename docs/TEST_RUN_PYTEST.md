# MC-1148 Phase 6: standalone pytest progress

Branch candidate, dark under the existing default-off flag. No UI, config
change, merge, push, restart, dependency installation in a user project, or
four-engine adoption claim. Design: `TEST_RUN_PROGRESS_DESIGN.md` sections
4/5/7/8. Existing launch contract: `TEST_RUN_LAUNCHER.md`.

## Assets and loading

`data/test_reporters/clayrune_pytest_progress.py` is a stdlib-only pytest plugin.
It never imports `mc`, performs HTTP, receives capabilities, finalizes a run,
launches a process or changes test selection. `tools/test_run_pytest.py` owns
the launcher adapter. Existing launcher edits only construct/delegate to that
adapter. The transport adds a bounded enrollment barrier.

Direct `pytest`/`py.test` executable argv and `<selected-interpreter> -m pytest`
argv are recognized. The adapter inserts `-p clayrune_pytest_progress` after the
runner prefix and prepends the installation-derived `data/test_reporters`
directory to the child's existing `PYTHONPATH`. The executable, arguments,
cwd, timeout and all other environment entries retain their selection. No
preliminary collection, target conftest/config edits, dependency install,
rerun or interpreter substitution. Interpreter switches preceding `-m`
(including `-I`/`-E`), shells, wrappers, aliases and opaque scripts retain P5
script-unit fallback. Passive recognition remains Phase 8.

Before allocating a sidecar, the adapter waits for successful scoped child
enrollment, at most **8 seconds**. Config, parent enrollment and child enrollment
each have P5's two-second socket budget. This startup wait applies only to
recognized pytest commands; generic commands retain P5 behavior. Off, refused,
restricted, unavailable or timed-out enrollment runs the original command
without injecting the reporter or creating a test-name sidecar. The diagnostic
is explicit. Missing reporter assets and sidecar preparation failures also
run original argv once. Execution is never retried after it has begun.

The parent still counts commands (`unit=script`); each recognized pytest child
counts tests (`unit=test`). Child test results never enter the parent script
denominator. A fallback child with a recognized pytest command has unknown
test counts, rather than pretending one command equals one test.

## Serial accounting

The plugin uses the actual pytest run's selected `session.items` after
collection/deselection. Before collection the total is null. Deselection and
collection errors are absolute separate counters. Collection skips produce a
code-only diagnostic and never become completed/skipped test units. The
current schema has no collection-skip counter.

Each setup/call/teardown report carries node ID, attempt, phase, outcome,
per-phase revision and final flag. Failing phases appear immediately; a test
is completed only on its teardown report. Setup/teardown failures are errors,
and therefore a passed call with broken teardown is an error. Separate
pass/fail/skip/error/xfail/xpass counts follow actual pytest outcomes. Strict
xpass remains a failure. Exception text, tracebacks, skip/xfail reasons, stdout,
env and credentials do not enter the sidecar. Oversized node IDs use a stable
SHA-256 alias plus an explicit missing-detail diagnostic, never silent truncation.

If the actual session item count changes, a collection revision changes the
denominator. No fake monotonic percentage or ETA. Runtime item removal is
tested; arbitrary third-party dynamic collection behavior is not guaranteed.
Collection-only mode has a diagnostic and zero executed tests. Repeated node
starts identify a new attempt and degrade unproved retry accounting.

`pytest_sessionfinish` supplies a reporter verdict, exit code and known not-run
count. Interrupted, internal-error, usage-error and no-tests statuses have
explicit reporter states/codes; if finish is missing, the adapter adds a
code-only exit diagnostic after the actual wait. Informational finishes never
clear an earlier capture warning. Actual child `wait()` remains the only finalization
source; late cleanup can turn reporter success into process failure. P4b's
unchanged finalize schema maps nonzero process exits to terminal `failed`
unless the launcher itself observed timeout/cancellation. Thus pytest code 5
has reporter state `no_tests`, reporter/actual exit 5
and terminal `failed`; code 2 similarly retains its interrupted reporter reason.
Terminal snapshot `not_run` remains null under P4b; the reporter event contains
it and selected minus completed expresses the known remaining count. No new
process-evidence authority was added to solve those existing schema limits.

## Unsupported aggregation and bounds

xdist and retry aggregation are **not measured**. Detected distributed/retry
configuration makes total unknown and withholds final per-test counters;
known failure phases remain visible with capture partial. xdist workers never
open the controller's sidecar, even though they inherit its environment. No
worker totals are summed and no worker-spool aggregator is claimed. Controller
report detection is also defensive when worker reports arrive unexpectedly.
Repeated starts/rerun reports degrade capture. Neither optional dependency is
installed here; simulated hook contracts test refusal of invented metrics,
not actual distributed runs, worker crashes or rerun-plugin integration.

The local launcher creates a fresh exclusive sidecar under an OS temporary
directory after enrollment. The target receives only its path in
`CLAYRUNE_PYTEST_PROGRESS_PATH`, never run IDs, producer receipts or finalize
tokens. One controller writes sequential JSONL, schema/sequence validated by
the adapter; the existing server validates canonical event payloads. Records
are at most 16 KiB; the plugin stops at 64 MiB, warns, and does not change pytest
exit. Writes flush per event. A launcher thread reads at most 64 KiB per poll
(20 ms); events use P5's bounded telemetry queue/batches. Overflow, malformed
records, incomplete final records, missing finish and I/O failures remain
visible incomplete capture. No lossless high-volume throughput claim.

After actual wait, the adapter stops its reader, drains remaining bounded
blocks, then queues finalization. Temporary files are removed normally;
cleanup failure logs a sanitized code. A hard-killed launcher can leave a
sidecar; retention/restart adoption is not added here. Restricted/incognito
capture stays deferred under Dave's Q4 decision. No claim that a same-user
test process is a filesystem security sandbox.

The frozen schema does not carry per-test duration, collection-skip totals or
authoritative final counter snapshots. ETA stays unmeasured. Store replay and
whole-envelope persistence costs remain P1/P3's documented limitation.

## Distribution checks

`installer/build-macos.spec` explicitly includes the reporter source and all
four launcher sources: `test-run.py`, `test_run_launcher.py`,
`test_run_transport.py`, `test_run_pytest.py`. Source files are necessary
because they execute in the target interpreter outside PyInstaller's import
graph. The packaging test reads actual spec datas, copies exactly these
assets into an isolated installation path containing spaces, then launches
pytest from a separate target directory through that copied entry.

Windows `installer/install.ps1` obtains application source through `git clone`
(with existing-checkout update/recovery paths); the Windows exe fetches the
bootstrap using the commit URL/hash pinned by `installer/win-exe/build.ps1`,
rather than distributing a filtered application data tree. Linux
`installer/install.sh` likewise clones the source. The new tracked
`data/test_reporters` source is included without an installer allowlist change.
This is source/manifest/layout verification on Windows, **not** an actual
frozen macOS build or fresh Windows/Linux VM install. Those release gates
remain unverified; no release artifact was built or published.

## Verification commands and evidence

All pytest commands run via `_scratch/run_phase6_checks.py`, a gitignored
foreground wrapper that launches the same `python -m pytest` argv, immediately
registers its owned PID with Process Manager, waits and returns its rc. No
shell backgrounding. Focused command:

```text
python _scratch/run_phase6_checks.py tests/test_pytest_progress.py tests/test_test_run_pytest.py
```

Fixtures exercise actual pass/fail/skip/setup-error/teardown-error/xfail/xpass/
strict-xpass, deselection, collection skips/errors, no tests, maxfail, changing
totals, interruption, write failure, long names and late cleanup failure.
Launcher fixtures use P5's isolated ephemeral HTTP app and real OS socket/
ancestry resolver, never a second :5199 server or live config change. Live
failure capture holds teardown open until a failing name appears in the store
with zero completed tests and a still-running process. A separate throwaway
venv copies only already installed pytest dependency distributions; its real
interpreter asserts a different prefix and that `mc` cannot be imported.

Type check:

```text
pyright data/test_reporters/clayrune_pytest_progress.py tools/test_run_pytest.py tools/test_run_launcher.py tools/test_run_transport.py
git diff --check
```

Final focused command above passed **38** in **85.80 seconds**, rc=0, under
registered pytest PID 60216. It includes the final diagnostic-health correction
and its regression. The extra interpreter uses the same installed pytest
version, not a second version-compatibility target. Type check and whitespace
check passed with rc=0, zero type errors/warnings/informations; pyright emitted
only a non-failing available-version notice.

Combined regression command (started before that final diagnostic correction;
the focused rerun above covers the final changed reporter/adapter):

```text
python _scratch/run_phase6_checks.py tests/test_pytest_progress.py tests/test_test_run_pytest.py tests/test_test_run_launcher.py tests/test_test_run_transport.py tests/test_test_run_store.py tests/test_test_run_ingest.py tests/test_test_run_routes.py tests/test_test_run_finalize.py tests/test_test_run_capture.py tests/test_agent_jobs.py tests/test_background_job_endpoint.py tests/test_dev_harness_runner.py tests/test_atomic_json.py tests/test_caller_attribution.py tests/test_floor_routes.py tests/test_clayrune_mcp_tools.py tests/test_long_job_guidance.py
```

Combined result: **741 passed, 3 skipped, 1 warning, in 302.87 seconds**, rc=0,
registered pytest PID 63072. The warning is an existing invalid escape in
`tests/test_agent_routes.py:2361`, unchanged here. The final reporter diagnostic
correction and added regression were verified in the separate 38-pass focused
run above; the broad run is not claimed as a post-correction rerun. Earlier
unchanged launcher/transport plus corrected refusal fixtures passed 70, skipped
3, in 170.95s, rc=0. The three skips are POSIX signal behavior on Windows (2)
and unavailable sh (1). No UI/USER_GUIDE/README/rules change is appropriate for
the dark phase; no static changes, so browser smokes are inapplicable.

Hook references: [pytest API](https://docs.pytest.org/en/stable/reference/reference.html),
[xdist controller/worker lifecycle](https://github.com/pytest-dev/pytest-xdist/blob/master/docs/how-it-works.rst).
Only pytest 9.1.1 is exercised here; no multi-version compatibility claim.
