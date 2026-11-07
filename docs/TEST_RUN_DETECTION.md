# MC-1148 Phase 8: passive generic test-run fallback

Branch candidate based on `98fb8664`, default off. Dave's Phase 8 decision
in the main project journal selects delivery by vendor tool-call identity,
with wrapper/job exclusion and no cross-component launch correlation.
The schema-1 store and earlier capture/ingest/launcher/reporters are unchanged.
No UI, live configuration, restart, merge or deployment is part of this phase.

## Recognition and identity

`mc/test_run_detection.py` observes raw records from both Claude readers,
the shared Mode A callback composition and Gemini's bespoke reader. The
monoliths contain imports and one-line hooks only. The shared composition
passes the untouched line/sequence/session arguments to the previous callback;
its own errors cannot set agent capture failure. Existing callback failures
retain their existing behavior. When disabled, or for an unsupported provider,
composition returns the original callback unchanged. Enabling during an
already-running shared Mode A reader takes effect on its next reader/turn.

Every assistant/user content block is scanned, including mixed text/tool
records. Qwen user results are observed before its UI parser suppresses them.
Codex supports current command_execution start/update/completed items and old
message content blocks/function-call declarations with separate output items.
Gemini uses tool_name/parameters/tool_id and correlated tool_result records.
Malformed JSON/envelopes are ignored; malformed blocks do not hide valid peers.
Model prose, tool-output text and the provider process's exit are never evidence
of a test result.

Recognized direct commands: pytest/py.test, python/py `-m pytest`, Node `--test`,
Node JS/MJS/CJS paths with test/tests/smoke components, npm/pnpm/yarn test or
run test[:name], and npx playwright test. Quoted executable paths and explicit
argv arrays are supported. Commands are limited to 32 Ki characters for
classification. Shell composition, redirection, substitutions, shell functions,
aliases and arbitrary script bodies are not evaluated. Recognition of npm test
does not prove that its script executes tests. No configurable script registry
or framework adapter is added here.

Commands invoking `tools/test-run.py` and job handoffs are excluded. Job tool
names are outside the shell-tool allowlist; HTTP/job handoff commands are not
direct test candidates. Excluded calls never join launcher/job records. A
hidden wrapper behind an alias/script can still produce a visible duplicate;
Dave explicitly accepted that boundary rather than command-text matching.
There is no command wrapping, rewriting, rerun, shell execution or new child
process in the detector.

Identity is project + managed session + provider + a hash of provider-process
nonce and vendor tool-call ID. The nonce is private process-object metadata,
not a child PID, environment value or OS process claim. Identical commands in
different calls, sessions or provider processes are separate runs; repeated
start/update/completed observations of the same call are deduplicated. This is
process-local deduplication, with no restart adoption or durable identity index.

## Generic records and verdict authority

The worker enrolls one record through TestRunStore with source=generic-fallback,
runner=unknown, unit=script and a fixed Test command label. The hashed identity
is target_id. Actual child process identity, cwd and turn identity remain null
when the reader cannot prove them. Commands, arguments, output, exception
messages, environment and vendor-selected ownership fields are never persisted.

Only explicit integer exit_code fields on a completed tool result/item, or its
structured metadata, establish a process verdict: zero passed, nonzero failed.
Boolean/string codes, status success/error, is_error and output resembling an
exit or summary do not establish one. Missing exit evidence maps to the frozen
store's lost state with exit_code=null and exit_code_unavailable: unknown,
never passed. A declaration alone remains running until a result is observed;
silence/EOF is not promoted to a result. There is no inferred cancellation or
timeout classification. Shell/npm exits describe that command, not every
nested suite's outcome.

Generic capture is explicitly partial, marked before any later process result.
Tool error indication supplies only tool_error_reported, without invented test
failure names. There are no collection/unit-report events, inferred counts,
percentage or ETA. The frozen schema's zero counters mean no unit reports were
received; they are not a measured zero-test suite. selected_total, percent and
eta_seconds stay null. A later renderer must honor the generic source and
unknown total, rather than present those initial counters as measured results.

An output-presence observation records a heartbeat at server receipt time,
coalesced to at most one per 0.5 seconds plus completion. This is not an
incremental stdout reader or a claim about actual byte-production timestamps.
Claude/Qwen/Gemini may expose output only with a result; raw diagnostics remain
in their existing logs, outside telemetry.

## Isolation and bounds

Only literal test_run_progress_enabled=true enables observation. Off/unset/
nonboolean values cause no observer worker, store construction or telemetry
write. Wiring retains configuration paths without opening storage. Both reader
and worker check restricted session policy; the worker revalidates the exact
session object in the managed registry, project binding and incognito-project
policy before writing. Incognito and Codex unattended sandbox coverage remains
deferred under Q4. Untrusted envelopes cannot set project/session ownership.

Readers parse/classify, discard commands and enqueue compact lifecycle records
without waiting for disk. A single daemon worker owns store writes. The queue
holds at most 256 records; the identity table holds at most 4096 calls.
When a new candidate needs a slot, the worker evicts the oldest terminal run
by terminal-transition order; repeated results do not refresh that order.
Active runs are never evicted. A table full of active runs logs incomplete
progress and refuses new identities; a full observation queue still drops
incoming observations with a log.

Each evicted identity leaves only a SHA-256 hash of its full scoped key in a
separate FIFO of at most 65536 tombstones. Candidates with retained hashes
are ignored before eviction or storage, so replay cannot recreate those
completed calls or consume an active run's slot. Tombstone hits do not refresh
FIFO order. Once the oldest tombstone ages out, that identity can be enrolled
again: replay protection is bounded and process-local, not permanent.
Terminal-order and tombstone structures are owned by the same single worker
as the run table; flag-off returns before reading or mutating any of them.
All generic runs are already partial. Queue loss can leave a run running or
undelivered; it cannot manufacture success. Store/decoder failures log exception
class only, never arbitrary message contents, and cannot change the agent's
capture status or exit verdict. Rewiring drains for at most two seconds and
logs an incomplete drain; hard shutdown/restart recovery is outside this phase.
No large-suite throughput, delivery-after-shutdown or retention guarantee is
claimed. Storage remains under data_root/test_runs, outside data/projects.

## Verification

The new suite uses isolated store roots, fake vendor transports and the actual
five reader paths (Claude A/B, shared Codex/Qwen and bespoke Gemini). It checks
identity collisions, repeated/completed-only observations, both Codex schemas,
all blocks, Qwen suppression, missing/nonnumeric exits, output/prose spoofing,
wrapper/job exclusion, policy refusal, queue/identity bounds, storage/observer
faults and unchanged existing callback/agent behavior.

```text
python _scratch/run_phase8_checks.py tests/test_test_run_detection.py
pyright mc/test_run_detection.py tests/test_test_run_detection.py
git diff --check
```

The scratch foreground wrapper invokes python -m pytest, immediately registers
the owned pytest PID with Process Manager and waits. The required broad run is
owned by the Clayrune session-job route, with its returned PID also registered:

```text
python _scratch/run_phase8_checks.py tests/test_test_run_capture.py tests/test_test_run_detection.py tests/test_test_run_finalize.py tests/test_test_run_ingest.py tests/test_test_run_launcher.py tests/test_test_run_node.py tests/test_test_run_pytest.py tests/test_test_run_routes.py tests/test_test_run_store.py tests/test_test_run_transport.py tests/test_agent_jobs.py tests/test_stream_reader_malformed.py tests/test_claude_runtime.py tests/test_provider_runtimes.py tests/test_mode_a_tool_call_parity.py tests/test_gemini_delta_reply_truncation.py tests/test_codex_stdin_banner_suppression.py tests/test_codex_run_error.py
```

The broad command passed **1017**, skipped **4**, in **305.18 seconds**,
rc=0. Its imported code predates the final disabled-callback, malformed-name,
startup-partial, output-time and call_id refinements. The final focused/reader
command below ran after all code and test changes:

```text
python _scratch/run_phase8_checks.py tests/test_test_run_detection.py tests/test_stream_reader_malformed.py tests/test_claude_runtime.py tests/test_provider_runtimes.py tests/test_mode_a_tool_call_parity.py tests/test_gemini_delta_reply_truncation.py tests/test_codex_stdin_banner_suppression.py tests/test_codex_run_error.py
```

That final command passed **515**, skipped **1**, in **11.57 seconds**, rc=0.
The broad skips are two POSIX signal cases on Windows, an unavailable shell,
and the opt-in real Codex session; the final reader run skips only that live
session. No authentication or live vendor run was attempted. New module and
test-suite pyright: **0 errors, 0 warnings, 0 informations**, rc=0 (only a
non-failing available-version notice). Working/staged whitespace checks pass.
The first 72-pass focused run and intermediate 78-pass run are recorded in the
main journal with fixture corrections and later review refinements.

Four-engine live adoption,
frozen artifacts, clean installs, restricted capture, UI and restart survival
remain unverified. No static edits: browser smokes are inapplicable. No new
dependencies, user-facing controls or binding rules: USER_GUIDE, README,
CLAUDE/AGENTS and agent-rule changes are inapplicable.

## 2026-11-06 identity-capacity follow-up verification

Six additional regression cases cover slot reuse and completed-call replay,
terminal-transition ordering (including repeated results), all-active refusal,
bounded hash-only FIFO tombstones without hit refresh, separate session scope,
and disabled reader/queued-worker behavior with populated retention state.

Commands run from the branch worktree:

```text
./.venv/Scripts/python.exe _scratch/run_eviction_checks.py -m pytest tests/test_test_run_detection.py
./.venv/Scripts/python.exe _scratch/run_eviction_checks.py all-test-runs
C:/Users/levir/AppData/Local/Programs/Python/Python312/Scripts/pyright.exe mc/test_run_detection.py tests/test_test_run_detection.py
git diff --check
```

The scratch runner starts the shared-venv Python with the supplied arguments,
immediately registers its child PID, and waits for its exit code. Its
`all-test-runs` selector expands every `tests/test_test_run_*.py` in sorted
order (12 files) and invokes `-m pytest` on that full set. Focused result:
**87 passed**, rc=0. Full test-run result: **660 passed, 3 skipped in 187.17s**,
rc=0. Skips are two POSIX signal cases on Windows and an unavailable requested
shell. Pyright: **0 errors, 0 warnings, 0 informations**, rc=0; the shared
venv lacks pyright, so the existing Python 3.12 installation supplied it.
Whitespace check: rc=0. No dependency installation, live-config change,
merge, push, restart, store/anchor/route edit or deployed-server verification.
