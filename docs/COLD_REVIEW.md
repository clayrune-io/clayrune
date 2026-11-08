# Cold reviewer on agent handoff (MC-1165)

Branch candidate. No push, merge, server restart or live setting change is part of
this. The setting is off unless it is boolean `true`; nothing is enabled on any
existing project. Tests never call a model or dispatch an agent.

When a project sets `cold_review_required`, an agent branch with commits is
reviewed at session end by a **toolless one-shot model call** that sees only the
dispatched task text and the git diff. `merge_back` then lands the branch only if
a **cold** pass for the exact tip SHA is on record. Any error, timeout, bad reply
or out-of-attempts state leaves the branch held (`awaiting_review`).

## Calls made by Dave (2026-11-07), as built

1. **Reviewer = A, toolless one-shot.** `agent_runtime.run_text_transform('claude', ...)`,
   the Scribe / mail-launder seam (no tools, no MCP, neutral cwd, TOOL_FREE_TRANSFORM
   authorization). Fenn-by-hand stays for judgment reviews. The verdict is written by
   an internal call, `merge_review_gate.record(cold=True, reviewer_session='')`, from
   `mc/cold_review_runner.py`. **No HTTP route was added** and the record route never
   passes `cold`, so no agent-reachable write path exists (tests:
   `test_no_new_http_route`, `test_record_route_cannot_mint_a_cold_record`).
2. **Timing = pre-lock sync, on a worker thread.** See "Threading" below.
3. **Hold = one automatic retry, cap 2 attempts per commit SHA, then a human.** Attempt
   count and last error are stored in the review store and lead the `awaiting_review`
   detail, so the merge state shows them.
4. **Ask = the dispatched task text** (`session['task']`).

## Pieces

| What | Where |
|---|---|
| Setting, packet, prompt, verdict parser | `mc/cold_review.py` |
| Cold-aware hold + attempt state | `mc/cold_review_gate.py` |
| Review, retry, worker thread | `mc/cold_review_runner.py` |
| Store (one, shared): cold marker, side sections | `mc/merge_review_gate.py` |
| Hold wiring in `merge_back` | `mc/agent_worktree.py` (`_cold_gate`, 4 lines) |
| Session-end wiring | `_worktree_merge_back_on_end` in `mc/blueprints/agent_routes.py` |
| Human-only setting | `cold_review_required` in `mc/project_safety_settings.py` |
| Tests | `tests/test_cold_review.py`, `tests/test_cold_review_wiring.py` |

## How "cold" is enforced

By construction. `cold_review.build_packet(repo, base, tip, ask)` returns a dict with
exactly `ask, base_sha, tip_sha, diffstat, diff`; `render_prompt` refuses any other
key. The runner is handed the project, the branch name and the ask string only; it
never receives the session, so it has no path to a transcript, journal or final
message (`test_cold_review_module_imports_nothing_from_session_state`,
`test_reviewer_receives_only_the_prompt_built_from_ask_and_diff`). The diff is read
from git (merge-base..tip), not from anything the builder wrote. The model call is
toolless, so the reviewer cannot read anything else. An empty diff, a missing ask,
a diff over 256 KiB (refused, never truncated) or an unreadable ref is a failed
attempt, not a pass.

## The cold record requirement

`merge_review_gate.hold_reason(..., require_cold=True)` passes a tip only when the
`pass` on record carries `cold: true`. `cold_review_gate.hold_reason` sets it
whenever `cold_review_required` is on, and `merge_back` calls that for both of its
hold checks (before and after verification). A pass recorded by hand, or by a
reviewer session through the route, does not satisfy it. The latest record for a
branch+SHA wins, so a later hand-recorded pass replaces a cold one and holds again.
With the setting on, the hold runs even when `merge_requires_review` is off. With the
setting off, behavior is byte-for-byte the old path.

## Threading (the check Dave asked for)

`_worktree_merge_back_on_end` is called synchronously from
`_log_agent_completion_body` (`agent_routes.py`, the "Worktree isolation" block). That
function is reached from `_log_agent_completion`, whose callers include:

* the Claude stream-reader threads, **inside** `with get_manager(...).lock:`
  (`agent_routes.py` ~L6221 -> `_log_agent_completion` ~L6247, and ~L6535 -> ~L6574);
* the non-Claude runtime exit hook, **inside** `with get_manager(project_id).lock:`
  (~L10077 -> ~L10115);
* the stop / delete routes, inside the same project lock, for a session that was
  waiting for a limit reset (~L14632, ~L15146);
* the deferred-removal `threading.Timer` (`_worktree_retry_removal`, ~L1625), which is
  a plain worker thread.

So it does run under the project lock, which every other session of that project
waits on (and the spawner wake, `_notify_spawner_unless_jobs_open`, follows it). A
model call there would hold the lock for up to two attempts x 180 s. Therefore
`_worktree_merge_back_on_end` hands a qualifying branch to
`cold_review_runner.start`, which reviews and then calls the same function again with
`_cold_done=True` on the worker thread (the Timer path already runs merge-back off the
lock). Pre-existing, not changed here: with `verify_command` set, `landing_gate.verify`
also runs its commands (default 1200 s each) inside that same lock.

A branch qualifies when the setting is on, it is not a hivemind worker, and its tip is
ahead of the landing base.

**A second end-of-session while a review is running is remembered, not dropped.**
`start()` returns `None` for the caller but records the newest completion. When the
current review ends, the worker re-checks the branch tip: a newer tip gets its own
review, an unchanged one is already recorded or out of attempts (no model call), then
the worker calls the NEWEST completion's callback. If the tip moves while an attempt is
in flight, the loop stops without spending another attempt on the old SHA
(`superseded`) and the worker reviews the new tip.

**The callback is bound to the turn that ended** (`_worktree_cold_review_done`). The
review runs minutes after the turn ended, and the session may have resumed. Under the
project lock (every resume path flips `status` to `running` under it) the callback
skips when the session is `running`/`idle` or its `last_status_change_time` is no
longer the stamp captured at turn end; the resumed turn's own end re-runs the
merge-back. Only that cheap check and the merge-back itself run in the lock, never
the model call. Agent processes are excluded from the teardown guard's live-process
list, so nothing else would stop the worktree being removed from under a resumed
session.

**The gate reads one snapshot.** `hold_reason` takes the rejection list, the branch's
record and its `cold` marker from a single read of the store. Review writes do not take
the landing lock, so separate reads could combine a hand pass with a later cold
rejection into a cold pass that was never recorded.

## Retry and the visible state

Attempts are reserved **before** the model call (a crash mid-call still spends one),
per commit SHA, in the review store's `cold_attempts` section (the same file, preserved
across record writes). The key is the SHA, not branch+SHA: a second branch name on the
same commit shares the budget. The check and the increment are one store mutation, so
two callers cannot both take the last attempt. The second attempt runs immediately after the first fails. A
verdict of either kind (`pass` or `changes_requested`) is recorded and ends the
review; `changes_requested` sticks to that commit as before. After two failed attempts
no further model call is made for that SHA. The held state reads, in the
`awaiting_review` detail (also written to the project activity feed):

    cold review attempt 2/2, last error: model_timeout; out of attempts, needs a human - no review on record for clayrune/agent/<id> at <sha8>

Error codes: `packet:<missing_ask|empty_diff|diff_too_large|git_unavailable>`,
`model_timeout`, `model_error:<ExceptionType>`, `claude_unavailable`,
`reply:<empty_reply|not_json|not_an_object|unknown_verdict|bad_summary>`,
`record_refused:<http status>`, `internal:<ExceptionType>`.

A new commit is a new SHA and starts at zero. There is no HTTP route and no CLI to
reset a spent SHA: the human merges by hand after their own review (hand merges are
not cold-gated, see below) or the agent commits a change.

## Limits, stated plainly

* **An injected "approve" in the diff can forge a pass.** The diff is untrusted text
  fed to a model. The boundary fence is random per render and the prompt says to
  ignore instructions in it, but nothing proves the model obeyed. Fail-closed covers
  absence and error, not a manipulated verdict. A pass is evidence, not proof.
* **The reviewer sees only the diff.** It cannot open surrounding code or run tests,
  so it can miss what only context shows. That is the price of enforced coldness.
* **Default reviewer model is the one-shot runtime's default (Haiku).** Set the global
  config key `cold_review_model` to change it; empty means the runtime default.
* **The spawner is woken before the landing.** With the review on a worker thread, the
  completion wake can fire while the review is still running; the branch lands (or
  holds) a minute or more later. The activity feed gets "Cold review started" and the
  outcome.
* **Triggered at session end only.** The startup `gc_stale` pass and the Timer retry
  call `merge_back`, which holds for want of a cold pass but does not start a review.
  A server restart while a review runs leaves the branch held until its session ends
  again.
* **Hand merges are not cold-aware.** `tools/merge-review-check.py` and the opt-in git
  hooks still read `merge_requires_review` and accept any pass; they do not know the
  `cold` marker. Client-side hooks remain advisory.
* **Task text is the ask, not the backlog item.** It is what the builder was told, up
  to 4000 characters, and may include the dispatcher's own framing.

## Follow-up (not built): explicit `backlog_item_id` at dispatch

No session-to-backlog-item link exists in the code. Recording a `backlog_item_id` on
the dispatched session (a new field on `POST .../agent/dispatch`, stored on the
session and agent-log row) would let the reviewer be given the item's own text
instead of the task string. Then `session['task']` in
`_worktree_merge_back_on_end` becomes a lookup of that item, falling back to the task.
Filed here, not implemented, per Dave's call 4.

## Verification

`tests/test_cold_review.py` (packet and parser, 29 checks) and
`tests/test_cold_review_wiring.py` (31 checks: cold record requirement, one-shot
input, retry and cap with visible state, worker thread and dedupe, session-end wiring
including an end-to-end landing). Mutation-checked: disabling `require_cold` fails 3
tests, disabling the deferral fails 2. Adjacent suites and exact commands are in the
journal and the final report. Pyright basic: 0 errors on the touched `mc/` modules
(`agent_routes.py` carries 15 pre-existing errors, unchanged; `agent_worktree.py` is
excluded from pyright by `pyproject.toml`).
