# Higgsfield quote refusals: diagnostic checkpoint

This document records the original checkpoint. The later notice-only response
and captured-schema findings are in [HIGGSFIELD_QUOTE_NOTICES.md](HIGGSFIELD_QUOTE_NOTICES.md).

Base: `master` at `7d353094`. Branch: `clayrune/higgsfield-price-reason`.
No merge, push, restart, media upload or generation was performed.

## Live observations

Free server estimates with a short landscape description and no picture return
10 credits for Kling 3.0 and 35 credits for Seedance 2.5 at five seconds. Kling
also quotes 12 credits for that short description at six seconds.

The supplied storyboard fails on its first scene. Its exact first-scene label
and description, submitted without a picture at the same six-second duration,
also fail on both models. A separate one-scene text-only storyboard containing
that exact text likewise fails through the storyboard estimate route. These
are comparisons, not replacements for the original request. The original
storyboard was read but never edited. Operator IDs are kept only in the ignored
diagnostic journal; the exact scene text remains in the live diagnostic board.

This narrows the issue to the quote request/response rather than picture
upload: the free picture estimate intentionally sends the text parameters
without uploading media. It does **not** prove a prompt-length limit, missing
parameter, payment requirement, mode problem or allowance choice. The captured
schema lists those possible response shapes but is not the actual quote reply.

No existing route exposes the raw quote response. The running adapter discards
it when it lacks numeric `cost.credits_exact`/`cost.credits`, and the live vault
can only be used inside the server. Calling the provider from a separate
script, monkey-patching the live process or changing credentials was not used.
**The actual failing vendor response and root cause remain unobserved.**

## Implementation

`mc/desk_higgsfield_quote_response.py` owns response-shape diagnostics and
plain-language refusal translation. The adapter and picture-call wrapper
delegate quote refusals to it, including MCP `isError` quote replies. A valid
finite nonnegative numeric price retains exact fractional/zero credits;
boolean values cannot masquerade as prices. A usable quote is not discarded
because additional input-readiness information is present: pricing is separate
from generation validation, as the captured contract specifies.

Missing-media checks, allowance choices, structured billing/media recovery,
specific recognized error facts, brand-kit status and setup suggestions become
controlled plain sentences. Unrecognized prose never becomes a claimed cause.
The fallback explicitly says Higgsfield did not explain the missing price.
No vendor tool name grants permission, no purchase URL is followed, and
`get_cost: true`/`use_unlim: false` are unchanged. Uploaded-picture preparation
and paid submissions keep their existing gates and reservation behavior.

`[higgsfield_quote]` logs known top-level field names and bounded nested shapes
for `input_check`, `prepared_params`, `next_step`, `unlim_choice`, `error`,
media/billing recovery, `notice` and `cost`. Only enumerated routing values,
booleans and bounded numbers survive; arbitrary strings become lengths,
unknown field names become counts, URLs/media IDs/prompts are never logged.
Promotional and assistant-response fields are reported by name only.

## Integration checkpoint

After review and integration, Ron needs a server restart to load this code.
Then repeat the original free storyboard estimate on each model. Inspect the
`[higgsfield_quote]` line; the response key names and structured facts establish
which branch actually happened. If only unknown prose is supplied, the
diagnostic does not reveal that prose or pretend the cause is known.

No root-cause parameter correction is claimed on this branch. Any needed
product choice (for example, switching payment balances) belongs to Dave;
this patch does not choose it. A real paid picture render remains unverified.

## Verification

- Six engine/schema/Higgsfield test files: 235 passed, rc 0.
- Basic Pyright on the quote-response module, MCP adapter and media wrapper:
  0 errors, rc 0.
- Full `desk-v1-live-render.mjs`: rc 0, including Connections, Studio video
  and image, Director and flag-off scenarios. New assertion verifies a price
  refusal displays its plain sentence, disables Render and makes no submission.
- Full `desk-v1-studio.mjs`: all checks passed across the three tones and phone
  layout, rc 0.
- Main-checkout smoke dependencies were junctioned into this worktree; the
  junction itself was removed with PowerShell `.Delete()` before commit.

The scripted tests prove local handling and no-cost call behavior. They are
not evidence of the actual live refusal reason or a successful paid render.
