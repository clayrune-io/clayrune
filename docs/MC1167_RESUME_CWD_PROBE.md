# MC-1167: resume cwd provenance probe

2026-11-09, Sol_Tobin. Backlog `c220712f`. Base `63b8b1df` (master at
branch creation), branch `clayrune/c220712f-resume-cwd`.

## Fix candidate after Dave's decision

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
