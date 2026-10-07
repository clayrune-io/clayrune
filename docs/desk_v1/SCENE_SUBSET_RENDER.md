# Render one or selected scenes (MC-1070)

## Delivery boundary

Wired and verified on `clayrune/agent/f138abc66aec`, rebased onto master
`a405a363` (MC-1068). Branch delivery only: no merge, push, server restart,
real upload, paid generation or configuration change. Dave owns integration
and live-account verification. The earlier isolated foundation is `cde85e1d`
after rebase (previously `6345a437`).

## Request and clip contract

Optional `scene_ids` selects a nonempty, unique list of existing scene IDs.
Omitting the field retains the full render. Explicit null, empty, duplicate or
unknown IDs refuse rather than falling back to everything. Selection is
canonicalized in storyboard order before pricing or submission preparation.
One/N/all selected prices come from the same planner as full Render; no full
quote is obtained and then subtracted. Unselected pictures are not uploaded.

`mc/desk_render_scenes.py` owns validation, ready-clip snapshots and final
assembly order. Under the enqueue lock, it freezes the selected storyboard,
the full current scene order and ready saved outputs from the latest same-owner
render. Snapshots are resolved server-side; the browser cannot supply job IDs,
prior clips or arbitrary render records. A reused clip requires the same final
aspect ratio. Selecting every scene permits a new shape without reuse.

The new parent's `children`, progress, reservations and cost cover ONLY newly
submitted jobs. `base_scenes` privately holds saved outputs for local assembly;
it never participates in submission or charged-cost accounting. Sequential
subset renders retain both earlier replacements and untouched clips. Deleted
scenes drop out and the current storyboard determines assembly order. Original
records and files are preserved. Square-crop metadata follows reused clips.

The existing local join/crop finalizer consumes new outputs and saved clips.
`scene_ids`, `reused_scene_ids`, `missing_scene_ids` and `complete_storyboard`
are exposed for UI feedback. With no prior clips, rendering one scene produces
that clip and labels missing scenes as a partial video. A partial result does
not replace a piece's full-video attachment. It never pays for missing scenes.

## MC-1068 behaviour retained

The existing human/passcode route guards every submission, including replays.
The route creates its durable pending record before any remote call. Its board,
clip count and whitelisted request snapshot describe only the selection;
passcodes and credentials are not persisted. Active owner clicks still replay.

The same worker prepares every chosen scene, checks caps and compares the exact
prepared total with `shown_total` BEFORE any generation. MC-1068 carries a
later `render_price_changed` in an asynchronous held record after HTTP 201;
the initial POST does not synchronously return that later price as HTTP 409.
Existing 409 confirmation helpers remain supported. A higher price requires a
new click, a new key and the same passcode gate. Acceptance cannot bypass caps.

Submission holds, atomic parent-to-child reservation transfer, free linked-clip
polling and human acknowledgement are unchanged. Unknown or interrupted paid
calls are never automatically retried. Acknowledgement retains clips and costs;
the next subset or full Render still needs its passcode. Reused clips stay
outside `children`, so recovery cannot submit or re-charge them.

## Studio and Director

`static/js/desk-v1-render-scenes.js` and its own CSS own card checkboxes,
**Render this scene**, **Render selected (N)** and **Price selected again**.
Studio and Director pass their scene containers to the shared render panel.
The first single-scene click obtains and displays its quote; the next opens
the real shared passcode prompt. Multiple selections price automatically.
Cancelling sends nothing. Shared busy state disables full and subset actions
while either passcode flow or a render submission is active.

The shared panel adopts subset records into its existing poll/recovery flow.
A saved price offer matches owner, selected IDs, engine/model/shape/project and
the selected board fingerprint. Reload restores the held price and multi-scene
checkboxes. A subset offer cannot replace the full Render quote. Explicit
repricing, storyboard edits and picker/vault refresh invalidate it; dismissal
survives reload in the same tab. A changed fresh price requires another click.

Shared Python/JS files contain only wiring to these modules. No new dependency,
boot entry, alternative approval path or vendor-submission implementation was
introduced. `static/index.html` is unchanged.

## Verification (2026-10-07)

Exact commands from the worktree root:

```text
python -m pytest -q -o addopts= tests/test_desk_engine_schemas.py tests/test_desk_engines.py tests/test_desk_render_price_confirmation.py tests/test_desk_render_recovery.py tests/test_desk_render_worker.py tests/test_desk_spend_guard.py tests/test_higgsfield_mcp_catalogue.py tests/test_higgsfield_mcp_snapshot.py tests/test_higgsfield_picture_render.py tests/test_higgsfield_preset_decline.py tests/test_higgsfield_quote_response.py tests/test_desk_render_scenes.py tests/test_desk_render_scenes_flow.py
pyright mc/desk_render_scenes.py mc/desk_render_worker.py mc/desk_render_recovery.py mc/blueprints/desk_render_recovery_routes.py
```

| Exact check | rc / result |
| --- | --- |
| Backend command above (MC-1068 suite + subset tests) | 0; 435 passed |
| Pyright command above | 0; no errors/warnings |
| `node tools/smoke/desk-studio-render-worker.mjs` | 0; 68 checks |
| `node tools/smoke/desk-v1-live-render.mjs` | 0 |
| `node tools/smoke/desk-studio-passcode-replay.mjs` | 0 |
| `node tools/smoke/human-proof-busy.mjs` | 0 |
| `node tools/smoke/desk-v1-studio.mjs` | 0 |
| `node tools/smoke/desk-v1-engine-vault-recovery.mjs` | 0 |
| `node tools/smoke/desk-v1-render-scenes.mjs` | 0; independent controls, 1440/390 |
| `node tools/smoke/desk-studio-render-scenes.mjs` | 0; 64 checks, Studio/Director at 1440/390 |
| `node --check static/js/desk-v1-render-scenes.js` | 0 |
| `node --check static/js/desk-v1-engines.js` | 0 |
| `node --check static/js/desk-v1-render-price.js` | 0 |
| `node --check static/js/desk-v1-studio.js` | 0 |
| `node --check static/js/desk-v1-video.js` | 0 |
| `git diff --check` | 0 |

Backend tests use the actual guarded routes, durable worker, planner, accounting
and finalizer with mocked vendor/ffmpeg fixtures. They cover one/N/all prices,
malformed IDs, durable selected snapshots, unchanged replay, untouched files,
sequential reuse, partial results, one-picture upload, held pricing, passcodes,
caps, uncertain submission, acknowledgement and square-crop retention.

Browser checks use the real shared render panel/passcode modal and actual card
CSS with fixture scenes and mocked APIs. They cover cancelled approval, pending
and working states, higher prepared prices, single/multi offer reload, correct
subset totals, full quote isolation, clip-reuse feedback, submission holds,
free acknowledgement, the next passcode and viewport/runtime errors. They are
not live account or real generation evidence. Screenshots/logs/rc matrix live
in ignored `_scratch/mc1070/`. Generated tracked PNGs are restored before commit.
The main checkout's existing smoke dependencies are linked temporarily;
the worktree junction is relocated into ignored scratch before commit. No
packages or browsers are installed. Owned smoke processes are registered with
Clayrune by the scratch runner. No project or server process is stopped.

## Ron's five-line UI test (after Dave integrates)

1. Restart the server after backend integration, then hard reload to load the new JS/CSS; a hard reload alone cannot update the Python worker.
2. Open a rendered storyboard in Studio or Director, edit one scene, price/approve **Render this scene** and verify the other saved clips are reused in the rebuilt video.
3. Select two scenes, review **Render selected (2)** and its exact price, approve and verify only those two clips are newly generated.
4. Cancel a passcode prompt; then, if pictures raise the price, verify Held starts no generation, reload restores the same selection/price, and another click/passcode is required.
5. Render one scene on a new storyboard: verify the partial result names missing clips; at phone width verify controls fit, and an uncertain/interrupted hold requires dashboard checking and free acknowledgement before another paid approval.

## Continuity

The task's explicit branch-only boundary overrides general shipping steps in
the document/commit and changelog skills. No open design decision remains.
The global plan write was fenced in the initial slice; this report and the
project journal hold the plan and recovery context. Runtime-attributed memory
is unavailable; no memory ID was invented or another session reused.
