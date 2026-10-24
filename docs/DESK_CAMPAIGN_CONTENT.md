# Campaign content lifecycle and placement (MC-1112)

What and Where use persisted pieces. A suggestion is an unplaced proposal,
projected with a `suggestion` marker and **no versions**, until its first
action materializes it. The existing campaign record retains the original
suggestions and a `suggestion_pieces` fingerprint-to-piece mapping. This makes
acceptance idempotent, avoids duplicate rows after reload, and prevents a
deleted accepted post from returning as a fresh proposal.

## Lifecycle

`mc/desk_piece_lifecycle.py` owns whole-post archive and reversible removal.
`mc/desk_pieces.py` exposes `archivedAt`, refuses archived edits/approval, and
shares the existing version-state withdrawal and piece-delete cascade.
Archive stores its timestamp and server-owned snapshots of affected versions
on the **existing piece record**. Mutable versions enter `archived`; sent
versions remain live history and can still receive their receipts. Restore
reinstates the exact saved versions, including prior human approval stamps,
without rewriting history that advanced during archive. No client can PATCH
an archive snapshot or grant an approval. Archived text, assignments, media
and storyboard writes are blocked until restore.

Delete uses the existing Studio disk trash manifest/token and its existing
restore route; there is no second trash store. The snapshot includes the
piece and its storyboard. Library files remain in place and are protected
from library deletion for the undoable post's trash lifetime. Restoring a
deleted approved draft returns it to review. Sending/sent pieces cannot be
deleted, using the existing refusal.

Routes (ordinary reversible data operations; none publishes):

| Method | Route | Result |
| --- | --- | --- |
| POST | `/api/desk/pieces/<id>/archive` | Saved piece |
| POST | `/api/desk/pieces/<id>/unarchive` | Restored piece |
| POST | `/api/desk/pieces/<id>/trash` | Existing trash token |
| POST | `/api/desk/studio/trash/<token>/restore` | Existing restore contract |
| POST | `/api/desk/campaigns/<id>/materialize` | Idempotently saved unplaced piece; body `{title,channel_id?}` |

## Placement and editor boundaries

Where's column drop only changes `plan.accounts`. Suggestion acceptance no
longer fabricates a planned version with its suggested channel. A message
drop materializes first, then POSTs a real version; existing pieces keep all
assignments. Source tiles include disconnected/read-only configured channel
accounts; account capabilities and publishing/human gates do not change.
Column X reuses the existing plan removal/version archive operation. Version
X archives its unsent assignment. Undo of a removed approved assignment
returns it to review, following the existing writable-state contract.

`desk-v1-article-media.js` owns inline editor widgets, serializing them back
to portable library Markdown references. Both writer entry points share it;
`desk-v1-article-pictures.js` remains the old caller compatibility bridge.
Images and video use existing library URLs. Additional library files are
listed by `mc/desk_article_media.py`, with audio playback and links for other
files. Discovery uses the existing serve-file secrets denylist, root checks,
and file-serving behavior; it creates no new import/upload path. Embedded
references participate in the existing Studio/library usage protection.

## Validation and integration

`tests/test_desk_piece_lifecycle.py` exercises exact version-state round trips,
mixed sent history, empty posts, archived write guards, existing token undo,
reference protection, and idempotent suggestion materialization/placement.
`tools/smoke/desk-v1-campaign-content.mjs` runs real pointer drags and reloads
against a temporary Flask/filesystem fixture, with 1440/390 screenshots under
`_scratch/mc1112/`. It never imports the application server or reads live Desk
data, credentials, or browser profiles. Its sample video uses local ffmpeg.

Also run the existing campaign, Where, live-pieces, Studio, Studio article,
Studio delete, library, piece and boot smokes. The campaign Resume success
fixture uses a future deadline; the explicit expired-deadline case remains.

MC-1111 overlap: `mc/desk_pieces.py`, `static/index.html`,
`static/js/desk-v1-library-picker.js`, and `static/js/desk-v1-studio.js`.
The two MC-1111 `desk_library_folders.decorate_materials` import/call lines
are untouched. The picker retains playback/download metadata and offers file
links; Studio has editor serialization/render/wiring hooks and starts new live
drafts empty instead of using demo sample text. Follow the
normal post-merge smokes when integrating after MC-1111. No merge, push or
server restart is part of this branch delivery.

## Branch verification record (2026-10-23)

218 Python checks passed; the four new Python modules have zero type errors.
All eleven smoke scripts passed, including two consecutive final runs of the
real Flask/browser regression. The final fixture's state read uses the same
Desk lock as production reads (avoiding Windows read/replace contention).

| Exact command from repository root | rc |
| --- | --- |
| `python -m pytest -o addopts= tests/test_desk_piece_lifecycle.py tests/test_desk_pieces.py tests/test_desk_studio_items.py tests/test_desk_studio_articles.py tests/test_desk_tick.py tests/test_desk_storyboard.py tests/test_desk_workspace.py tests/test_desk_accounts.py -q` | 0 |
| `pyright mc/desk_piece_lifecycle.py mc/desk_suggestion_pieces.py mc/desk_article_media.py mc/blueprints/desk_piece_lifecycle_routes.py` | 0 |
| `node tools/smoke/desk-v1-campaign-content.mjs` | 0 |
| `node tools/smoke/desk-v1-campaign-content.mjs` | 0 |
| `node tools/smoke/desk-v1-campaign.mjs` | 0 |
| `node tools/smoke/desk-v1-where.mjs` | 0 |
| `node tools/smoke/desk-v1-live-pieces.mjs` | 0 |
| `node tools/smoke/desk-v1-studio.mjs` | 0 |
| `node tools/smoke/desk-v1-studio-article.mjs` | 0 |
| `node tools/smoke/desk-v1-studio-delete.mjs` | 0 |
| `node tools/smoke/desk-v1-library.mjs` | 0 |
| `node tools/smoke/desk-v1-piece.mjs` | 0 |
| `node tools/smoke/desk-v1-store.mjs` | 0 |
| `node tools/smoke/boot-smoke.mjs` | 0 |

Screenshots (both widths; isolated fixture, not live user data):

- `_scratch/mc1112/where-drop-hint-1440.png`
- `_scratch/mc1112/where-empty-tile-1440.png`
- `_scratch/mc1112/where-unconnected-dropped-1440.png`
- `_scratch/mc1112/where-x-removed-1440.png`
- `_scratch/mc1112/what-archived-1440.png`
- `_scratch/mc1112/what-restored-1440.png`
- `_scratch/mc1112/what-article-media-1440.png`
- `_scratch/mc1112/where-drop-hint-390.png`
- `_scratch/mc1112/where-empty-tile-390.png`
- `_scratch/mc1112/where-unconnected-dropped-390.png`
- `_scratch/mc1112/where-x-removed-390.png`
- `_scratch/mc1112/what-archived-390.png`
- `_scratch/mc1112/what-restored-390.png`
- `_scratch/mc1112/what-article-media-390.png`

Raw command/return-code records, output logs and UI assertions are in
`_scratch/mc1112/`. Smoke dependencies use the main checkout's existing
node_modules through a temporary junction; no packages were installed. The
junction is removed before commit. This verifies the branch in isolation;
post-MC-1111 merge and live activation remain integration work.
