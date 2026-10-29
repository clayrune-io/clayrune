# What: add-piece source popup

Branch candidate, 2026-10-29. No production restart or deployment is part of this change.

Clicking, keyboard-adding or dropping a palette type opens one centered native
dialog. Its name field and source cards stay together. Escape, close and backdrop
return to What with the draft retained; Discard uses the existing piece delete.
Tab wraps within the dialog and focus returns to the type tile. A route change
removes the dialog. Source bodies still use the existing What/Studio handlers.
Existing piece-open routing is unchanged.

## Ownership

| File | Responsibility |
| --- | --- |
| `static/js/desk-v1-what-add-modal.js` | Per-type option registry, native dialog, keyboard/backdrop/focus and route cleanup |
| `static/css/desk-v1-what-add-modal.css` | Centered layout and 390px source cards |
| `static/js/desk-v1-text-import.js` | Shared piece-editor/article file validation and HTML-to-text conversion |
| `static/js/desk-v1-what-article-import.js` | Article file source and undoable persistence |
| `static/js/desk-v1-what-post-reuse.js` | Existing-post picker and independent copy operation |
| `static/js/desk-v1-what.js` | Existing create/source handlers, with minimal modal/adapter wiring; shrinks overall |

## Existing API contracts

No new product backend route is required or added.

- A tray action creates a piece using `POST /api/desk/pieces`, as before.
- Article import uses the same importer as the piece editor: `.txt`, `.md`,
  `.markdown`, `.html`, `.htm`; 4 MB file cap, 100,000 character cap. HTML is
  reduced to text, with scripts/styles excluded. Existing `PATCH
  /api/desk/pieces/<id>` saves body and word count. The article adapter saves
  immediately; the piece editor retains its existing unsaved-import behavior.
- Post reuse lists `GET /api/desk/pieces`, excluding the new piece and archived
  posts. It copies body text into the newly created piece using PATCH and reads
  only same-origin server-issued `/api/serve-image` or `/api/serve-file` URLs.
  Files are downloaded/uploaded one at a time through the existing multipart
  `POST /api/desk/pieces/<id>/assets`. Each attachment gets a new ID and a new
  physical library file. Source objects, versions, approval stamps and schedules
  are not attached to the new piece.
- A failed multi-request copy compensates completed attachment writes and body
  changes; rollback failures are explicitly reported with a reload instruction.
  Undo restores the previous body and removes copied attachments. As with normal
  uploads, detaching keeps the library file. These operations use existing
  endpoints and are not a single backend transaction.

## Validation

Run from the repository root with the project's Python environment (Flask >=3.1.3)
and the existing smoke dependencies; do not install a separate worktree copy.
All final commands below returned **0**:

```text
node tools/smoke/desk-v1-what-add-modal.mjs --before
node tools/smoke/desk-v1-what-add-modal.mjs
node tools/smoke/desk-v1-piece-copy.mjs
node tools/smoke/desk-v1-live-pieces.mjs
node tools/smoke/desk-v1-campaign.mjs
node tools/smoke/desk-v1-studio.mjs
node tools/smoke/desk-v1-piece.mjs
node tools/smoke/boot-smoke.mjs
python -m pytest tests/test_desk_pieces.py tests/test_desk_routes.py -q
git diff --check
```

The new browser regression runs the actual SPA against Flask and a temporary
filesystem store, with no live operator data. At 1440/390 it verifies pointer
drop/click/keyboard opening, exact registries, focus wrap/return, Escape/backdrop,
route cleanup, import formats/limits/refusals/reload/Undo, campaign and library
post selection, physical media independence, second-upload rollback and no
inherited scheduled version/approval. The existing backend selection is 117 tests.
Real product recording, external source connectors and generation engines were
not exercised; their existing handlers were retained.

## Screenshots

| View | 1440 | 390 |
| --- | --- | --- |
| Before, inline Video chooser | [Before](screens/what_add_before_1440.png) | [Before](screens/what_add_before_390.png) |
| After, centered Video chooser | [After](screens/what_add_after_1440.png) | [After](screens/what_add_after_390.png) |
| Article import | [Article](screens/what_add_article_1440.png) | [Article](screens/what_add_article_390.png) |
| Post reuse | [Post reuse](screens/what_add_post_reuse_1440.png) | [Post reuse](screens/what_add_post_reuse_390.png) |
