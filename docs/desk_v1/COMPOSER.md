# Desk composer: one screen to make a post

Decided 2026-10-30 (Ron chose the mockup). Evidence: `FIELD_SCAN_COMPOSER_UX.md`
(9 tools, 4 flows, cut list). Mockup: `_scratch/composer_mockup/composer.png`
(gitignored, on the main checkout). Position recorded: supersedes the 2026-09-30
"Where owns channel placement" call.

## What the screen is

One page, opened straight from the Post tile (no add-piece modal, no Name field,
no "Write new / Reuse" fork):

- **Top bar:** breadcrumb (Desk > campaign > New post), Save draft, When, Send to Review.
- **Post to:** account avatars, preselected from the campaign's accounts.
  Click to tick or untick. "Customize per channel" gives one channel its own text.
- **Write:** one text box, per-channel length counter (X 280).
- **Media:** drop or paste anywhere on the composer, or one "Add media" picker with
  two tabs, Upload (default) and Library (thumbnails, recent first). Every upload
  lands in the library. Any video the browser can play is accepted.
- **Preview:** side panel, one tab per ticked channel, live as you type, shows the
  "See more" cut line and the X length warning. Reuses the Where hover-preview
  module (Tilda, `eaa9e78a932b`).

Nothing publishes from here: Send to Review hands the post to the existing Review
stop. Every human-only gate stays as it is.

## Rules that bind the build

- **The piece is created on first content**, not on tile click. Empty "New post"
  drafts stop piling up.
- **Channel ticks write per-piece accounts; the campaign's `plan.accounts` stays the
  default set and the approval bound.** A composer may not tick an account outside
  the campaign's bound without the same approval Where requires today.
- **When in the composer edits the same schedule field the When calendar edits.**
  The When stop remains the campaign-wide time view.
- **Where becomes a read-only overview** (which posts go to which accounts). Drag
  to place is removed only after the composer ships and the smokes pass.
- **One unit, one file** (AGENT_RULES): `desk-v1-composer.js` (shell),
  `desk-v1-composer-channels.js`, `desk-v1-composer-media.js`,
  `desk-v1-composer-preview.js`. Do not grow `desk-v1-what.js` or `desk-v1-piece.js`.

## Campaign stops after the composer (Dave, 2026-10-30)

Today: Brief, Goal, What, Where, When, Launch. Where does two jobs: it sets the
campaign's account set (`plan.accounts`, the approval bound; Launch's missing-item
list points at it) and places each post on accounts. The composer takes the
second job, so the stops become:

**Brief, Goal, Channels, Posts, When, Launch.**

- **Channels** (was Where, moved before What): pick the accounts this campaign
  may post to, once. Shows per account how many posts go there (the read-only
  overview). No drag-to-place.
- **Posts** (was What): the list of posts as cards; New post and any card open the
  composer. Its avatars default to the Channels set.
- **When** and **Launch** unchanged.

Why Channels before Posts: the composer preselects from it, and an empty set is
what makes a composer show nothing to tick.

## Build order (one builder, sequential)

| # | Slice | Depends on |
|---|---|---|
| C1 | Composer shell: Post tile opens it, text, channels preselected, Save draft, When, Send to Review, create-on-first-content | Sol_Tobin batch 1 + Tilda Where merged |
| C2 | Media: drop/paste + one Add media picker (Upload, Library); retire the other three entry points for Post | C1 |
| C3 | Live preview panel per ticked channel, reusing the Where preview module | C1 |
| C4 | ⋯ Duplicate on any post card: copies text and media, opens the copy in the composer; "Duplicate to campaign…" second | C1 |
| C5 | Where to read-only overview; remove drag-to-place | C1-C3 shipped, smokes green |
| C6 | Campaign stops become Brief, Goal, Channels, Posts, When, Launch: rename Where to Channels and move it before Posts (was What); Launch's missing-item links follow | C5 |

Each slice: its own commit(s), smokes for what it touched, screenshots at 1440 and
390 wide, a short report under this file's `## Reports`.

## Reports

### C1 — Sol_Tobin — 2026-10-30 (branch candidate)

Base: `_trial_merge_dave` fast-forwarded to `74b03a5d`, then spec `7b1fbf8f`
merged as `8f508234`. Work stays on `clayrune/agent/d49def5ca015`; no merge
to master, push, restart or live Desk mutation.

Delivered the shell, text and destination flow in `desk-v1-composer.js`,
`desk-v1-composer-channels.js`, `desk-v1-composer-when.js` and a separate
stylesheet. What and Campaign each gain one opening hook; Shell/Index register
the route and modules. Post click, keyboard activation and tray drop share the
opening hook. The first nonblank text creates the piece; opening/closing an
empty composer creates nothing. Subsequent text uses Save draft. Initial titles
keep the existing `New post` default, with no Name field or source fork.

Campaign accounts are preselected, and ticks create/archive per-piece versions
without editing `plan.accounts`. Only bounded accounts are selectable. Saves
share the existing per-piece queue; a failed create retains typing, and partial
destination failures retry only missing destinations. When shows individual
destination times and uses the calendar's own command, `scheduled_at` and
approval-day confirmation. That command now returns its write promise so the
composer can report a refusal after persistence. Send to Review saves first,
sets editable selected versions to `needs_review`, sets `draft_status=in_review`
and opens the existing Review screen. Approved copy is read-only until returned
to review; archived/sent editing remains constrained. No approval/publish
endpoint is called. Demo makes no mutation requests.

No C2–C6 implementation: media, per-channel customization, preview, duplicate,
Where drag removal and campaign-stop rename/reorder remain outside this slice.
The character counter counts Unicode code points; X's platform-weighted URL
counting remains unimplemented. Real platform posting was not exercised.
The old generic piece route is retained for direct links. Legacy piece/media
smokes explicitly test that route after checking that post cards open the
composer. The old post source-popup smoke expectations were replaced by the
composer contract; article/image/video popup and media checks remain.

Validation from this worktree (final runs):

| Exact command | rc | Result |
|---|---:|---|
| `node tools/smoke/desk-v1-composer.mjs` | 0 | 1440/390; empty/whitespace creates nothing; one create under typing races; text/create/partial-version retries; bounded ticks; time persistence/refusal/cancel/approval withdrawal; Review; reload; no horizontal overflow; demo |
| `node tools/smoke/desk-v1-what-add-modal.mjs` | 0 | Real temporary Flask/filesystem store: article imports/refusals and composer create/Save draft/reload at both widths; empty campaign bound disables Review |
| `node tools/smoke/desk-v1-live-pieces.mjs` | 0 | Existing create/rename/delete, multi-asset, writer, refusal and demo contracts |
| `node tools/smoke/desk-v1-piece.mjs` | 0 | Generic piece/direct-link compatibility, calendar Undo, article/video routes; initial old post-card expectation failed (rc 1), then updated |
| `node tools/smoke/desk-v1-piece-media.mjs` | 0 | Existing What and generic-piece media controls, removal/refusal/Undo, mobile and demo |
| `node tools/smoke/desk-v1-live-calendar.mjs` | 0 | Existing scheduling, same-day approval, cross-day withdrawal, refusal, sent and demo contracts |
| `node tools/smoke/desk-v1-calendar.mjs` | 0 | Calendar themes, slots, scheduling, Undo and responsive layouts |
| `node tools/smoke/desk-v1-campaign-content.mjs` | 0 | Isolated real connector placement, archive/restore/delete Undo and inline article media at both widths |
| `npm test` (cwd `tools/smoke`) | 0 | Full npm suite, including boot smoke |
| `node tools/smoke/boot-smoke.mjs` | 0 | Explicit final boot recheck after the last module edits |
| `python -m pytest tests/test_desk_pieces.py -q` | 0 | 99 backend piece-store tests; no `mc/` code changes |
| `node --check static/js/desk-v1-composer.js` | 0 | Syntax |
| `node --check static/js/desk-v1-composer-channels.js` | 0 | Syntax |
| `node --check static/js/desk-v1-composer-when.js` | 0 | Syntax |
| `git diff --check` | 0 | Whitespace |

The initial optional command `python -m pytest tests/test_desk_pieces.py
tests/test_desk_when.py -q` exited 1 because `test_desk_when.py` does not exist;
it ran no tests. The actual piece suite above and both calendar smokes passed.
The initial new composer smoke failed because `crypto.randomUUID` is absent on
an insecure LAN/test origin; non-secret client IDs now follow the existing
Date/random convention, and the final smoke passes on that origin.

Screenshots, visually inspected, are in this worktree:
`_scratch/composer_c1/composer-1440.png` and
`_scratch/composer_c1/composer-390.png`. The dependency junction to the main
checkout's `tools/smoke/node_modules` is removed before commit. CHANGELOG and
USER_GUIDE are updated; no agent-rule or architecture-rule changes are needed.

The session fence blocked writing the home `.claude/plans` file; the execution
plan was shell/channels/scheduling modules, opening hooks, browser contracts,
screenshots, documentation and a branch-only commit. It also blocked restoring
the two tracked screenshots regenerated by the existing calendar smoke. Those
two PNG changes are deliberately unstaged. Spawner cleanup, in this worktree:

```powershell
git restore -- docs/desk_v1/screens/t4_calendar_desktop_1440.png docs/desk_v1/screens/t4_calendar_phone_390.png
```

No open product choice is required for C1. Integration/release belongs to Dave.

### C3 — Sol_Tobin — 2026-10-30 (branch candidate)

Required C1 base `clayrune/agent/d49def5ca015` merged with
`git merge --no-edit clayrune/agent/d49def5ca015` (rc 0). Work stays on
`clayrune/agent/f9c7d01b359c`; no master merge, push, restart or live Desk writes.

Added `static/js/desk-v1-composer-preview.js` and its own stylesheet. The shell
has exactly one import and one mount line. The module mounts a desktop side
panel; at <=960px Show preview/Hide preview opens it below the full-width editor.
Every ticked campaign account gets a tab, with arrow/Home/End navigation and one
keyboard tab stop. Unticking the active account selects the first remaining
account; an empty set explains how to get a preview. Unsaved input updates without
saving. Image additions/removals refresh through the draft and sibling DOM changes.

Where's `DeskV1PostPreview` gains an inline `render(fam, version, channel, options)`
API; the existing skins, content projection, icons and media helpers are reused.
An opt-in cut marker is shown only when the shared skin's copy clamp actually
overflows. Literal composer text stays escaped instead of being parsed as article
HTML. Where's popup/modal behavior is unchanged and its smoke passes. No new
dependencies, backend code, approval calls or publishing paths.

Limits: feed cut positions remain the shared skins' approximate line clamps,
explicitly labelled in the panel. X uses C1's Unicode code-point counter, not
platform-weighted URLs. C2 media integration is exercised through its draft/DOM
contract, not the sibling's not-yet-merged implementation. Real platform feeds
and publishing were not exercised. No open product decision is required.

Validation from the repository root, with
`$env:MC_PYTHON = Join-Path (Split-Path (git rev-parse --path-format=absolute --git-common-dir)) '.venv/Scripts/python.exe'`.
Main checkout's `tools/smoke/node_modules` was linked for these runs and the
junction removed before commit; nothing was installed.

| Exact command | rc | Result |
|---|---:|---|
| `node tools/smoke/desk-v1-composer-preview.mjs` | 0 | 1440/390: unsaved text, literal markup, selected-account tabs and keyboard, overflow-only cut line, X 280/281 and emoji boundary, media add/remove, empty selection, no horizontal overflow or mobile editor squeeze |
| `node tools/smoke/desk-v1-composer.mjs` | 0 | Existing C1 create/race, refusal/retry, bounded ticks, scheduling, Review, reload and demo contracts |
| `node tools/smoke/desk-v1-post-preview.mjs` | 0 | Existing Where desktop hover/modal/drag and mobile tap contracts |
| `node tools/smoke/boot-smoke.mjs` | 0 | Seven boot scenarios and all cross-module guards |
| `git diff --check` | 0 | Whitespace |

Initial C3 smoke iterations exited 1 on harness issues: PowerShell stdin changed
the emoji fixture to two question marks; the media selector also matched the
platform logo; a wait expected Saved without pressing Save draft; and a fixture
search assumed every family had a body. The final smoke fixes those assumptions
and passes. These failures did not establish product defects.

Screenshots visually inspected in this worktree:
`_scratch/composer_c3/composer-1440.png`,
`_scratch/composer_c3/composer-390.png` (preview open), and
`_scratch/composer_c3/composer-390-editor.png` (preview collapsed).
Execution plan saved to the home `.claude/plans/desk-composer-c3.md`.
CHANGELOG and USER_GUIDE updated; no agent-rule or architectural-rule changes
are needed. Dave owns sibling integration and release verification.

### C4 — Sol_Tobin — 2026-10-30 (branch candidate)

Base merged exactly as dispatched: `git merge --no-edit
clayrune/agent/d49def5ca015` (rc 0), producing `0eff82e7` on
`clayrune/agent/80723533f165`. C1 is `e6ecc1e0`. No master merge, push,
server restart or live Desk mutation.

Delivered `static/js/desk-v1-composer-duplicate.js`,
`mc/desk_piece_duplicate.py` and its own nested blueprint
`mc/blueprints/desk_piece_duplicate_routes.py`. Composer has one import;
Campaign has one menu-mount call, and desk_routes has import/registration
only. What, Piece and the sibling media/preview slices are untouched.

Post-card Duplicate now saves text and all media references atomically and
opens the copy in the composer. Duplicate to campaign… lists other
non-archived campaigns with their project names. New drafts keep source
destinations only where they intersect the target plan.accounts and connected
workspace accounts; inactive source versions are excluded. An empty
intersection produces a saved draft with no destinations selected, never a
widened campaign bound. Per-channel saved text and format are retained.
The route is `POST /api/desk/pieces/<id>/duplicate` with optional `id` (new
piece ID) and `campaign_id` (defaults to the source campaign); it returns the
persisted v1 piece with HTTP 201. Unknown request fields are refused, so a
caller cannot inject schedule, account or approval state through the route.
The copy uses the target project, new piece/attachment/version IDs and a
`(copy)` title suffix. No schedule, slot, approval, receipt, failure, archive,
manual outcome, claim verdict or revision is inherited. Claim content and
media provenance remain; claim decisions start fresh. Media stay library
references, following the existing store contract. Missing/outside-root media
refuses the entire operation before persistence. Demo makes no mutation
request. Refusal rolls back the optimistic copy, double activation is guarded,
and Undo deletes only the copy through the existing writable-piece route.

The picker reuses existing menu styling and placePopover, with its own small
event lifecycle. It preserves the card corner's position and consumes Escape
before it reaches the Desk shell. This avoids changing the shared picker or
its callers. Keyboard arrows move between campaigns; Escape and outside click
dismiss, with Escape restoring focus to the card trigger.

Validation, from repository root. Smoke environment: `MC_PYTHON` set to the
main checkout's `.venv/Scripts/python.exe` (machine path kept out of source).
The new smoke registers itself and its temporary Flask fixture with Process
Manager and shuts down only its own fixture. Its store/uploads live in an
OS temporary directory, never the operator's Desk.

| Exact command | rc | Result |
|---|---:|---|
| `python -m pytest tests/test_desk_piece_duplicate.py tests/test_desk_pieces.py -q` | 0 | 114 tests (15 new): same/cross campaign, persisted text/media, fresh lifecycle/claims, independent edits, no-bound/disconnected/inactive versions, unavailable/unsafe media atomic refusal, input validation, archived/sent source, unsupported kind/unknown source and archived target |
| `pyright mc/desk_piece_duplicate.py mc/blueprints/desk_piece_duplicate_routes.py` | 0 | 0 errors/warnings |
| `node tools/smoke/desk-v1-composer-duplicate.mjs` | 0 | Real Flask/filesystem + real SPA, 1440/390: same/cross campaign, media and text reload/edit, target bounds, empty bound, Undo, double activation, refusal/retry, picker geometry/cancel and demo no-write |
| `node tools/smoke/desk-v1-composer.mjs` | 0 | Existing C1 contract at 1440/390 and demo |
| `node tools/smoke/boot-smoke.mjs` | 0 | Seven boot scenarios and all dispatch, migration, model, identity, backlog, memory, calendar, scheduler, question and Floor guards |
| `node --check static/js/desk-v1-composer-duplicate.js` | 0 | Syntax |
| `node --check tools/smoke/desk-v1-composer-duplicate.mjs` | 0 | Syntax |
| `git diff --check` | 0 | Whitespace |

Earlier runs: backend first rc 1 because the disconnected-account test removed
the account registry entry but left presence data which re-lifts that account;
the corrected fixture removes both. Browser first rc 1 when the initial shared
picker closed on its opening click; the expanded cancel check then exposed
Escape also reaching the shell. The isolated picker resolves both and the
final runs above pass. The screenshot review also caught the shared helper
moving the card trigger; the smoke now asserts its rectangle stays unchanged.

Screenshots, all visually inspected, under this worktree's
`_scratch/composer_c4/`: `menu-1440.png`, `menu-390.png`,
`campaign-picker-1440.png`, `campaign-picker-390.png`, `composer-1440.png`,
`composer-390.png`. C1 alone displays text; displaying/editing copied media in
the composer remains C2's slice. The media copy is verified in the real saved
store and card thumbnails. Actual platform posting is untested/outside C4.

CHANGELOG and USER_GUIDE are updated. No new dependency or agent/architecture
rule, README change, live configuration or approval-gate change is required.
The smoke node_modules junction is removed before commit. No product question
is open; integration/release belongs to Dave.

The unattended fence refused the requested home `.claude/plans` write; it was
not retried. The execution plan is retained here and in the gitignored C4
journal: merge C1, inspect patterns/bounds, implement atomic copy and isolated
menu, run backend/browser checks, inspect screenshots, document and commit
only this branch. A variable-in-command-position pytest invocation was also
fenced; execution through an explicit interpreter path passed, as did the
portable final command above.

### C2 — Sol_Tobin — 2026-10-30 (branch candidate)

Requested C1 base merged first as `31b2447e` (includes `e6ecc1e0`). Work is
on `clayrune/agent/917d441c61d2`; no master merge, push, restart, approval,
publishing or live Desk mutation. No open product choice.

`desk-v1-composer-media.js` and its stylesheet own the single Add media
dialog (Upload default, Library thumbnails/search, newest first), multiple
uploads, whole-composer file drop/paste, attachment viewing/removal and
existing Undo. Text paste stays native. Images and videos share the picker;
video uses the existing broad extension/MIME policy and size caps, without
adding a codec restriction. A real H.264 MP4 plays in the isolated smoke;
MOV acceptance is exercised too. Every successful upload uses the existing
M21 Library/Uploads storage. Removing/undoing an attachment keeps that file.
Demo also keeps a local library entry, with no mutation requests.

Media may be first content. Empty picker visits create nothing. Text/media
share the existing piece queue, concurrent gestures create one post, failed
creates/uploads can be retried, and a remount during upload updates the
current composer. Save/Review wait for media. Media-only drafts may go to
Review. Approved/sent/archived composer media remains viewable but read-only.
Library read errors expose Retry; incomplete upload responses report the
missing saved asset instead of retaining a revoked blob preview.

Integration footprint: one import and one mount in the shell, plus **three
existing guard predicates** for media-first creation, busy Save/Review and
media-only Review eligibility. Those small predicate changes are necessary
for C2; sibling builders must retain them when merging hooks. The existing
library item serializer now returns its existing filesystem `mtime`, so
cross-folder recency is real, not just concatenated folder order. No new
Python module or new concern in a monolith; this extends the current item
serialization. What and Piece bodies were not grown. The three retired
Post entry controls are empty-card Add media, card-strip Add media and old
detail-page Add media. Image/video controls remain. The shared remove helper
gets one focus fallback to the composer's Add media after the last removal.

Updated the campaign/piece/media/name-upload smokes that referenced retired
Post controls. Post removal/viewer checks now run in the composer; the
generic picker regression runs on an image. C2 separately asserts that the
retired Post controls are absent and approved composer media refuses writes.
USER_GUIDE and CHANGELOG are updated. No README, AGENTS or agent-rule change
is needed; no new convention or permission was introduced.

Validation from the **worktree root**, final runs all rc 0. Environment:
`$env:MC_PYTHON='C:\Users\levir\Documents\_claude\mission-control\.venv\Scripts\python.exe'`.
The temporary `_scratch/run-smoke.mjs` wrapper registers its own PID with the
Process Manager then imports the named unmodified smoke entry point. The C2
smoke registers itself and its isolated Flask child. No dependencies installed.

| Exact command | rc | Result |
|---|---:|---|
| `node tools/smoke/desk-v1-composer-media.mjs` | 0 | 1440/390; real upload/storage/reload, MP4 metadata, MOV drop, paste/text paste, Library recency/thumbnails, native file selection, tab/focus/Escape, Undo, refusal/missing-response/retry/invalid, locked media, text+paste+drop race, create retry, remount, media-only Review, demo |
| `node _scratch/run-smoke.mjs tools/smoke/desk-v1-composer.mjs` | 0 | All C1 contracts at 1440/390 |
| `node _scratch/run-smoke.mjs tools/smoke/boot-smoke.mjs` | 0 | 7 boot themes and all dashboard guards; repeated after final product edits |
| `node _scratch/run-smoke.mjs tools/smoke/desk-v1-piece-media.mjs` | 0 | What/composer viewer, remove/Undo/refusal, archived, mobile and demo |
| `node _scratch/run-smoke.mjs tools/smoke/desk-v1-piece.mjs` | 0 | Direct-link compatibility, remaining image picker, header identity, calendar and video navigation |
| `node _scratch/run-smoke.mjs tools/smoke/desk-v1-piece-name-upload.mjs` | 0 | Name/Undo/refusal and remaining generic upload at both widths |
| `node _scratch/run-smoke.mjs tools/smoke/desk-v1-live-pieces.mjs` | 0 | Existing image/video/article create, attach, storage/refusal, writer and demo contracts |
| `node _scratch/run-smoke.mjs tools/smoke/desk-v1-what-add-modal.mjs` | 0 | Isolated real-store article import and C1 create/save/reload at both widths |
| `node _scratch/run-smoke.mjs tools/smoke/desk-v1-campaign.mjs` | 0 | All campaign themes, controls, Undo and responsive checks |
| `& 'C:\Users\levir\Documents\_claude\mission-control\.venv\Scripts\python.exe' -m pytest tests/test_desk_pieces.py -q` | 0 | 99 piece-store tests, including actual mtime serialization |
| `node --check static/js/desk-v1-composer-media.js` | 0 | Syntax |
| `git diff --check` | 0 | Whitespace |

Initial smoke runs exposed and fixed the dialog Escape leaking to the Desk's
global close shortcut. The first updated piece-media run also failed because
its old fixture was approved; that editing regression now uses a drafting
fixture, while C2 explicitly checks the approval lock. An initial new locked
paste assertion waited for a disabled Add media button to enable; the harness
now checks the refusal and zero writes directly. All final commands above pass.

Visually inspected screenshots, gitignored in this worktree:
`_scratch/composer_c2/{composer,upload,library}-{1440,390}.png`. Real platform
publishing, production data, native OS clipboard permission workflows and
every possible browser codec were not exercised. Clipboard/drop browser
events use real File/DataTransfer objects; native file input and real storage
are exercised separately. No full npm suite was run in C2.

The dependency junction is removed before commit. The campaign smoke
regenerated two tracked screenshots. The unattended-session fence refused
`git restore`; both generated PNG changes are deliberately unstaged. Dave's
cleanup command, in this worktree (not retried in the guarded session):

```powershell
git restore -- docs/desk_v1/screens/t2a_campaign_desktop_1440.png docs/desk_v1/screens/t2a_campaign_phone_390.png
```

For replay without the temporary registration wrapper, run the named
`node tools/smoke/<name>.mjs` entries directly from the repo root with the
main checkout's smoke dependency junction and the `MC_PYTHON` value above.
Integration/release remains with Dave.

### C5 — Sol_Tobin — 2026-10-30 (branch candidate)

Base `fbfaf484`, branch `clayrune/agent/fa8e76c967e7`. Where is now a
read-only overview of individual assignments: distinct live post counts per
campaign account, existing version cards and hover/keyboard/tap previews.
Archived pieces and archived/skipped versions do not inflate counts; multiple
versions of the same piece on one account count once. Empty accounts show zero.

Removed Where's pointer handlers, message-add and version-move menus, individual
assignment removal, drop targets and drag styling. Retired
`desk-v1-where-removal.js` and its index registration. Account selection is
click/Enter/Space; account removal, voices, suggestion-account acceptance,
Connections handoffs, plan persistence, rollback and Undo retain their existing
contracts. `plan.accounts` still supplies composer defaults and approval bounds.
Expansion still shows Awaiting approval and routes to Launch; narrowing preserves
approval, archives pending versions and preserves published history. Undo cannot
restore a human approval via a plain version PATCH. No backend or gate changes.
`DeskV1WhereActions.addVersion` remains solely for the existing campaign card
menu; there is no Where UI path to it. What and Piece were not edited.

Retired smoke assertions (named explicitly for review):

- `desk-v1-where`: message drag adds a version/pill increment; version drag moves
  with unchanged total/pill; published-version drag is refused; source drag adds
  an empty column, widens approval and accepts read-only planning; keyboard
  message/version menus add/move and restore their trigger focus. Replaced by
  no ghost/drop-target, no assignment mutation/menu/remove controls, correct
  per-account counts and click/keyboard account selection/approval checks.
- `desk-v1-post-preview`: preview suppression during drag and drag adding a
  version. All actual popup/modal, copy/media, platform, Escape and touch checks
  remain; the Where smoke owns the no-drag invariant.
- `desk-v1-where-drag-service` and `desk-v1-where-attach`: pointer ghost/hover/drop
  and dropped account placement. The entry name is retained for existing runners;
  saved-sign-in explanation, human-only attachment/cancellation/refusal/retry,
  no permission grant/browser-session use and selected-account persistence remain.
- `desk-v1-campaign-content`: suggestion drag persists a piece/version and keeps
  its suggestion, assignment reload, individual X/Undo restores drafting, refused
  assignment add rolls back with honest copy. Replaced with real-store account
  bound reload/refusal rollback and absent individual X; piece lifecycle/media
  checks remain. Composer smokes cover destination persistence/refusal/Undo.
- `desk-v1-live-accounts`: Where message activation POSTs M17, keeps its account,
  then a move PATCHes M18 with no approved/scheduled state. Replaced by zero
  individual version writes; account-removal archive and refusal checks remain.
- `desk-v1-journey`: source drag and message drag placement. Replaced by account
  selection and an actual composer draft appearing in the overview; the rest of
  the continuous journey remains.

Browser validation: all rc 0 from the worktree root using
`node _scratch/run-smoke.mjs tools/smoke/<name>.mjs`. The gitignored wrapper
registers its own PID and spawned fixtures/Playwright processes before importing
the unmodified entry point. Set `MC_PYTHON` to the main checkout's venv Python;
link that checkout's smoke node_modules (no installation).

| Smoke name | Coverage |
|---|---|
| `desk-v1-where` | Three themes, counts including duplicate/inactive/archived cases, no pointer/menu mutations, account widening/narrowing, Undo, empty campaign, 1440/390 layout and touch targets |
| `desk-v1-where-drag-service` | Missing account explanation, assisted/read-only account selection, both widths |
| `desk-v1-where-attach` | Existing human-only attachment and selection persistence, both widths |
| `desk-v1-where-services` | Saved source/status truthfulness, failure/retry, no new connection or grants |
| `desk-v1-post-preview` | Existing desktop and touch previews |
| `desk-v1-campaign-content` | Isolated Flask/filesystem store, bound reload/refusal, piece lifecycle/Undo and inline article media |
| `desk-v1-live-accounts` | Existing live-account writes, bound selection/removal/refusal and demo no-write |
| `desk-v1-journey` | Full campaign journey, now through composer selection |
| `desk-v1-campaign` | Campaign controls, themes, responsive layout and Undo |
| `desk-v1-composer` | C1 creation/races, bounded ticks, save/refusal/retry, schedule/Review and demo |
| `desk-v1-composer-media` | C2 media and Library contract |
| `desk-v1-composer-preview` | C3 live panel and account tabs |
| `desk-v1-composer-duplicate` | C4 real-store copies, bounds, media, lifecycle and Undo |
| `boot-smoke` | Seven boot scenarios and all dashboard guards |

`python -m pytest -q tests -k desk` completed with rc 0 (the repository's quiet
configuration suppresses the final test-count summary). Two collection
SyntaxWarnings concern existing escape sequences in `test_agent_routes.py`
and `test_process_sweep.py`. Full output: `_scratch/composer_c5/pytest.log`.
No new Python module means pyright is not applicable. `git diff --check` passes.
Initial harness failures: the refusal locator matched both toast and screen-reader
announcement (now scoped to the toast); the new journey tried to click the map
while still inside the composer (now uses the visible Back button first).
Both final retries pass. Screenshot review also caught and removed the old dashed
drop-zone affordance in `desk-v1-campaign-content.css`.

Visually inspected screenshots in this worktree:
`_scratch/composer_c5/r2_10_where_1440.png` and
`_scratch/composer_c5/r2_10_where_phone_390.png`. Guide and CHANGELOG updated;
README/agent-rule/architecture-rule changes are not needed. Actual platform
publishing is outside this slice and untested. No master merge, push, server
restart or live Desk writes; Dave owns integration. No product decision is open.
The home-plan write was fenced; the execution plan and progress are retained in
the gitignored journal `docs/_journal/fa8e76c967e7-composer-c5-c6.md`.

The unattended fence refused `git restore` for five tracked screenshots generated
by the existing journey/campaign/services smokes. They are deliberately unstaged.
Cleanup, in this worktree, for Dave after review (not retried here):

```powershell
git restore -- docs/desk_v1/screens/r2_13_journey_goal_1440.png docs/desk_v1/screens/t2a_campaign_desktop_1440.png docs/desk_v1/screens/t2a_campaign_phone_390.png docs/desk_v1/screens/where_configured_after_1440.png docs/desk_v1/screens/where_configured_after_390.png
```

**Handoff blocker:** the fence also refused the required smoke dependency-junction
removal (`destructive delete outside scratch`). The junction still targets the
main checkout's dependencies. No alternate deletion was attempted. Because Dave
explicitly required removing it before committing, C5 remains uncommitted and C6
has not been applied. C5's named source/test/doc paths are staged; generated PNGs
are not. After deleting ONLY this junction, resume this session to commit C5 and
then implement/test/commit C6. Do not recursively remove the dependency directory.

```powershell
cmd /c rmdir "C:\Users\levir\Documents\_claude\mission-control\.clayrune\agents\fa8e76c967e7\tools\smoke\node_modules"
```

C5 handoff resolved: Dave removed the junction and verified the main dependency
directory intact. On resume, absence was checked and the reviewed staged paths
were committed as `83aea780`. The generated screenshots stayed unstaged.

### C6 — Sol_Tobin — 2026-10-30 (branch candidate)

Base C5 `83aea780`, branch `clayrune/agent/fa8e76c967e7`. Stops now read
**Brief, Goal, Channels, Posts, When, Launch**. Internal `where`/`what` IDs,
saved cursors, aliases and selectors remain compatible. Shared navigation drives
Next/Back and draft-resume labels. Launch's **Fix in Channels** still targets
`where`; composer guidance, editor return/save labels, suggestions and USER_GUIDE
use the new names. When's empty-state action opens Posts for composer selection;
the legacy piece action reads **View campaign channels**.

What and Piece only change strings/comments and do not grow. No approval,
publish, account-bound or persistence logic changed; Python edits are copy only.
No dependency, new module, agent-rule, architecture-rule or README change is
needed. CHANGELOG and USER_GUIDE updated. No master merge, push, running-server
restart or production Desk writes; actual platform publishing is untested.

Validation from the worktree root, with main-checkout venv `MC_PYTHON` and its
smoke dependency junction (nothing installed):

| Check | rc | Result |
|---|---:|---|
| `python _scratch/run-c6-checks.py` | 1 initially | All 126 `desk*.mjs` entries (fixture helper excluded) plus boot ran; five initial failures retained in the raw record |
| Final named smoke entries, with targeted replays below | 0 each | **127/127 final passes**, including campaign, all composer entries, post-preview, boot and the 21-child Exit aggregate |
| `python -m pytest -q tests -k desk` | 0 | 410.9 seconds; quiet configuration prints no final count |
| `git diff --check` | 0 | Whitespace |

Per-entry logs, raw rc records and the final reconciliation are in
`_scratch/composer_c6/{results.jsonl,summary.json}`. Entries ran through the
registered `node _scratch/run-smoke.mjs tools/smoke/<name>.mjs` wrapper, which
preserves normal Node argv. Default routing used a registered disposable
route-only server, own data, ephemeral loopback port and `MC_REMOTE_ENABLED=0`;
no boot workers; only its captured PID was stopped. Its 11 checks passed.

Failures/replays: Map's added helper and expired Resume/Renew success fixtures
were corrected (expiry-refusal checks remain); campaign-content's 390 Library
timeout passed an unchanged replay; Default's stale Experimental locator now
uses System → Runtime and availability; Exit passes after the How correction;
the video-upload regression now exercises the C2 composer. Final replay logs:
`desk-v1-map-verified`, `desk-v1-campaign-content-retry`,
`desk-v1-default-isolated`, `desk-v1-exit-verified`, and
`desk-video-upload-regression-verified` (all `.log`, all rc 0).

Retired assertions named for review: How's accepted suggestions read **◇ planned**
and clear the source banner; replacements assert **no versions yet**, zero
destination versions and all three source proposals retained. Video's Post MP4
rejection (zero uploads/assets and PNG-only toast) and hidden Library videos are
replaced by composer MP4 upload/playback and Library video attachment. Generic
video upload/refusal/malformed-response/preview-failure checks remain. No product
gate was relaxed.

The initial scratch wrapper left its path in argv[1], skipping the executable
guards in `desk-v1-connect-api-step` and `desk-v1-connect-signin-reuse`. Those
initial zero exits are **not passes**. After restoring argv, both passed actual
checks (their `*-verified.log` files, rc 0). The argv audit found no other affected
completed entry; QA ran all four desktop/phone cases with corrected argv.

Map coverage checks literal order/names, Channels at 3 and Posts at 4, stable
IDs, Next/Back and Launch's account-fix link. Visually inspected captures:
`_scratch/composer_c6/{channels,posts}-{1440,390}.png`. Generated tracked PNGs
remain unstaged. Home-plan writing remains fenced; the journal retains the plan
and detailed handoff. Required dependency-junction cleanup is pending before
the separate C6 commit. No open product decision.
