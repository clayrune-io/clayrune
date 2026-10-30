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
