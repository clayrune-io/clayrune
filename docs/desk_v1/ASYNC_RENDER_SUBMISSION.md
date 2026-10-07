# Async storyboard submission (MC-1068)

Dave selected option A: create the render before upload, and carry any price
increase in a held record. Local branch delivery only. No merge, push,
server restart, live upload, live engine call or spending is part of this work.

## API and state

The existing POST `/api/desk/engines/renders` human/passcode gate is unchanged.
After local owner/model/shape, click-key, shown-total and spending-cap checks,
`mc/desk_render_worker.py` snapshots the storyboard and whitelists request
fields. It persists a render and its USD reservation before starting a server
thread. It returns HTTP 201 immediately with `status/submission_state: pending`.
There is no schema discovery, credential refresh, price RPC, upload, generation
or output polling in this HTTP path. Existing keys return HTTP 200 replay;
another click key for an active owner also returns the same render. The gate
still runs before either replay.

The worker marks the record `working`, refreshes schema evidence, prices the
saved scenes, checks caps, uploads/prepares every required picture and checks
the exact total and caps again. It uses the existing adapters and MC-1066
network retry/classification. No paid clip starts until every scene is prepared
and the full total passes both the shown-price comparison and existing caps.

- Success: `status: rendering`, `submission_state: submitted`. Existing free
  polling downloads clips, joins/crops them and transitions to ready or a
  joining hold. Studio displays Submitted while generation is outstanding.
- Higher price: `status/submission_state: held`, zero paid child jobs,
  zero unsent reservation. `hold` contains `kind` and `code`
  `render_price_changed`, `message`, `shown_total`, `prepared_total`, `currency`,
  `estimate`, `plan`, sanitized `request` and `board_fingerprint`.
- Other failure: a persisted failed record and a plain reason. Earlier child
  costs are retained if some clips already went out. A child's uncertain
  submission holds with `submit_uncertain` and prevents a new paid retry.

The initial POST no longer answers the later prepared-price increase with HTTP
409. Immediate malformed-input or cap refusals still use the existing HTTP
codes. Caps take precedence over price confirmation, so accepting a higher
price cannot override a limit or campaign budget. Missing/invalid `shown_total`
is refused before launching; valid shown totals use the engine's own unit.

## Reservations and interrupted calls

The parent initially reserves the displayed USD total in the existing engine
job store, outside the project-records directory. Plan-credit engines do not
convert credits to USD. The reservation participates in `budget_state`, so
other render owners, standalone generation jobs and the existing campaign
spending guard see that amount as unavailable. Exact pricing excludes only
the render's own parent hold from its cap check and reduces the reservation
to the prepared USD total.

As each child starts, `desk_engines.submit` transfers that part of the parent
hold to a durable child job and links it to the parent in the SAME write,
before calling the vendor. There is no double counting or unreserved gap.
Definitive/not-sent rejection removes the unsent child and its link. Unknown
acceptance retains the child and cost. Terminal submission releases only the
parent's unsent remainder. No passcode, credential, token, prepared media
arguments or vendor upload URL is persisted in the parent record.

Threads belong to the running server; this feature spawns no separate OS
worker process. A process-instance stamp and an active-worker set identify
lost startup work. The GET/latest read paths turn interrupted pending/working
records into `held/submit_interrupted`, release the provably unsent parent
remainder and retain all linked child reservations. They never re-submit a
paid call, including a child whose vendor acceptance was not saved. These
holds require investigation. `mc/desk_render_recovery.py` keeps polling only
linked children for free, downloading accepted clips and settling their costs
through the normal job path. A vendor-declared free failure releases its cost;
an accepted clip retains its billed expense. Missing scenes are never submitted.
Legacy ready/failed/rendering records remain readable.

POST `/api/desk/engines/renders/<render_id>/acknowledge`, in its own blueprint
`mc/blueprints/desk_render_recovery_routes.py`, accepts only these two held
submission kinds. It marks the parent failed with failure kind
`submission_acknowledged`, clears its hold and retains children, clips and
recorded costs. Missing records return 404; other states and repeated
acknowledgements return 409. The existing unattended-caller guard returns 403.
There is no passcode on this free action. The next Render still goes through
the unchanged passcode and spending gates. Linked children continue polling
even after acknowledgement; a racing GET cannot restore the old hold.

## Studio and saved offers

`desk-v1-render-price.js` matches a held offer against current owner,
engine/model/shape and project fields, plus the current free quote's
`board_fingerprint`. Owner-key order in Flask JSON does not affect matching.
The panel imports these helpers; the new concern is kept outside the engine
monolith. Backend submission orchestration was removed from that monolith
into its own worker module; only accounting/record hooks remain there.

Polling displays waiting, working, submitted, held or failed in plain words.
A price hold paints one explanation and replaces the button price. A fresh
page selects the saved render's engine/model/shape, gets a free quote, and
restores the offer only if its saved scenes still match. Price again, picker
changes, storyboard refreshes or a changed free quote invalidate the offer.
Explicit dismissal survives a reload in the same tab through session storage.
The second click uses a NEW key, rechecks the current free quote and asks for
the existing passcode at the displayed prepared total. Cancellation sends
nothing. An additional price increase holds again. Uncertain/interrupted
submission holds disable Render and show **I checked the engine dashboard,
allow a new render**. `desk-v1-render-recovery.js` sends that free acknowledgement,
refreshes the price/budget and keeps clips visible on the failed parent. The
next paid start requires a fresh passcode. In-flight progress reads cannot
overwrite the acknowledged record or a newer render.

## Verification

All tests and browser requests are scripted; none reach a vendor. Shared
adapter tests replace the worker-launch seam with deterministic completion;
the dedicated worker tests exercise queued transitions, real thread blocking,
simultaneous clicks and durable accounting independently.

Backend selection includes every `test_*.py` containing `higgsfield`, and
every Desk engine, render or spending-guard test. Exact command:

```text
python -m pytest -q -o addopts= tests/test_desk_engine_schemas.py tests/test_desk_engines.py tests/test_desk_render_price_confirmation.py tests/test_desk_render_recovery.py tests/test_desk_render_worker.py tests/test_desk_spend_guard.py tests/test_higgsfield_mcp_catalogue.py tests/test_higgsfield_mcp_snapshot.py tests/test_higgsfield_picture_render.py tests/test_higgsfield_preset_decline.py tests/test_higgsfield_quote_response.py
pyright mc/desk_render_worker.py mc/desk_render_recovery.py mc/blueprints/desk_render_recovery_routes.py
```

Browser checks use the main checkout's existing `tools/smoke/node_modules`
through a temporary junction, without installing dependencies or Chromium:

```text
node tools/smoke/desk-studio-render-worker.mjs
node tools/smoke/desk-v1-live-render.mjs
node tools/smoke/desk-studio-passcode-replay.mjs
node tools/smoke/human-proof-busy.mjs
node tools/smoke/desk-v1-studio.mjs
node tools/smoke/desk-v1-engine-vault-recovery.mjs
```

All three new modules pass Pyright basic. Including `mc/desk_engines.py` reports
the existing optional campaign-ID argument in the untouched ffmpeg-hold path;
the earlier picture-price work records the same diagnostic. No baseline
cleanup is included. `static/index.html` is unchanged; a separate boot smoke
is not required for this ticket. Logs, exit-code matrices and desktop/mobile
screenshots live under `_scratch/render_worker/` and are not committed.
The dependency junction is removed before committing.

Final backend result: **391 passed**; all six smoke files exited 0. The dedicated async smoke has **68 passing checks** and covers 1440px
and 390px, including reload, explicit dismissal, changed scenes, cancellation,
another price increase, single explanation, both submission holds, free linked
clip polling, acknowledgement, preserved clips and the next paid passcode.

| Check | Exit code |
| --- | --- |
| Selected backend suite (391 cases) | 0 |
| New worker and recovery modules Pyright basic | 0 |
| Async render / held quote / recovery smoke (68 checks) | 0 |
| Full live-render / legacy price-confirmation smoke | 0 |
| Studio passcode / replay smoke | 0 |
| Shared passcode busy smoke | 0 |
| Studio smoke | 0 |
| Engine vault recovery smoke | 0 |
| JS syntax and diff whitespace checks | 0 |

## Ron's UI test script (after Dave integrates)

Use your intended storyboard, engine, model and account. The builder has not
run these against a live account. The acceptance click in step 5 spends the
displayed engine credits; run it only when you want those clips generated.

1. Open Desk > Studio and the video storyboard. Confirm its selected model,
   pictures and free price. Press Render, enter the dashboard passcode once,
   and confirm. The popup should close promptly; the render panel should
   show Waiting to start, then Working while upload/submission continues.
2. While Working, Render is disabled. Reload once: the same render record
   should return and polling should continue. No additional clip generation
   should appear on the engine dashboard from that reload.
3. If pictures make the prepared price higher, the panel should stop at Held,
   show the old/new totals once and put the new total on Render. No generation
   should have started. Uploads can remain in the engine's media library.
4. Reload that held board. Its new total should remain. Press Render and
   cancel the passcode popup: no generation starts and the total remains.
   Price again should restore the free quote; a storyboard edit should also
   invalidate the older prepared offer.
5. To accept the prepared total, press Render again and enter the passcode.
   A further increase should hold again; an equal/lower price should reach
   Submitted, then Ready with saved clips/video. Your configured caps still
   apply. Check that only this accepted generation appears on the engine.
6. On a submission failure, expect Failed with a readable reason. If acceptance
   is uncertain or the server was interrupted, expect Held with an explanation,
   retained costs and disabled Render. Accepted linked clips should still finish
   and appear in the Material library. Check the engine dashboard, then press
   **I checked the engine dashboard, allow a new render**: no passcode popup or
   new generation should occur. The parent becomes Failed and retains its
   clips. Reload and confirm Render is available after the free price check.
   Starting a new render must ask for the passcode again. Missing scenes from
   the older record must never start automatically.

Round 2 fixes the permanent owner hold found in review of `4dfe95ea`.
The branch was rebased onto master `59e11b5b` first (prior async delivery is now
`10902573`). Recovery tests cover accepted and free-failed children, preserved
costs, new owner enqueue, invalid-state and unattended refusals, missing/repeated
acknowledgements, passcode continuity and a GET/acknowledgement race.

Integration and live observation remain Dave's handoff. Reverting the recovery
commit removes acknowledgement and strands linked-child polling on submission
holds again. Reverting the original async delivery restores synchronous startup;
investigate pending/working/held records first, because older code has no
async-startup recovery logic. No schema migration, dependency or server restart
is included; only the explicitly requested free human acknowledgement is added.
