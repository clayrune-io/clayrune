# MC-1167: resume cwd provenance probe

2026-11-09, Sol_Tobin. Backlog `c220712f`. Base `63b8b1df` (master at
branch creation), branch `clayrune/c220712f-resume-cwd`.

## Round 3 candidate: borrowed provenance survives the log

Branch `clayrune/c220712f-r3` starts at round-2 `b2bcf773`. Fenn's P1
reproduced pending-log/purge/revive in both Claude modes: the isolated bit
survived but the borrowed flag/cwd did not, so revival invented ownership.

`resume_cwd.isolation_log_fields` writes isolation, borrowed ownership and actual
cwd together in dispatch, INIT identity refresh and completion rows. Upserts
retain the previous provenance when a rebuilt session lacks it.
`borrowed_log_cwd` validates the saved Git worktree before any ownership
inference. Claude revival consumes it directly; the shared vendor/delegation/
scheduler helper passes a borrowed override through dispatch into either
Claude mode or the vendor adapter. Missing/invalid borrowed provenance refuses
before dispatch; owners retain MC-1165 recreation, review and merge-back.
Existing legacy rows without borrowed provenance retain their prior behavior;
this cannot reconstruct a cwd that was never persisted.

Permanent `tests/test_resume_cwd_log_roundtrip.py` covers both log writers,
INIT identity refresh, purge/revive with an uncommitted sentinel, owner
log/purge/recreation/review/merge, missing borrowed trees, vendor/delegation/
scheduler inputs, actual dispatch override propagation, and partial-session
log rewrites. Fenn's round-2 probe is also run unchanged from scratch.
No locking or acknowledgment restructuring; MC-1168 integration remains a
separate merge/review responsibility. No static files or node_modules link.

Validation results and exact commands follow below and in the main-checkout
journal. No real provider launch or live resume; no merge,
push or restart. Roll back by reverting this round's commit.

Final validation: **384 passed**, 21 files, pytest 115.01s, reporting
launcher exit 0. This repeats the exact round-2 selection (359 cases) and adds
25 permanent round-3 regressions. Focused run: **27 passed**, pytest 11.71s,
launcher exit 0 (25 permanent plus Fenn's two unchanged assertions).
Targeted pyright versus exact `b2bcf773`: 15 baseline errors / 15 candidate
errors, zero new (comparison exit 0; raw pyright exits 1 for each). All 15
remain in the route module; `resume_cwd.py` has zero errors.

Both test commands reported `transport_unavailable_progress_incomplete`,
`parent_unavailable_progress_incomplete` and
`pytest_enrollment_unavailable_progress_incomplete`. Counts come from captured
pytest output and JUnit, not successful telemetry. No reporting configuration,
permissions or enrollment was changed. No real provider or live resume tested.
Source whitespace check passes. Scheduler, lifecycle and worktree engine are
unchanged from round 2. No node_modules junction was created.

Exact command shape (from the candidate checkout; installation-root paths
are expanded in the main journal and the handoff command JSON):

```text
<project-python> <installation-root>/tools/test-run.py --project mission_control --port 5199 -- <project-python> -m pytest tests/test_resume_cwd_provenance.py tests/test_claude_runtime.py tests/test_one_process_per_conversation.py tests/test_resume_cwd_routes.py tests/test_worktree_nonclaude_dispatch.py tests/test_revive_notify_carry.py tests/test_worktree_dispatch.py tests/test_resume_cwd_review_regressions.py tests/test_resume_cwd_ownership.py tests/test_isolated_launch.py tests/test_worktree_lifecycle.py tests/test_mc1165_isolation_record.py tests/test_mc1165_review3_probes.py tests/test_mc1165_review4_probes.py tests/test_mc1165_review5_probes.py tests/test_mc1165_review7.py tests/test_mc1165_review8.py tests/test_mc1165_round9.py tests/test_mc1165_review9_probes.py tests/test_cold_review_wiring.py tests/test_resume_cwd_log_roundtrip.py -q --tb=short -o addopts= --junitxml=_scratch/mc1167-r3-suites-final.xml
<project-python> <installation-root>/tools/test-run.py --project mission_control --port 5199 -- <project-python> -m pytest tests/test_resume_cwd_log_roundtrip.py _scratch/test_mc1167_r2_fenn.py -q --tb=short -o addopts= --junitxml=_scratch/mc1167-r3-roundtrip.xml
<project-python> _scratch/check_mc1167_types.py
```

## Round 2 candidate: ownership-aware integration

Branch `clayrune/c220712f-r2`, integrating exact master `1fdffd96` with
round 1 `00fbca52`. Dave selected policy (c) on 2026-11-09: known owners keep
master's worktree recreation and ownership; transcript-only resumes refuse
when the recorded worktree is missing or unusable. No silent relocation to main.

- `mc/resume_cwd.py` owns provider provenance and typed refusal. Managed
  recorded paths must pass the existing Git worktree identity validator, so a
  restored directory without a usable marker cannot masquerade as isolation.
  A validation exception is also a refusal, with the original error surfaced.
- Qwen's runtime performs an exact lookup in its cwd-encoded recording directory.
  That directory remains provenance when normal Qwen rows omit cwd. There is
  no broad glob fallback. Codex uses its native-id rollout lookup independently
  of directory existence, then accepts metadata only for this project or its
  managed agent trees. The metadata/refusal test now uses real CodexRuntime.
- Route conflict resolution keeps both `ResumeCwdMissing` and
  `RevivalRefused` HTTP 409 handlers, the owner-id reuse checks and lifecycle
  exclusion. An owner resumes with `_worktree_borrowed: false`, so identity
  diagnostics, cold review and merge-back remain active. A transcript-only
  borrower cannot acquire cleanup ownership or recreate a tree under its own id.
- Refused transcript-only input stays in the response and, except incognito,
  the conversation log. Existing transcripts remain readable and untouched.

Fenn's archived five probes were copied into this checkout's scratch directory.
The candidate case was changed to read current working source instead of the
immutable rejected commit `00fbca52`; the exact `63b8b1df` baseline control
remained pinned. Live fixtures now create real private Git worktrees, and the
plain-directory case explicitly removes its marker. All five adapted probes
passed. These are not claimed as a byte-identical run of the archived source.
Permanent equivalents cover both Claude modes, Qwen's exact lookup and
resolver, and real Codex missing-tree refusal. Separate integration tests cover
owner recreation and finish-time identity/review/merge in both Claude modes.

Combined launcher run: **359 passed, 20 files, exit 0**. This includes the
original seven files plus permanent review/ownership regressions and MC-1165's
isolation, lifecycle, review-round and cold-review wiring tests. Local JUnit is
the evidence: launcher telemetry was incomplete (queue, transport, event flush,
capture diagnostic and unfinalized-run warnings). No reporting permission or
configuration was changed. The preceding run had 355 passes and one ordinary-
directory fixture failure; that fixture now uses a real Git worktree.

Final affected-suite rerun after the error-detail/logging changes: **70 passed,
exit 0**, including all five adapted scratch probes. The main journal records
exact commands, per-file counts and final commit. Targeted pyright comparison against an isolated archive of exact master
`1fdffd96`: 15 errors on each side, zero new. All 15 are existing route errors;
`mc/resume_cwd.py` and `mc/isolated_launch.py` have zero. Both raw pyright
commands exit 1; the diagnostic-count comparison exits 0. No baseline rewritten.

USER_GUIDE and CHANGELOG describe policy (c). No new UI, static assets, setup or
standing rules; README/CLAUDE/AGENTS changes and frontend smokes do not apply.
The route monolith holds existing integration points only; provider provenance
lives in the separate resolver module. The touched send reconciliation catch
now logs its failure; temporary-file cleanup and cosmetic response tagging
remain best effort. No real provider was launched, no live session resumed,
and no merge to master, push or restart is authorized. Reverse this branch's
integration commit to roll back; retain the round-1 branch as the starting point.

## Round 1 candidate (historical; superseded by round 2)

Dave selected option 1 on 2026-11-09: refuse when the original working tree
was removed; do not recreate it or silently relocate to the project checkout.
The probe checkpoint below is historical. Both strict xfail marks are removed.

`mc/resume_cwd.py` reads the first recorded absolute cwd (Claude JSONL, plus
the Codex session-meta format), checks that directory, and owns the distinct
`ResumeCwdMissing` refusal and request-retention payload. The route module's
lookup still uses the existing broad transcript reader; it no longer equates
finding a transcript with finding the original cwd. The missing-cwd exception
escapes its broad lookup catch, `/send`'s revive catch, and vendor revive's
dispatch catch.

Cold `/send` (saved session and transcript-only), `/followup`, and dispatch
resume return HTTP 409 with `code: resume_cwd_missing`. The error names the
removed directory and says to start a new chat or restore it. There is no
spawn, recreation, rollover, or fallback dispatch. Dispatch checks cwd before
auto-fresh clears its resume id. Recorded resume cwd also takes precedence
over an explicit cwd override, so an existing worktree cannot relocate to main.

The original transcript remains untouched and readable. The response returns
the original input as `request_message`; `request_saved` reports whether it
was also appended to the conversation record's `resume_refusals`. Existing
identity, task and completion status are preserved. The row moves to the front
so the existing newest-N log cap does not immediately discard the request.
Incognito requests are returned only; storage failure is logged and reported
as `request_saved: false`, with no fallback process.

Live agent trees carry `_worktree_isolated: true` and
`_worktree_borrowed: true` for both dispatch/revival and both Claude modes.
The latter prevents merge/removal ownership transferring to a resumed session;
that matches the old behavior of skipping cleanup for these resumed trees.
Automatic recreation and the broader retained-session lifecycle remain with
MC-1165; this patch does not reimplement that lifecycle.

Final tests, via project interpreter and `tools/test-run.py` explicit manifest:

| File | Passed | Child exit |
| --- | ---: | ---: |
| `tests/test_resume_cwd_provenance.py` | 7 | 0 |
| `tests/test_claude_runtime.py` | 111 | 0 |
| `tests/test_one_process_per_conversation.py` | 18 | 0 |
| `tests/test_resume_cwd_routes.py` | 12 | 0 |
| `tests/test_worktree_nonclaude_dispatch.py` | 6 | 0 |
| `tests/test_revive_notify_carry.py` | 12 | 0 |
| `tests/test_worktree_dispatch.py` | 9 | 0 |

Total: 175 passed, manifest exit 0. The existing
`test_transcript_path_finds_worktree_chat_after_worktree_is_gone` passes.
The final launcher reported transport/parent/enrollment unavailable, so its
telemetry is incomplete; the table records captured pytest results, not a
claim that reporting succeeded. The final candidate's affected route/vendor
suites were rerun after establishing recorded-cwd precedence over explicit
overrides: 18 passed, exit 0. Exact argv and manifest are in the main journal.

Full pyright comparison against the exact starting commit: 171 errors on each,
zero new; `mc/resume_cwd.py` has zero errors. The checked-in baseline gate
exits 1 because of six pre-existing baseline differences (two `ctypes.WinDLL`
findings and four changed streaming-bool diagnostic headlines); this patch
does not change that baseline. No real provider was launched or session resumed.

USER_GUIDE and CHANGELOG updated. No static, installer, setup, or standing-rule
change; frontend smoke and README/CLAUDE/AGENTS edits are not applicable.
Commit on this branch only: no merge, push or restart authorized. Revert the
fix commit to roll back. MC-1165 integration must retain the missing-cwd
exception boundaries and the distinction between isolation and tree ownership.

## Confirmed baseline defect (historical)

The real `ClaudeRuntime.transcript_path(project_path, native_id)` finds a
worktree transcript through its encoded-directory glob. `_resume_cwd_for`
interprets that successful broad lookup as proof that `project_path` is the
conversation's original cwd, returning main without scanning local worktrees.

`tests/test_resume_cwd_provenance.py` reproduces both cases with a temporary
transcript store and the real runtime/helper; no provider process is launched.

| Fixture | Observed helper return | Probe result before xfail |
| --- | --- | --- |
| Worktree directory exists | Project checkout | FAIL |
| Worktree directory created then deleted; transcript retained | Project checkout | FAIL |
| Main-checkout transcript | Project checkout | PASS |
| No transcript | None | PASS |

The transcript includes its original worktree cwd. Both worktree fixtures
also verify that broad lookup still finds the transcript through main and
through the original worktree path, even after directory removal. This read
behavior is intentional and already covered by
`test_transcript_path_finds_worktree_chat_after_worktree_is_gone` in
`tests/test_claude_runtime.py`; changing it would break opening old chats.

The Claude dispatch branch is code-traced, not provider-exercised: it consumes
`_resume_cwd_for` and uses a truthy result as `_agent_cwd` with isolation false
before passing cwd to `spawn_engine_proc`. The probe does not claim every
dispatch reaches that branch (explicit cwd overrides and auto-fresh decisions
are separate paths).

## Decision checkpoint (historical)

The task explicitly says to stop if the fix needs a missing-worktree policy.
Master has no settled cold-resume policy that can simply be reused here:
`_session_cwd` falls back to main; `agent_worktree_followup` tries recreation
but also falls back to main on failure. The newer MC-1165 fail-closed launch
module referenced by Fenn is not present on this base. Returning None here
would merely make dispatch choose fresh isolation, potentially main again.

1. **Recommend: refuse cold resume when its original worktree is gone.**
   Preserve transcript and request, report an actionable error, and never
   reinterpret the transcript as permission to run in main. This needs a
   distinct missing-cwd result/error wired through callers; the helper's
   current broad exception catch must not swallow it. Lowest recovery scope,
   but a cleaned-up chat cannot continue until its tree is restored.
2. **Recreate the original owned worktree, then resume; refuse if recreation
   fails.** Better continuity, but requires resolving the MC owner id separately
   from the native conversation id, choosing the surviving branch/base, and
   preserving ownership/isolation flags. More integration and git-failure tests;
   should align with MC-1165 rather than introduce a competing lifecycle policy.
3. **Explicitly allow relocation to main after cleanup.** Smallest recovery
   change, but gives up isolation and may expose a different branch/file state.
   Needs a visible relocation contract; current silent main selection is the bug.

At this probe checkpoint, no option had been selected and no production code
had changed. Dave subsequently selected option 1; see the fix candidate above.

## Probe-only validation and checkpoint (historical)

The initial bare `python` child resolved to system Python. It independently
produced 2 failures and 2 passes, but is not the project-interpreter evidence.
The run below explicitly used the project's interpreter for launcher and child:
2 failed, 2 passed, exit 1 before the expected-failure annotations were added.

```text
<absolute-project-python> <absolute-installation-root>/tools/test-run.py --project mission_control --port 5199 -- <absolute-project-python> -m pytest tests/test_resume_cwd_provenance.py -v -s -o addopts=
```

Run from this branch's checkout. The journal records the actual local paths;
the command above uses placeholders to keep operator paths out of source.

The probe checkpoint marked only the two known defect probes `xfail(strict=True)`;
they are not fixed or counted as passing coverage. Use `--runxfail` to reproduce
the failures after checkout. Remove the annotations when implementing the fix.
Main and absent-transcript controls remained ordinary passing tests. The fix
candidate has now removed those annotations and passes both worktree cases.

Project-interpreter regression run of this probe, `tests/test_claude_runtime.py`
and `tests/test_one_process_per_conversation.py`: **131 passed, 2 xfailed**, exit
0. The launcher reported `telemetry_queue_full_progress_incomplete`; reporting
coverage is partial, independently of pytest's observed result and exit code.

No `mc/` module was edited, so the requested touched-module pyright gate is
not yet applicable. No user-visible behavior changed, so no changelog or user
guide update is required. No merge, push, restart, provider login, or live
session resume was performed. Journal is append-only in the main checkout's
`docs/_journal/c220712f-resume-cwd.md` as well as the worktree copy.
