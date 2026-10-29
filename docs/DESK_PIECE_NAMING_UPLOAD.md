# Campaign piece names and browser media uploads

Branch candidate, 2026-10-29. No server restart, merge, push or live workspace
mutation is part of this build.

## Behavior

- The piece header offers Edit name, a 200-character Post name field, and Save
  name/Enter. Generic New post/image/video/article names prompt immediately.
  An archived piece has no editor. Failed writes retain the typed name.
- The title command uses DeskV1Store.write and the per-piece queue, PATCHing
  only title. Undo PATCHes the previous title and resolves the current family
  object, including after a workspace refresh or navigation remount.
- What, Where, calendar cards, article pickers and Studio family rows read the
  same family title. Filled calendar slots resolve by version ID, with their
  saved text retained as fallback for a missing/historical piece.
- Add media includes From this computer… alongside library and project files.
  It opens an image/* or video/* browser input synchronously from the menu
  click. A phone selects its own files; there is no host-side dialog.
- Local files pass through the existing M21 asset command and multipart route.
  The optimistic blob preview becomes the server asset URL after saving. Undo
  detaches the asset; it retains the library file. A library read failure is
  shown and does not disable local selection.

## Ownership

- `static/js/desk-v1-piece-title.js`, `static/css/desk-v1-piece-title.css`:
  title editing, saved header/crumb/Posy labels and reversible writes.
- `static/js/desk-v1-local-upload.js`: browser input, client validation, blob
  lifecycle. Existing `desk-v1-what.js` owns attach/undo/persistence.
- `desk-v1-piece.js` and `static/index.html`: minimal wiring only.
- Existing `PATCH /api/desk/pieces/<id>` and multipart
  `POST /api/desk/pieces/<id>/assets`: unchanged; no new backend route.

## Verification

Run from the checkout root with the main checkout's smoke dependencies linked
into this worktree; remove that junction without recursing before cleanup.

```text
python -m pytest tests/test_desk_pieces.py tests/test_desk_routes.py -q
node tools/smoke/desk-v1-piece-name-upload.mjs
node tools/smoke/desk-v1-live-pieces.mjs
node tools/smoke/desk-v1-piece-copy.mjs
node tools/smoke/desk-v1-piece.mjs
node tools/smoke/desk-v1-where.mjs
node tools/smoke/desk-v1-calendar.mjs
node tools/smoke/desk-v1-store.mjs
node tools/smoke/desk-v1-library.mjs
node tools/smoke/desk-v1-studio.mjs
node tools/smoke/desk.mjs
npm --prefix tools/smoke test
```

The new smoke uses the real SPA and hermetic APIs at 1440 and touch/mobile
390 pixels. Existing live-pieces smoke exercises the production live store
and a stateful server double, including a fresh workspace read after rename.
Backend tests cover multipart image/video attach, IDs, unsupported/empty/large
file refusal, partial-file cleanup, detach and title persistence in workspace
and article picker responses. Screenshots are generated under
`_scratch/piece-name-upload/{title-edit,add-media}-{1440,390}.png`.

Build validation: 115 backend checks passed; all ten listed Desk smokes and
the full npm smoke suite returned 0. The copy smoke had one earlier focus
assertion failure and passed an immediate isolated rerun; no copy code changed.
Initial invocations of nonexistent `desk-v1-what.mjs`/`desk-v1-live.mjs` names
were corrected to the actual `desk-v1-live-pieces.mjs` suite; those missing
script names are not counted as successful runs.

## Limits and integration

An actual phone upload over the tunnel is unverified. Browser file selection
is exercised at both viewport sizes; production paths are the same-origin
existing M21 route. No real user files were uploaded or edited.

Client checks mirror existing asset writer extensions and caps (25 MiB image,
500 MiB video). The application's existing global request limit is 50 MiB,
including multipart overhead, so Flask may reject larger video requests before
the asset writer. No request-limit policy changed in this build.

Revert this commit to remove the new controls. Existing titles/assets use the
same schema, so no data migration or rollback is needed.
