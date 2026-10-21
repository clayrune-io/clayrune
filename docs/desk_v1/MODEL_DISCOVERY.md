# Saved-connection model discovery (MC-1067, slice 1)

Branch build, not deployed. The server can browse a saved generation connection's
catalogue and obtain free text-only Higgsfield credit quotes. Discovery never
uploads, generates, writes a render/job record, changes settings, grants a tool
permission, or dispenses credentials. The original human Render/passcode gates
remain in their existing routes.

## HTTP contract

`GET /api/desk/engines/<engine_id>/models?kind=video|image&q=<optional>&project_id=<optional>&refresh=1`

`q` is at most 1024 characters. Omit `refresh` to reuse a fresh observation.
Returns `engine_id`, `kind`, `models`, `cached`, `captured_at`, `expires_at`,
`source:discovered`, and the untrusted-data warning. A successful empty catalogue
is an empty result, not a fallback. Invalid inputs return 400, unusable saved
credentials 409, vendor/partial/pagination failures 502. No partial catalogue is
cached or returned as a successful full list.

Each discovery adapter owns `discover_models()` in its own module under
`mc/desk_model_discovery/`. `catalogue.py` is the registry/cache boundary;
`quotes.py` owns free quotes; the dedicated blueprint owns HTTP.

| Connection | Read operation | Kind information | Free quote |
| --- | --- | --- | --- |
| Higgsfield sign-in | `models_explore` list/search, `type`, `limit:100`, `after` from `next_page_token` | Vendor `output_type` | `generate_video`/`generate_image`, literal `get_cost:true`, `use_unlim:false` |
| Google API key | `GET /v1beta/models`, `pageSize:1000`, `pageToken` | Conservative Veo / Gemini-image / Imagen ID families | Price unknown |
| OpenAI API key | `GET /v1/models` | Conservative GPT-image / DALL-E / Sora ID families | Price unknown |
| Higgsfield API key | No verified model-list endpoint; 501 `discovery_unavailable` | Curated fallback only | Price unknown |

Google/OpenAI's list responses do not establish arbitrary model output modalities
or complete generation schemas. ID-family classification is disclosed as
`kind_basis:model_id_family`; it never grants rendering support. Catalogue picture,
required-picture and audio facts are nullable booleans from explicit declarations;
absence is unknown. No raw vendor descriptions, recovery arguments or quoted
unit prices are exposed as a computed job price.

The discovery cache lives at `<desk-store-parent>/desk/model_discovery/`, outside
`data/projects/`. Files are SHA-256-keyed by engine, project, credential identity,
kind and query; neither credentials nor queries are stored verbatim. Every cache
hit still resolves the saved connection with its scope, lock and unattended policy.
Expiry is 24 hours; token/key rotation changes the cache identity. Individual
records are at most 4 MiB; traversal refuses after 50 pages/5000 media models.
Cache failures log their exception type. Vendor transport retains the existing
no-redirect and credential-redaction boundaries.

## Quotes and Studio

`POST /api/desk/engines/<engine_id>/models/quote` accepts only:

```json
{"owner":{"kind":"studio","id":"<saved-owner-id>"},"model_id":"<discovered-id>","kind":"video","aspect_ratio":"16:9","project_id":"<optional-project-id>"}
```

For an image, supply `kind:image` and `prompt` instead of an owner. No media,
caller tool arguments, price flags or engine override is accepted. Video reads
the entire saved storyboard, never caller-supplied or unsaved scenes. Prompts are
the same label-plus-line form used by Render. Durations snap to the model's
declared durations using the existing Render helper; the response distinguishes
`requested_sec` and `sent_duration_sec`. A quote returns `price.state:quoted`,
credits, per-scene credits, `adjusted`, `preset_declined`, `text_only:true` and
`pictures_omitted`, plus the saved `board_rev` so a survey can refuse owner drift.
Sent duration is not a confirmed output length when the vendor
adjusts a request. All scenes must be quoted successfully; no partial total or
zero substitution is returned. Other services return `price.state:unknown`.

The existing schema-checked preset-decline helper permits one free literal retry
when Higgsfield suggests a preset. Only its bounded, agreeing decline ID is added;
model, prompt, credit choice and cost-only flag remain literal. Unknown notices,
missing quote values and unsupported retry contracts fail. Vendor suggestions
never select a tool, rewrite the prompt or create a generation.

Studio loads discovered models for its chosen image/video kind. Curated entries
remain labels and known submission contracts, and a discovery error explicitly
labels their fallback. New catalogue models can be explored/quoted, with Render
and scene-subset Render disabled until their submission contract is wired. Known
Higgsfield models retain their existing picture-aware estimate and human Render
flow. Google, OpenAI and the API-key fallback show **Price unknown** and disable
Render, including scene subsets; Studio does not use approximate hardcoded price
tables for them. Legacy generation API contracts remain unchanged in this slice.
Connections receives the original metadata list; Studio discovery does not mutate
that shared frontend list. Vault unlock reloads the Studio catalogue.

### Availability and quote waiting (MC-1067 follow-up, 2026-10-21)

Studio filters engines by the selected media kind **after** saved-connection
discovery. OpenAI's curated descriptor is image-only; a connected OpenAI account
whose discovered catalogue includes a `sora-` video model can still appear in
the video picker. Those entries keep Price unknown and disabled whole/subset
Render. An absent connection is not queried: its curated image option says
not connected, and no OpenAI video option is invented. A successful empty
catalogue stays empty. The HTTP metadata connection state, rather than a missing
picker option alone, distinguishes missing configuration from available models.

While a whole-storyboard estimate is pending, **Price again** is disabled. The
engine/model/shape pickers remain usable, and stale responses cannot replace a
newer selection. Once a quote succeeds or fails, Price again becomes available.
This prevents a repeated click from starting another outstanding full-board
quote. It does not cache prices, change the mandatory pre-approval price check,
or shorten an individual vendor call.

The render-estimate path quotes scenes serially. Each current Higgsfield MCP
call performs initialize, initialized notification and the quote tool request;
schema refresh and a supported preset-decline retry can add work. An isolated
Flask-route probe with a fresh captured schema and a stub transport counted 18
MCP requests for six scenes, and 36 for two identical six-scene estimates. These
are request counts, not a live latency benchmark. The earlier approximately
21-second observation did not capture per-call timings, so it cannot establish
which portion was vendor processing, transport or schema refresh. No session
reuse, parallel vendor calls, timeout change or timing guarantee is introduced.

The follow-up uses deterministic browser fixtures at 1440/390 for saved, absent
and empty OpenAI catalogues, unknown prices, render gates, delayed quotes and
selection changes. These fixtures are not authenticated acceptance. The original
owner-specific comparison remains unperformed. The pending-button repair is
branch-only until separately integrated; no connection or live state was changed.

## Sources and verification limits

Official sources checked 2026-10-07:
[Google models list](https://ai.google.dev/api/models),
[OpenAI models list](https://developers.openai.com/api/reference/resources/models/methods/list),
[Higgsfield documentation index](https://docs.higgsfield.ai/docs/llms.txt), and
[supplementary Higgsfield OpenAPI](https://docs.higgsfield.ai/docs/openapi.json).
Higgsfield directs model discovery to console/model documentation and does not
establish an account-list REST endpoint in those sources. Its supplementary spec
is explicitly not an authoritative catalogue. This is an unverified API-key
capability, not a claim that the vendor has no such endpoint. The overview.md
page could not be opened by the web tool. No guessed endpoint was called.

Higgsfield MCP contracts came from the previously captured `models_explore` and
free-quote schemas. No live authenticated discovery/quote was performed in this
run. Dave must merge, run the relevant smokes after that merge, restart through
the approved server workflow, and execute the owner-specific comparison command
in `docs/_journal/6ddda8d3-model-discovery.md` on the main checkout. That journal
checks the saved request basis before producing the full table and preserves
individual quote failures as failures. No account/connection substitution.

## Validation (repo root)

| Exact command | Final result |
| --- | --- |
| `python -m pytest tests/test_desk_model_discovery.py tests/test_desk_engines.py tests/test_desk_engine_schemas.py tests/test_desk_render_worker.py tests/test_desk_render_scenes.py tests/test_desk_render_scenes_flow.py tests/test_desk_render_price_confirmation.py tests/test_desk_render_recovery.py tests/test_higgsfield_preset_decline.py tests/test_higgsfield_picture_render.py` | rc=0, 351 passed |
| `python -m pytest tests -k 'desk and (engine or render or higgsfield or generation)'` | rc=0, 246 passed, 11887 deselected; before the final new discovery/preset tests |
| `pyright mc/desk_model_discovery mc/blueprints/desk_model_discovery_routes.py` | rc=0, 0 errors |
| `node tools/smoke/desk-v1-model-discovery.mjs` | rc=0, desktop/mobile video, image, quote, fallback and subset guards |
| `node tools/smoke/desk-v1-studio.mjs` | rc=0 |
| `node tools/smoke/desk-v1-live-render.mjs --studio` | rc=0 |
| `node tools/smoke/desk-v1-render-scenes.mjs` | rc=0 |
| `node tools/smoke/desk-v1-engine-vault-recovery.mjs` | rc=0 |

New backend tests stub vendors and vault access. New browser checks load the real
Studio engine module at 1440/390 pixels with mocked HTTP. The smoke dependency
junction points to main's existing installation and is removed before commit;
no npm install. `static/index.html` was not edited.

Rollback: revert this slice's commit. No migrations or live settings changes.
