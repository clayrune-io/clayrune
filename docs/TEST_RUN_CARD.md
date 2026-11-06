# MC-1148 Phase 10: the in-chat test-run card

`static/js/test-run-card.js` (+ `static/css/test-run-card.css`) renders the
`[test-run:<id>]` chat anchor as a live card. It fetches nothing itself: every
word on the card comes from the shared client data layer
(`static/js/test-run-data.js`, `docs/TEST_RUN_DATA.md`), so there is no new
polling, SSE or timer-per-run. Branch candidate only; dark behind the existing
server flag `test_run_progress_enabled` (default off). No config write, merge,
push or restart is part of this phase.

## What the server still has to do

**The server does not emit the `[test-run:<id>]` line yet.** Phase 10 is the
client half (design §7: "inserted once by the server when the launch is
enrolled"). Until a server phase posts that line when it enrolls a run, no card
appears in a real chat. The client path is exercised end to end by the smoke
below with synthetic marker lines.

## Contract

- **Marker.** `^\s*\[test-run:<32 lowercase hex>\]\s*$`, on a line by itself.
  The marker carries only an id. A marker typed into a reply can only surface a
  run the server recorded for this chat's session; any other id reads "This test
  run is no longer available." Runs owned by a different chat are never shown.
- **Hooks (wiring only in existing files).**
  - `conversation.js` cold render (`agentPanelHTML`, next to the agent-read
    branch): `window.testRunCardPlaceholderHTML(id, projectId, sessionId)`.
  - `conversation.js` `appendAgentLine` (live): `window._handleTestRunLine(sid, text, el)`.
  - `index.html` `refreshModalById`: `window.mountTestRunCards(content)`; the
    stylesheet `<link>` and the module `<script>`.
- **No duplicates, decided by the DOM.** A mount is dropped if its chat view
  already holds a mounted twin (`.test-run-card-mount[data-trc-mounted][data-test-run-id]`);
  there is no JS set that outlives a rebuild
  (memory `discovery_askuserquestion_dom_dedup`). Repeating a marker in the log,
  repeated `mountTestRunCards`, and repeated `refreshModalById` rebuilds each leave
  one card per run.
- **Subscription.** One `TestRunData.subscribe(project, session)` per
  (project, session), shared by that chat's cards, dropped when every card is
  terminal or detached (a 15 s sweep covers cards whose DOM was thrown away by a
  rebuild). A terminal run already in the cache is painted without subscribing.
- **Flag off / restricted / missing.** The data layer reports `disabled`,
  `restricted` or `missing`; the card then renders the marker as the ordinary
  `.agent-line` text it was before. With the flag off the server emits no
  marker, so a chat is unchanged; the only static differences are one extra
  stylesheet link and one inert module script.

## What the card shows

| Part | Source / rule |
| --- | --- |
| Title | `display_label` (server-redacted), else `runner`, else "Test run"; clipped to 200 chars. |
| Verdict | `running` Running, `passed` Passed, `failed` Failed, `error` Error, `cancelled` Cancelled, `timeout` Timed out, `no_tests` No tests ran, `lost` Lost: no exit record. Unrecognised state: "Status unknown". |
| Finalizing | `running` with `completed == selected_total` reads **Finalizing**, never Passed: success waits for the process exit the server records. |
| Bar | `<progress>`. Determinate (value + "N%") only when `selected_total > 0`, `capture === 'complete'` and `completed <= selected_total`; otherwise native indeterminate (no `value` attribute), no percent. Hidden once terminal and not determinate. |
| ETA | Only the server's `eta_seconds`, only while running, never after `total_recalculated`, never while finalizing. Nothing is extrapolated client-side. |
| Counts | pass/fail/skip always; error/xfail/xpass when non-zero. fail/error counts are tinted and also carry text. |
| Failures | `<details>`/`<summary>` ("N failures"), keyboard-native. At most 50 names, 300 chars each, "N more not listed here" for the rest. Open by default when ≤ 5; the reader's own open/close choice is kept across rebuilds. |
| Notes | stale/error telemetry, partial or lost capture, total changed, collection errors, not-run count, exit code. |
| Announcement | One `role=status aria-live=polite` line, only when a card that was watched running becomes terminal. |

Every dynamic string enters the DOM through `textContent` / `setAttribute`
(the placeholder's three `data-*` attributes are escaped, and the id must match
the 32-hex pattern or no placeholder is produced).

## Window surface

`testRunMarkerId`, `testRunCardPlaceholderHTML`, `mountTestRunCards`,
`_handleTestRunLine`, and `window.TestRunCards = { cardModel, cardStats }`
(diagnostics and tests; `cardModel` is pure).

## Verification

```text
node --test tools/smoke/test-run-card.test.mjs   # 12 unit cases, no browser
node tools/smoke/test-run-card.mjs               # real Chromium, real index.html + conversation.js, stubbed read API
node tools/smoke/boot-smoke.mjs                  # SPA still boots with the module loaded
node tools/smoke/test-run-data.mjs && node --test tools/smoke/test-run-data.test.mjs
node tools/smoke/test-run-floor.mjs              # neighbouring phases still green
```

`test-run-card.mjs` covers: live marker lines (one card each, raw marker hidden),
unknown total (indeterminate, no percent, no ETA), known total, Finalizing,
terminal verdicts, keyboard toggle of the failure list, hostile label/name/phase
(no element injected, no handler run, shown literally), foreign-session and
unknown ids, cold render through the real `agentPanelHTML`, three
`refreshModalById` rebuilds, tab switch away and back, repeated mounts, a
running card turning Passed from the poll with no rebuild, subscription and
sweep-timer teardown, no stream opened, and flag off (bare 404 → plain line).

## Not verified / not in this phase

- No real server run: the marker line is not emitted yet (see above), so a card
  has never been driven by a live agent turn.
- Screen-reader output was not listened to; the semantics (`progress` with
  `aria-valuetext`, `details/summary`, one polite live region, an `aria-label`
  carrying the verdict) are asserted, not heard.
- The card does not force-scroll: it mounts into the line's own slot and the
  existing `_scheduleAgentPinScroll` path keeps its current pin rules. A card
  growing taller after a poll (failure list opening) is not separately pinned.
- Mobile layout was checked structurally only (card is a block that wraps); no
  device pass.
- `tools/smoke/agent-read-request-ui.mjs` (part B, Desk Connections) and
  `tools/smoke/agent-time-tag.mjs` fail identically on the unmodified base
  `bb6429e6`; both are outside this change.
