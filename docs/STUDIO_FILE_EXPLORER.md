# Studio library folders and host explorer (MC-1111)

## Scope and compatibility

The host is read-only. Home and current registered project paths are the only
browse roots. Each request reads current project registrations; a removed
external project loses access immediately. Secret names, hidden/system paths,
browser directory names and browser profile structural markers are denied on
both lexical and resolved paths. `mc/desk_explorer_guard.py` inspects each
component with lstat before resolution, rejecting symlinks/reparse points even
above a registered root. Root registrations retain their lexical path so they
cannot erase a link ancestor. Name rules protect AppData, Application Data,
Local Settings and Windows legacy private aliases independently of attributes;
home Library is denied on every platform. Windows hidden/system attributes and
macOS UF_HIDDEN are checked (the filesystem anchor itself is exempt from hidden
flags because Windows marks the drive anchor hidden/system). Firefox markers cookies.sqlite,
key4.db and logins.json join the Chromium profile checks. Preview/copy verify the final opened file
handle, not just its pre-open path. Arbitrary non-media bodies are never served.

`mc/desk_file_explorer.py` owns roots, shortcuts and bounded directory/search
listing (2,000 results, 3,000 entries, depth 8, two-second traversal budget).
Directory chains are checked once per visited directory; immediate entries use
lstat and name/profile checks. `mc/desk_explorer_file.py` owns
opened-handle verification (Windows final handle name, Linux proc fd, macOS
F_GETPATH). Their blueprint is `mc/blueprints/desk_file_explorer_routes.py`.

`mc/desk_library_folders.py` owns user names, mixed-media aggregation,
provenance and deletion. Its blueprint is
`mc/blueprints/desk_library_folders_routes.py`. New UI is isolated in
`static/js/desk-v1-library-folders.js`, `static/js/desk-v1-file-explorer.js` and
`static/css/desk-v1-file-explorer.css`. Existing monoliths contain wiring only.

Existing `desk/library/<image|video>/<folder>/` paths stay intact. New folders
use stable generated physical names within the same layout. Display names live
in `uploads/desk/_library_folders.json`; copying receipts in
`uploads/desk/_explorer_copies.json`. Neither is under project-record DATA_DIR.
The existing materials API retains its kind-indexed shape with renamed display
titles/provenance; Studio merges both kinds into one folder. The shared picker
merges same-title folder chips while still filtering files by target kind.
No migration or rewriting of saved piece/storyboard paths is needed.

Deleting a folder holds the Desk store lock, reuses Studio's `_references`, and
conservatively checks other stored paths (including article markdown/reference
images). Any reference refuses deletion (409). Non-media or nested unknown
contents also refuse deletion. Unreferenced folders move to
`uploads/desk/_folder_trash/<token>/<image|video>/`; the manifest retains the
folder key/name. There is no automatic purge or UI Undo for folder trash.
Operators can restore those directories to their original kind/folder paths;
the generated folder's display name can be restored from its manifest. Deleting
a legacy folder does not prevent future automatic uploads from recreating it.

## API

| Method | URL | Input/output |
|---|---|---|
| GET | `/api/desk/library/folders` | `{folders:[{id,title,files,items}]}` |
| POST | `/api/desk/library/folders` | `{title}` → created folder |
| PATCH | `/api/desk/library/folders` | `{id,title}` → renamed folder |
| DELETE | `/api/desk/library/folders` | `{id}` → `{deleted:true}` or 409 |
| POST | `/api/desk/library/folders/copy` | `{id,files:[{root,path}]}` → `{items,errors,folder}` |
| GET | `/api/desk/explorer` | `root=home|project-…`, relative `path`, `q`, `filter=media|all`, `client`, `search` |
| DELETE | `/api/desk/explorer/search` | `client,search`; cancels that request only |
| GET | `/api/desk/explorer/preview` | `root,path`; supported media only, range requests supported |

All-files exposes names/size only and disables selection for unsupported types.
The server refuses filter=all with 403 unless the peer is loopback with no
Cloudflare headers; loopback tunnel requests are remote. Responses include
`allow_all_files`; the UI offers the option only when explicitly true.

`mc/desk_explorer_search.py` serializes traversal per client cancellation channel
(peer, tunnel client IP, browser page ID), cancels the prior lease and bounds
the live registry to 128 clients (429 when full). Waiting consumes the same
two-second budget. Browser input/navigation aborts the prior fetch and sends a
targeted cancellation; closing clears its debounce and cancels pending work.
The request `search` ID prevents a late DELETE cancelling the next search.
The route checks a closed Werkzeug socket when available, and traversal checks
the cancellation/deadline between entries and directories. Budgets are
cooperative: an individual blocked OS filesystem call cannot be preempted.
`truncated` (and compatible `limited`) signals partial results in the UI.

`mc/desk_explorer_copy.py` owns preflight and staged transfer. Copy is capped at
50 files and 1 GiB total, with existing 25 MB/image and 500 MB/video limits.
Every accepted source handle is verified/opened before payload writes; aggregate
limit refusal is 413, insufficient free space is 507. A process-wide reservation
subtracts concurrent explorer transfers and preserves 2 GiB free. Reservation
accounting is conservative while staged bytes consume actual free space;
unrelated writers are not reserved, so each chunk also rechecks free space.
Source growth/shrink cannot exceed the preflight allocation. Transfers use a
request-owned temporary directory under uploads/desk/_explorer_staging, outside
the library lock. Only the final folder recheck, same-filesystem rename and
receipt update hold that lock. A concurrent folder delete wins; copy refuses
publication instead of recreating it. Temporary files/reservations are released
on normal error paths; a process crash may leave unlisted staging files for
operator cleanup. There is no cumulative library quota.
Each selected file is copied separately, reauthorized, checked for source changes,
and given provenance. Partial outcomes are explicit; failures stay selected for
retry. Source files are never written, moved or deleted. Runtime faults log and
return plain errors without leaking OS paths to the UI.

## Validation and integration

The task journal records exact commands/results and screenshot locations.
`tests/test_desk_file_explorer.py` covers scope, links/junctions, secrets,
handle mismatch, byte ranges, copy limits/provenance, rename compatibility and
reference-protected deletion. `tools/smoke/desk-v1-file-explorer.mjs` exercises
the real SPA with a fake API at 1440 and 390. Existing Desk, Studio, library and
boot smokes cover wiring and older consumers.

This implementation is branch-only. Wren audit F1-F6 are addressed; F7 absolute
source-path receipts, F8 render-job references/trash retention, and F9 loopback
CORS remain follow-ups by scope. After integration: verify live host
shortcuts/media on the target system.
Windows handle/junction behavior is locally exercised; macOS/Linux native handle
behavior and redirected/localized user folders require platform acceptance.
