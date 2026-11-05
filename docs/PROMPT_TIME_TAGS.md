# User prompt time tags

## Agent reply time tags (2026-11-05, Option A)

Branch candidate `clayrune/agent/3953878b77d0`, based on `5d755fac`.
No merge, push, server restart or live configuration changes.

Dave selected narration-run grouping after the prior investigation established
that render buffers flatten real reply boundaries. A run is contiguous rendered
agent narration (including tables), bounded by a user prompt, tool, card,
date divider or session start. Two back-to-back replies with nothing between
them deliberately share one tag. Diagrams and structured cards keep their own
rendering and separate the surrounding narration. Attribution is transparent.

The tag takes the first valid recorded line timestamp anywhere in the run.
Null, absent, false and invalid timestamps never supply a time. Neither a
session-start fallback nor a clock embedded in text can date an undated reply.
The visible time is local 24-hour HH:MM, with the full local date/time on hover.
It lives in the first nonempty narration element, so later streamed lines do
not move it; when the first valid timestamp arrives later, the tag appears there.

`static/js/agent-time-tag.js` owns timestamp metadata, cold HTML decoration and
incremental DOM tagging. It imports the existing prompt formatter, shares its
muted styling and keeps timestamps out of source buffers. The `<time>` has an
accessible full-date label and CSS-generated visible text, so native copy,
`textContent`, `innerText`, plan extraction and stop-hook draft comparisons
exclude the time. There is no persistent session cache to outlive a rebuilt DOM.

`conversation.js` contains only renderer integration: metadata on narration and
table blocks, the cold-decoration call and incremental stamping. Cold rendering
now flushes buffered narration before a new day divider, preserving live order
and preventing a divider from dating the wrong run. Table body repaints restore
the tag. Existing stop-hook, interim and hidden-plan wrappers retain their
contents and are decorated recursively. No new backend contract or dependency.

### Agent verification

`node tools/smoke/agent-time-tag.mjs` serves this checkout's actual SPA with
hermetic API fixtures and the real SSE handler, at 1440 and 390 pixels in
America/Los_Angeles. It covers cold/replay equivalence; Codex/Qwen buffer
contracts; first-valid, null/invalid and session-anchor cases; adjacent replies;
date/tool/card boundaries; unchanged buffers; search highlighting; native
select/copy/paste; stable streaming tags; rollover full-buffer loading; tagged
draft expand/collapse; hidden plans; Mermaid source preservation; file/image/
browser markers; table repaints; question-card DOM deduplication and boundaries;
overflow and browser exceptions. Codex/Qwen coverage uses their shared frontend
buffer contract, not new provider processes or an authenticated transcript read.
The real `openConversation` reconstruction fetch is also exercised against a
hermetic session-reconstruct response carrying the original per-line timestamps.
There is no copy-message button in this base checkout; the smoke verifies native
copy/paste of a complete tagged bubble and both text extraction properties.

Screenshots, inspected at both widths:
`_scratch/agent_time_tag/chat-1440.png`,
`_scratch/agent_time_tag/chat-390.png`.

| Command | Final rc |
| --- | --- |
| `node tools/smoke/agent-time-tag.mjs` | 0 |
| `node tools/smoke/prompt-time-tag.mjs` | 0 |
| `node tools/smoke/boot-smoke.mjs` | 0 |
| `node tools/smoke/stop-hook-draft-collapse.mjs` | 0 |
| `node tools/smoke/chat-fork-guard.mjs` | 0 |
| `node tools/smoke/codex-resume-render.mjs` | 0 |
| `node tools/smoke/chat-date-dividers.mjs` | 0 |
| `node tools/smoke/codex-preamble-dim.mjs` | 0 |
| `node tools/smoke/agent-stream-dedupe.mjs` | 0 |
| `node tools/smoke/chat-rollover-load-earlier.mjs` | 0 |
| `node tools/smoke/denied-chip.mjs` | 0 |
| `node tools/smoke/chat-text-selection.mjs` | 0 (Chromium + WebKit) |

`node --check` on the new module, conversation.js and the new smoke, plus
`git diff --check`, return 0. The prompt smoke initially failed because its
bounds assertion assumed every `<time>` belonged to a prompt; it now selects
prompt tags explicitly and passes. The expanded agent smoke initially compared
a growing table's entire text to its pre-append text; comparing time metadata
while separately asserting the new row fixes that harness error.

Changed files: `static/js/agent-time-tag.js`, `static/js/conversation.js`,
`static/css/app.css`, `tools/smoke/agent-time-tag.mjs`,
`tools/smoke/prompt-time-tag.mjs`, `docs/PROMPT_TIME_TAGS.md`,
`docs/USER_GUIDE.md`, `CHANGELOG.md`.

USER_GUIDE and CHANGELOG are updated. README, architecture and agent rules need
no change for this display-only behavior. The existing shared clock style stays
in app.css; all new grouping logic is in its own module. Revert this commit to
remove the feature; no data migration is required. Dave owns integration and
live acceptance after a hard reload. The unattended fence refused the requested
global `~/.claude/plans/` write; the plan is recorded here: inspect existing
renderers, implement shared run metadata, verify the rendering matrix, document,
then commit only this branch. No permission or ownership gates changed.

## Earlier user-prompt implementation

Branch candidate based on `ded084e54b6c24d807adf09f1c45eff263deedd1`,
`clayrune/agent/cc916b5c34f1`. No merge, push, server restart or live config change.

`static/js/prompt-time-tag.js` owns the HTML formatter and display-only clock
stripper, exposed through `window`. The shared `escPromptWithImages` renderer
adds a muted `<time>` footer for each timestamped prompt, serving cold render,
SSE append, reconstruction, reconciliation and rollover replay. The title uses
the viewer's full local date/time; the visible text is 24-hour `HH:MM`.

`sendFollowup` and plan approval explicitly supply `new Date()` for optimistic
bubbles. The SSE handler replaces those bubbles with the server timestamp.
Missing, false and invalid timestamps render no tag. `_repaintAgentOutput`
still supplies a session-start fallback to the date divider, but passes a
separate prompt timestamp so it cannot date an undated historical message.
No new dependencies or backend changes.

## Clock display evidence

On base `ded084e5`, the frontend's `stripSysPreamble` has resume, brevity and
phone patterns but no clock pattern. Running the original function on a
buffered `> Ron: [Now 2026-11-05 09:27 PST …]` prompt with its adjacent
`Since your last reply:` list preserves both injected blocks. Exact negative
control and output are in `_scratch/prompt_time_tag/baseline-clock.mjs` and
`baseline-clock.log`.

Normal Claude reconstruction already calls `strip_injected_preamble` in
`mc/blueprints/agent_routes.py` (`_transcript_buffer_lines_and_ts`). Codex and
Qwen transcript extraction also call it in `mc/agent_runtime.py`. Thus this
is a demonstrated frontend leak for stamped buffer input, not a claim that a
real user's normally reconstructed transcript was observed leaking. The new
browser smoke exercises stamped buffered input through cold, replay and SSE
rendering. The injected header and its attached gap list disappear; a standalone
user-written `Since your last reply:` list is preserved. Source buffers are
asserted byte-identical after rendering.

## Verification

Run `node tools/smoke/prompt-time-tag.mjs` from this checkout. It serves the real
SPA with hermetic API fixtures and the actual stream handler, at 1440 and 390
pixels with the browser timezone fixed to America/Los_Angeles. It covers:

- The exact line timestamp, UTC-to-local date rollover and full hover date/time.
- Null, missing and invalid timestamps without borrowing another line's date
  or deriving a time from an embedded clock header.
- Multiple lines, RTL text, literal HTML escaping and clock display stripping.
- Cold and replay renders, legacy session-anchor dividers, unchanged buffers.
- Live SSE with/without timestamps, optimistic send, plan approval and server replacement.
- In-chat search highlighting preserves the prompt's time tag.
- Two day dividers, tag containment, horizontal overflow and page errors.

Screenshots: `_scratch/prompt_time_tag/chat-1440.png` and `chat-390.png`.
The registered matrix runner writes per-command logs and `smoke-results.json`
to the same scratch directory; `smoke-results-extra.json` records the additional
rendering regressions and fork-guard retry. All 32 distinct commands finish
with rc=0. The primary batch exits 1 because it retains the initial fork-guard
failure; the retry batch exits 0.

Three legacy live-API harnesses (fork guard, Codex resume, stop-hook draft
collapse) previously served the server's HTML alongside this checkout's JS.
That omits newly wired modules until the server checkout changes. They now
serve this checkout's HTML too, keeping their live GET API behavior and
non-GET refusal. The fork guard initially failed seven history assertions
with the mixed page; its corrected run passes every assertion. The other two
were updated before running to avoid that same mismatch.

`mobile-dave-chats-live.mjs` is a pre-existing live-data check that explicitly
serves `git show HEAD:static/js/conversation.js`. At execution HEAD was the
base `ded084e5`, so its rc=0 validates that existing live-data invariant, not
the uncommitted feature. The new hermetic smoke exercises this feature's
actual working-tree files at both widths.

`node --check` also returns 0 for prompt-time-tag.js, resume-preview.js,
conversation.js and the new smoke. `git diff --check` returns 0.

## Changed files

- `static/js/prompt-time-tag.js`: new formatter and clock display stripper.
- `static/js/resume-preview.js`: shared prompt renderer and replay timestamp call.
- `static/js/conversation.js`: prompt timestamp call sites and optimistic echoes.
- `static/index.html`: load the new module before resume-preview.
- `static/css/app.css`: muted time footer in the prompt section.
- `tools/smoke/prompt-time-tag.mjs`: new browser regressions and screenshots.
- `tools/smoke/chat-fork-guard.mjs`, `codex-resume-render.mjs`,
  `stop-hook-draft-collapse.mjs`: serve matching checkout HTML.
- `CHANGELOG.md`, `docs/USER_GUIDE.md`, `docs/PROMPT_TIME_TAGS.md`: behavior and handoff.

## Review and rollback

USER_GUIDE and CHANGELOG include the discoverable behavior. No agent-rule,
architecture or install instructions change. Dave reviews and merges; a hard
reload loads the static assets after integration. Revert this commit to remove
the feature; no data migration or rollback is needed.

## Smoke command results

| Command | rc |
| --- | --- |
| `node tools/smoke/boot-smoke.mjs` | 0 |
| `node tools/smoke/chat-date-dividers.mjs` | 0 |
| `node tools/smoke/chat-emoji-picker.mjs` | 0 |
| `node tools/smoke/chat-fork-guard.mjs` | 1 initially; 0 after harness fix |
| `node tools/smoke/chat-list-discovers-new-session.mjs` | 0 |
| `node tools/smoke/chat-rollover-load-earlier.mjs` | 0 |
| `node tools/smoke/chat-search.mjs` | 0 |
| `node tools/smoke/chat-text-selection.mjs` | 0 |
| `node tools/smoke/conversation-persona-filter.mjs` | 0 |
| `node tools/smoke/desk-v1-article-chat.mjs` | 0 |
| `node tools/smoke/desk-v1-campaign-chat.mjs` | 0 |
| `node tools/smoke/desk-v1-chat-popout.mjs` | 0 |
| `node tools/smoke/desk-v1-conversations.mjs` | 0 |
| `node tools/smoke/desk-v1-story-chat.mjs` | 0 |
| `node tools/smoke/mobile-chats-agentlog-fetch.mjs` | 0 |
| `node tools/smoke/mobile-child-chat-composer.mjs` | 0 |
| `node tools/smoke/mobile-dave-chats-live.mjs` | 0 |
| `node tools/smoke/new-conversation-short-viewport-scroll.mjs` | 0 |
| `node tools/smoke/parked-chat-send-dots.mjs` | 0 |
| `node tools/smoke/revived-chat-stop-button.mjs` | 0 |
| `node tools/smoke/split-view-idle-conversation.mjs` | 0 |
| `node tools/smoke/prompt-time-tag.mjs` | 0 |
| `node tools/smoke/agent-stream-dedupe.mjs` | 0 |
| `node tools/smoke/triggered-collapse.mjs` | 0 |
| `node tools/smoke/context-rollover-marker.mjs` | 0 |
| `node tools/smoke/codex-resume-render.mjs` | 0 |
| `node tools/smoke/conv-stale-scheduled-tab.mjs` | 0 |
| `node tools/smoke/agent-log-epoch-reconcile.mjs` | 0 |
| `node tools/smoke/rtl-support.mjs` | 0 |
| `node tools/smoke/stop-hook-draft-collapse.mjs` | 0 |
| `node tools/smoke/denied-chip.mjs` | 0 |
| `node tools/smoke/inline-handler-scope-check.mjs` | 0 |
