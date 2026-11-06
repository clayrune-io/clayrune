# MC-1148 Phase 13: project test-run panel

Branch candidate based on `bb6429e6`. Uses the existing default-off
`test_run_progress_enabled` flag; no configuration change or deployment.

## Contract

`static/js/project-test-runs.js` owns project-panel rendering, availability and
subscription lifecycle. `static/css/project-test-runs.css` owns scoped styles.
`modal-manager.js` attaches on project open and detaches on close; `render-core.js`
only delegates navigation, tab validation and panel HTML. `index.html` adds one
module script line. Existing modal templates receive no new feature body.

`window.ProjectTestRuns` exposes `attach(projectId)`, `detach(projectId)`,
`available(projectId)`, `navigationHTML(projectId, activeTab, menu=false)`,
`panelHTML(projectId, activeTab)` and `refresh(projectId)`. Controller creation
and pure rendering functions are exported for focused tests.

- A project modal has exactly one idempotent `TestRunData.subscribe(pid, null,
  callback)` subscription for its open lifetime. All reads, caching, sequence
  reconciliation, retry/backoff, visibility suspension and polling remain in
  Phase 9's reader. No extra fetch, timer or EventSource is introduced. Closing
  unsubscribes; callbacks from a detached subscription cannot revive the panel.
  Minimized project modals retain their subscription until closed.
- The server flag is not exposed in client configuration. An initial successful
  shared-reader response is required before adding **Test runs** to the project
  menu/tab navigation or loading its stylesheet. A disabled, restricted or
  missing scope removes both navigation and content. An initial network error
  cannot establish availability, so adds no control. After a successful read,
  stale/error snapshots remain visible with explicit retry wording. A global
  flag-off response removes entries from every open project immediately.
- Flag-off navigation and panel helpers return the empty string; insertion
  points add no whitespace. Existing project HTML is byte-identical with the
  module disabled or absent. The module script and discovery GET are the only
  default-off additions; no added visible elements or stylesheet. An open menu
  does not prevent discovery or restriction cleanup: only this module's nodes
  are inserted/removed in place when the normal modal rebuild is deferred.
- The panel groups independent **Active** and **Recent** rows, newest first by
  canonical numeric creation timestamp/run ID. It does not aggregate parent and
  child measurements. Each row identifies the run, optional parent, launch
  source, runner, owner conversation or **Ownerless**, unit, observed counts,
  progress and canonical verdict/exit. Matching names never merge rows.
  Project identity is checked before rendering; run IDs are scoped by project.
- A project with no live figures/sessions can show registered ownerless runs or
  empty history. No process scanning, enrollment, invented session, command
  execution or history mutation is performed. Existing server read visibility
  and restricted-mode policy remain authoritative.
- Known complete test/script measurements show done/total and an accessible
  native progress bar for active positive totals. Zero, unknown, invalid and
  partial totals have no bar. Generic units have unknown outcome counts, never
  inferred passing tests. Finalizing is distinct from process success; all
  canonical terminal states remain explicit, including lost/unknown exits.
  Not-run remainder, collection errors, denominator recalculation, partial
  capture, recent failure names and omitted failure details are labelled.
- Renderer text and attributes escape both quotes and HTML markup; counters
  require nonnegative safe integers. Navigation and refresh are keyboard-usable
  buttons. Ordinary data updates repaint only the panel, keeping an active
  refresh button focused without scrolling the page. Modal rebuilds regenerate
  exactly one panel and one row per canonical run. No chat scroll is changed.

## Limits

The shared reader reads the newest 100 runs per project and retains a bounded
cache (currently up to 300, evicting terminal rows). It can also hold older
session rows loaded by other consumers. This panel shows that cache honestly;
it cannot promise complete history or discovery of older active ownerless runs
beyond the newest page. No pagination, detail endpoint, ETA calculation or
additional poller is added in this phase. Only retained snapshot failures are
shown, with the server's explicit omitted count; there is no load-more control.

Server flag-off is discovered through the existing reader cadence, not pushed.
No claim of four-engine live adoption, process restart survival, packaged/frozen
installation readiness or physical-device/Capacitor behavior is made.

## Verification

All commands run from the worktree root unless noted. The foreground scratch
wrapper starts each command, immediately registers its PID with Clayrune and
returns its actual exit code; equivalent reproducible commands are below.

| Command | Result |
| --- | --- |
| `node --test tools/smoke/project-test-runs.test.mjs` | 10 passed, rc=0. Ownership/project isolation, unknown measurements, all terminal states, escaping, ordering, lifecycle and global disable. |
| `node tools/smoke/project-test-runs.mjs` | 10 cases passed, rc=0. Real worktree SPA/shared reader with hermetic API fixtures. |
| `node tools/smoke/modal-menu-scroll.mjs` | All checks passed, rc=0. Windowed/maximized/mobile menu bounds and resize. |
| `node tools/smoke/mobile-modal-back.mjs` | All checks passed, rc=0. Project and other modal back/minimize behavior and teardown. |
| `node tools/smoke/boot-smoke.mjs` | PASS, rc=0. Seven boot scenarios plus all existing dispatch, backlog, memory, calendar, scheduler, question and Floor guards. |
| `npm test` (in `tools/smoke`) | Full existing script sequence passed, rc=0; includes scope, provider, stream, selection, boot, delivery, paste, image and file-viewer regressions. |

The new browser smoke covers two projects with two owners each, identical test
and failure names and matching cross-project run IDs; ownerless history without
live figures; empty history; malicious data; stale data and recovery; repeated
modal rebuilds; tab reopen; first enabled read while a menu is open; global
disable while a menu is open; subscription teardown; keyboard navigation and
390px overflow. It permits only the exact expected Mermaid CDN-import failure
caused by its deliberate offline routing; all other page errors fail the check.
It never contacts live store/config APIs. Desktop and fresh-open 390px PNGs were
visually inspected under `_scratch/project-test-runs/` (gitignored).

The existing smoke dependencies are junctioned from the main checkout for
verification and removed before commit. No dependency install. User-guide and
changelog entries describe the conditional UI; README/AGENTS/CLAUDE/rules need
no change because there is no new setup, dependency or binding convention.
Dave owns review/integration and live enabled verification. No merge, push,
restart or config write belongs to this phase.
