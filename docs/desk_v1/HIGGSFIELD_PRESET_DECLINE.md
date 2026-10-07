# Higgsfield literal preset decline: local implementation checkpoint

Branch: `clayrune/higgsfield-preset-decline`. Base: `196d38ee`.
Sol_Tobin, dispatched by Dave. No push, merge, restart, provider generation,
upload, credential read or live provider call was performed in this session.

## Decision and observed trigger

Dave reported that the live free quote for `A desk in the office` on
`kling3_0` returned `preset_recommendation`, with data keys `preset`,
`retry_literal_with` and `use_preset_with`. His decision is to render the user's
storyboard text as written. This branch does not repeat or independently
verify that provider observation, and does not assume an identifier's value
from those key names.

## Retry boundary

`mc/desk_higgsfield_preset_decline.py` owns literal quote retries. It recognizes
only the exact notice type `preset_recommendation`. It accepts an identifier
from `notice.data.preset.id`, `.preset_id`, and/or
`notice.data.retry_literal_with.declined_preset_id`. Every supplied source must
be structurally valid and every supplied identifier must agree. A top-level
`notice.data.preset_id` is not an accepted source. Missing or malformed source
objects fail closed even when a different source contains a valid identifier.

Identifiers fully match ASCII `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`: a maximum of
128 characters, including ordinary UUIDs, without whitespace, slashes, dots,
URLs or Unicode. Invalid values never appear in the refusal or diagnostic.
The captured tool input schema must explicitly declare a string
`params.declined_preset_id`; allowing arbitrary additional properties alone
does not count. The copied retry input is validated against that schema.

Only `declined_preset_id` is added to the caller's copied parameters. Prompt,
model, count, duration, ratio, media and `use_unlim: false` stay unchanged.
`use_preset_with`, vendor-supplied prompts, flags, URLs, recovery calls and all
other suggested arguments are ignored. The input object is not mutated.
Every retry is a `get_cost: true` quote. There is exactly one retry per quote:
any second notice, provider error or unusable price stops with a controlled
plain refusal. Successful prices carry the static note:
`Higgsfield suggested a preset; your text is used as written`.

Valid prices accompanying other notice types preserve the earlier behavior.
A preset notice invokes literal pricing even when accompanied by a cost,
because a preset's price is not proof of the literal request's price.
The new log line contains only the caller's tool identifier and the checked
decline identifier, never preset name, preview URL, message or nested values.
Existing quote-refusal logs retain bounded shapes, not vendor prose or values.

## Exact arguments at Render

`mc/desk_higgsfield_mcp.py` uses the helper for ordinary estimates and approved
render preparation. Text requests now also prepare a fresh free quote; picture
requests retain their existing guarded upload/confirmation before the actual
media quote. Preparation retains the final priced arguments, removes only
`get_cost`, and returns the literal price for the final cap check.

`mc/desk_generation_preflight.py` prepares every storyboard scene when its
adapter supports preparation, including text-only Higgsfield scenes, before
the first paid submission. Higgsfield is currently the only generation adapter
with a preparation method. A later literal price above the cap stops before
any generation. The existing human Render/passcode, complete credit cap,
campaign budget and idempotency gates remain in place.

The preset note is independent of `picture_pending`. Text quotes do not
accidentally acquire the picture-pricing warning; preliminary picture quotes
retain their original warning, while prepared actual-media quotes clear it.

## Submit retry is deliberately withheld: contract evidence missing

The existing capture at `data/desk/higgsfield_mcp_tools.json` (version 2,
read from the main checkout) was inspected without credentials or refresh.
Both direct tools describe `get_cost: true` as not submitting a job. Direct
`generate_video` explicitly declares the literal decline parameter. Direct
`generate_image` does not declare it, so a future image preset notice refuses
unless a captured contract explicitly adds the parameter.

The direct tools' output `notice` is an object with `type`, `message`, and
optional unrestricted `data`. It supplies no notice-type enum, no
`submitted: false` or no-charge guarantee, and no mutual-exclusion rule
between a notice and `results`. The direct video description warns that a
transport timeout can leave submission unknown. The image-batch output
describes retrying an indexed preset recommendation literally, but does not
establish no-charge/no-submit semantics for the direct video tool.

Therefore no paid-call retry was enabled. A notice from the real submit,
including inside an MCP `isError` envelope, raises a non-definitive error:
submission and charging could not be confirmed; check the provider dashboard.
It retains the reservation and idempotency key, and emits only a static
unknown-outcome diagnostic. Replaying the same key makes no new paid call.
It never claims that nothing was submitted or that no credits were spent.

**Handoff to Dave:** obtain an explicit provider guarantee for the direct
tool's preset-notice branch before authorizing any paid submit retry. No
speculative retry or paid probe was used to fill this gap. Carrying the
successful free quote's decline ID into the initial paid request is implemented;
successful literal rendering against the live provider remains unverified.

## Validation

| Check | Result | rc |
| --- | --- | --- |
| Higgsfield preset/quote/picture/snapshot/catalogue, engine schemas and engines pytest | 317 passed | 0 |
| Pyright basic, all three touched `mc/` modules | 0 errors | 0 |
| `desk-v1-studio.mjs` | All checks passed | 0 |
| `desk-v1-studio-article.mjs` | All checks passed | 0 |
| `desk-v1-studio-capture.mjs` | Both desktop and phone passed | 0 |
| `desk-v1-studio-delete.mjs` | All checks passed | 0 |
| `desk-v1-live-render.mjs`, full run | All requested checks passed | 0 |

Coverage includes agreeing and disagreeing identifier sources, malformed and
out-of-bounds values, second notices even with a price, missing/invalid costs,
missing schema declaration, unmodified prompt and caller arguments, free
quote `isError` envelopes, text/picture notes, exact final priced submit
arguments, picture credit-cap movement, all-scene preparation, and ambiguous
paid notices with and without `isError`. Paid-notice tests prove one paid call,
retained credit reservation, and replay without resubmission.

All vendor responses in these checks are fixtures; browser smokes exercise
the product UI with a fake engine and do not spend provider credits. The
main-checkout `tools/smoke/node_modules` junction was used and removed before
commit. Smoke-generated screenshots were retained under `_scratch/`, with
the tracked reference screenshots restored to their pre-run state.

The verification wrapper's first invocation stopped while printing a passed
studio log through Windows cp1252 (Unicode checkmark encoding). That wrapper
was corrected to UTF-8 and resumed only the remaining checks. A Clayrune
job-launch command had been rejected by automatic review for PowerShell
command-position substitution; verification ran in the foreground instead,
with its process registered. No blocked job launch was retried.

Raw logs and the rc matrix are local, gitignored `_scratch/*-preset-decline.log`
and `_scratch/preset-decline-verification.json`. This document is the durable
implementation handoff.

`docs/USER_GUIDE.md` and `CHANGELOG.md` document the user-visible quote and
Render behavior. README and agent rules need no changes: connection setup,
installation and permission boundaries are unchanged. No SPA source changed.

Rollback is a revert of this branch's scoped implementation commit.
