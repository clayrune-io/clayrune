# Cold reviewer on agent handoff (MC-1165)

Status: **partly built, three design choices open.** Branch candidate only. Nothing
here dispatches an agent, calls a model, records a verdict, or changes `merge_back`.
Nothing is on for any project: the setting is off unless it is boolean `true`.

## What exists (built on, not duplicated)

| Piece | Where | Role here |
|---|---|---|
| Verdict store, keyed `branch@sha` | `mc/merge_review_gate.py` | The only review store. A cold verdict goes here. |
| Hold in the auto-merge path | `agent_worktree._merge_back_locked` returns `awaiting_review` | No pass for the exact tip = nothing lands. Already fails closed. |
| Record route (attributed, lineage-checked) | `mc/blueprints/merge_review_routes.py`, `mc/review_lineage.py` | A reviewer *session* records `pass` / `changes_requested`. |
| Reviewer callback seam | `landing_gate.verify(..., reviewer=cb)` | Only `{"status": "pass"}` allows; raise/None/other = block. |
| Human-only settings | `mc/project_safety_settings.py` | `cold_review_required` added next to `verify_command`. |

## What this change adds

`mc/cold_review.py` (stdlib + `mc.dev_harness_diff`), tests `tests/test_cold_review.py`.

* `SETTING = 'cold_review_required'`, `enabled()`: strict `is True`. Added to
  `PROJECT_SAFETY_KEYS`, so an agent caller is refused the whole write, same as
  `verify_command`.
* `build_packet(repo, base, tip, ask)`: the reviewer's entire input, read from git
  (merge-base..tip, so what landing would add) plus the item ask.
* `render_prompt(packet)`: prints exactly `PACKET_KEYS`; the diff and ask sit
  between a per-render random boundary the diff cannot forge.
* `parse_verdict(reply)`: one JSON object, `verdict` exactly `pass` or
  `changes_requested`. Prose, two objects, a repeated `verdict` key, unknown
  values, a non-string summary: all `hold`. Never raises.

### How "cold" is enforced

By construction, in this module: `build_packet` has no parameter for a transcript,
journal or final message; `render_prompt` refuses a packet with any other key
(test: `test_prompt_prints_only_packet_fields_and_a_fresh_boundary`). The diff is
read from git, not from anything the builder wrote. A missing ask, an empty diff,
a diff over 256 KiB (refused, never truncated: a half-read diff is an unreviewed
diff) and an unreadable ref all raise `ColdReviewError` = no pass.

This holds for a **toolless** reviewer. It does **not** hold for a reviewer with
tools (see choice 1): the packet is then only its first message.

## Open design choices (stopped here, per the brief)

**1. Which agent reviews.**

* **A. Toolless one-shot** (`ClaudeRuntime.oneshot`, the Scribe/Distiller sandbox: no
  tools, no MCP, neutral cwd). Cold is real: it can see only the prompt. Cost: one
  model call, up to ~65k input tokens at the 256 KiB cap, <= 180 s default timeout.
  Weaker review (diff only, cannot open surrounding code). It is not a session, so it
  cannot use the record route (which refuses unattributed callers by design); the
  server would write the record itself through `merge_review_gate.record` with an
  empty `reviewer_session` and a cold marker. That is a new server-side record path
  and is the one place the "no human path" route rule is not reused.
* **B. Fenn session** (`global:code-reviewer` dispatched as a new agent). Better
  review (reads the repo, runs tests). Records through the existing route with its
  existing attribution and `review_lineage` checks. Cold is **by instruction and
  lineage only**: it has tools, so it can read `docs/_journal/`, the agent log
  under `data/`, and the builder's worktree. Cost: a full agent session per
  handoff (minutes; tens to hundreds of thousands of tokens on a premium model).

  *Recommendation: A for the gate*, because the brief requires cold to be enforced
  and only A can; keep Fenn-by-hand for judgment reviews. Residual either way: the
  diff is untrusted text, so an injected "approve" can forge a `pass`; fail-closed
  covers absence and error, not a manipulated verdict.

**2. Sync vs async verdict.**

* **Sync, inside landing:** pass `reviewer=cb` to `landing_gate.verify`. Simple, one
  place. But it runs under the repository-wide landing lock, after the verify
  commands, and holds that lock for the whole model call. Workable only with A.
* **Async:** on handoff, start the review, return `awaiting_review` (existing), land
  when a verdict for the tip exists. Needs something to call `merge_back` again when
  the verdict arrives; today session end calls it once and a later pass is noticed
  only by a manual retry or `gc_stale`.
* **Pre-lock sync (A only):** run the one-shot in the session-end handler *before*
  `merge_back`, record the verdict, and let `merge_back` find it via the existing
  hold. No lock held, no new retrigger. Blocks that handler for up to the timeout;
  I did not check what else waits on it.

  *Recommendation: pre-lock sync with A.* Smallest surface that works; revisit async
  only if B is chosen.

**3. How a block is re-tried.** A verdict is per tip SHA, so a new commit is a new
review with no extra mechanism, and `changes_requested` already sticks to its commit
(`merge_review_gate.rejected_by`). The open part is `hold` (error, timeout, bad
reply): (i) retry at the next handoff only; (ii) one automatic retry then a human;
(iii) a human route/button. *Recommendation: (ii), cap 2 attempts per tip SHA*, so a
broken reviewer costs two calls, not an unbounded loop, and the state is visible.

**4. Where "the item ask" comes from (a gap, not a choice).** No session-to-backlog-item
link exists in the code (grep of `agent_routes.py`, `dispatch_lineage.py`,
`dev_harness_landing.py` finds none). Options: record `backlog_item_id` at dispatch
(new field, honest, needs the dispatcher to pass it); parse `Backlog <id>` out of the
task text (fragile); or use the dispatched task text itself as the ask (exists today,
is what the builder was told, is not its transcript). *Recommendation: task text now,
explicit `backlog_item_id` as a follow-up.*

## Wiring that is left (depends on 1-3)

* `merge_review_gate.hold_reason` must require a **cold** record when
  `cold_review_required` is on, otherwise a hand-recorded `pass` satisfies it. That
  needs a cold marker on the record (small change to `record()`).
* The dispatch/one-shot call, the server-side record path (A) and the retry cap.
* Session-end trigger in `_worktree_merge_back_on_end`.

## Verification

`tests/test_cold_review.py` (29 checks, temporary repos, no model, no dispatch).
Adjacent: `test_project_safety_settings.py`, `test_merge_review_gate.py`,
`test_landing_gate.py`. Pyright basic on `mc/cold_review.py`: 0 errors.
