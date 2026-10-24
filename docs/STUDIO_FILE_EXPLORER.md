# Studio library folders and host explorer (MC-1111)

## Scope and compatibility

The host is read-only. Home and current registered project paths are the only
browse roots. Each request reads current project registrations; a removed
external project loses access immediately. Secret names, hidden/system paths,
browser directory names and browser profile structural markers are denied on
both lexical and resolved paths. Preview/copy verify the final opened file
handle, not just its pre-open path. Arbitrary non-media bodies are never served.

`mc/desk_file_explorer.py` owns roots, shortcuts and bounded directory/search
listing (2,000 results, 20,000 entries, depth 8). `mc/desk_explorer_file.py` owns
opened-handle verification (Windows final handle name, Linux proc fd, macOS
F_GETPATH). Their blueprint is `mc/blueprints/desk_file_explorer_routes.py`.

`mc/desk_library_folders.py` owns user names, mixed-media aggregation, copying,
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
| GET | `/api/desk/explorer` | `root=home|project-…`, relative `path`, `q`, `filter=media|all` |
| GET | `/api/desk/explorer/preview` | `root,path`; supported media only, range requests supported |

All-files exposes names only and disables selection for unsupported types.
Copy is capped at 50 files, with existing 25 MB/image and 500 MB/video limits.
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

This implementation is branch-only. Before merge: independent Wren security
audit. After integration: verify live host shortcuts/media on the target system.
Windows handle/junction behavior is locally exercised; macOS/Linux native handle
behavior and redirected/localized user folders require platform acceptance.
