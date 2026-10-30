# Delete a published platform post (MC-1126)

Implemented in `36df5fc0`, integrated by `731c8141`; no live platform
verification. This is separate from deleting an unsent Desk piece or archiving
its history.

## Human action

`POST /api/desk/pieces/<piece_id>/versions/<version_id>/delete-post`
accepts the retyped dashboard `passcode`. The target comes exclusively from the
stored version and receipt. Extra request fields cannot choose another account,
post id, platform or attribution. The gate matches version approval: refuse
`is_unattended_caller()` first, then `_require_human_passcode` on every call,
including repeats. No configured passcode, missing/wrong proof and throttling
retain the existing guard's refusals.

The piece page's Versions section offers the action for live-mode
`verified_published` versions on X/LinkedIn with a receipt. A confirm sheet
explains permanence; the existing `humanProofFetch` prompt collects proof.
Cancelling either step sends nothing. No optimistic mutation or Undo is used.
Errors remain beside the button as text, never interpreted as HTML.

## Platform and state contract

- Only stored `verified_published` versions are eligible. Unknown, submitted,
  manually reported, archived and draft versions are refused before token use.
- X uses the version's direct account and `desk_account_refs.oauth_arg`, then
  `desk_oauth.x_token(consumer='desk_post_delete', project_id=..., unattended=False)`.
  Refresh runs through the connector's existing lock and vault write path;
  rotated refresh tokens are saved before `DELETE /2/tweets/<receipt.post_id>`.
  Legacy account bindings remain supported. A missing account never falls back
  to somebody else's singleton login. No raw vault token reads exist here.
- X must confirm `data.deleted == true` (JSON boolean). HTTP/platform refusals
  return 502 `platform_refused` with bounded, redacted platform detail. No
  response or malformed JSON returns 502 `unknown_outcome`: the post may already
  be gone; no synthetic deletion record is written. A 404 is a refusal, not proof
  this call deleted the post. There are no automatic retries.
- LinkedIn returns 422 `unsupported` before any token or HTTP call. In this tree,
  `desk_oauth.SERVICES` has no LinkedIn refreshing adapter and organization
  posting remains disabled. A manually pasted LinkedIn token cannot prove
  scope or satisfy the refresh-path requirement, so it is never used here.
- Success adds version `deleted: {at: <UTC ISO timestamp>, by: 'human'}` and
  updates the piece timestamp. The receipt, published state, approval, ledger
  and original content remain unchanged. `v1_piece` projects the deletion
  record. Ordinary PATCH cannot forge or erase it. The piece page displays
  **Deleted from X** instead of the live-state label.
- A module lock serializes repeated deletes through persistence. Already
  recorded deletion returns the original piece without token/network use.
  Network calls do not hold the shared Desk lock; persistence re-reads and
  checks the receipt, preserving concurrent store edits. If saving fails after
  X confirms deletion, return 500 `record_failed` stating that the platform post
  is deleted and log the target/time for record repair. A crash between remote
  success and persistence can also leave an unrecorded deletion: check X and
  repair the local record rather than assuming an HTTP failure means it is live.

## Ownership and verification

`mc/desk_post_delete.py` owns target validation, OAuth use, HTTP and recording;
`mc/blueprints/desk_post_delete_routes.py` owns the route and gates, registered
as a child of the existing Desk blueprint. `static/js/desk-v1-post-delete.js`
owns the interaction, with small slots in the piece page and script registry.
No deletion concern is appended to either existing monolith.

Backend coverage: `tests/test_desk_post_delete.py`, plus existing piece,
approval, OAuth, publisher, tick, account-reference and route regressions.
All token and platform endpoints in the deletion tests are fakes. They exercise
the real refresh/rotation store path with an in-memory vault and fake transport.

Browser coverage: `node tools/smoke/desk-v1-post-delete.mjs` intercepts every
request and serves this worktree's actual assets. At 1440px and 390px it uses
the real confirmation and passcode UI, checks cancellation/retyping/refusals,
unsupported LinkedIn, unchanged receipt/state, reload persistence, and demo
mode suppression. Screenshot artifacts are under `_scratch/post-delete/`.
Existing piece, live-piece, live-review, Desk and boot smokes cover integration.

New modules must pass:

```text
pyright mc/desk_post_delete.py mc/blueprints/desk_post_delete_routes.py
```

No real sign-in, platform deletion, merge, push or restart is part of this task.

## Verification follow-up (2026-10-29)

The repeated MC-1126 dispatch started at `c11af60b`, which already contains the
implementation and its integration commit. This follow-up changes only tests
and this document; it does not rebuild or redeploy the feature. Seven additional
cases use the real `is_unattended_caller` function with isolated session state:
dispatch/scheduled/unstamped sessions are refused even with proof, and neither
a forged Origin nor a manual agent chat replaces the retyped passcode. Refusals
leave the store unchanged and perform no token reads or platform calls.

Commands run from the worktree root, all with fake platform endpoints:

| Command | Result | rc |
| --- | --- | --- |
| `python -m pytest tests/test_desk_post_delete.py tests/test_desk_pieces.py tests/test_desk_oauth.py tests/test_desk_publish.py tests/test_desk_publish_linkedin.py tests/test_desk_tick.py tests/test_desk_account_refs.py tests/test_desk_routes.py -q -o addopts=''` | 323 passed | 0 |
| `pyright mc/desk_post_delete.py mc/blueprints/desk_post_delete_routes.py` | 0 errors | 0 |
| `node tools/smoke/desk-v1-post-delete.mjs` | 1440px and 390px passed | 0 |
| `node tools/smoke/desk-v1-piece.mjs` | Passed | 0 |
| `node tools/smoke/desk-v1-live-pieces.mjs` | Passed | 0 |
| `node tools/smoke/desk-v1-live-review.mjs` | Passed | 0 |
| `node tools/smoke/desk.mjs` | Passed | 0 |
| `node tools/smoke/boot-smoke.mjs` | 7 boot scenarios and all guards passed | 0 |

The deletion screenshots at `_scratch/post-delete/deleted-{1440,390}.png` were
also inspected. Both show the retained version and human deletion record.
The main checkout's smoke dependency junction was used without installation
and removed before committing. A variable-driven smoke launcher was rejected
by the unattended command fence before execution; the literal foreground
commands above subsequently ran successfully. No live sign-in, post, delete,
server restart, merge or push was performed. LinkedIn remains explicitly
unsupported because this tree has no scoped, refreshing LinkedIn OAuth adapter.

No user-facing behavior changed in this follow-up; the existing user guide and
MC-1126 changelog entry remain applicable. Live platform behavior is unverified.
