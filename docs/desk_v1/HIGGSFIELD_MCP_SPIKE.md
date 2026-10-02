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
