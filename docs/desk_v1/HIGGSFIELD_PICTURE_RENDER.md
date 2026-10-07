# Higgsfield picture scenes: captured contract wiring

Follow-up to the capture prerequisite merged as `89b4b628`; base `3114dac6`.
This implementation is on `clayrune/higgsfield-picture-render` for integration.

The revision-2 capture supplies `generate_video`, `media_upload`, and
`media_confirm` input/output schemas plus selected-model catalogue records.
The fixture in `tests/fixtures/higgsfield_picture_contract.json` preserves
those schema and model-result shapes, without account data or credentials.
Test responses are scripted fixtures, not claims of live upload success.

`mc/desk_higgsfield_picture_contract.py` joins fresh catalogue roles to the
`params.medias[]` wire contract. Kling's scene picture maps to `start_image`;
Seedance's scene picture uses `image_references` with the catalogue's `omni_reference` option.
No model-id switch chooses roles or mode. Missing/ambiguous/expired evidence
does not enable pictures. Catalogue-declared empty media inputs are the basis
for a no-picture refusal. Reference count defaults conservatively to one
because this capture does not declare a larger limit.

The captured media item requires `value` and `role`; there is no identifier-free
picture-presence input. `generate_video.outputSchema.input_check` describes
required-media presence for Genjutsu only and explicitly says a successful
quote does not validate media existence or generation readiness. It is not
permission to invent an ID or make a role-only quote. Free price checks therefore
quote the text request, with `picture_pending` and the visible "Text-only price;
picture priced at render" note. Seedance's text quote uses its prompt-only mode;
it is explicitly not the price of `omni_reference`.

After the existing human/passcode gate, `desk_generation_preflight.py` prepares
all storyboard pictures before any generation: allocate `media_upload`, PUT
the local bytes, `media_confirm`, then quote with the returned identifier in
`medias[].value` and the catalogue role. Actual picture quotes replace the
fallback amounts; the full storyboard credit cap is checked again before the
first job. Child submissions reuse in-memory prepared arguments, never IDs
supplied by request JSON. Retry reservations still prevent duplicate paid jobs.
Preparation failures spend no generation credits, but a successfully uploaded
picture can remain in the vendor library when a later upload or cap check fails.

`desk_higgsfield_media_upload.py` uses the existing public-address guard, checks
all DNS answers and pins TCP to those addresses while TLS authenticates the
original hostname. It allows HTTPS port 443 only, sends the file MIME and bytes
without a vendor token, requires PUT HTTP 200, follows no redirect and enforces
20 MB. `desk_mcp_schema_validation.py` supports the bounded captured schema
vocabulary; unknown validation constructs fail closed, external references are
never fetched and vendor instructions are never executed. Allocation/confirm/
quote schema failures stop before generation. A malformed generation response
is non-definitive: keep the actual quoted reservation and idempotency key, tell
the user to check the vendor dashboard.

The MCP adapter now lives in `desk_higgsfield_mcp.py`; `desk_engines.py` keeps
only integration hooks for validation, notes, preparation and cap enforcement.
The public pricing panel and passcode description display fallback semantics.

Verification on this branch:

- Seven engine/Higgsfield/connect test files: 317 passed, including 49 new
  picture tests for catalogue roles, upload order, malformed schemas, actual
  credit caps, all-scenes preflight, retry handling and pinned public PUT.
- Basic Pyright for the five new modules and the existing picture parser:
  zero errors. `desk_engines.py` retains its pre-existing optional campaign-ID
  error in the ffmpeg hold path; no new error there.
- `node tools/smoke/desk-v1-live-render.mjs --studio`: 25 checks passed, including
  explicit fallback note and approval text, price/refusal/render/progress and
  image generation. This is the documented Studio-only scenario selection.
- The full smoke's separate Connections scenario fails at the old
  `engine:higgsfield` Add-service selector before it reaches Studio. It is not
  claimed green; newer Connections setup replaced that path. Connections,
  Director and flag-OFF scenarios were not included in the passing Studio run.

No real upload, generation, account spending, merge, push or restart was done.
After Dave integrates this branch, restart the server and reload the dashboard.
The first human paid picture render still needs live confirmation; the supplied
fixtures validate the captured shapes and local behavior, not vendor execution.
