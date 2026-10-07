# Higgsfield plan-credit MCP: Desk Studio research spike

Author: Kestrel. Researched **2026-10-01**. Code baseline: `08b4f45e`.
Research only: no sign-in, client registration, token exchange, credential access,
MCP session initialization, tool invocation, upload, generation, or spending.
Public documents and unauthenticated discovery GETs are the evidence below.

## Verdict

1. The official endpoint is `https://mcp.higgsfield.ai/mcp`; Higgsfield documents
   access from arbitrary MCP-compatible clients using the customer's plan credits.
2. Public metadata advertises authorization code, refresh tokens, PKCE S256,
   dynamic client registration (DCR), and a separate device-flow option.
3. Third-party access is expressly contemplated by the terms; a new Clayrune
   client's registration, redirects, and actual generation remain untested.
4. Recommend route A, a direct Studio MCP adapter, **subject to an integration
   proof**. Route B, a Claude Code connector worker, excludes non-Claude users
   and adds a model-mediated spending path.
5. This is sufficient evidence to plan an integration, not to mark it ready:
   transport negotiation, schemas, uploads, polling, downloads, and credit caps
   still need verification. No route was implemented or selected for shipment.

## 1. Endpoint, transport, and account model

Higgsfield's [official MCP explanation][hf-mcp] publishes the endpoint above,
OAuth access without an API key, use of existing plan credits, and support for
other MCP-compatible agents. It distinguishes this from the separately billed
developer API. MCP generation always deducts credits; web-only free/unlimited
generation does not transfer to MCP.

**Transport: Streamable HTTP is the intended implementation target, but was not
conclusively verified from an explicit Higgsfield transport declaration.** The
official pages inspected supply an HTTPS `/mcp` URL without naming the protocol
variant. Anthropic documents `http` as Streamable HTTP and separately distinguishes
legacy SSE. This supports the choice, but an endpoint suffix is not proof.
No `initialize` request was sent, and no authenticated SSE/JSON response was
observed. Do not label legacy HTTP+SSE supported or unsupported on this evidence.
An SSE response body alone would not distinguish it from Streamable HTTP.
[Protocol/client reference][cc-mcp]

The [connection guide][hf-connect] requires an active paid Higgsfield subscription.
Thus “every user” means any eligible customer connecting their own account,
subject to provider entitlements; it does not mean every plan is free or unlimited.

## 2. Public OAuth discovery

GETs below were made without authorization headers or cookies on 2026-10-01.
Only public discovery metadata was requested from the authentication services.

| Public document | Observed result |
|---|---|
| [MCP protected resource][prm] | HTTP 200 JSON; resource `https://mcp.higgsfield.ai/mcp`; authorization servers `https://clerk.higgsfield.ai` and `https://fnf-device-auth.higgsfield.ai`; bearer method `header`; scopes `openid`, `email`, `offline_access` |
| [MCP-origin authorization server][mcp-as] | HTTP 200 JSON; issuer `https://mcp.higgsfield.ai`; `/oauth2/authorize`, `/oauth2/token`, `/oauth2/register`; response `code`; modes `form_post`, `query`; PKCE `S256` |
| [Clerk authorization server][clerk-as] | HTTP 200 JSON; issuer `https://clerk.higgsfield.ai`; `/oauth/authorize`, `/oauth/token`, `/oauth/register`, `/oauth/token/revoke`, `/oauth/device_authorization`; PKCE `S256`; `client_id_metadata_document_supported: true` |
| [Device authorization server][device-as] | HTTP 403, Cloudflare error 1010, `browser_signature_banned`; advertised issuer could not be inspected. No user-agent workaround attempted. |

Both successful authorization-server documents advertise `authorization_code`,
`refresh_token`, and `urn:ietf:params:oauth:grant-type:device_code` grants, with
token authentication methods `client_secret_basic`, `none`, and
`client_secret_post`. The MCP-origin document does not supply a
`device_authorization_endpoint` despite listing the device grant.

Clerk's broader scope list is `openid`, `profile`, `email`, `public_metadata`,
`private_metadata`, `offline_access`, `user:org:read`. Those are issuer
capabilities, **not a reason for Studio to request all of them**. Start with the
resource's advertised scopes and validate the least privilege needed.

The protected-resource document's custom `higgsfield_auth_hints` chooses by client
capability: redirect-capable clients use authorization code with PKCE; clients
without a callback receiver can use device authorization. Its client-name
examples are hints, not an advertised exclusive allowlist.

### What this establishes, and what it does not

- **OAuth 2.1/MCP:** discovery and S256 are consistent with the
  [MCP authorization specification][mcp-auth], which requires OAuth 2.1 security
  practices. This is not a conformance test of Higgsfield's implementation.
- **DCR:** registration endpoints are advertised. No POST was made, so open
  registration, any initial-access-token requirement, and acceptance of a new
  Clayrune client are unverified. Clerk also advertises client-ID metadata
  documents; that is a possible onboarding mechanism, not one exercised here.
- **Redirect URIs:** neither discovery document lists permitted callbacks.
  `localhost`, `127.0.0.1`, `[::1]`, variable ports, custom schemes, and Clayrune
  tunnel/hosted HTTPS callbacks remain unverified for this provider. Clerk's
  [general guide][clerk-guide] uses a localhost callback, but that does not prove
  Higgsfield accepts it for a new client.
- **Allowlisting:** public Higgsfield documentation says other MCP-compatible
  clients can connect. No Claude-only restriction was found. That claim and
  advertised DCR do not prove every registration policy or entitlement.
- **Issuer selection:** the protected resource names Clerk and device auth,
  while the MCP origin also exposes its own authorization metadata. Follow
  resource discovery and bind a flow to its selected issuer. Do not combine one
  issuer's authorization endpoint with another's token endpoint on assumption.

The MCP specification also requires resource/audience binding and appropriate
redirect validation. Implement PKCE, state, issuer validation, scoped token
storage, and refresh handling through a maintained MCP/OAuth client, rather than
hand-written browser-login automation. Token lifetimes, refresh rotation,
revocation behavior, and supported protocol versions were not measured.

## 3. Tools and schemas: published capabilities versus a usable contract

**No complete, authoritative JSON Schema tool catalog was found in the public
Higgsfield documentation inspected.** A current authenticated `tools/list` was
not fetched. Do not turn these names into a hardcoded submission body yet.

| Operation | Public evidence | Contract still missing |
|---|---|---|
| Image generation | `generate_image` appears in Higgsfield's [MCP-to-CLI mapping][hf-skill] | Required fields and nesting; prompt/model selector; reference representation; count, resolution, ratio enums |
| Video generation | `generate_video` appears in the same mapping | First/last frame keys, duration units/enums, ratio, audio flags, reference limits, exact model IDs |
| Voice/audio | `generate_audio`, `list_voices` appear in the mapping | Text/voice inputs, output shape and supported voice operations |
| Async jobs | Mapping names `job_status` and maps it to CLI wait/poll operations | MCP job ID field, status enum, polling interval, errors, cancellation, idempotency and reconnect behavior |
| Balance | Official [connection guide][hf-connect] documents credit-balance checks | Canonical tool name, input/output schema, workspace ownership, balance units and credit buckets |
| Upload/reference reuse | Guide describes provider upload UI, web-image import and reuse of past generations | Programmatic upload schema, media IDs, first-frame mapping, limits and desktop/headless compatibility |
| Download | Official [creative-studio article][hf-studio] describes returning/reusing generated assets | Canonical download tool name, URL field, authentication, expiry, format and MIME guarantees |

The [official CLI README][hf-cli] separately documents prompt, aspect ratio,
duration, local first-frame input, polling and result URLs. Those are **CLI
contracts**, not proof that the remote MCP accepts identical JSON. The provider's
current connection guide recommends CLI setup for coding agents. This spike did
not substitute CLI execution for the requested MCP investigation.

### A concrete compatibility warning

A firsthand report in Higgsfield's public issue tracker,
[issue #93, opened 2026-09-20][schema-issue], says `generate_image` and
`generate_video` advertised only an opaque object schema while requiring nested
parameters; submissions failed, although read-only tools worked. It was open
when inspected. This is **a reporter's observation, not a maintainer-confirmed
current outage**, and was not reproduced here. It makes schema capture and one
explicitly approved generation essential acceptance evidence. Do not assume a
Claude worker avoids the problem; that report used Claude's connector.

No tool count, catalog completeness, exact duration/ratio range, download TTL,
or native credit-estimate endpoint is claimed by this spike.

## 4. Terms and plan-credit permission

The [Terms of Use][hf-terms], updated July 26, 2026, expressly address this:

> “You may access the Service’s MCP integration through third-party AI assistants,
> applications, agents, platforms, hosts, and tools that Company does not own or control”

That is the relevant excerpt from **§11.13**. **§1.2** contemplates end-user
applications under §11; **§11.1** applies developer terms to MCP; **§11.12** makes
customers responsible for automated actions and costs. **§11.5** prohibits
standalone resale/pass-through access without independent value. **§11.3**
requires credential protection; **§1.5** preserves usage limits. Supplemental or
enterprise terms can control under **§1.3**.

**Reading:** a Studio connection where each customer authorizes their own account
fits the documented third-party-client model. Existing plan-credit billing is
confirmed by the [MCP help page][hf-mcp], not inferred from an API license. This
is not an individual approval or certification of Clayrune. Do not pool accounts,
resell access, or promise unrestricted generation. There is no need to infer a
blanket prohibition on third-party MCP use from the API's separate billing model.

## 5. What Clayrune can reuse

These are source findings at the baseline above, not claims about an account's
current connection or the running server's deployment.

| Existing surface | Reuse and boundary |
|---|---|
| `mc/mcp.py:89` (`normalize_config`) | Validates stdio/HTTP/SSE configuration; HTTP/SSE keep `type`, `url`, `headers`. Unknown keys are dropped, including an OAuth-specific configuration object. This is configuration management, not an MCP client or token broker. |
| `mc/mcp.py:310` (`write_server`) and `mc/blueprints/mcp_routes.py:67` | Global/project management, atomic config writes, UI/API discovery and project loadouts are reusable patterns. Do not put bearer/refresh tokens into plaintext MCP headers/config. |
| `mc/mcp.py:670` (`_to_gemini_config`) | Translates `http` to `httpUrl`, SSE to `sseUrl`; transferring configuration does not transfer a user's OAuth grant. |
| `mc/blueprints/agent_routes.py:665` and `mc/agent_runtime.py:2164` | Project selection becomes `--strict-mcp-config --mcp-config`. The catalog comes from local/global/plugin configuration, not a fetched claude.ai connector inventory. Connector inheritance under the actual deployed CLI flags needs testing. |
| `mc/desk_engines.py:653` (`HiggsfieldAdapter`) | Current adapter uses `https://api.higgsfield.ai`, `Authorization: Key <id>:<secret>`, estimate/submit/status REST calls, then downloads. The vault entry is `higgsfield` (`:81`). It cannot consume an MCP OAuth token by swapping the base URL. |
| `mc/desk_engines.py:181` (`GenerationRequest`) | Existing prompt, frame/reference, duration, ratio and model fields provide the internal request shape; map them only after learning MCP schemas. |
| `mc/desk_engines.py:596`, `:978`, `:1010`, `:1063` | Estimates, public jobs, campaign budgets and caps use USD. Plan credits need an explicit unit-aware design; balance is not a per-job estimate and prepaid credits are not zero cost. |
| `mc/secrets_store.py:2087`, `mc/blueprints/secrets_routes.py:131`, `:235` | Vault persistence and the human passcode gate can support a future connection flow. Existing secret creation is human-only. No reusable Higgsfield OAuth callback/refresh broker exists in the inspected MCP/Desk modules. See [Secrets](../SECRETS.md). |

Reuse the existing Desk job record, output persistence, asset attachment and
download validation after adapting their inputs. Preserve reservations for
ambiguous submissions and avoid automatic resubmission after a timeout. API
pricing, API model IDs and the existing seven-day output TTL must not be copied
to the MCP path without evidence. Keep API and plan connections distinctly
identified so jobs retain their billing route; no silent fallback between them.

## 6. The two requested routes

| | A: Studio owns an MCP client | B: Studio dispatches a Claude Code worker |
|---|---|---|
| User setup | Desk Connections opens a human-authorized Higgsfield OAuth flow; server stores resulting tokens in the vault | User authorizes Higgsfield in claude.ai; eligible Claude Code session inherits that connector |
| Reach | Independent of the user's chosen reasoning engine; each user needs their own eligible Higgsfield account | Requires Claude subscription authentication and connector availability in that worker |
| Main new work | MCP transport/session lifecycle; OAuth callback and refresh; schemas; media upload; credit-aware budgeting | Worker launch and tool restrictions; structured job/result handling; credit enforcement; connector preflight |
| Cost | Integration and maintenance effort; generation consumes Higgsfield credits | Same generation credits plus Claude allowance/usage and orchestration latency |
| Credential owner | Clayrune server and human-owned vault grant | Claude's connector platform; do not extract its tokens into Clayrune |
| Failure boundary | Explicit adapter errors and durable job state | Adds agent/tool-selection failures, connector visibility and output parsing to provider failures |

### Route A: recommendation, contingent on proof

Add a separate plan connection and adapter behind the existing Studio interface.
The human clicks Connect, passes the existing human gate, and completes provider
consent. A short-lived server-side flow must bind that consent to the intended
user, issuer, redirect and vault entry; the callback must not become a generic
agent-callable secret-write route. Refresh may maintain that already-approved
grant server-side, never broaden scopes or create a fresh grant automatically.
Account replacement and reconnect retain human control.

Desktop loopback and a remotely viewed/hosted dashboard need separate callback
tests: localhost in a phone browser names the phone, not the Clayrune server.
Use a verified registered HTTPS callback or a provider-supported device flow
where appropriate; neither was validated in this spike.

Plan-credit accounting is required before rendering. Add explicit credit units,
provider estimates if available, reservations and settlement; retain the USD
campaign contract until a reviewed policy bridges the two. Do not guess a
universal dollar-per-credit rate or weaken existing spending/publish gates.
Only generation tools needed by Studio should be callable through this adapter.

### Route B: possible limited bridge, not the all-user solution

Anthropic [documents claude.ai connector inheritance][cc-mcp] for Claude Code
using subscription login. API-key/provider authentication and `setup-token`
credentials do not provide that inheritance. Connector disabling, organization
policy, and duplicate local endpoint entries can affect visibility. **No local
Claude session was started to verify this route.**

A worker must return a durable provider job reference and validated outputs to
Studio. A prompt asking it to respect a cap is insufficient: provider guidance
says conversational spending limits are not hard enforcement. A restricted
submission path must preserve Studio's caps, approvals and idempotency. Otherwise
the worker could spend outside the existing `desk_engines.submit` ledger.
Route B is smaller in OAuth work but not automatically smaller in reliable
spending control. Recommendation remains A; implementation selection is outside
this research spike.

## 7. Verification still required before implementation can be called working

1. Human-approved onboarding for a newly registered client: actual DCR or client
   metadata acceptance, loopback/HTTPS callback rules, account separation, and
   provider consent. Do not reuse another client's identity.
2. Negotiate the transport; capture `initialize` and paginated `tools/list` with
   all input/output schemas. Resolve the reported opaque-schema issue with
   provider evidence if it persists.
3. Read-only capability/balance checks, programmatic reference-upload design,
   exact model constraints and a usable pre-spend credit estimate/cap policy.
4. Separately authorized minimum-cost image and video runs: reference frame,
   poll/recovery, download, real balance delta, durable Studio asset attachment.
   This spike authorizes none of those calls.
5. Token expiry/refresh/revoke, denied consent, vault locking, callback failures,
   insufficient credits, unknown schemas, ambiguous submit, concurrent jobs and
   application restart. Route B additionally needs an actual worker connector
   visibility test under Clayrune's normal runtime flags.

**Research limitations:** the web reader could not open some discovery URLs;
direct anonymous GETs obtained the three HTTP 200 documents above. The device
issuer returned 403 and remains unverified. A guessed `/terms-of-use` URL was
unavailable; the official indexed `/terms-of-use-agreement` page was read instead.
A direct HTML fetch of that terms page also returned 403; the web reader supplied
the cited contents. No protected endpoint was used to fill evidence gaps.

Documentation-only validation covers local references, source links, quote and
diff. Runtime tests are not applicable; integration is untested.
The report itself is the deliverable. No UI, README, user guide, release
changelog, rules or product code change is implied.

[hf-mcp]: https://higgsfield.ai/creator-hub/help-center/integrations/what-is-higgsfield-mcp
[hf-connect]: https://higgsfield.ai/creator-hub/help-center/integrations/how-do-i-connect-higgsfield-to-ai-agent
[hf-studio]: https://higgsfield.ai/blog/claude-higgsfield-mcp-creative-studio
[hf-terms]: https://higgsfield.ai/terms-of-use-agreement
[prm]: https://mcp.higgsfield.ai/.well-known/oauth-protected-resource
[mcp-as]: https://mcp.higgsfield.ai/.well-known/oauth-authorization-server
[clerk-as]: https://clerk.higgsfield.ai/.well-known/oauth-authorization-server
[device-as]: https://fnf-device-auth.higgsfield.ai/.well-known/oauth-authorization-server
[mcp-auth]: https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
[clerk-guide]: https://clerk.com/docs/guides/configure/auth-strategies/oauth/scoped-access
[cc-mcp]: https://code.claude.com/docs/en/mcp
[hf-skill]: https://github.com/higgsfield-ai/skills/blob/main/higgsfield-video-explainer/SKILL.md
[hf-cli]: https://github.com/higgsfield-ai/cli
[schema-issue]: https://github.com/higgsfield-ai/cli/issues/93

## 8. Integration proof, 2026-10-02 (Dave; script `_scratch/hf_proof2/hf_proof_run.py`, not committed)

Items 1 to 4 of section 7 now have runtime evidence for **image**; video, uploads
and item 5 remain untested.

1. **Onboarding works for a new client.** DCR returned 201 (granted scope
   `openid email offline_access`), loopback callback on `127.0.0.1:<port>`
   accepted, `state` and `iss` verified, token exchange 200 with a refresh token
   (access token lifetime 86399 s). DCR returns no `registration_client_uri`, so
   a registered client cannot be deleted; tokens revoke cleanly (200).
2. **Transport:** protocol `2025-11-25` negotiated, SSE responses, no session id.
   `tools/list` returned 131 tools in one page.
3. **Pre-spend price exists, but not in the model list.** `models_explore` carries
   no cost field. Price comes from `generate_image` with `params.get_cost: true`
   (returns `cost.credits_exact`, submits nothing). Quotes seen: soul_cast,
   soul_2, soul_cinematic 0.12; z_image 0.15; gpt_image_2 0.5; nano_banana 1;
   cinematic_studio_2_5 and marketing_studio_image 2 credits. Pin
   `use_unlim: false` or the server decides which balance pays.
4. **Minimum-cost image run:** soul_cast, quoted 0.12, charged exactly 0.12
   (balance delta and `transactions` both show -0.12). The server silently applied
   `aspect_ratio 16:9` and `params.budget 50` (returned under `adjustments`; the
   adapter must surface these). Poll with `job_status {jobId, sync:true}`; a loose
   `status` match picks `marketing_studio_v2_status` and fails. Result: 500x288
   webp on `cdn.higgsfield.ai`, signed URL expiring ~4 h, so download on completion.

Lesson recorded: run 3 wrongly concluded "no pre-submit price" from the model list
alone; the tool descriptions documented `get_cost`. Read every tool description
before declaring a capability absent.

## 9. Picture inputs, 2026-10-06 (Sol_Tobin): DOCS-DERIVED, NOT LIVE-VERIFIED

**The local refusal is confirmed; the repair is blocked on a missing remote-MCP
upload contract.** `_higgs_mcp_model` declares `first_frame: false`,
`reference_images_max: 0`, and `reference_kinds: []` for all six models.
`HiggsfieldMcpAdapter._params` sends no media. `_render_plan` therefore refuses
picture scenes before calling the provider. This proves a Clayrune capability
gap, not that Higgsfield's video models cannot take pictures.

Research used public web documents only. No credential, authenticated MCP call,
upload, or generation was attempted. The task explicitly requires stopping
where schemas cannot be determined rather than inventing them. Accordingly,
this checkpoint changes documentation only. The picture fix is **not built**.

### Published capabilities by catalogued model

The [official media-input reference][hf-media-inputs] says it mirrors MCP
media-handling logic, but describes CLI flags, roles, and UUID/path inputs,
not a complete `tools/call` JSON schema. The
[official CLI model tables][hf-cli-models] are generated from CLI model discovery.
Their counts establish documented CLI capabilities; they do not prove identical
remote-MCP fields, nesting, limits, or model aliases.

| Clayrune model ID | Public evidence | Count / unresolved detail | Current Desk inputs |
|---|---|---|---|
| `kling3_0` | CLI model table: optional `start_image` and `end_image`, each single | One first and one last frame; no generic image-reference slot listed | Conservative: false / 0 / [] until MCP upload and media shape are established |
| `seedance_2_5` | Official skill: `start_image`, `end_image`, `image_references`, `video_references`, `audio_references`; `omni_reference` accepts media, `t2v` accepts none | No numeric image-reference maximum found in these sources; absent from the inspected CLI `MODELS.md` despite appearing in CLI README/skills | Conservative: false / 0 / [] |
| `gpt_image_2_5` | CLI model table: repeated `image_references` | At most 16 references in CLI; remote object representation unproven | Conservative: false / 0 / [] |
| `nano_banana` | CLI model table: `image_references` | 0..8 in CLI; not interchangeable with `nano_banana_2` or `nano_banana_flash` | Conservative: false / 0 / [] |
| `soul_2` | Exact ID has no media schema in the inspected official tables | `text2image_soul_v2` lists one reference, but equivalence to MCP `soul_2` is not established | Conservative: false / 0 / [] |
| `z_image` | Official media reference identifies it as prompt-only; CLI table lists prompt and ratio only | No picture input | false / 0 / [] is appropriate |

`false / 0 / []` above means `first_frame / reference_images_max /
reference_kinds`, not a statement about the provider's intrinsic capability.
There is no trustworthy basis here for adding character-ID reference support
to Desk's asset-reference contract. Keep unknown models conservative. In
particular, do not enable first frames solely to bypass the local refusal while
the adapter would still omit the picture.

The [official generation skill][hf-generate-skill] explicitly selects
`mode: omni_reference` for Seedance 2.5 picture inputs; its default prompt-only
`t2v` mode cannot be retained on that path. This requirement belongs in both
estimate and submit when the adapter is implemented. The
[official model-catalog reference][hf-model-catalog] independently lists those
Seedance modes and Kling frame roles. Neither provides a headless upload schema.

### Upload and generation wire contracts: established versus missing

The [official MCP connection guide][hf-connect] describes a human upload widget,
confirmation, importing a web-image URL, and reusing prior generations or saved
characters. It does not give programmatic upload arguments, response fields,
confirmation arguments, base64 handling, or a signed-PUT contract for a headless
MCP client.

The [official API upload guide][hf-api-uploads] documents a different service:
`POST https://api.higgsfield.ai/files/generate-upload-url` with API-key-pair
authentication and `content_type`, then a raw PUT with all returned
`upload_headers`, and use of `public_url` in an API model's URL parameter.
Higgsfield credentials must never accompany the storage PUT. The
[official JavaScript SDK source][hf-js-client] implements that REST URL flow.
Neither source says an MCP OAuth token is accepted there or that this public URL
is a valid MCP media input. Do not borrow this contract for the sign-in adapter
or silently fall back to the API-key engine.

[CLI issue #93][schema-issue] is a firsthand report of opaque generation tool
schemas and a required nested `params` object, not a provider-authored schema.
The reporter says both flat and nested attempts failed. Clayrune's section 8
already proves the nested text-only image call and `params.get_cost`; the issue
adds no usable picture/upload fields. Search results from independent MCP
wrappers were not used as implementation authority.

The following remain unknown and block upload wiring:

1. Exact current remote tool names and full input/output schemas for allocating
   and confirming a programmatic upload; whether the supported path is a
   headless signed PUT, base64, a user widget only, or something else.
2. Required filename, MIME, size, and header fields; upload completion/readiness
   semantics; the returned ID field and how it becomes a generation reference.
3. Whether media belongs beside `params`, inside `params`, in `medias`, or in
   model-specific reference fields, and the exact object keys/value types.
4. How `kling3_0` and `seedance_2_5` represent a first frame in remote
   `generate_video`; Seedance's numeric reference limits and whether its MCP
   selector uses the documented CLI `omni_reference` mode unchanged.
5. Exact remote picture schemas/counts for `gpt_image_2_5`, `nano_banana`, and
   `soul_2`, including the Soul CLI/MCP alias relationship.
6. Whether `get_cost: true` validates the uploaded media and mode exactly as
   submit does; image-upload expiry/reuse and provider readiness checks.

### Required next evidence and acceptance checks

The running server, whose vault is already unlocked, must supply sanitized
`tools/list` definitions/descriptions for the upload/confirmation and
`generate_image`/`generate_video` tools plus per-model media schemas. If its
catalog is still opaque, obtain the missing contract from Higgsfield. An agent
process must not extract `oauth.higgsfield`, bypass the vault restriction, or
introduce a general credential-bearing tool-call proxy to make this possible.

Once that contract exists, put substantial upload handling in its own `mc/`
module. Read every asset through the existing `_read_asset` guard, which restricts
files to `data/uploads`, supported image extensions, and 20 MB. Send exactly the
same media/mode arguments on cost and submission; only `get_cost` differs.
Keep `use_unlim: false`, and never print or log tokens or signed URLs.

Before production code changes, fake `_mcp_post` and the upload transport to
demonstrate failing picture tests for both video models. After implementation,
prove upload/confirmation, identical cost/submit media, Seedance mode selection,
asset confinement, and error redaction. Preserve the existing `takes no picture`
test for the genuinely text-only API model; also exercise prompt-only `z_image`.
No guessed upload tests were written at this checkpoint.

After merge/restart of the **eventual implementation**, use the existing Desk
estimate route only, through the server. Write two JSON request files with this
body, substituting each model ID and a real absolute path under the server's
`data/uploads` (include `project_id` only if its grant is project-scoped):

```json
{
  "engine_id": "higgsfield_mcp",
  "model_id": "kling3_0",
  "kind": "video",
  "prompt": "A slow camera push toward the subject in the supplied picture.",
  "aspect_ratio": "16:9",
  "duration_sec": 5,
  "count": 1,
  "first_frame": {"path": "<absolute path under data/uploads>"}
}
```

For the second file use `"model_id": "seedance_2_5"`; the adapter must supply
the verified picture mode, not a new unrecognized Desk request field.

```bash
curl -sS --fail-with-body -X POST http://localhost:5199/api/desk/engines/estimate -H "Content-Type: application/json" --data-binary @kling-picture-estimate.json
curl -sS --fail-with-body -X POST http://localhost:5199/api/desk/engines/estimate -H "Content-Type: application/json" --data-binary @seedance-picture-estimate.json
```

Those curls are a future proof recipe, **not executed evidence**. On the present
code they refuse the first frame; merging this documentation cannot change that.
Acceptance requires a usable credit quote, no generated job IDs, and preserved
picture/mode arguments for each model. Paid generation remains separately gated
and is never authorized by this research task.

Validation at this documentation checkpoint:
`python -m pytest tests/test_desk_engines.py -o addopts=''`:
**89 passed in 6.37s**. This verifies the unchanged engine regression suite,
including its no-picture refusal; it does not verify an MCP picture fix or any
live upload. No frontend, USER_GUIDE, README, release changelog, or rules change
is needed because no product behavior was changed.

[hf-media-inputs]: https://github.com/higgsfield-ai/skills/blob/main/higgsfield-generate/references/media-inputs.md
[hf-cli-models]: https://github.com/higgsfield-ai/cli/blob/main/MODELS.md
[hf-generate-skill]: https://github.com/higgsfield-ai/skills/blob/main/higgsfield-generate/SKILL.md
[hf-model-catalog]: https://github.com/higgsfield-ai/skills/blob/main/higgsfield-generate/references/model-catalog.md
[hf-api-uploads]: https://docs.higgsfield.ai/docs/concepts/file-uploads
[hf-js-client]: https://github.com/higgsfield-ai/higgsfield-js/blob/main/src/client.ts

## 10. Server-side schema capture, 2026-10-06 (Sol_Tobin; Dave's decision)

The existing human-started `POST /api/desk/connect/verify` for
`{"service":"higgsfield","method":"oauth"}` now retains the `tools/list`
response it already obtains. The server follows `nextCursor` pages with distinct
request IDs; malformed pages/cursors, repeated cursors, a provider failure, or
more than 50 pages fail the probe without replacing an older complete snapshot.
No generation, upload, credential endpoint, or new network destination is added.

`mc/desk_connect/higgsfield_mcp_snapshot.py` owns the atomic runtime write to
`data/desk/higgsfield_mcp_tools.json`, derived from the server-wired Desk data
directory. `.gitignore` excludes `data/desk/`; it is outside `data/projects/`
and must never be committed or bundled as product source. The record contains
`captured_at`, `untrusted_vendor_text: true`, an explicit warning, and `tools`:

- Always include `generate_video`, `generate_image`, and `job_status` if present.
- Also include any name/description mentioning upload, media, image, file, or
  reference, case-insensitively (filter before description truncation).
- Retain only name, full `inputSchema`, and description capped at 2000 characters.
  Transport credentials, headers, and unrelated response fields are not supplied
  to the writer. It has no network or vault access.

A snapshot write failure logs its exception type, never arbitrary exception
text, and does not invalidate an otherwise successful sign-in probe. This is
diagnostic evidence, not an executable registry or authorization to adopt tools.
The picture adapter/catalogue remain unchanged and still refuse pictures.

Tests fake `_mcp_post` and the access token; they cover pagination, every filter
keyword, truncation, credential/header omission, empty-list replacement,
incomplete-list preservation, the page bound, and a logged write failure that
leaves the probe successful. The main snapshot test failed before implementation
with `FileNotFoundError` (the old probe discarded the list).

After merge and Ron's restart, Dave runs the existing free verify operation and
hands the snapshot back for schema-driven implementation. No live verification
or capture has been run by this worker, and no provider schema is yet claimed
live-verified. A successful schema capture does not authorize uploads or paid
generation.

## 11. Automatic discovery and runtime evidence, 2026-10-06 (Sol_Tobin)

This supersedes section 10's requirement to press the human-only verification
button for discovery. Server saves now discover tools without an extra human
step: the legacy OAuth callback captures after its durable vault save; the
Connect wizard captures after a held sign-in is committed, outside the commit
lock. Merely holding a sign-in does not capture. Verification retains its human
gate and is never marked successful by automatic discovery.

`mc/desk_engine_schemas.py` supplies a small registry of read, refresh and optional
capability callbacks keyed by engine, with a connection service/method mapping.
Higgsfield registers its snapshotter; another remote MCP engine can register its
own callbacks without copying the save/price hooks. Durable non-sign-in
credential saves share the same registry hook; OAuth saves wait until their
callback or held-sign-in commit. This is not a general tool
executor, model catalogue importer, credential API or permission grant.

Missing or 24-hour-old snapshots are refreshed before single-job estimates,
submits and storyboard price/render validation. One capture per engine may run
at a time; competing requests do not queue behind it. Discovery shares a
15-second RPC deadline across initialization and all cursor pages, retains the
50-page limit, and uses deadline-aware bounded response reads so SSE heartbeats
cannot keep the capture open. Credential resolution follows existing OAuth
policy; price/render paths pass the credential they already resolve. No worker
reads the live token, invokes live discovery, uploads or generates media.

Discovery/network/read/write failures log exception types without arbitrary
exception text or signed URLs. A failed capture preserves the old complete
snapshot, does not undo a saved connection, and permits text-only price checks
to continue. Missing/expired evidence is retried on the next ordinary price
check. Snapshot data remains gitignored under `data/desk/`, outside project
records and selectively bundled installer assets.

`mc/desk_mcp_picture_schema.py` derives **evidence**, separately from submission
support. It reads only structured generation input schemas with a model `const`
or `enum` at the root or within `params`. Supported evidence is a first-frame
field (`start_image`, `first_frame`, `start_image_url`), a reference array
(`image_references`, `reference_images`, `input_images`), or a `medias` array
whose item `role` has an explicit image/start-image enum. The evidence retains
the tool, parameter path, role, original field schema and an explicit-count
flag. Explicit `maxItems` supplies the count; an unspecified count is restricted
to one reference locally and is not claimed as the vendor's maximum. Local
JSON pointers and uncomplicated model unions are supported. Descriptions never
grant capability. External references, unsupported intersections/conditionals,
opaque model selectors, duplicate tool definitions and conflicting model
branches remain conservative. A fully closed prompt-only contract establishes
no picture input.

`get_model()` and the engine listing overlay this runtime evidence without
mutating the static catalogue. Absent evidence explains:
"Clayrune has not read Higgsfield's model list yet; it does so on the next price
check". Stale and unknown evidence have distinct explanations. Documented
picture evidence **still refuses generation/estimates with pictures** because
upload wiring is not implemented. Both validation and the adapter guard that
boundary, preventing silently dropped pictures. `use_unlim` stays false.

### Evidence needed for step 4 (picture wiring)

The server-produced snapshot must establish the actual `generate_video` and
`generate_image` parameter paths and model-specific applicability: the accepted
first-frame/reference fields or `medias` item shapes and roles, identifier/URL
types, required companion fields, allowed modes and counts for each supported
model. It must also identify the actual upload/media tool and its accepted
payload (base64/file/mime/size fields, or signed-upload allocation parameters).
The returned asset id/public URL shape and how that value is referenced by
generation must be documented as well. The current snapshot intentionally
retains input schemas only; if descriptions/public documentation do not define
upload output, an output schema or other authorized server-side evidence is
still required. Do not substitute the public CLI's contract for the remote MCP.

After merge and Ron's restart, an ordinary text-only price check also triggers
capture for an already saved connection. Example **free estimate only** (Bash):

```bash
curl -sS --fail-with-body -X POST http://localhost:5199/api/desk/engines/estimate \
  -H 'Content-Type: application/json' \
  -d '{"engine_id":"higgsfield_mcp","model_id":"kling3_0","kind":"video","prompt":"Schema discovery price check","aspect_ratio":"16:9","duration_sec":5}'
```

Read `data/desk/higgsfield_mcp_tools.json` as untrusted vendor data. The discovery
may succeed even if the subsequent credit quote fails; inspect the timestamp
and complete filtered tool list. No live schema has been read in this branch.

### Verification

- The two save-path assertions fail on base `946464f6` (no MCP discovery calls),
  then pass with the hooks. Full eight-file regression: `232 passed in 71.04s`.
  The final generic credential-save hook and focused provider/discovery tests:
  `83 passed in 6.80s`.
- Final engine-file command: `python -m pytest tests/test_desk_engines.py -o addopts=''`
  returned `89 passed in 4.19s`. Fake discovery/capability/transport tests cover
  TTL, a single in-flight capture, timeout/heartbeats, redacted failures,
  model-specific image/video evidence, conflicts and explicit pending refusals.
- New modules and changed connection modules pass basic Pyright. Including
  `desk_engines.py` reports its pre-existing optional campaign-id error in the
  ffmpeg hold path; the same error is reproduced from base `946464f6`.
- No frontend files changed, so JavaScript/Playwright smokes are inapplicable.
  No merge, push, restart, live provider call, upload or paid generation occurred.

## 12. Captured generation schema is not the model catalogue (2026-10-06)

The server-produced tool snapshot has the generation and upload tools, but it
does not yet establish the contract required by step 4. The actual
`generate_video` / `generate_image` input schema declares:

- `params` as an object or serialized string; the object has `model: string`
  without a model enum or per-model branches.
- `params.medias[]` items requiring `value: string` and `role: string`, with no
  role enum, model-specific count, accepted MIME list or image-size limit.
- `get_cost: boolean` promises a quote without submitting a job, but supplies no
  special picture-slot form that omits a media identifier. Whether the cost
  path accepts an empty/omitted identifier is not established. Never invent an
  uploaded UUID or silently quote a different text-only request.
- `media_upload` has upload-URL allocation inputs; `media_confirm` has media-ID
  inputs. The snapshot retains no output schemas, so allocation response shape
  and confirmed-ID extraction are not established by these input schemas.

The snapshot filter omits `models_explore`; its model-specific result data is
also absent. An official CLI [media-input reference](https://github.com/higgsfield-ai/skills/blob/main/higgsfield-generate/references/media-inputs.md)
documents model-dependent roles, but substituting that CLI contract would not
satisfy a runtime implementation driven by this remote MCP snapshot.

Direct evaluation on the captured shape returns `schema_state: unknown` for
both `kling3_0` and `seedance_2_5`, with no picture capability. The running
server's metadata-only engine listing reports the same states. Thus the current
observed contract gap precedes the upload-wiring guard; removing `PENDING` alone
cannot fix this shape.

Required evidence: the `models_explore` tool definition and selected-model
results; upload/confirm output schemas or vendor-documented response shapes;
and a server-run free `get_cost` proof with a picture slot declared and no upload.
Do not infer that the vendor requires an upload merely because that proof is
missing. Recommended next implementation is server-only read-only contract
capture/probing, keeping credentials in the server and all human gates intact.
