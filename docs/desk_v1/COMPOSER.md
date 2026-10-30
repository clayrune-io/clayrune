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

Each slice: its own commit(s), smokes for what it touched, screenshots at 1440 and
390 wide, a short report under this file's `## Reports`.

## Reports
