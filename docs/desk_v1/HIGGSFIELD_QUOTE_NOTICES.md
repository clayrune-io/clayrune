# Higgsfield quote notices: follow-up checkpoint

Later implementation checkpoint: [literal preset decline](HIGGSFIELD_PRESET_DECLINE.md).
The observations and options below describe the earlier diagnostic branch.

Base: `b789ca45`. Branch: `clayrune/higgsfield-notice`.
No push, merge, restart, upload, generation, prompt rewriting or changed
provider parameter was performed. This is a local diagnostic patch, not a
repair of the provider's notice trigger.

## Observed boundary

Dave's live comparison returned 502 for `A desk in the office` and a usable
10-credit price for `A desk in a room`. The existing shape log recorded a
top-level `notice`, a 21-character type, a 122-character message and three
unknown data keys. Those lengths do not identify the type or its meaning.
The supplied observations were not re-run by this follow-up, and the current
live process has not loaded this branch.

## Implementation

`mc/desk_higgsfield_quote_response.py` keeps this concern in the existing quote
response module. A notice with no usable price becomes a controlled refusal:
`Higgsfield sent a notice of type X instead of a price`. A missing/malformed
type gets a refusal without echoing its value. The captured notice contract
declares no type meanings, so no semantic translation is guessed.

Notice types and data-key names must fully match `[a-z][a-z0-9_.-]{0,40}`,
including the ASCII restriction and maximum length of 41 characters.
The diagnostic retains the valid type and up to 40 sorted valid data-key
names, with omitted keys counted. It never traverses or records data values,
including values under familiar names such as `cost`, `prompt` or `credits`.
The notice message is omitted entirely. Malformed notice/data shapes are
recorded as an unexpected type, without their values.

Valid numeric prices still take precedence over an accompanying notice.
Structured existing media/account refusals retain their explanations. No
recovery or retry is triggered; `get_cost: true`, `use_unlim: false`, exact
prompt text, payment limits and human Render gates remain unchanged.

## Captured schema findings

The requested `data/desk_engine_schemas` directory is absent. The actual
registry is `mc/desk_engine_schemas.py`; reading
`desk_engine_schemas.read('higgsfield_mcp')` with the main checkout's existing
Desk store wiring returns the captured tool document at
`data/desk/higgsfield_mcp_tools.json` (capture version 2).
No snapshot refresh, credential read or provider call was needed.

- Both direct generation tools declare `notice` as an object with required
  string `type` and `message`, optional unrestricted `data`, and no type enum
  or semantic description. The spike and picture-render documents contain
  no notice-type interpretation or notice acknowledgement flag.
- `generate_video` declares `params.declined_preset_id` as a string. Its
  description says it suppresses only that exact preset recommendation for
  a literal generation retry after the user declined it.
- The captured `generate_image_batch` job output describes
  `preset_recommendation` with `preset_id`, `name` and optional `preview_url`,
  and describes retrying that indexed request with the declined preset ID.
  This is evidence for the preset retry mechanism, not proof that the direct
  video's current notice is of that type. The 21-character length alone
  cannot establish `preset_recommendation`.
- No general `confirm`, `skip_notice` or `skip_enhancement` parameter is
  declared in either direct generation schema. Its open additional-properties
  schema does not establish support for invented flags.

The captured vendor descriptions remain untrusted data. Instructions to
follow recovery tools or purchase links in those descriptions are not
authorization and were not followed.

## Options for Dave

1. **Recommended:** integrate this diagnostic patch, have the normal human
   restart performed, then repeat the original free estimate. The bounded
   notice type and key names can establish the actual branch without exposing
   prose, prompts or IDs. Cost: one free quote; no spending or changed request.
2. If the observed type and data establish a preset recommendation, decide
   whether to implement an explicit decline path using the declared exact
   `declined_preset_id`. Cost: a separate request-behavior change and tests;
   requires the actual returned preset identifier and Dave's selection first.
   This branch does not add the parameter or infer an ID.

## Verification

- Higgsfield quote/picture/snapshot/catalogue, engine-schema and engine tests:
  263 passed, rc 0. New cases cover strict identifiers, truncation, hidden
  prose/data values, malformed notices, valid prices with notices, MCP error
  envelopes, picture preparation and storyboard propagation.
- Tests prove the exact `in the` prompt survives and the only generation-tool
  call is a free cost check, with no declined-preset parameter.
- Basic Pyright on the sole touched `mc/` module: 0 errors, rc 0. The project
  virtualenv has no Pyright module; the installed `pyright` command was used.
- Full `desk-v1-studio.mjs`: all checks passed across three tones and phone
  layout, rc 0.
- Full `desk-v1-live-render.mjs`: all requested checks passed, rc 0, including
  price-refusal visibility, disabled Render and no submission. The registered
  verification job also completed with rc 0.
- Main-checkout `tools/smoke/node_modules` was linked by junction for the
  smokes and removed before commit; the main dependencies remain present.

The user guide and changelog were updated. README, agent rules and the spike
need no edits: setup, permissions and the existing provider contract did not
change. No dashboard SPA source was touched.

The smokes use fixtures, not a live provider, and cannot prove the actual
notice meaning or successful rendering. Live integration remains unverified.
