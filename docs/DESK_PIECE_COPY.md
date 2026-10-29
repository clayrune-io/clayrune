# Desk piece Copy editor

Branch candidate for the 2026-10-29 request to start a post by typing or importing a file. No backend/schema changes, live data changes, merge, push or server restart.

## Behavior and ownership

`static/js/desk-v1-piece-copy.js` owns Copy markup, drafts, import and saving; `static/css/desk-v1-piece-copy.css` owns its styles. The piece renderer delegates Copy rendering and binding to it, disabling old blur handlers before a repaint. Media and article-edit behavior remain unchanged.

Active `post`, `image` and `video` pieces show the full `body` in a labeled textarea with **Import from file** and **Save**. New pieces show **Start typing your post…**. Existing destination rows and Review/retry/assisted controls follow the editor; an empty versions list says Where is the next step. Archived pieces and articles retain the original excerpt path; articles retain **Edit article**.

The local picker accepts `.txt`, `.md`, `.markdown`, `.html` and `.htm`. Files are read in the browser, never uploaded. HTML becomes decoded plain text with paragraph/line breaks; scripts, styles and template content are discarded. Import does not save or truncate the draft. Unsupported files, files over 4 MB and imported text over 100,000 characters show an error while retaining the previous text. Drag/drop import was optional and is not implemented.

Blur and Save PATCH only `{body}` via `DeskV1Store.api`, then use `DeskV1Store.load({force:true})` and the existing piece repaint hook. The browser limits textarea input/import to 100,000 UTF-16 code units (conservative relative to the backend's 100,000 Unicode characters). Server-normalized outer whitespace is reflected after saving. Fixture saves update only fixture data and say **Saved in demo only**.

Drafts are kept per piece in tab memory, so failures and unrelated piece repaints retain input. They are not a durable browser draft store: reloading before a successful save can discard unsaved text. Writes serialize per piece; a blur during an earlier save queues a newer draft. A save completing during typing retains the current draft, focus and selection. Repainting never triggers a blur retry. Save failures show the server message; a successful PATCH followed by a failed workspace refresh explicitly says the body saved but refresh failed.

## Verification

Commands below run from the branch worktree root. The harnesses load the actual SPA and static assets in headless Chromium with intercepted APIs. Persistence/reload is verified against hermetic server state, not the live workspace. No authenticated/live integration was exercised.

| Exact command | Final rc |
| --- | --- |
| `node tools/smoke/desk-v1-piece-copy.mjs` | 0 |
| `node tools/smoke/desk-v1-piece.mjs` | 0 |
| `node tools/smoke/desk-v1-live-pieces.mjs` | 0 |
| `node tools/smoke/desk-v1-post-delete.mjs` | 0 |
| `node tools/smoke/desk-v1-assisted-post.mjs` | 0 |
| `node tools/smoke/desk-v1-store.mjs` | 0 |
| `node tools/smoke/desk-v1-campaign.mjs` | 0 |
| `node tools/smoke/desk.mjs` | 0 |
| `node tools/smoke/desk-v1-harness.mjs` | 0 |
| `node tools/smoke/boot-smoke.mjs` | 0 |

The new smoke covers typing/save/reload over 400 characters, blur, the real file chooser and `.txt` persistence, HTML decoding/script removal, Markdown, oversized/unsupported imports, the exact body limit, failure/repaint/retry, serialized saves, typing/caret preservation, workspace refresh failure, version controls, image/video/article/archive behavior, fixture-only writes and the mobile editor bounds. Existing smokes cover review controls, media attach/Undo, published receipts and broader Desk/boot behavior.

Empty-state screenshots, visually inspected:

- `_scratch/piece-copy/empty-1440.png`
- `_scratch/piece-copy/empty-390.png`

Scratch logs and the process-registering test runner live in `_scratch/`; no dependency installation was performed. The main checkout's `tools/smoke/node_modules` junction was removed before commit. Syntax checks for both changed JS modules and the new smoke, `git diff --check`, and an exact comparison of `_mediaHTML` against HEAD also passed.

The existing campaign smoke regenerated its two tracked screenshots under `docs/desk_v1/screens/`. Automatic approval review refused `git restore -- docs/desk_v1/screens/t2a_campaign_desktop_1440.png docs/desk_v1/screens/t2a_campaign_phone_390.png` as an unattended working-tree overwrite. Those test-generated image changes are left unstaged and excluded from this feature commit. Global plan writing was separately refused by the runtime fence; the full plan and checkpoints are retained in `docs/_journal/85b6a99abb5e-piece-copy.md`.

Rollback: revert this branch commit, removing the Copy module/style references and restoring the prior excerpt rendering. No state migration is needed.
