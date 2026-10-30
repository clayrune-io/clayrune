# Field scan: composer UX (media, reuse, channels, preview)

Researcher, 2026-10-30, for Dave (Ron's ask: "Our way is most certainly not easy
enough"). Read-only scan; no code changed.

**Finding in one line.** Every one of the nine tools puts all four jobs on ONE
composer screen: write, attach media, tick channels, see the preview. The Desk
spreads them across four places (add-piece modal, piece page, the Where board,
Review) and has no per-platform preview at all.

## Method and coverage limits (read first)

- Sources are vendor help centres, changelogs and release notes. Dates are given
  where the page shows one. "Undated" means the page carries no date.
- **Not verified hands-on.** I did not sign into any tool. Click counts are
  derived from the documented steps, not measured in the product.
- **Blocked sources.** Sprout Social, Later and Canva help centres returned HTTP
  403 to the fetcher; for those three I relied on search-engine extracts of their
  own help pages (marked *extract*). The Clayrune browser pane was refused by the
  steward fence, so no screenshots or videos were viewed. Meta Business Suite has
  no official help page in my results; its rows rest on third-party guides and
  are marked *3rd-party*.
- Absence claims ("doesn't offer X") mean "not found in the docs I read", not
  "proven absent", unless the vendor states the limit.

## 1. Adding media to a post

| Tool | Default (on the composer) | Behind a menu | Deliberately not offered / limits | Clicks: upload · library |
|---|---|---|---|---|
| **Buffer** | Drag, paste, attach button in composer; "Suggested Media" tray ([Scheduling posts](https://support.buffer.com/article/642-scheduling-posts), undated) | Integrations button: Dropbox, Google Drive, Google Photos, OneDrive (via Uploadcare) ([cloud](https://support.buffer.com/hc/en-us/articles/4410101480211)); Canva ([Canva](https://support.buffer.com/articles/using-canva-with-buffer-dP9Hu8fhao)) | Cloud upload one file at a time; no cloud picker on mobile. No user-facing media library found in docs or the 2025-2026 changelog (unverified absence) | 1 (drop) · n/a |
| **Hootsuite** | Drag files into post, or "Add media" ([help, updated 2025-10-21](https://help.hootsuite.com/hc/en-us/articles/37661365345691-Add-images-and-videos-to-your-posts)) | Content/Media Library with stock and Canva designs (Canva in library: beta, 2025-10-01 per search extract) | Library is org-shared, pre-approved assets | 1 · ~3 |
| **Later** | Media Library is a **sidebar next to the calendar**; drag a file onto a day to start a post (*extract*, [later.com](https://later.com/social-media-content-calendar/)) | Upload into the library | One library per Access Group, shared across its profiles | 1 drag · 1 drag |
| **Sprout Social** | Upload in Compose; Asset Library button (*extract*, [Asset Library](https://support.sproutsocial.com/hc/en-us/articles/214723923-How-do-I-use-the-Asset-Library)) | Bynder, Canva, Dropbox, Google Drive | Asset Library plan-gated (Professional/Advanced); auto-save of Compose uploads into "Auto Saved Media From Compose" is an opt-in setting | 2 · 3 (Asset Library, select, Send to Compose) |
| **Metricool** | Drag onto calendar slot or into Create post ([help](https://help.metricool.com/how-to-add-images-and-videos-to-your-posts-in-metricool-b26gi), "updated 2 days ago") | Multimedia icon dropdown: Add image or video (device/URL), Canva, Google Drive (paid), Post Library, Pexels, GIF, Adobe Express, Story template | Media from earlier posts only via Analytics, "Reuse content", and only if scheduled through Metricool | 1 · 3 |
| **Typefully** | Media in the draft editor (images, video, GIF; [formats](https://support.typefully.com/en/articles/8717768-supported-media-formats)) | n/a | No media library found (unverified absence) | 1-2 · n/a |
| **Publer** | Upload in Create; Media Library ([help, updated 2026-09-21](https://publer.com/help/en/article/1pffqka/)) | Imports: URL, Drive, OneDrive, Dropbox, Unsplash, Giphy, Canva, VistaCreate, AI images | Media used in posts is saved to the library by default (setting); Used/Unused filter makes "pick from earlier posts" a filter, not a feature | 1-2 · ~3 |
| **Canva** | The design *is* the media; scheduling starts from the design (*extract*, [Content Planner help](https://www.canva.com/en_gb/help/content-planner/)) | Multi-page: choose pages | No external media picker in the scheduling step | 0 (design) |
| **Meta Business Suite** | "Add photo/video": upload, or pick from existing (File Manager per one guide) (*3rd-party*, [Buffer guide](https://buffer.com/resources/how-to-use-meta-business-suite/)) | n/a | FB/IG only | 2 · 3 (unverified) |

**Pattern.** Upload is always on the composer and always accepts drag (and in
Buffer paste). The library is one button away, and the best tools make every
upload land in the library automatically (Publer default; Sprout opt-in), so
"pick from an earlier post" is just "pick from the library". Nobody has a separate
"from earlier posts" media source except Metricool, which hides it in Analytics.

## 2. Reusing / duplicating a post

| Tool | Default | Cross-workspace | Limits | Clicks |
|---|---|---|---|---|
| **Buffer** | ⋯ on a post (Queue, Drafts, Sent, Calendar) → Duplicate → edit → schedule ([help](https://support.buffer.com/articles/how-to-duplicate-posts-in-buffer-6ExWJtsEGD), undated) | Not mentioned | Only posts published through Buffer; Facebook text not copied | 2 + schedule |
| **Hootsuite** | Planner: select post → Duplicate; "Post and Reuse Networks" keeps the composer open with the same networks ([2019-04-30](https://www.hootsuite.com/whats-new/duplicate-posts-and-reuse-networks-in-composer); old, current UI unverified) | Not found | | 2 |
| **Later** | Open post → "Copy Post" in Post Builder (*extract*, [help, ~early 2025](https://help.later.com/hc/en-us/articles/1500010572781-Schedule-Copy-Posts-With-Later-on-the-Web)) | Same Access Group only | **One profile per copy**; IG Stories cannot be copied | 2-3 |
| **Sprout Social** | "Send + Duplicate" on the send button reopens Compose with text and media; calendar Duplicate icon keeps tags and media (*extract*; [2017 release](https://sproutsocial.com/insights/release/october-5-2017)) | Not found | | 1 (at send) / 2 |
| **Metricool** | Planner: select → Duplicate; bulk "Duplicate as draft" / "Duplicate and schedule" ([help](https://help.metricool.com/how-to-duplicate-posts-in-metricool-17prr), "updated 4 months ago") | **"Duplicate to another brand"**, single or bulk, picks destination brands | Times not shifted across time zones | 2 · 4 cross-brand |
| **Typefully** | Not documented in sources found | | | unverified |
| **Publer** | Calendar → "Reuse" (single or bulk) → Create page ([help, 2025-07-08](https://publer.com/help/en/article/how-to-reuse-posts-1we8gjl/)); "Recycle" is a separate auto-repeat | Not stated | Threads/follow-up comments not carried | 2 |
| **Canva** | Not documented for scheduled posts (unverified) | | | unverified |
| **Meta Business Suite** | ⋯ → "Duplicate Post" (*3rd-party*, single source, unverified) | n/a | | 2 |

**Pattern.** Duplicate is an action **on the existing post** (⋯ menu or a button
at send time), never a "source" chosen while creating a new blank post. It copies
text and media, opens the copy for editing, and that's it. Cross-workspace copy
exists only in Metricool and is a second, explicit action.

## 3. Choosing where a post goes

| Tool | Default | Behind more | Not offered | Clicks (2 channels) |
|---|---|---|---|---|
| **Buffer** | Channel avatars at top of composer; you can write first and pick later; recent channel sets suggested; "Select all" and channel groups ([2026-06-29](https://buffer.com/changelog/post-composer-improvements); [help](https://support.buffer.com/articles/how-do-i-schedule-posts-for-multiple-social-channels-at-the-same-time-XNPoSegb1W)) | "Customize for each network" splits text per network | After scheduling, each channel's post is separate | 1 (suggested set) – 3 |
| **Hootsuite** | "Publish to" list grouped by network/team (*extract*, older help) | Per-network tabs for custom text and media | | ~2-3 |
| **Later** | Pick profile at top of calendar; Post Builder dropdown, up to 10 profiles (*extract*) | | Profiles limited to one Access Group | ~2 |
| **Sprout Social** | Profile Picker; **last selection is kept** for the next post (*extract*, Compose FAQs) | | | 0 (repeat) – 2 |
| **Metricool** | Tick networks; "Edit by network" opens a Template tab plus one tab per network ([help](https://help.metricool.com/multiposting-edit-per-social-network-85out)) | | | 2 |
| **Typefully** | Accounts grouped in a "social set"; channel toggles top-left; versions auto-sync until you toggle sync off ([help, 2026-04-16](https://support.typefully.com/en/articles/8718168-publish-cross-post-to-linkedin)) | Per-platform edit by disabling auto-sync | | 0-1 |
| **Publer** | Account list on the left of Create; account Groups ([help, 2026-09-27](https://publer.com/help/en/article/how-to-customize-posts-for-each-social-network-1cgw0wg/)) | "Customize Post" icon per account | | 2 |
| **Canva** | Channel dropdown per scheduled post (*extract*) | | **One channel per scheduled design**; a second channel is a second schedule | 1 per post |
| **Meta Business Suite** | FB + IG chosen at top (*3rd-party*) | "Customize post for Facebook and Instagram" toggle | Only FB, IG, groups | 0-1 |

**Pattern.** Channel choice is a row of avatars/toggles **inside the composer**,
pre-filled from last time or a saved group. Per-channel variation is an opt-in
split ("Customize", "Edit by network", "disable sync"), not the default.

## 4. Previewing as it will look on each platform

| Tool | Default | Behind more | Clicks |
|---|---|---|---|
| **Buffer** | Live per-channel preview as you type; "Preview" toggles it off (Focus mode) ([help](https://support.buffer.com/article/642-scheduling-posts)) | Fullscreen mode Cmd+Shift+F ([2026-02-05](https://buffer.com/changelog/fullscreen-composer-layout-refresh)) | 0 |
| **Hootsuite** | Preview pane on the right; select a network tab to see it; generic preview before any network is chosen ([2019-05-03](https://www.hootsuite.com/whats-new/improved-post-preview-experience-in-compose); old) | | 0-1 |
| **Later** | Visual Planner: Instagram **grid** preview with scheduled posts in place (*extract*) | | 1 |
| **Sprout Social** | Fullscreen Compose: preview per network on the right; compact Compose: eye icon with desktop/mobile toggle and per-profile tabs (*extract*) | | 0 / 1 |
| **Metricool** | "Show preview" top right, mobile and desktop feed, per network | | 1 |
| **Typefully** | LinkedIn "realistic preview": Publish/Schedule → Preview; collapsed with "See more", Mobile toggle ([2024-04-24](https://typefully.com/changelog/realistic-post-preview-for-linkedin-94)) | | 2 |
| **Publer** | Preview on the right per network, plus warnings (X truncation, missing settings) ([help, 2026-09-27](https://publer.com/help/en/article/how-to-customize-posts-for-each-social-network-1cgw0wg/)) | Hover a network logo to confirm destination | 0 |
| **Canva** | Not confirmed in Canva docs (unverified) | | unverified |
| **Meta Business Suite** | Preview on the right; FB vs IG, desktop vs mobile (*3rd-party*) | | 0 |

**Pattern.** A live, always-visible side panel is the norm (Buffer, Hootsuite,
Sprout fullscreen, Publer, Meta). Desktop/mobile is a toggle inside it. The extra
value the best ones add is **warnings** (Publer: X truncation; Typefully: the
"See more" cut line), not more preview modes.

## Ours, read from the code (branch head 8b780dad)

**1. Media.** A post's media is added on the piece page, not where the post is
created. Path for a new post: Post tile → "Write new" → piece page → "＋ Add
media" → "From this computer…" → OS dialog (`desk-v1-what.js:579`,
`openAddMedia` at `:676`). Four overlapping entry points exist:
"From this computer…" (`desk-v1-local-upload.js`), library files listed as
text-only "folder / title" menu rows with no thumbnails (`_openAddMediaLive`,
`:709`), "From your projects…" (`DeskV1LibraryPicker`), and Studio's host file
explorer, whose action is "Copy to folder" into the Material library
(`desk-v1-file-explorer.js`). The piece page has no drop or paste target
(grep of `desk-v1-piece*.js`). For Image pieces, the "Upload" and "From library"
source cards render the **same** body (`card.source === 'upload' || 'library'`
→ `_uploadBodyHTML`, `desk-v1-what.js:423`).

**2. Reuse.** Two unrelated paths:
- Add a piece → Post → "Reuse an existing post" → pick (`desk-v1-what-post-reuse.js`).
  Copies text and media, lists posts from every campaign. But it first creates a
  blank piece and asks for a Name, and the list includes other empty drafts:
  the screenshot (`data/uploads/agent_0e24239551.png`) shows a "New post · This
  campaign · 0 media" entry, a blank draft left by an earlier tile click (the
  piece is POSTed when the tile is clicked, `_addCreate`).
- What card ⋯ → Duplicate (`desk-v1-campaign.js:1222`). From reading the code
  it copies only title and word count (no body, no assets, no channel) and runs
  through `commandBus.run`, not `DeskV1Store.write`, so it appears not to persist
  in live mode. Not exercised; flag for a builder to confirm.

**3. Where.** A separate stop, a drag board (`desk-v1-where.js`): drag an account
tile up from SOURCES to add a column, then drag each message into each column.
The board carries a rule line explaining add-vs-move semantics and a per-column
"Voice" field. A third path exists on the What card ⋯ menu: "Add a channel version ▸".

**4. Preview.** None that shows how a post looks on X or LinkedIn. The What card
shows first image + text; the Where version card says "Post → X post"; Review
shows the body text with claim checks. The "Preview · not connected" tags in What
and Where mean "feature not wired yet", not a post preview.

## Click counts: ours vs best-in-class

Counted for: one new text post with one image, to X and LinkedIn, accounts
already connected. Ours counted from code paths; competitors from docs.

| Flow | Ours | Best-in-class | Who |
|---|---|---|---|
| Add media from computer, new post | 5 + OS dialog (tile, Write new, ＋ Add media, From this computer, pick), on a second screen | 1 drag or paste in the composer | Buffer, Hootsuite, Metricool |
| Add media from library, existing post | 2 (menu, text-only row) | 2-3 with thumbnails | Sprout, Metricool |
| Add media used in an earlier post | Not offered except by copying the whole post | Same as library (uploads auto-saved) | Publer |
| Duplicate a post, same campaign | 3 via Reuse (tile, Reuse, pick) + a Name; or 2 via ⋯ Duplicate that drops text and media | 2 (⋯ → Duplicate), text + media | Buffer, Metricool, Publer |
| Duplicate into another campaign | 3 via Reuse, from inside the target | 4 ("Duplicate to another brand") | Metricool |
| Choose 2 channels | 5 drags on a separate screen (2 columns + 2 placements + navigate); 3 if columns exist | 0-1 in the composer (last set kept / suggested set) | Sprout, Buffer, Typefully |
| See it as it will look on X / LinkedIn | Not possible | 0 (live side panel) | Buffer, Publer, Sprout |

## What we should cut

1. **The Where drag board as the way to pick channels.** Put channel avatars at
   the top of the piece page, preselected from the campaign's accounts or last use.
   Where can stay as a read-only overview, or go.
2. **The add-piece modal for Post.** Post tile opens the composer directly. No
   Name field (competitors name a post by its text), no "Write new / Reuse" fork.
3. **"Reuse an existing post" as a creation source, and the broken ⋯ Duplicate.**
   Replace both with one ⋯ → Duplicate (text + media, opens the copy), plus
   "Duplicate to campaign…" as its second item.
4. **Three of the four media entry points.** One "Add media" picker with two tabs,
   Upload (default, plus drop and paste anywhere on the composer) and Library
   (thumbnails, recent first). Every upload lands in the library automatically,
   which also covers "media from an earlier post". Fold "From your projects…" into
   Library; keep Studio's host file explorer in Studio only.
5. **Duplicate cards and pre-created drafts.** Image "Upload" and "From library"
   lead to the same screen: keep one. Create the piece on first content, not on
   tile click, so empty "New post" drafts stop piling up and stop showing in pickers.
6. **"Preview · not connected" placeholder tags.** Use the space for a real preview.

## Recommended default flow, per job

| Job | Recommended default | Hidden behind "more" | Clicks ours → target |
|---|---|---|---|
| 1. Media | Drop/paste onto the composer; "Add media" opens Upload ▸ Library tabs with thumbnails | Projects, URL, Studio-made assets (as a Library filter) | 5 + dialog → 1 |
| 2. Reuse | ⋯ on any post card → Duplicate; copy opens in the composer | "Duplicate to campaign…" | 3 + name → 2 |
| 3. Where | Channel avatars at top of composer, preselected; tick/untick | "Customize per channel" (split text), Voice per account | 5 drags → 0-1 |
| 4. Preview | Live side panel on the piece page, one tab per ticked channel, shows the "See more" cut line and X length warning | Desktop/mobile toggle | not possible → 0 |

This is the Buffer/Publer shape: one screen, write first, channels and preview
alongside. Canva's "one channel per scheduled post" is the counter-example of
deliberately offering less, and it is the one tool here with no multi-channel
composer at all.

## Open questions for Dave

- Does Where survive as a board (an overview of the campaign) once channel choice
  moves into the composer, or is it removed? Removing it touches approval-bounds
  logic that Where's comments say falls out of `plan.accounts`.
- A faithful X/LinkedIn preview needs per-platform truncation rules; Typefully's
  public preview tool shows that is mostly text layout, achievable without the
  platform APIs.
- The ⋯ Duplicate defect (no body/media, likely not persisted live) is a bug
  regardless of redesign and should get its own ticket after a builder confirms it.
