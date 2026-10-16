# Project files in the Material library

The Desk discovers supported pictures and videos across registered projects.
Studio, the shared **Pick from library** chooser, and What's upload source show
**From your projects**, with a project filter, filename/folder search, previews
and expandable project/folder groups. Existing-piece **Add media → From your
projects** opens the same chooser. Online sources remain reserved for drives.

Selecting a source saves a durable library copy before handing a relative
library path to the existing piece, storyboard, article or reference-image
consumer. Studio saves a copy without attaching it to a campaign. A source can
subsequently disappear without breaking campaign media; the existing Studio
delete guard protects a library file referenced by a piece or storyboard.

## Discovery and limits

`mc/desk_project_media.py` owns discovery, the cache, copy receipts and its Flask
blueprint. `server.boot()` starts one daemon thread; it
refreshes at startup, every 300 seconds, and when requested. Material GETs read
the index, never walk directories or transcripts. The index is atomically
replaced at `data/media/_desk_project_index.json`, outside the project-record
directory. The last successful snapshot survives a refresh failure/restart.

Per registered project, discovery merges its bounded `data/media/<pid>.jsonl`
tail (2 MiB) with a folder traversal capped at 2,000 media files, 20,000 directory
entries and depth 8. Caps and read failures log with `[desk-project-media]`;
depth-limit logging is bounded to one message per project per refresh. Chat
rows win deduplication, retaining session and agent names when recorded.
Supported extensions reuse `desk_pieces.IMAGE_EXTS` and `VIDEO_EXTS`.

Traversal skips `.git`, `node_modules`, `venv`, `.venv`, `__pycache__`, `dist`,
`build`, `.clayrune`, `_scratch`, and the common uploads directory. Symlinks
are not followed during traversal and resolved directories are visited once.
Shared uploads are included only when a project's chat-media index attributes
them; files already in `data/uploads/desk` are excluded. Historical uploads
without that attribution are deliberately skipped: the upload endpoint has no
project context and discovering an owner would require transcript scans.

`mc/media.py` now forward-indexes supported video paths and media `[file:]`
markers, including marker paths with spaces. Both existing agent stream hooks
record the selected agent name when available. No transcript backfill occurs.
The project Media gallery can play those videos and filter them separately.

## API and safety

| Endpoint | Contract |
| --- | --- |
| `GET /api/desk/materials` | Adds summary-only `projects: [{project_id,title,files,thumb,folders:[{title,files}]}]`; projects and folders follow newest-file order. No project file rows. `online` stays unchanged. |
| `GET /api/desk/project-media` | Same project summaries plus `updated_at`, `refreshing`, and a refresh error when present. Optional `q` searches project/file/folder/agent names; repeated `kind=image` / `kind=video` restrict counts. |
| `GET /api/desk/project-media/items?project_id=…` | One flat `items` page for that project, with optional exact `folder`, `q`, repeated `kind`, `offset` (default 0), `limit` (default 50, maximum 100). Returns `{items,total,offset,limit,next_offset}`; null next offset ends pagination. Missing project ID or invalid paging arguments return 400; unknown/removed project IDs return an empty page. |
| `POST /api/desk/project-media/refresh` | Queues a refresh in the existing worker; returns 202. |
| `GET /api/desk/project-media/file?id=…` | Previews one indexed picture/video; supports video range requests. |
| `POST /api/desk/project-media/copy` | JSON `{id}`; returns 201 with a library item and provenance. Accepts no arbitrary source path. |

Page items carry only `id`, `project_id`, `title`, `folder`, `kind`, `src`
(image) or `play` (video), and optional `agent_name`. No absolute path or full
index row is serialized, and no item list is duplicated inside folder groups.
The internal index retains size/mtime/origin and any session attribution for
copy provenance. Summary and page reads filter out removed projects but never
resolve or stat indexed paths. Their metadata can be stale until refresh:
a vanished file remains listed and preview/copy returns the existing 404
"refresh the list" error. Preview/copy look up an indexed ID and
re-authorize against current registered project roots, uploads and media.
Absolute paths with `..`, unsupported extensions, missing files and resolved
symlink escapes are refused. Drive-root projects do not expand the allowlist.
The existing `/api/serve-image` and `/api/serve-file` guards are unchanged.

Copies land at `data/uploads/desk/library/<kind>/Projects/<safe project name>/`.
The ordinary library enumeration supports that extra folder level. Existing
upload type/size limits apply. A serialized receipt map at
`data/media/_desk_project_copies.json` deduplicates by source path, project ID,
mtime in nanoseconds and size. Missing library copies are recreated. Source
changes during a copy refuse and remove the partial result. Receipts retain
source path, project, mtime, size and any known agent/session provenance; the
library listing and attached piece assets retain that provenance as well.
Both files are install data and inherit the existing `data/media/*` gitignore.

`static/js/desk-v1-project-media.js` owns the shared browser. The picker, Studio
and What hand it summaries. Opening a folder fetches 50 files; **Load more**
appends the next page. Debounced search fetches matching summaries and a page
per matching project, keeping kind restrictions on both reads. Late requests
cannot repaint a changed selection. Page-load failures show a retry button;
copy refusals stay visible without invoking the consumer. Refresh polls the
worker for up to a minute and reports failures.
The demo fixture never reads the live project index.

## Validation and handoff

- Unit coverage: `tests/test_desk_project_media.py` (caps/skips, path escapes,
  current-root checks, attributed uploads, persistence, concurrent copy dedupe,
  source loss/change, size limits and existing asset deletion protection;
  4,824-row summary payload below 50 KB, exact summary/page shapes, no per-row
  path resolve/stat, bounded pagination, search/kind/folder filters).
- Browser coverage: `tools/smoke/desk-v1-library.mjs` exercises existing library
  flows plus Studio/project previews, filter/search, copy refusal/retry and
  returned library paths, lazy fetch, page error/retry and distinct 50+5 file
  pages at 1440 and 390 pixels, with a demo isolation check.
- Type check: `pyright mc/desk_project_media.py`.

This is a branch build. Integration, a normal server restart and an open-tab
reload are needed before the new browser appears in an existing installation.
Do not infer live deployment from the direct discovery diagnostic.
