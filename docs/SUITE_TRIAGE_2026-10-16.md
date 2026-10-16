# Suite triage, 2026-10-16

Base: `da55b9d149651d53ee9f0f2bf5c79c60e575a096`.
Branch: `clayrune/agent/a70dfeffd7d7`. Test/harness changes only; no product
edits, merge, push, or restart. **Not release-cleared.**

## Result and limits

The complete requested run, with only the scheduler Pool race deselected,
finished in **2,106.46 seconds: 16,310 passed, 34 failed, 51 skipped,
1 xfailed, 1 deselected; zero errors**. It used Python 3.14.0 / pytest 9.1.1
on Windows, the same venv as the original run, with a 2,400-second external
timeout. XML counts xfail as skipped: 16,396 executed test cases, 52 skipped.
The new GC regression test was added after this run collected its tests.

Every failure in that completed run is accounted for:

| Class | Failed tests | Root causes | Disposition |
|---|---:|---:|---|
| A: product behavior / existing structural contract | 4 | 4 | Reported below; untouched |
| B: stale test assumptions | 5 | 3 | Corrected fixtures/gold answer |
| C: test environment, ordering, synchronization | 25 | 3 | Shell selection, mail import path, audit completion |
| Unclassified in this completed run | 0 | 0 | |

Two additional stale rollover tests failed in the original log and isolated
probe, and were already repaired before this complete run collected them.
The independent concurrent-root GC defect was also demonstrated and repaired.
Do not add overlapping targeted pass counts together.

The original log is weaker evidence: **122 F + 233 E markers across 46
files**, no traceback section or final summary. Its collection was 16,389,
whereas this base has 16,397 tests; it also includes an uncommitted test absent
from the base. Of its 355 failure/error markers, **2 map to A, 7 to B, 23 to C,
2 to out-of-snapshot tests, and 321 remain unreproduced/unattributed**.
Those 321 are not silently called environment failures: the missing original
tracebacks prevent proving their cause. The file-by-file reconciliation below
includes skips explicitly.

## Scheduler Pool hang: reproduction attempts, not a claimed cure

Original evidence: the run stopped at 85% in
`tests/test_scheduler_slot_claim.py::test_two_processes_racing_one_slot_yield_exactly_one_dispatch`.
The supplied py-spy stack put the main thread in `_winapi.CreateProcess`,
inside `multiprocessing.popen_spawn_win32.Popen.__init__`, constructing
`Pool(4)`. This precedes the worker's scheduler claim call.

Bounded reproductions:

| Scenario | Result |
|---|---|
| Race alone, 90-second process timeout | 1 passed, 1.67s |
| Full suite collection, only race selected | 1 passed / 16,397 deselected, 4.87s |
| LOC stdin-rewrite module collected with race and spawn probes | 3 passed / 52 deselected, 2.30s |
| Pipe tests before fence/spawn/race probes | 60 passed, 5.12s |
| Managed Git Bash, original flags, full collection, race selected | 1 passed / 16,397 deselected, 5.42s |
| Windows `DETACHED_PROCESS`, full collection, race selected | 1 passed / 16,397 deselected, 5.11s |

Recovered original command (inside the main checkout):

```bash
PYTHONUNBUFFERED=1 timeout 2400 .venv/Scripts/python.exe -u -m pytest -v -p no:cacheprovider -rfE tests > _scratch/full_suite_2026-10-16e.log 2>&1
```

It was launched through the former runner's `run_in_background=True`, which
is not exposed here. Managed jobs supply DEVNULL stdin. These attempts test
important differences, but are not claimed to reproduce that exact launcher.
Python's inspected 3.14 Windows spawn implementation already bypasses the
venv redirector and uses `sys._base_executable`; Windows already uses spawn.
Neither changing start method nor replacing the venv executable is supported
by the evidence. `map_async(...).get(timeout)` would **not** bound the blocked
Pool constructor. No speculative Pool test change was made.

A second complete bounded run **passed the Pool race after the preceding
suite**: 16,319 passed, 27 failed, 51 skipped, 1 xfailed; zero errors, in
1,815.54 seconds. It had a 2,700-second process timeout. The original native
hang's exact cause remains unresolved; no hang fix is claimed.

That run collected before the mail-path and stream-audit repairs were made,
so it is **not a full-suite validation of the final branch**. Its 27 failures
are A1, A2, A4, the same 23 mail-pollution failures, and one new browser input
failure discussed below. Capture and the audit race happened to pass. All
earlier stale-fixture and shell-selection failures were absent.

## A. Product findings left unchanged

### A1. Context fingerprint's error handler raises on a missing project

- Test: `test_context_freshness.py::test_it_never_raises_on_a_project_with_no_paths`.
- Failure: `_context_fingerprint(None)` raises `AttributeError` in
  `mc/dev_harness_context.py:69`, whose exception handler calls `project.get`.
- Introducing commit: **`9dc642ee3a511d058a7b22e3c12461fd99fbb529`**, adding
  dev-harness fingerprinting to `mc/blueprints/agent_routes.py`.
- Exact-function before/after probe, same current dependencies:
  `9dc642ee^` returns a string; `9dc642ee` and base both raise. The existing
  test fails alone and in the complete run. This establishes the function's
  contract regression; a user-facing call with `None` was not demonstrated.

### A2. Two new process-group termination sites bypass the shared guard

- Test: `test_proc_kill_guard.py::test_no_raw_killpg_outside_the_guard`.
- `mc/desk_connect/custom_npm_scripts.py:67`: introduced by
  **`eaca7fc0933f36a91388dd69f3429dd64ce35a1e`**.
- `mc/desk_connect/github_start_check.py:90`: introduced by
  **`88712b5c07dabfca9dbca73fc2db735931dc2839`**.
- Both call `os.killpg` outside `mc/proc_kill.py`. Both also launch with
  `start_new_session` on POSIX. Thus this is an existing guard-contract
  violation, **not evidence of an observed server kill**. POSIX runtime
  termination was not tested on this Windows host. The structural test fails
  alone and in the complete run; no allowlist exception was added.

### A3. Studio capture sometimes aborts its first navigation

- Test: `test_desk_capture.py::test_real_chromium_capture_guard_and_cleanup`.
- Failure at `mc/desk_capture.py:137`: CDP `Page.navigate` returns
  `net::ERR_ABORTED` for the fixture's running local HTML server, and capture
  returns a 502 error. Proxy refusal list is empty.
- Feature's first and only implementation commit:
  **`97f3c6ab2bb5a569a06c3111900519f2f4da0859`**. The feature did not exist in
  its parent; this is a defect in the added feature, not a claimed older
  working version found by bisect.
- Fails both in the complete suite and independently. First 16 fresh traced
  processes: 10 pass / 6 fail. A later six-run probe: 5 pass / 1 fail.
  A diagnostic observer waited 0.5s after the error: the target was still
  `about:blank`, ready `complete`, with no destination document committed.
  A separate complete file run passed all 28 tests.
- Exact Chromium cancellation trigger remains unresolved. Startup timing is
  a hypothesis, not a diagnosis. Product code and test retries remain unchanged.

### A4. Three production spawns omit the explicit encoding guard

- Test: `test_subprocess_text_encoding.py::TestSubprocessTextEncoding::test_no_text_mode_subprocess_without_explicit_encoding`.
- `mc/browser_default_ua.py:83`: **`7a686fa051a5e714b61b37af8505a22a3529f613`**.
- `mc/browser_default_ua.py:125`: **`28099a2872c888a199cd6f45b4aa4f9118a8486a`**.
- `mc/skills.py:889`: **`9dc642ee3a511d058a7b22e3c12461fd99fbb529`**.
- `git blame` and the added calls identify the introducing commits. All use
  `text=True` without `encoding=`. This is a structural regression against
  the existing explicit-encoding rule; no actual mojibake was observed here.
  The two browser commands normally emit version strings and Git stdout is
  a SHA, so do not describe this finding as demonstrated corrupted UI text.

## B. Stale fixtures repaired

1. **Existing conversations need existing transcripts (3 tests).**
   Resume healing in `7e1f8b93` checks disk and transcript history. Two
   `test_double_roll_handoff.py` cases and one
   `test_one_process_per_conversation.py` case described existing conversations
   but supplied only handoff text. They now provide temporary transcripts and
   rollover history. The real healing and ownership gates still execute.
2. **Project creation needs human provenance (3 tests).** Routing guards
   `b5426a52` / `daa1437d` require browser origin. The concurrent-create loser
   becomes an edit and needs passcode proof. `test_delegation_privacy.py`
   supplies these through the real gate, using a throwaway passcode store.
   No guard is stubbed out or weakened.
3. **Document search accepts a newly valid answer (1 test).** `c51cf706`
   added `docs/research/XERJ_FIT_2026-10-16.md`, which ranks first for Q11 and
   explains the same deferred retrieval decision in prose. The gold pattern
   recognizes that precise passage as well as the older table wording.
   The top-three and overall 13/15 acceptance gates are unchanged.

Combined repaired fixture/shell/search/GC checks: **135 passed in 20.76s**.
Rollover plus resume-target checks: **19 passed in 4.72s**.
Privacy/ownership/routing checks: **76 passed in 8.85s**.
Document search alone: **23 passed in 7.68s**.

Two additional **warnings**, not failed tests, came from the Claude revival
fixtures in `test_delegation_delivery.py`: they stubbed only the Mode A
reader, so Mode B ran a real reader against a fake `Proc` without `wait()`.
Both complete runs reported the resulting thread exceptions. The fixtures
now stub both readers, consistent with their stated fake process plumbing.
All **23 tests pass in 5.51s with unhandled-thread warnings treated as errors**.

## C. Proven test/harness defects repaired

### C1. Mail import changes which server later tests reload (23 suite failures)

`test_mail_imap_quote.py::_load` embeds the standalone mail server. That
script prepends `tools/mail-mcp` to `sys.path` for its sibling helper.
The next `importlib.reload(server)` finds **the mail server's `server.py`**.
Reload preserves old module dictionary names, so this is not an immediate
missing-app crash: memory remains wired to the last application's fixture
directory. Later tests mix old notes, session logs, counters, and paths.

Evidence:

- In the completed suite, `delivdenom.md` appeared under the earlier
  `test_legacy_sidecars_still_in_exclusion_tuple` temp directory. The condense
  traceback explicitly identifies the reloaded module as
  `tools/mail-mcp/server.py`.
- Memory group alone: **92 passed** (12.66s). Full collection with only
  delivery selected: **9 passed / 16,389 deselected** (4.96s).
- Ordered files from `test_load_projects_sidecar_exclusions.py` through
  `test_memory_delivery.py`: **5 failed / 188 passed / 13 skipped** (10.55s).
- Minimal mail-quote then delivery pair: **8 failed / 15 passed** (1.00s);
  with no prior app import, memory is never wired at all.
- Introduced by test/script combination **`c2cf4187`**. Fix only the embedding
  test helper: snapshot and restore `sys.path` in `finally`, including failure
  paths; assert the standalone import leaves the path unchanged.
- Ordered predecessor group plus all affected memory, condense, and port
  tests after repair: **283 passed / 13 skipped** (26.38s).

The 23 failures are delivery 5, entry dedup 3, link graph 5, BM25 2,
per-turn memory 4, step-8 wiring 2, condense visibility 1, port forensic log 1.

### C2. POSIX test selects Windows' WSL launcher (1 suite failure)

`shutil.which('bash')` resolved to `System32/bash.exe` under PowerShell.
The failing install markers were printed, but the final status flag was lost
and return code was 0. The same command passes under native Git Bash.
The test now uses existing `agent_jobs.resolve_shell('bash')` on Windows,
before simulating Linux for command composition. No product behavior changed.
Installer check plus agent-job tests: **16 passed** (4.95s).

### C3. Audit assertion races the watchdog's final write (1 suite failure)

`StreamSession.teardown` removes its registry entry at
`mc/secrets_exec_stream.py:472`, then persists the end event at line 477.
The old test waited for the child to exit and the registry to become empty,
then immediately asserted the event was on disk. Those are different events.

The test passes alone (2.66s), fails in the complete suite, and a diagnostic
0.3-second delay before the real audit write reproduces the exact failure.
The audit tests now wait, with the existing bounded helper, for the specific
reason's persisted event. Kill, socket-close, and reason assertions remain.
With the injected delay: **2 passed** (5.98s); complete audit file:
**17 passed** (27.03s). Related test/teardown introduced by `0f261a89`.

### C4. Startup GC deletes another live pytest's state (independent defect)

Conftest GC introduced in `be9ad54c` removed every other `data-root-<pid>`,
even when its owner was live. This invalidated the first attempted baseline
when parallel probes started. That run was stopped and discarded. A second
attempt with its temp root inside the repo was also discarded because that
placement interferes with install-directory guards. Neither is product evidence.

A second-process probe proves old startup deletes the live parent's marker,
and fixed startup retains it. GC now removes only roots whose numeric owner
PID is absent according to psutil. Missing psutil or PID reuse retains state
conservatively; Windows `os.kill(pid, 0)` is not used. The new real-process
regression test also checks dead-owner collection: **1 passed** (0.62s).

## D. New browser failure: cause not established

The second complete run failed
`test_browser_input_live.py::test_wheel_over_a_scroller_scrolls_that_scroller`
at line 366 with **`TypeError: '>' not supported between NoneType and int`**.
The helper returned no value for `document.getElementById('s').scrollTop`;
this was not an observed numeric zero or evidence of incorrect wheel scaling.
The helper drops CDP exception details, and its navigation readiness loops
can exhaust without asserting success. Missing DOM or an evaluation error is
plausible, but the full run captured neither the page nor the CDP response.

The test passes alone (2.29s); its entire file passes **24 tests in 11.97s**;
eight further fresh invocations passed. This establishes intermittence, not
the cause or whether the fix belongs in product code. No speculative retry
or weakened assertion was added. A diagnostic probe using 40 independent
fixture parametrizations also passed **40/40 in 74.13s**; it preserves the
original assertion result and records CDP navigation/evaluation details on
failure (`_scratch/wheel-param40.log`, `_scratch/wheel_observe.py`). Across
the two complete runs there are **35 distinct
failed tests: A=4, B=5, C=25, unresolved=1**.

## Original-log reconciliation

F/E are original progress markers, not claimed distinct test-case counts.
Rerun skip includes the XML's xfail representation. A zero in rerun failures
does not prove the original cause; skipped cases are not passes.

| Original file (`tests/`) | Original F/E | Rerun pass / fail / error / skip | Disposition |
|---|---:|---:|---|
| `test_caller_attribution.py` | 1/0 | 27 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_cli_version_check.py` | 1/0 | 16 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_codex_apply_patch_fence.py` | 1/0 | 15 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_codex_steward_fence.py` | 6/0 | 22 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_context_freshness.py` | 1/0 | 10 / 1 / 0 / 0 | A: product |
| `test_delegation_privacy.py` | 3/0 | 13 / 3 / 0 / 0 | B: stale fixture |
| `test_delegation_restart.py` | 0/3 | 3 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_delegation_startup_harness.py` | 1/0 | 2 / 0 / 0 / 1 | Unreproduced; no original traceback |
| `test_desk_connect_github.py` | 1/0 | 27 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_desk_connect_github_output.py` | 1/0 | 20 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_desk_connect_npm_node_paths.py` | 6/0 | 22 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_desk_connection_removal.py` | 1/0 | 56 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_desk_harvest.py` | 0/10 | 22 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_dev_harness_diff.py` | 1/18 | 20 / 0 / 0 / 1 | Unreproduced; no original traceback |
| `test_dev_harness_runner.py` | 0/32 | 40 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_dispatch_lineage.py` | 0/3 | 10 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_doc_search.py` | 1/0 | 22 / 1 / 0 / 0 | B: stale fixture |
| `test_double_roll_handoff.py` | 2/0 | 2 / 0 / 0 / 0 | B: stale fixture |
| `test_fence_fail_closed_unattended.py` | 1/0 | 50 / 0 / 0 / 1 | Unreproduced; no original traceback |
| `test_fence_shell_self_protect.py` | 2/0 | 741 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_fence_tamper_corpus_1012.py` | 27/0 | 344 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_followup_removed_worktree.py` | 0/6 | 6 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_frozen_hook_entry.py` | 2/0 | 9 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_guard_command_shells.py` | 5/0 | 11 / 0 / 0 / 2 | Unreproduced; no original traceback |
| `test_hivemind_worktree_dispatch_hook.py` | 0/4 | 4 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_hivemind_worktree_isolation.py` | 0/16 | 23 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_human_proof_guard.py` | 0/3 | 80 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_installer_auth_probe.py` | 7/0 | 7 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_installer_folder_recovery.py` | 8/0 | 8 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_mac_self_update.py` | 12/0 | 60 / 0 / 0 / 13 | Unreproduced; no original traceback |
| `test_memory_delivery.py` | 5/0 | 4 / 5 / 0 / 0 | C: mail import pollution |
| `test_memory_entry_dedup.py` | 3/0 | 11 / 3 / 0 / 0 | C: mail import pollution |
| `test_memory_link_graph.py` | 5/0 | 6 / 5 / 0 / 0 | C: mail import pollution |
| `test_memory_search_bm25.py` | 2/0 | 9 / 2 / 0 / 0 | C: mail import pollution |
| `test_memory_turn.py` | 4/0 | 15 / 4 / 0 / 0 | C: mail import pollution |
| `test_memory_v2_step8_wiring.py` | 2/0 | 11 / 2 / 0 / 0 | C: mail import pollution |
| `test_merge_review_gate.py` | 0/51 | 38 / 0 / 0 / 13 | Unreproduced; no original traceback |
| `test_midturn_rollover.py` | 0/55 | 55 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_no_real_cli_guard.py` | 1/0 | 10 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_one_process_per_conversation.py` | 1/0 | 17 / 1 / 0 / 0 | B: stale fixture |
| `test_p2_1_condense_visibility.py` | 1/0 | 3 / 1 / 0 / 0 | C: mail import pollution |
| `test_port_conflict.py` | 1/0 | 17 / 1 / 0 / 0 | C: mail import pollution |
| `test_preserved_worktrees.py` | 0/32 | 33 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_proc_kill_guard.py` | 1/0 | 11 / 1 / 0 / 4 | A: product |
| `test_provider_env_scope.py` | 3/0 | 29 / 0 / 0 / 0 | Unreproduced; no original traceback |
| `test_provider_neutral_distiller_topics.py` | 2/0 | absent | Outside base: future API test |

The uncommitted `test_provider_neutral_distiller_topics.py` was copied only
to scratch for a supplemental probe, not added to this base: **2 passed /
2 failed** (0.22s). Its failing tests expect Topics `_recent_conversations`
and `_resolve_model`, neither of which exists in this base. It is prospective
feature/test work, not evidence for a regression in this snapshot.

### Native Git Bash replay of the original affected files

All 45 original affected files present at the requested base were also run
together under native Git Bash, using the main checkout's exact Python venv
and a 420-second process timeout, with the repaired tests in that group.
Result: **2,014 passed, 2 failed, 9 skipped, 1 xfailed in 254.39s**. The only
failures were A1 (fingerprint) and A2 (raw process-group termination).
`test_mac_self_update.py` exercised **72 passing tests / 1 skip**, compared
with 60 passing / 13 skipped under PowerShell. The remaining Mac skip is the
MSYS PID platform condition. Dev-harness diff: 20 passed / 1 skipped;
dev-harness runner: 40 passed. Thus the shell-dependent checks were exercised,
but this still does not establish the original launcher's failure causes.
Results: `_scratch/original-bash-group.log` and `.xml`.

## Commands, artifacts, and handoff

Commands run from this worktree. `_scratch/triage_run.py` supervises the exact
`python -m pytest -o addopts=` child, supplies DEVNULL stdin, file-backed
stdout/stderr, and a distinct OS-temp root; it registers the captured PID and
kills only its own captured child tree on timeout. Scratch files are local
diagnostic artifacts, not source additions.

```text
# Complete requested run: 16,310 passed / 34 failed / 51 skipped / 1 xfailed / 1 deselected
python _scratch/triage_run.py 2400 _scratch/full-clean.log -q -rfE --deselect tests/test_scheduler_slot_claim.py::test_two_processes_racing_one_slot_yield_exactly_one_dispatch --junitxml=_scratch/full-clean.xml tests

# Pool after full collection: 1 passed / 16,397 deselected
python _scratch/triage_run.py 90 _scratch/pool-full-collection.log -q --tb=short tests -k test_two_processes_racing_one_slot_yield_exactly_one_dispatch

# Full execution including Pool: 16,319 passed / 27 failed / 51 skipped / 1 xfailed
# Collected before mail-path and audit synchronization repairs.
python _scratch/triage_run.py 2700 _scratch/full-fixed.log -q -rfE --junitxml=_scratch/full-fixed.xml tests

# Initial repairs together: 135 passed
python _scratch/triage_run.py 120 _scratch/fixes-combined.log -q --tb=short tests/test_agent_routes.py::test_compose_install_batch_posix_runs_each_vendor_independently tests/test_agent_jobs.py tests/test_delegation_privacy.py tests/test_project_routing_guard.py tests/test_double_roll_handoff.py tests/test_one_process_per_conversation.py tests/test_resume_target.py tests/test_doc_search.py tests/test_pytest_data_root.py

# Entire audit module after bounded synchronization fix: 17 passed
python _scratch/triage_run.py 180 _scratch/stream-suite-fixed.log -q --tb=short tests/test_secrets_exec_stream_audit.py

# Ordered mail predecessors plus all affected memory/condense/port checks: 283 passed / 13 skipped
python _scratch/triage_run.py 180 _scratch/memory-prefix-fixed.log -q --tb=short tests/test_load_projects_sidecar_exclusions.py tests/test_local_auth_routes.py tests/test_mac_self_update.py tests/test_mail_imap_quote.py tests/test_mail_launder.py tests/test_mcp_read_deadline.py tests/test_mcp_trim.py tests/test_mcp_write_gate.py tests/test_media.py tests/test_memory_archive_dedupe.py tests/test_memory_delivery.py tests/test_memory_entry_dedup.py tests/test_memory_link_graph.py tests/test_memory_search_bm25.py tests/test_memory_turn.py tests/test_memory_v2_step8_wiring.py tests/test_p2_1_condense_visibility.py tests/test_port_conflict.py

# Final GC test after explicit UTF-8 decoding was added: 1 passed in 0.47s
python -m pytest -o addopts= -q tests/test_pytest_data_root.py

# Revival fixture warnings promoted to errors: 23 passed in 5.51s
python -m pytest -o addopts= -q -W error::pytest.PytestUnhandledThreadExceptionWarning tests/test_delegation_delivery.py

# Browser failure probe, 40 independent fixture instances: 40 passed in 74.13s
python _scratch/triage_run.py 240 _scratch/wheel-param40.log -q --tb=short -s -p _scratch.wheel_observe tests/test_browser_input_live.py::test_wheel_over_a_scroller_scrolls_that_scroller
```

Detailed current-run failures: `_scratch/full-clean.xml`. Historical-function
probe: `_scratch/context_before_after.py`. Original marker inventory:
`_scratch/original_counts.json`. Capture observations: `_scratch/capture-*.log`.
Ordered repro/fix: `_scratch/memory-last-prefix.log`,
`_scratch/imap-memory-before.log`, `_scratch/memory-prefix-fixed.log`.
Audit race: `_scratch/stream-race-before.log`, `_scratch/stream-race-fixed.log`.

README, USER_GUIDE, and project rules need no change: no user-facing behavior
or new standing policy is introduced. No frontend or new product-module
checks apply. The explicit no-merge/no-push/no-restart dispatch overrides the
generic closeout playbook.

No production fixes are included. A clean release verdict is not supported
by these results: product findings and the unreproduced original hang remain.
