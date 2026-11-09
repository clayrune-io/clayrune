# MC-1167: resume cwd provenance probe

2026-11-09, Sol_Tobin. Backlog `c220712f`. Base `63b8b1df` (master at
branch creation), branch `clayrune/c220712f-resume-cwd`.

## Confirmed

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

## Decision required from Dave

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

No option selected. No production code changed. Live-tree resolution and
read-only transcript lookup can be separated once the missing-tree result
contract is selected. Preserve broad transcript readability in every option.

## Validation and checkpoint

The initial bare `python` child resolved to system Python. It independently
produced 2 failures and 2 passes, but is not the project-interpreter evidence.
The run below explicitly used the project's interpreter for launcher and child:
2 failed, 2 passed, exit 1 before the expected-failure annotations were added.

```text
<absolute-project-python> <absolute-installation-root>/tools/test-run.py --project mission_control --port 5199 -- <absolute-project-python> -m pytest tests/test_resume_cwd_provenance.py -v -s -o addopts=
```

Run from this branch's checkout. The journal records the actual local paths;
the command above uses placeholders to keep operator paths out of source.

The checkpoint marks only the two known defect probes `xfail(strict=True)`;
they are not fixed or counted as passing coverage. Use `--runxfail` to reproduce
the failures after checkout. Remove the annotations when implementing the fix.
Main and absent-transcript controls remain ordinary passing tests.

Project-interpreter regression run of this probe, `tests/test_claude_runtime.py`
and `tests/test_one_process_per_conversation.py`: **131 passed, 2 xfailed**, exit
0. The launcher reported `telemetry_queue_full_progress_incomplete`; reporting
coverage is partial, independently of pytest's observed result and exit code.

No `mc/` module was edited, so the requested touched-module pyright gate is
not yet applicable. No user-visible behavior changed, so no changelog or user
guide update is required. No merge, push, restart, provider login, or live
session resume was performed. Journal is append-only in the main checkout's
`docs/_journal/c220712f-resume-cwd.md` as well as the worktree copy.
