# Continuity rollover repair (2026-10-29)

The rollover state used to repeat the chat's first message as the original
task. Scribe omitted human text, and continuity read the oldest 12,000
characters of each delta. A later user correction could therefore disappear
while an older assistant inference was repeatedly promoted into working state.

## Contract

- `mc/midturn_rollover.py`: unanswered user messages remain first; the latest
  user message is labelled current direction, including when it equals the
  first message. Missing latest text says unavailable rather than inventing a
  current task. The first message remains verbatim but explicitly historical.
  Git output is bounded per invocation by LINES, not characters (follow-up,
  2026-10-29): 30 lines of at most 160 characters each; when the line cap
  bites, the first 29 lines, an `[... truncated: N line(s) omitted]` marker
  and the LAST line (the `N files changed` summary of `diff --stat`) are kept.
  The earlier 1,000-character cap bound at about 14 stat rows, so later files
  and the summary vanished.
- `mc/memory.py`: Scribe emits USER for strings and text blocks, capped at 600
  characters per turn after the existing runtime injection filters. Synthetic,
  metadata and compact-summary turns cannot produce USER. Tool results retain
  RESULT and image data is omitted. Malformed/non-object JSON rows are skipped.
- `mc/scribe_user_turn.py` (follow-up, 2026-10-29): the USER text is cut at the
  FIRST occurrence anywhere of `=== Mid-task rollover state ===`,
  `=== Prior conversation, started on` or `[dispatched agent finished]`; what
  precedes the marker is kept, then the leading-injection strip runs again.
  The anchored strip alone left the state block in 74 of 713 recent USER turns
  (73 with the `Original task` line), 2 full handoff replays and 202 callbacks,
  all arriving under the label the extractor is told outranks ASSISTANT.
- The extractor takes `delta[-12000:]`. Its model response adds optional
  `confirmed_threads`: exact thread strings supported by the new slice. Merely
  retaining a thread does not refresh it; retained wording should remain stable.
  User corrections take precedence over older assistant claims.
- `mc/continuity_threads.py` owns timestamp parsing, serialization and age
  labels. Thread bullets store `<!-- confirmed:ISO -->`; public thread strings
  stay plain text and `thread_confirmed` supplies their timestamps separately.
  New threads and explicitly supplied direct writes are stamped at write time.
  Existing extracted threads refresh only when included in `confirmed_threads`.
  Updates that omit threads and legacy shared-to-owner claims preserve age.
- At 48 hours without confirmation, prompt rendering flags the thread
  `unconfirmed since ... (stale; verify before acting)`. Legacy undated,
  malformed and future stamps are also flagged. Flags apply to own, shared,
  merged and other-agent views. Threads remain stored; this is not deletion.
- Successful atomic writes log `[continuity] wrote owner=... threads=N
  updated=...`. Failed writes do not emit that success line. Logs contain
  metadata, not transcript text.

Per-source claim verification and owner bucket re-keying remain out of scope.
Confirmation is still a model interpretation of the slice, not independent
verification of external state. No live server or saved continuity was changed.

## Verification

- Final focused run: `test_continuity_regression.py`, `test_continuity.py`,
  `test_continuity_owner.py`, `test_midturn_rollover.py`: 112 passed, rc=0.
- Broad selection: all `tests/test_memory*.py`, `tests/test_*rollover*.py`,
  `tests/test_continuity*.py`, `tests/test_scribe*.py` and
  `tests/test_effort_continuity.py`: 726 passed, 3 skipped, 1 failed; rc=1.
- The failing
  `test_codex_transcript_path_lookup_failure_falls_back_cleanly` also fails
  against unchanged HEAD's memory module: 1 failed, rc=1. It replaces
  `get_runtime` with a raiser; the existing automatic background-model policy
  calls it again outside the transcript-lookup exception handler. No unrelated
  provider fallback change is included here.
- Basic pyright on the timestamp and rollover modules: 0 errors, rc=0.
  Including memory reports 2 errors, rc=1, reproduced at the corresponding
  canonical-reader calls in unchanged HEAD (2 errors, rc=1).
- New regression cases exercise the human correction reaching extraction,
  stale age retention, explicit reconfirmation, legacy claims, the 48-hour
  boundary, malformed/future stamps, failed-write logging and noisy git output.

No merge, push, restart or live multi-roll retry is part of this branch.
Rollback after integration is a normal revert of this repair commit.
