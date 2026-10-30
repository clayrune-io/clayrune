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
