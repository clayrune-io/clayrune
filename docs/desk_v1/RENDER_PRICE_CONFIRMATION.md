# Studio render price confirmation

Dave's specified fix for the picture price gap: Render cannot submit a prepared
storyboard whose total exceeds the total displayed before the click. This is a
local implementation on `clayrune/render-price-confirm`, based on `b164784d`.
No merge, push, server restart or real account spending is part of this task.

## Request and refusal contract

`POST /api/desk/engines/renders` requires `shown_total`, a finite, non-negative
JSON number in the selected engine's own unit: plan credits for
`higgsfield_mcp`, USD for dollar engines. Studio obtains this from
`estimate.credits` or `estimate.usd`, respectively. Missing values, booleans,
strings, negative values, NaN/infinity and numbers outside float range refuse
with `invalid_input` (400) before picture preparation. Already-submitted
idempotency keys still return the existing render without spending again.

`mc/desk_render_price_confirmation.py` validates this total and compares it
with the prepared plan, at the plan's six-decimal precision. `desk_engines.py`
keeps only three integration lines. The original cap checks stay before
preparation and after preparation; the new comparison is immediately after
the final cap check and before creating any render, idempotency reservation or
child generation job. Equal or lower prices proceed. A higher price returns:

```json
{
  "code": "render_price_changed",
  "error": "Price with your pictures is 79 credits (was 72). Press Render again to accept.",
  "shown_total": 72,
  "prepared_total": 79,
  "currency": "credits",
  "estimate": {"usd": 0, "credits": 79, "approximate": false},
  "plan": {"clips": 2, "total_credits": 79, "scenes": [
    {"scene_id": "s1", "credits": 39.5},
    {"scene_id": "s2", "credits": 39.5}
  ]}
}
```

This is an illustrative response subset. The real response includes the full
existing public plan and each scene's USD price, duration and picture metadata.
Dollar totals and non-picture price increases use the same refusal code with
appropriate units and wording. Caps take precedence over price confirmation:
accepting the new total never overrides a job limit or campaign budget.

## Studio behavior

`static/js/desk-v1-render-price.js` owns quote matching and confirmation state;
`desk-v1-engines.js` wires it into the existing storyboard panel shared by
Studio and the Director. After 409, the panel displays the returned estimate
and the server's refusal text; the Render button carries the higher total.
The next click still rechecks the free quote, but retains the prepared price
when the request and free quote match the previous attempt. It then sends that
displayed total through the existing passcode prompt. Another prepared-price
increase refuses again before any paid submission.

Any storyboard refresh, engine/model/shape change or explicit **Price again**
clears the prepared quote. A changed fresh quote also invalidates it. Cancelling
the second passcode prompt sends no render and retains the price for retry.
Successful submission clears confirmation state. No new approval bypass,
client-supplied prepared arguments, media-ID acceptance or spending-cap changes
were introduced.

## Uploads and live boundary

Existing `prepare_plan`/MCP preparation keeps media IDs only in its per-request
`Prepared` objects. A refused attempt therefore leaves no reusable server-side
prepared plan, and a second request uploads and confirms the pictures again.
The tests prove two pictures cause four uploads across refusal and acceptance.
An uploaded picture may remain in the vendor library after refusal.

Preparation calls `media_upload`, PUT, `media_confirm` and `generate_video`
with `get_cost: true`; no paid generation is submitted on the refused attempt.
The captured contract explicitly says `get_cost` returns credits without
submitting a job. It does not establish a billing guarantee for media uploads,
so upload fees are **unverified**, not asserted to be free. No live account
upload, generation or billing test was performed.

## Verification

- Backend confirmation tests cover actual picture-inclusive totals/per-scene
  prices, equal/lower prices, a second increase, missing/malformed totals,
  dollar totals, cap priority, passcode refusal and idempotent replay.
- Full `desk-v1-live-render.mjs` covers Connections, Studio video/image,
  Director and flag OFF. New confirmation sequences run at 1440px and 390px,
  including 72→79→85 credits, cancellation, explicit repricing, storyboard
  edits and accepting the new total through a second passcode prompt.
- All four `desk-v1-studio*.mjs` checks ran using a junction to the main
  checkout's existing `tools/smoke/node_modules`; no dependencies installed.
- The new module passes Pyright basic. Checking both touched Python modules
  reports the existing optional campaign-ID error in `desk_engines.py`'s
  untouched ffmpeg hold path (base line 2195; changed line 2198). The same
  diagnostic is present in the base source and documented by the picture-render
  follow-up; it is not a new diagnostic from this change.

The requested backend suite passed **3,019 tests in 530.95 seconds**, including
18 new confirmation cases. The suite selects every `tests/test_*.py` filename
containing `desk`, `higgsfield` or `engine`; no matching files are excluded.
The complete live-render smoke and all four Studio smokes passed. Local output
and screenshots are in `_scratch/render_price/`; they are not tracked artifacts.
The dependency junction was removed after the browser checks; the main
checkout's dependency directory remains intact. `git diff --check` passed.

| Check | Result |
| --- | --- |
| Full `desk-v1-live-render.mjs` | 60 checks passed |
| `desk-v1-studio.mjs` | 366 checks passed |
| `desk-v1-studio-delete.mjs` | 36 checks passed |
| `desk-v1-studio-article.mjs` | 47 checks passed |
| `desk-v1-studio-capture.mjs` | 1440px and 390px scenarios passed |

Smoke child exit codes are all 0. The first scratch runner's stdout used
Windows cp1252 and failed while printing Unicode checkmarks after three passing
smokes; their child results, full logs and recorded exit codes were preserved.
The runner now prints with replacement for unsupported stdout characters.
The final full live-render invocation, including both viewport sequences,
also exited 0 at the runner level.

## Handoff

Dave owns integration and the live walkthrough: free storyboard quote,
passcode-approved first Render, higher picture-inclusive price refusal with no
paid job, then a second passcode-approved Render at that price. Keep Ron's
selected model and account; no alternative account or request substitutes.
To reverse this implementation, revert its commit. No state migration exists.

Automatic approval review rejected the mandated `.claude/plans` write because
the unattended fence treats `.claude` content as human-owned. It was not
retried; the working plan is `_scratch/render_price/plan.md`. No new product
design choices were delegated back to Ron.
