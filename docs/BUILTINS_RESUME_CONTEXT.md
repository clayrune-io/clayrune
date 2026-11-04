# Current instructions on resume (BUILTINS-2)

Codex, Gemini and Qwen send the current Clayrune persona and rules on cold
revival. Warm native followups re-send context only when it changed or no
delivery fingerprint is recorded; unchanged followups send the new message
alone. Native thread IDs and full vendor history are retained. A separate
reference envelope identifies current instructions when a refresh is needed;
the new user message follows an explicit boundary. This replaces saved older
Clayrune instructions through the same user-prompt channel those runtimes
already use for context injection. It does not change vendor instruction
hierarchy or erase old messages from the transcript.

The envelope boundary is derived from context only. Quoted headings and ordinary
Markdown separators in persona/task text do not become display boundaries. Saved Codex
and Qwen messages peel that envelope exactly once before legacy display cleanup.
Codex's legacy prefix cleanup requires recognizable Clayrune context, preserving
Markdown separators in bare user messages.

The session's `_ctx_delivered_sha` is SHA-256 of the final composed context,
including the tool protocol and Gemini's slimming. Fresh and cold-resume
dispatches record it after successful stdin write/close; changed-context warm
resumes update it the same way. A failed write logs the exception type and
leaves the fingerprint unchanged so the next followup can retry. The stamp
tracks pipe delivery, not vendor acknowledgement or model compliance.

After a server restart, a session with no delivery fingerprint sends the
current-context envelope once, then returns to bare messages until context
changes. It does not infer delivery from an older vendor transcript. Gemini
removes its exact appended protocol before slimming and appends it once again,
so repeatedly composing unchanged context produces the same fingerprint.

Both non-Claude followup and interrupt routes now gate `_fresh_context_for`
on the full `_context_fingerprint(project)` versus `_system_prompt_fp`, matching
Claude's resume staleness rule. Dispatch records the build fingerprint;
a missing stash/fingerprint or changed project inputs rebuilds and re-stamps
it. Fingerprint errors reuse an existing stash, and failed rebuilds retain the
old fingerprint so a later turn retries. The original runtime-only fix was
insufficient: unconditional route rebuilds included task-ranked sections and
recent activity, changing the context string on each turn even without edits.

The project fingerprint includes the entire Markdown memory corpus, rules,
skill/agent listings and its existing config/development-harness inputs.
Character files in both global and project scopes are stat-fingerprinted,
including nested packs supported by character lookup. The memory directory
scan matches the corpus reader's `*.md` inputs: JSON/SQLite retrieval telemetry
is output and does not invalidate the stash. In particular, each search writes
`delivery_stats.json`; treating it as setup would defeat the gate again.
No rendered-context section is removed from the final delivery SHA.

`mc/native_followup_context.py` owns the gate and per-message assembly.
Task-scoped memory now reaches non-Claude followups and interrupts through
`mc/memory_turn.py::refresh_for_turn`, prepended to the message rather than
changing the setup stash. Successful context builds seed the existing memory
delivery ledger; later matching notes are suppressed, while standing positions
retain their per-turn delivery policy. Incognito skips retrieval. Recent
activity/conversation/exploration/sibling sections remain snapshots until the
stash is legitimately rebuilt, as with Claude's cached setup.

`mc/resume_context.py` owns the envelope, fingerprint policy, delivery stamping
and display stripping. Empty context stays empty; incognito retains only the
existing tool protocol. Without a native thread ID, fallback still replays
context regardless of the fingerprint because a new thread has no history.

Claude's `mc/launch_marker.py::launch_cmd` applies the separate
`mc/claude_resume_snapshot.py` policy before the unattended fence gate. The
editable config key `claude_resume_fresh_prompt` defaults to **false**. When
true, `-r`/`--resume` and `-c`/`--continue` launches receive
`--system-prompt-snapshot off`, including attended launches and fence-disabled
launches. Fresh dispatches retain vendor defaults. The option requires a CLI
that supports it (introduced in 2.1.267; installed help verified 2026-10-31).
An unsupported older CLI will report its own argument error; the setting is
opt-in and does not silently downgrade. Existing fence settings and tests are
still applied for unattended launches.

Installed `claude --help` describes the default snapshot as recording the
initial prompt and replaying it on later requests/resumes even when the flags
change; `off` renders it afresh. Clayrune's existing fingerprint invalidation
of its prompt stash is distinct from this vendor snapshot layer.

Other engines' existing delivery paths: OpenCode prepends context even on cold
native resume and composes context on warm followup; Goose warm followup
composes context, but dispatch ignores native resume_id (native continuity is
not claimed); Aider sends a fresh read-context file and Kiro composes context
on each stateless turn. No paid canaries were run for any engine.

## Cache-cost measurement method (not run)

Use paired new Claude conversations on the same pinned model/CLI version,
one snapshot on and one off. Disable tools, use an identical stable prefix
long enough for cache eligibility, and deterministic short prompts. Compare
initial turn, unchanged-context resume, changed-persona/rules resume, then
another unchanged resume. Reverse treatment order and repeat multiple pairs.

Record per-request input tokens, cache-creation tokens, cache-read tokens,
output tokens, incremental cost, latency and changed-persona canary compliance.
Do not compare resumed cumulative totals as if they were per-turn cost. Keep
anonymized raw JSONL under `_scratch/`. Compare cache hit ratios and incremental
cost separately for changed and unchanged prompts. Dave explicitly prohibited
running this paid measurement in this task; the default remains off.

## Verification

`tests/test_builtins_resume_context.py` captures stdin at actual runtime spawn
boundaries without vendor calls: changed persona/rules for all six cold/warm
paths, two unchanged followups after fresh/cold dispatch, rules changes followed
by bare messages, missing-fingerprint restart, failed-write retries, incognito
and no-native-ID fallback. It also covers retained thread IDs, envelope display
handling, snapshot default-off, resume flag variants, idempotence and the shared
fence boundary. Existing
fake-Codex subprocess/rollout end-to-end coverage verifies one conversation
with real message labels. Delivery is verified; model obedience and cache cost
are unmeasured. No merge, push or server restart is part of this branch work.

`tests/test_native_followup_context_routes.py` additionally drives real Flask
followup and interrupt routes, `_fresh_context_for`/`_build_agent_context`, the
project fingerprint, retrieval and all three native runtime writers. Different
messages and changing recent activity leave setup stable. Rules and persona
body edits send one envelope, then bare messages. It covers initial dispatch
priming, topic changes delivering new memory through the message, incognito,
fingerprint errors and rebuild retries. Provider subprocess writes use captured
fake pipes; context construction, fingerprints and retrieval remain real.
Unrelated route side effects are stubbed, and the route does not receive a
constant test `_system_prompt`.

Second-review verification (2026-10-31): real-route/freshness subset **64
passed**, rc=0; the previous 18-file selection **655 passed, 1 skipped,
1 xfailed**, rc=0; supplementary memory/dispatch checks **199 passed**, rc=0.
The final combined 31-file selection **854 passed, 1 skipped, 1 xfailed**,
rc=0. This includes the original resume/rollout tests; the first review's
runtime-only tests did not establish route-level stability.

Pyright on native_followup_context, resume_context, claude_resume_snapshot,
retry_budgets, retry_capabilities, qwen_retry_wait, agent_runtime and
launch_marker: **0 errors**, rc=0. Including agent_routes reports **15 errors,
rc=1**; a `de8f1510` snapshot independently reports the same 15 diagnostics
with identical rules, messages and source statements. No new route diagnostics
were introduced, and that unrelated typing debt remains. No frontend changes
were needed for this correction.
