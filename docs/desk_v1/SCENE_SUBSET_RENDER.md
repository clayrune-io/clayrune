# MC-1070: render one or selected scenes

## Delivery boundary

Independent implementation only, on `clayrune/agent/f138abc66aec`.
**Not wired, not shipped.** MC-1068 must first land on master; the current
production render planner ignores `scene_ids`. The new JS module is not loaded
or mounted by the page. Do not manually connect it to the old synchronous path.
No paid render, live upload, merge, push, server restart or config change was
performed. Existing passcode and higher-price gates stay intact.

## Implemented modules

`mc/desk_render_scenes.py` contains pure helpers with no transport or generation
submission function:

- `selected_board`: validate explicit nonempty unique scene IDs; reject deleted
  IDs; filter in storyboard order without mutating the saved board.
- `subset_request`: canonicalize scene IDs, retain owner/project, engine/model,
  shape, idempotency key and the shown total. Omitted IDs mean a full render;
  explicit null, empty or malformed IDs refuse instead of spending on everything.
- `price_subset`: narrow the board **before** invoking a board-aware planner
  callback. Unselected scenes are not priced, validated for generation, prepared
  or uploaded. This seam awaits production integration.
- `validate_previous`: refuse cross-owner reuse, an active prior render, or a
  different final video shape before preparing or spending. A full regeneration
  does not need a reuse snapshot.
- `replacement_children`: replace precisely the chosen children with new job IDs,
  preserve untouched ready jobs with saved outputs, discard deleted scenes,
  order the combined children by the current storyboard. It records generated
  versus reused job IDs and explicitly names scenes with no saved clip.
- `rebuild_outputs`: pass downloaded outputs to a local join/crop callback;
  refuse until all included jobs are ready. It never submits generation.

`static/js/desk-v1-render-scenes.js` owns scene-card checkboxes, **Render this
scene**, **Render selected (N)**, the subset quote and explicit repricing. It
imports the existing price-confirmation helpers. A single scene's first click
obtains and displays its exact subset quote; a second click opens approval.
Selections price automatically after a short debounce.

Every paid request uses the existing `window.humanProofFetch`. A cancelled
prompt sends nothing. A changed free quote stops before opening the prompt;
a prepared-price 409 is displayed and requires another click and another
passcode approval. Credits and dollars use their own displayed amount.
Quotes and prepared confirmations clear on `refresh()` after board/model/shape
changes. Refused single-scene quotes can be retried after correcting a limit.

The mount takes `request(sceneIds)`, `onRender(render)`, `busy()`, `onBusy(on)`,
`alive()`, and optional persistent `state`. The caller supplies the current
shared engine pick, receives the new job for existing polling, and synchronizes
both full and subset submission busy states. It must call `refresh()` after
storyboard edits or picker changes and `dispose()` when leaving the surface.

## Required integration after MC-1068

1. Rebase this branch after an MC-1068 commit is visible on master. Read its
   actual worker/status/confirmation contract before modifying any shared file.
2. Add a board-aware seam to the existing planner (or call `selected_board`
   immediately after loading the board). Both estimate and render must use it.
   Do not obtain a full quote and subtract unselected prices.
3. Resolve the latest prior raw render and jobs server-side by the validated
   owner. Never accept prior job IDs or an arbitrary render snapshot from the
   browser. Validate a reusable settled snapshot before upload or spend;
   freeze its children when accepting the new worker task. Use the same store
   lock to prevent competing submissions from reusing a moving snapshot.
4. Keep the route's human/passcode check, idempotency, campaign/per-job caps,
   picture preparation and prepared-price confirmation on the existing worker
   path. Only selected rows go through picture preparation and paid submit.
   Accepting a higher price never overrides a spending cap.
5. Store reuse metadata on the new render; merge new and reused children after
   submission. Poll and locally join all included clips through the existing
   finalizer. Count only `generated_job_ids` in the new render's charged-cost
   display; retaining an old job is not a new charge. Preserve the previous
   render and its files. A no-prior single scene produces just its clip(s), with
   `complete_storyboard=false` when other scenes lack clips; do not label it a
   completed full storyboard or overwrite a full-video attachment with it.
6. Import/mount the controls from the shared Studio/Director render panel.
   Provide a separate toolbar slot and refresh after scene edits/reordering,
   engine/model/shape changes and vault recovery; share busy state with full
   Render. The new module has no fallback paid-submit path.
7. Add actual integrated route/worker regressions: request subset persisted to
   worker, vendor call counts, reused jobs untouched, subset 409/caps/passcode,
   active-render race, no-prior result and local finalization. Update USER_GUIDE
   and rerun the render/Studio smokes before declaring the feature usable.

## Verification of the independent slice

| Exact command (from this worktree root) | Result |
| --- | --- |
| `python -m pytest tests/test_desk_render_scenes.py tests/test_desk_engines.py tests/test_desk_render_price_confirmation.py -o addopts='' -q` | rc=0; 137 passed |
| `pyright mc/desk_render_scenes.py` | rc=0; 0 errors/warnings |
| `node --check static/js/desk-v1-render-scenes.js` | rc=0 |
| `node tools/smoke/desk-v1-render-scenes.mjs` | rc=0; 1440/390, real shared passcode modal, mocked render endpoint |
| `node tools/smoke/desk-v1-live-render.mjs` | rc=0; existing full-render/confirmation regression |
| `node tools/smoke/desk-studio-passcode-replay.mjs` | rc=0; real prompt/caller, 201/200 and 1440/390 |
| `node tools/smoke/desk-v1-studio.mjs` | rc=0 |
| `node tools/smoke/desk-v1-studio-article.mjs` | rc=0 |
| `node tools/smoke/desk-v1-studio-capture.mjs` | rc=0 |
| `node tools/smoke/desk-v1-studio-delete.mjs` | rc=0 |

The pytest planner seam supplies a narrowed board to the real existing engine
planner **only inside the test**, using a temporary monkeypatch. It establishes
1/N/all prices and call counts without wiring production to the synchronous
path. The passcode check tests the existing guarded route with a subset field;
the prepared-price check exercises the narrowed real picture-preparation plan.
Neither is evidence of an integrated subset worker/route, which remains pending.

The Playwright harness mounts the new controls explicitly on three fixture cards
inside the real dashboard and mocks every API/vendor response. It checks no
viewport overflow or page exceptions; screenshots are fixture controls, not a
claim that the real storyboard is wired. Artifacts: `_scratch/mc1070/` (ignored).
The main checkout's existing `tools/smoke/node_modules` was temporarily linked
into this worktree. The link was moved out of `tools/smoke` into ignored
`_scratch/mc1070-node-modules` before commit; the main dependencies remain
intact and nothing was installed. The unattended fence refused deleting the
junction; this reversible relocation removed it from the commit candidate.

The scratch check runner registered each owned smoke PID with Clayrune. Its
first batch stopped after a Windows console encoding exception printing the
already-passing full-render log; the runner was corrected and remaining smokes
were run. The new controls were rerun after final UI changes.

## Ron's five-line UI test (after integration only)

1. After Dave lands the backend, a **server restart is required**; hard reload the page to load the new JS (a reload alone cannot enable this slice).
2. Open Desk > Studio > a rendered video's storyboard; price/approve **Render this scene** on one edited scene, then verify other clips are reused and the video is rebuilt.
3. Select two scenes; verify **Render selected (2)** shows exactly their price, approve it, and verify only those two clips change.
4. Cancel a passcode prompt: no render starts; if pictures raise the price, verify it stops and requires another Render click and passcode before generation.
5. On a new storyboard, render one scene: only that clip is produced; repeat at phone width and verify all controls fit.

## Documentation and environment

USER_GUIDE is intentionally unchanged: these controls cannot yet be discovered
in the product. No README/agent-rule changes are needed for a dormant slice.
The document/commit and changelog skills were used with the task's explicit
no-push/no-merge/no-restart boundary overriding their general shipping steps.
Engram tools are unavailable in this session; continuity is recorded here and
in the project journal. A global plan-file write was rejected by the unattended
fence as an out-of-project `~/.claude` config edit; the plan and exact remaining
integration steps are recorded in this document instead.
