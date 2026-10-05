# Desk service profiles and user-chosen connections

2026-10-05. Specification for review. No connector is delivered by this document.

## 1. Contract, authority and scope

Desk Connect currently recognizes LinkedIn but explains only one restricted Company Page OAuth option. Recognition should instead open a maintained service profile: the routes for each purpose, their account requirements, costs and limits, and what Clayrune can actually execute. A user must also be able to add any MCP server or API of their own choosing without waiting for that profile or the curated catalogue.

This serves two people: the user connecting an account, who needs an informed choice, and the maintainer updating shared facts, who needs evidence and a reviewable release. Service facts are shared data. Account identity, credentials, approvals and route choices belong to each install.

**Decided by Ron, 2026-10-05:** profiles and the catalogue are conveniences and defaults, never permission lists. User-supplied npm/PyPI MCP packages, remote MCP URLs and APIs are in scope. Clayrune detects parameters, shows the actual configuration and risk, and requires human approval with the passcode on Save. Packages are pinned at first approval; later changes require new approval. Untrusted README, page and registry text reaches only a certified toolless classifier, never a tool-enabled agent.

This supersedes the October 3 curated-only decision and unknown-service information-only execution limit in [Connect by URL](DESK_CONNECT_BY_URL_SPEC.md). Its other constraints remain: human-started connection changes, one final passcode check, vault references, truthful verification, explicit partial provisioning, bounded discovery and no tool downgrade after a page-reader failure. Its later Decisions section overrides its original transaction-coordinator proposal; this spec does not reinstate that coordinator.

**Decided by Dave's brief:** profiles cover each purpose; central research runs on the designated maintainer install; changes are reviewed before release; the research agent never merges. Users receive approved profile data through the existing release channel. Optional community suggestions are opt-in and human-sent.

**Standing product rules:** automated Desk publication uses platform APIs and existing campaign approval. Own-account reading is the user's choice of API or browser pane, defaulting to the pane; broad listening remains in the pane. The September 23 manual-post decision also remains: a human may copy/open/post without an API connection. An agent never drives a browser to click Post. The profile can describe additional routes without enabling them as an automated Desk publisher.

**Non-goals:** automatic selection of a user's service or identity; generation of arbitrary connector code; claims that every generic connection implements Desk publishing or analytics; silent paid probes; automatic package upgrades; automatic central merge; distribution of user credentials or browser sessions; social platforms beyond the current Desk v1 destinations; removal of human gates. A generic API connection exposes approved requests to agents, not an automatically generated Desk publisher.

Open product choices are in section 12. Recommendations there are not decisions. This document changes neither schedules nor runtime settings.

## 2. Existing seams and known limits

The checked branch baseline is `c4fc8115`. `mc/desk_connect/registry.json` v1 contains eight services, a global information-only MCP row, and `options[{method,support,title,evidence,guidance,open?}]`. LinkedIn has only `oauth/restricted`; X has `oauth/available`. `registry.py` validates those fields, caches the data and matches exact host aliases. `methods.is_connectable` additionally requires an implemented provider. Thus a service's recognition, declared option and provider support are already separate, but purpose and transport are absent.

`mc/desk_accounts.py` owns account destinations and derives publishing readiness. `read_via` and `browser_profile` are already per account; engagement also reads the legacy presence copy. `mc/desk_services.py` owns workspace-wide `saved_for_agents` records in `data/desk.json`, with credential names and `publish:false`. These records are not accounts and must not become publishing destinations during migration.

Slice 3's `classifier.py` accepts at most four method/evidence/template options. Its `run_text_transform` denies tools, hooks, plugins, skills and MCP. Its guarded pane confines TCP, blocks non-proxied WebRTC UDP and narrows CDP origins. Unknown names still require an address. This spec extends those seams rather than introducing a tool-enabled discovery agent.

Slice 4 was outside this branch baseline. The same-project merge `538b306f` supplies `mcp_activation.py`, `mcp_catalogue.py` and `mcp_package_store.py`: a verified, self-contained npm tarball is downloaded directly, hashed, safely extracted and registered through `mc/mcp.py`. The earlier `511c8e99` design checked metadata but deferred execution to `npx`; do not reuse that launch path. Catalogue approval remains required to advertise a package as centrally reviewed, but catalogue membership no longer controls user-added connections.

Known slice 4 limits must be carried into implementation review: (resolved 2026-10-05, MC-1047: a passphrase-backed vault now supplies credentials to a stdio child through the server's streaming exec, while Clayrune is unlocked; see `docs/SECRETS.md`) frozen builds may lack the credential wrapper; the existing tarball installer needs total deadlines and concurrent-provisioning hardening; launch environments need `NODE_OPTIONS`/`NODE_PATH` stripping. Ron's latest instruction allows Save with a plain limitation label. Saving an approved pending connection must not claim the process can start. Server-parented streaming credential execution is the existing MC-1047 dependency, not a license to return plaintext secrets from an API.

`mc/mcp_installer.py` still has `_extract_via_claude`, which sends README text to a CLI without certified tool denial. [Untrusted input surface](UNTRUSTED_INPUT_SURFACE.md), finding 1, identifies its persistent execution risk. No new flow may call it. The shared extraction entry point and legacy URL installer must be redirected to the isolated extractor before arbitrary connection support ships.

## 3. Shared profile data

### 3.1 Ownership and vocabulary

`mc/desk_connect/registry.json` v2 becomes a data-only index of profile IDs and relative filenames, plus blocked Clayrune discovery origins. Each service has its own file, `mc/desk_connect/profiles/<service_id>.json`. Its label, aliases, home URL, exact host aliases and route facts have one source of truth in that file. The loader builds the existing name/host maps. It rejects duplicate service IDs, host/name collisions, path traversal, unknown schema fields and broken evidence references.

Purposes initially include `publish`, `read_own` and `listen_broad`. `read_own` names individual capabilities such as `own_posts`, `mentions`, `replies` and `post_metrics`. Generation services retain `generate_image` and `generate_video`; Drive/Photos/YouTube retain their existing recognized identity and information-only capabilities. Do not mislabel generation as publishing merely to fit a social schema. Additional purpose IDs require a schema/code review.

A **route** is an operation path, not just a login method. API and MCP describe how calls travel. OAuth, browser sign-in and API keys describe authorization. A route may therefore be `api + oauth`, `mcp + oauth` or `api + api_key`; it must not become three duplicate options. Browser username/password sign-in can include 2FA or SSO and stores cookies in a named profile. Possessing a password does not establish API authorization.

### 3.2 Required shape

| Field | Contract |
|---|---|
| `schema_version`, `service_id`, `revision` | Supported schema and stable service ID; monotonically increasing per-service revision, including evidence-only changes. |
| `label`, `aliases`, `home_url`, `hosts` | Reviewed identity data; exact host matching, no substring or wildcard ownership claims. |
| `account_kinds` | Distinct targets such as `member` and `organization`; facts cannot silently apply to both. |
| `purposes[]` | Purpose ID, requested capabilities, applicable account kinds, standing-policy restrictions and route references. |
| `routes[].id` | Stable service-local route ID, retained when wording changes; a changed endpoint/authentication contract needs a new approval fingerprint. |
| `routes[].transport` | `api`, `browser` or `mcp`; MCP additionally names `stdio`, `streamable_http` or `sse`. Unknown protocol is explicit. |
| `routes[].auth` | One or more alternatives from `oauth`, `browser_signin`, `api_key`, `bearer`, `none`, `unknown`; required credential parameter names, placement, scopes, issuer and registration requirement. No secret values. |
| `routes[].coverage[]` | Purpose, account kind and capability, each `documented`, `claimed`, `unknown`, `not_supported` or `none_verified`; partial coverage is explicit. |
| `routes[].requirements` | Developer-app ownership, product/tier review, Page role, account plan, permitted use and adapter prerequisites; distinguish provider approval from Clayrune's human Save. |
| `routes[].limits` | Named endpoint/version/rate/quota/content limits, units and reset period, or `unknown`; distinguish provider limits from UI/adapter limits. |
| `routes[].cost` | Currency, unit, endpoint, payer, eligibility condition, source/date and `published`, `account_specific` or `unknown`; zero requires evidence. |
| `routes[].adapter_id`, `catalogue_id` | Optional IDs resolved only against implemented code and centrally reviewed package data; absent means no built-in adapter, not a prohibition on a user connection. |
| `evidence[]` | ID, URL, publisher class, source publication date when supplied, retrieval time, claim IDs, digest and result (`verified`, `incomplete`, `unavailable`, `conflicting`). |
| `last_verified`, `last_attempted`, `verification_status` | UTC dates per route and per changing claim. Unknown/imported facts use `last_verified:null`; an attempt does not renew it. |
| `change_summary`, `supersedes` | Short maintainer-reviewed change and prior revision reference; no account data or raw page bodies. |

Claim IDs bind coverage, auth, costs, limits and requirements to evidence. Route `last_verified` is the oldest successful verification among its required claims; a newer pricing check cannot refresh an older permission claim. Negative findings record search scope and date: `none_verified` means no applicable route was verified, not that no server exists. Registry listing, publisher ownership, platform capability, implemented adapter and authenticated account success are separate facts.

Profiles are inert JSON: no arbitrary shell bodies, model prompts, credential values or executable adapter definitions. Reviewed adapter IDs resolve to code; reviewed catalogue IDs resolve to exact package descriptions. Profiles can describe an unreviewed community candidate using evidence without promoting it into the catalogue. A user may import that candidate through the separately approved path in section 6.

### 3.3 Per-purpose choices and combined coverage

An account holds a `connections` map by purpose and capability. Each binding records service/route ID or local custom-connection ID, profile revision shown at approval, credential reference IDs, browser profile reference, approved scope, configuration fingerprint and human approval time. It does not copy secrets or globally store `connected:true`.

Different purposes may use different routes. A single purpose may need several routes: for example, an API for post metrics and a browser profile for replies. The binding records which capabilities each route supplies; it never infers the missing half. A shared OAuth grant may support several selected routes, but selecting the second route cannot silently add scopes.

Derived readiness has two independent dimensions: setup state (`saved`, `needs_signin`, `pending_runtime`, `setup_failed`, `ready`) and capability verification (`not_checked`, `partial`, `verified`, with identity and timestamp). Readiness is the conjunction of a supported execution adapter, unchanged human approval, required local runtime, readable vault references, provider access and the actual target identity. An opened browser or registered MCP alone proves none of the requested operations.

Information-only, restricted, unknown, unsupported-in-Desk and outside-standing-policy are explanations, not global prohibitions. The user-added path remains visible. Generic MCP/API capabilities must not be substituted for a Desk publisher unless an approved built-in adapter implements that contract. An MCP supplying only documentation is never presented as a publish/read adapter.

## 4. Connect screen and local state

Keep the numbered Service → Routes → Details → Review flow from Connect by URL. Known service names/addresses open the profile without a model call. The Routes step asks account kind and intended purposes, then groups choices by purpose. Each row shows coverage, cost, required approvals, supported setup and evidence date. Expanding it shows the evidence links and limits. The same screen always offers **Add your own MCP or API**, including for a known service with no available built-in route.

The user chooses routes; the UI does not pre-consent to API spending or a package. Own reads default to the pane; missing pane support remains a visible gap instead of silently selecting paid API. Broad API/MCP search routes can be described but are not selected for the Desk's broad listener under the current policy. Manual posting remains an explicit human handoff, not an automated connection.

Details merges credential requirements for the selected routes, reuses existing vault references and named profiles, and keeps account kinds/identities distinct. Editing target, route, credential placement, command, scopes or purpose clears the relevant draft approval. A service URL suggests identity but does not prove it. Existing first-account X singleton bindings and later per-account references remain intact.

Review shows one complete operation, including local mutations and post-Save provisioning. Final Save calls the shared human-only commit gate and `_require_human_passcode` once. Keep host-only first-passcode setup, vault-passphrase separation, wrong-passcode draft retention and idempotent request reconciliation. Local writes use the existing compensating-rollback/undo approach. Add custom-connection approval metadata to the same Desk-store write where possible; if implementation adds an uncompensatable local write, escalate that concrete atomicity question rather than assume the overruled transaction design.

An approved custom definition may be saved while a runtime or vault launch path is unavailable. The result must say **Saved; cannot start yet** with the exact reason. No runnable MCP registration is created until its credential delivery and launch contract work. Retries that preserve the exact approval fingerprint may complete the same bounded setup; changes require a new Review and Save. External consent/install effects retain the existing explicit partial-success boundary.

Local custom definitions, approval records and capability checks live under a new `store['custom_connections']` section in `data/desk.json`, outside DATA_DIR. Package bytes remain in Clayrune-owned runtime storage outside the repo. Service records reference custom connection IDs and remain `publish:false`; account bindings exist only for real account records. Removing a binding never deletes a shared vault entry or browser profile. Actual uninstallation or user-data deletion uses existing human removal flows.

At 390px, purpose groups, risk details, command/URL and credential field names remain readable, with wrapping or an explicitly scrollable code area, a bottom action above keyboard/safe-area insets, and no page-wide horizontal scroll. Warnings cannot hide the final action. Evidence state and progress are announced to screen readers; never replace focus during a re-render.

## 5. Auto-detection without execution

### 5.1 Inputs and sources

The user can enter a service URL/name, an npm package spec, a PyPI package spec, a remote MCP URL, or an API base/spec URL. The flow also accepts a pasted non-secret configuration or OpenAPI document, with human editing when discovery is incomplete. Resolve ambiguous input by asking its type; never search for a different service to obtain a successful answer. Unknown names continue to request an address or an explicit package identifier.

Detection returns draft parameters: transport; package/source; command and argument array or URL; required environment/header/query/body credential field names; authorization method; OAuth issuer/consent/token endpoints and scopes; API operations and request schema; provenance and uncertainty per field. It must detect multiple alternative configurations rather than choose a single method for all purposes. It must not claim to discover parameters that the evidence does not reveal.

Use deterministic parsers for package metadata, fenced configuration examples and OpenAPI structure, plus the isolated classifier for untrusted text. Every candidate remains untrusted until the user reviews it. The user can correct or supply missing fields. A missing model, unreadable README or unknown licence produces an explicit risk/coverage label and editable details; it does not remove the user-added route.

Fetching and parsing are implemented services, not an agent with browsing tools. Public HTML uses the existing guarded pane and no-downgrade failure contract. Fixed package-registry metadata and explicitly supplied machine-readable API specs use a bounded non-executing reader, separate from the HTML reader. OpenAPI external references, example URLs and metadata links never trigger unrestricted recursive fetches; each fetch uses the approved discovery boundary. A failed page read is never retried with curl/requests or an archive decoder. Archives fetched specifically for package inspection are inert data, safely extracted and never imported or installed during detection. Dependency audit commands, package lifecycle hooks, repository hooks and MCP process startup are not detection tools.

Slice 3's public-address restrictions stay intact for automatic discovery. A user may deliberately configure a localhost, LAN, private-registry or non-HTTPS target; show **Can reach this local/private address** or **Unencrypted connection**. Do not turn public discovery into a private-network crawler. Such targets use manual/pasted parameters before Save; their first network probe follows the explicitly approved target contract. Never send credentials to a redirect origin that was not approved. Platform OAuth cookies/passwords are never given to the classifier.

### 5.2 Classifier contract

Add a distinct versioned parameter/proposal schema rather than loosening slice 3's current exact schema. Each output field has a type/length bound, evidence ID and confidence from an enum. Transport/auth/purpose/capability values come from enumerations. Commands are inert strings and argument arrays, never shell-evaluated by parsing. Credential fields contain names/placeholders only. URLs are candidates linked to fetched evidence or explicitly user-entered fields, not fetch instructions.

Retain slice 3's 20,000-character total model input, four route alternatives, 30-second transform and 60-second discovery budgets, three MCP-registry pages/100 results and explicit incompleteness. Four alternatives may cover several purposes through a capability matrix. Larger candidate sets are labelled incomplete; the manual form stays available. Package/spec fetches also have byte/member limits and total deadlines; an over-limit input returns a specific error rather than silently omitting requirements.

Only `run_text_transform` with certified tool denial may consume raw page/README/registry text. No hooks, plugins, skills, MCP fleet or tool-enabled fallback. Validate duplicate keys, unknown fields, evidence IDs, bounds, control/bidi characters and credential-like literal fields. Model output cannot set human approval, central-review status, adapter IDs, trusted publisher status or a vault value. Deterministic validation establishes shape and provenance, not truth or safety. Clearly label the result **Detected from untrusted evidence**.

Remote protocol/auth metadata may identify proposed endpoints without registering clients. Pre-Save detection never runs a local server, sends an API operation, starts OAuth consent/DCR, creates a profile, writes a vault entry, registers MCP or changes settings. Authentication, protocol initialization and `tools/list` occur only after approval; a package cannot be run merely to discover how to run it.

## 6. User-supplied execution and approval

### 6.1 The approval card

Both curated and custom configurations use the same final card and human Save, while distinguishing **Centrally reviewed** from **User supplied; not reviewed by Clayrune**. Plain risk labels inform, not categorically refuse, the user's choice. Validation errors, pin mismatches and missing runtime support still produce truthful failure states; accepting risk does not justify executing different bytes or leaking a secret.

The card must show:

- Exact executable plus argument array, or exact remote URL and transport; any dependency installation/build commands that will run, working directory and deferred first-start behavior. Shell/interpreter bodies are shown verbatim as inert text with **Runs arbitrary code**; no harmless paraphrase replaces the command.
- Package ecosystem, registry/source URL, exact version and artifact/dependency hashes, or a prominent **Unpinned** where bytes cannot be fixed, such as a mutable remote service or user command. Registry publisher identity is labelled claimed/verified/unknown; ownership is not endorsement. Licence and size may be unknown without preventing Save.
- Environment and credential parameter names, vault reference names, injection locations, requested OAuth scopes, consent/token origins and credential recipients. No value appears in the card, model input, logs or persisted draft. A password has its username on the same vault entry.
- Exposure: local code runs with the host account's filesystem/network permissions unless an actually enforced sandbox is present; the named secrets go to that process/provider. Remote services receive supplied credentials and requests. Claimed reach and enforced restrictions are separate. Global MCP scope can expose tools/content to agents in every project; project scope names the selected project.
- Purpose/capability coverage, read/write/spend abilities, provider billing/approval requirements, and setup limitations. A tool list is not a security boundary: a stdio process may do work before its first MCP tool call. Provider content/tool results may later reach tool-enabled agents; the toolless discovery boundary does not sandbox runtime MCP code or its results.

Recommended scope default remains open in section 12. Whichever default Dave selects, global/project scope is explicit, fingerprinted and change-gated. Existing global servers retain their existing scope during migration. A user can choose broader scope only through this same human Review and Save.

The approval fingerprint covers protocol, target/package source, version/artifact and dependency hashes, resolved command/arguments/working directory, install/build steps, credential reference/placement, auth/scopes/recipient origins, project scope, allowed API operations and exposure labels. The server recomputes it from the submitted immutable operation. A changed payload under an existing request ID returns 409. No request can pass `approved:true` to bypass the human gate. Deferred launch checks the stored fingerprint again.

### 6.2 npm and PyPI packages

Pin on first approval means a range, tag or omitted version resolves to an exact release and byte digest before executable approval. Show the resolved pin on Review; resolve metadata/archive information without running package code. Package membership, publisher status, proprietary licence or lack of a profile does not prevent choosing it. No later launch resolves `latest` or another semver range.

Reuse slice 4's direct download, digest check, safe extraction and owned package store for self-contained npm packages. Generalize that utility without making `mcp_catalogue.py` the policy validator for custom inputs. Catalogue facts remain reviewed; custom facts are not assigned `bundled:true` merely because a README says so. Verify archive path/member limits before extraction, use digest-addressed immutable directories, an atomic per-artifact lock and a total deadline. No other Save overwrites bytes used by a running server.

Unbundled npm packages need a recorded exact dependency closure and digests. Install from staged approved artifacts with lifecycle scripts disabled by default; if scripts are needed, show the exact script bodies and reach on a new Review before running them. Do not trust a version-only top-level pin while `.npmrc`, ambient registry configuration or transitive ranges choose executable bytes. Launch from the approved environment without `npx` fetching on demand. Strip ambient loader/package-manager overrides; intentionally needed overrides become explicit approved fields with their risks shown.

PyPI packages use a dedicated environment and exact artifact/dependency hashes. Prefer wheels. Source-only packages remain supported: build backend, build dependencies and build/install hooks execute code, so resolve and show their artifact pins and steps before human approval, then build without connection secrets and without undeclared dependency downloads. Locally produced outputs are hashed and bound to the approved receipt. A build that needs new dependencies returns to Review instead of quietly resolving them. Isolated build directories protect state organization; they are not claimed as an OS security sandbox.

If a dependency closure cannot be determined without executing code, Save can retain a pending definition with **Dependency resolution needs approval**. A separate human-approved build/resolution step shows its exact command, sources, reach and missing pins; no service secrets are supplied. Its resulting artifact/dependency manifest then requires the final executable Review and Save. The flow must provide this path rather than declare the package unsupported. Packages are never automatically launched as unpinned because dependency detection failed.

Changes to source, version, digest, dependency closure, scripts, command, environment or scopes invalidate executable approval and require a new passcode Save. A digest mismatch is an error, never something the installer repairs by accepting the registry's new value. Reinstalling identical verified artifacts can reuse the exact stored approval. Hashes prove bytes match what was approved, not that those bytes are safe.

### 6.3 Remote MCP

Support user-specified Streamable HTTP and SSE using `mc/mcp.py` normalization/registration and an execution wrapper where vault references require one. Autodetection proposes protocol/auth from metadata or documentation. An inconclusive probe leaves the protocol editable. Post-Save initialize/`tools/list` can verify protocol availability; it does not prove every purpose or endpoint safe.

The profile name `streamable_http` maps explicitly to `mc/mcp.py`'s current `http` transport enum. Strictly validate before that normalizer: it currently coerces argument strings and drops unknown fields. Store/fingerprint the exact normalized executable definition the user sees, not an earlier draft that could normalize into something different.

A remote service cannot generally be pinned to code bytes. Show **Remote server can change without a version pin** and record the endpoint, issuer, scopes, client-registration and observed capability contract. The approved URL/credential recipients remain fixed; redirects and newly requested scopes require human review. A changed observable tool/auth schema suspends affected verification and asks for review before adopting new capabilities. Undetectable server-side changes remain an explicit risk, not a promised reapproval guarantee.

Tokens are resolved server-side from vault references at execution. Never write resolved header values to `.claude.json`/`.mcp.json`; if a runtime cannot use that reference contract, use an owned proxy/wrapper with redacted output or retain `pending_runtime`. OAuth/DCR happens only after accepted Save and uses the existing human-started vault callback. No agent-facing credential creation or plaintext token-return route is added.

### 6.4 Any API

A generic API connection can be added even when no profile or built-in provider exists. Detect endpoint/auth/request parameters from an explicitly supplied OpenAPI document, public documentation or pasted non-secret examples. Support editable HTTP method, URL/base, paths, headers/query/body schema, credential placement, OAuth or API-key/bearer/basic/no-auth options, requested scopes and response mapping. Missing schema leaves an editable request template; it is not converted into a refusal or a made-up working adapter.

HTTP templates include GraphQL bodies and provider-specific request formats. A protocol that needs a client/bridge beyond this executor must be labelled **Needs a protocol adapter**, remain savable, and accept the user's explicitly approved MCP/client bridge through the custom execution path. Do not report HTTP-only verification as support for a different protocol. Generating a bridge is out of scope; profile absence is never the reason it cannot run.

Implement a shared request executor, not generated Python/JavaScript. It resolves only approved credential references and operation templates, sends credentials only to approved recipients, applies time/body/response limits and returns a provenance-marked result. A local/private/plain-HTTP API carries its exposure label and is contacted only after its exact target is approved. Redirects do not silently forward credentials. No destructive/paid operation is run as a connectivity test.

Save records which operations and intended read/write/spend permissions are approved. Default test is none unless the human chooses a documented read-only operation with its expected cost. Unknown billing says **Cost unknown**. A connection approval does not authorize publishing a Desk post, an external message, a payment or deletion; existing action gates remain. Generic API responses/specs remain untrusted data. Any subsequent parameter extraction uses the toolless path, not a full-tool agent reading documentation to invent requests.

Availability means agents can use the saved connection through the approved executor. Native Desk publish/read/engine bindings still require their existing adapter contract. For example, adding a LinkedIn API does not turn a member into the Company Page, grant Community Management access or flip the code's organization-posting gate.

### 6.5 Reuse and bypass closure

Reuse `mc/mcp.py` for MCP configuration storage/normalization, the slice 4 package store for verified artifacts, and `mcp_installer.py` only through shared non-executing extraction and staged-install services. Replace its unsafe README CLI fallback with the same certified transform. Existing MCP UI/edit/import paths must enter the same approval/fingerprint/vault-reference service; a second endpoint must not bypass approval by writing arbitrary config directly. Catalogue and custom activation share the executable approval check, not the curated membership check.

Registration must check conflicts and write under the same MCP-config lock. Never overwrite an unrelated server silently. Activation/provisioning returns redacted state and bounded failures. Package inspection, installation and first startup are separately observable; register every owned long-running process with the Process Manager. Retrying setup never starts an unregistered process or leaves duplicate children.

## 7. Migration and compatibility

Ship a versioned loader and fixtures before changing the live UI. During transition, v1 input is converted in memory and a v1 response projection preserves current callers. Only methods that map uniquely to existing reviewed providers may be selected through that projection. Once several routes share `oauth`, a v1 selection is ambiguous and must request a refreshed v2 draft; it must not pick the first row. New commit payloads select route IDs, purposes, account kind and approval fingerprint.

| Existing row/state | Migration |
|---|---|
| X `oauth/available`, `open:account:x` | API/OAuth route for existing account adapter; own-read coverage/choice is separately bound from the existing account settings. Preserve credential/profile IDs and manual capability. |
| Higgsfield OAuth and API-key rows | Distinct MCP/OAuth and API/API-key generation routes; retain current engine IDs, plans and credential references. Do not infer equal billing. |
| Google AI Studio and OpenAI API-key rows | Existing generation routes and adapter IDs; no new subscription entitlement or social-purpose claim. |
| LinkedIn `oauth/restricted` | Organization API/OAuth publication requirements retained; add separately evidenced member, browser and MCP-candidate coverage. No member account or organization approval is created. |
| YouTube, Drive and Photos `info_only` | Recognized profiles with explicitly unimplemented built-in capabilities; custom connection remains available. |
| Global `common_options` MCP information row | Remove blanket status; per-service evidence says official/community/none verified/unknown. User-added MCP is always offered independently. |
| Slice 4 Notion catalogue record on newer master | Preserve its exact approved package artifact and identity; move only profile guidance into Notion's own profile file. Existing executable approval cannot be expanded by migration. |
| `saved_for_agents` service | Preserve ID, label/link and credential name; optionally bind its recognized service ID. Remains non-publishing; activation requires a fresh human Save. |
| Legacy MCP/API configuration | Show **Existing configuration; approval/pins not recorded**. Do not claim new verification, replace it, auto-launch a changed version or migrate plaintext into a new config. Human adoption moves credentials to vault references and records pins. |

All eight original services and newer reviewed additions must retain name/host recognition and fallback behavior. Imported `evidence:"Clayrune's built-in list"` becomes legacy evidence, `verification_status:unverified`, `last_verified:null`; conversion time is not verification time. New researched claims alone gain dates.

Lazy account binding migration must be idempotent and preserve `read_via`, `browser_profile`, credential names, account IDs, voices, organization ID and legacy presence-read synchronization. No update creates a secret/profile, starts consent, installs packages or changes a manual account into a direct one. Existing direct bindings point to their exact route/approval; they are not regenerated from a profile's recommendation.

Rollback of the first data slice requires only reverting profile/loader code, since it does not mutate account state. Later binding additions are additive and old fields are retained until all callers move. Runtime/custom activation code must keep a schema-compatible reader for stored approvals; an older release unable to understand a custom definition leaves it visible/pending instead of selecting another route. Do not remove existing user records as rollback.

## 8. Central research and human review

The designated maintainer install has a local operator opt-in for profile maintenance, default off on every install. It is neither committed as an operator identity nor inferred from a clone's path. Only that opt-in creates/enables the central scheduler job; disabling it stops future cycles. Ordinary users never inherit the schedule. Cadence is an open choice in section 12.

One cycle works serially in a fresh branch/worktree against the shipped profile revision:

1. Load each due profile's evidence URLs, platform changelog/version notices, pricing/permission sources and official MCP-registry query terms. Check every route family, including negative official-MCP findings. New routes enter as candidates, not executable defaults.
2. A bounded source reader and certified toolless classifier collect/normalize untrusted documents into typed evidence claims. The central orchestration agent retains tools for branch edits, checks and backlog state, but raw README/page/registry bodies never enter its context. Human review of eventual output would not prevent an injection from acting during research; the latest input-isolation rule applies here too.
3. Compare claims by stable IDs: endpoint/auth/scopes, account eligibility, permissions, rates/quotas, pricing, deprecation, ownership, package pins and coverage. Separate source-digest changes from changed facts. Successful unchanged checks update evidence dates in a proposed branch; failed/incomplete checks retain prior verified facts/dates and record `last_attempted`/failure.
4. Commit only candidate profile/evidence changes and a bounded evidence-diff report. Open or update one backlog item identifying branch/base/commit, affected purposes, before/after claims, source URLs/dates, uncertainty, cost/permission consequences, validation and rollback. Use a journal for the running log, not backlog notes. No external PR publication/push occurs without the applicable authorization.
5. Stop at **Review required**. The agent cannot approve its own changes, merge, push master, replace package catalogue approvals or activate any user's connection. A human reviewer verifies source provenance and the semantic diff, accepts/rejects the exact candidate revision, and uses the existing release workflow.

The registry's [official API](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/api/official-registry-api.md) supports name search and dated synchronization, with deprecation/deletion status. Listings are evidence, not security review. A namespace/publisher or package change creates a reviewed change even if the display name is unchanged. Capped lookup records incompleteness; it cannot update a negative claim to verified absence.

Each cycle has a total runtime/model budget, bounded source requests and a per-service lock. Persist progress outside DATA_DIR; resume skips already checked revisions instead of repeating whole research. Deduplicate proposals by base revision plus claim-diff digest. If the base changes during a cycle, rebase/revalidate and show the new diff before review. A fetch/model failure produces an observable journal/error status, not a green renewal. No login, consent, provider-app registration, paid account test or package execution is authorized by the research schedule.

A review has two independent questions: are the shared service facts correct, and should a package be labelled centrally reviewed? The first cannot answer the second. Human-reviewed catalogue entries retain exact pins and security review; users may independently choose other entries via section 6. Central review cannot grant API permissions to a user's app or account.

## 9. Cascade and freshness

The mandatory initial delivery path is the current release channel: approved profile JSON is committed to `master` by the authorized reviewer/releaser and pushed through the existing process; installs receive it on their normal update. This spec's own branch is not merged or pushed. Offline/non-updating users keep their shipped snapshot; there is no claim that every running install changes immediately.

The loader validates schema compatibility, cross-references and content before exposing a snapshot. Profiles, index and catalogue references load as one coherent revision. On invalid or incompatible data, retain the previous valid shipped snapshot and show **Profile update could not be loaded**, while user-added connections remain available. If startup has no valid snapshot, show **Shared profiles unavailable** without breaking the dashboard or custom connection flow. Package profiles in source and frozen builds explicitly; test the actual artifact, since JSON next to a Python module is not automatically bundled. Clear backend caches on snapshot replacement and version the inspect response so an open SPA does not keep old routes under new metadata.

An update may change displayed facts/recommendations, never a local selection, package version, issuer, credential recipient, scope, cost consent or approval. Unaffected bindings stay as chosen. A removed/deprecated route remains visible on existing bindings as **Needs review**; no alternate API, browser or MCP is selected. A changed executable/auth contract cannot reuse its old approval. Scheduled work validates the current approval before execution and records a coverage/setup gap when a required contract is no longer valid. Discovery remains possible without an approved shared profile.

Show revision, last verified and last attempted independently. Unknown or failed research does not erase old evidence. Until Dave chooses a cadence/freshness policy, the loader must expose computed age and errors without inventing a TTL. New detection explicitly reports stale/incomplete facts. Age alone is not evidence that an approved local connection is unsafe or broken.

| Delivery option | Benefit | Cost and risk |
|---|---|---|
| Repo/release only | Reuses review, git history, rollback and current update trust; no extra public endpoint. | Facts can wait for a release and user update. |
| Separate signed profile feed | Faster independent data refresh with app/profile compatibility checks. | Signing/rotation/revocation/replay defenses, hosting and client-update code; a feed steering MCP package execution is a supply-chain target. |

**Recommendation for Dave:** release-only first; evaluate a signed feed after evidence shows release delay is causing wrong connection guidance. This is not a selection. A feed, if chosen, needs a separately reviewed slice: pinned trust root, signed canonical bytes and revision/schema/hash/expiry metadata, anti-rollback/replay checks, atomic acceptance, key rotation/revocation, offline last-good behavior and an auditable manual rollback authorization. Feed signing credentials are human-managed, never created by an agent.

Feed signatures authenticate publisher data, not package safety. A feed must not directly add executable packages, execute commands, rewrite local approvals or relax scopes. Catalogue changes still need human security review; installation still needs the user's exact Review and passcode Save. Local custom connections must not be overwritten or disabled because the feed lacks them. The feed choice is not a dependency of user-supplied MCP/API slices.

## 10. Unknown-service profiles and optional suggestions

Extend slice 3's explicit **Look it up** to return a multi-route purpose/capability proposal using section 5's isolated schema. Known service profiles may also offer an explicit lookup of a user-supplied candidate without rewriting the shared profile. No automatic search of an unknown name guesses the user's intended account.

Proposed profiles show **Untrusted evidence; not reviewed by Clayrune**, source links, claimed publisher, field confidence and lookup date. The classifier's result can seed an editable custom connection, but cannot approve or activate it. The human-reviewed exact definition is authoritative for local execution. **Save for agents** remains available when the user wants only a remembered service; it is no longer the only path for an unknown service.

**Suggest to Clayrune** is optional and human-sent. Default off, separate from Save and from account connection consent. Show the exact outbound payload and destination before Send: service identity, public evidence URLs, detected purpose/route facts and uncertainty. Strip account paths/identities, private hosts, project names, credential references/values, local paths and browser data; let the user preview/remove every field. No background telemetry, automatic registry publication or silent submission occurs. A suggestion remains an untrusted candidate for central review, not a mechanism for installing code on other users' machines.

The receiving channel is an open choice in section 12. Until it exists, the UI may provide an explicitly human-copyable, redacted proposal export and must not show a fake successful Send. Detection, local custom Save and optional export can ship without a central receiving service.

## 11. Worked examples and evidence

Start from [MCP route scan, checked October 1](desk_v1/MCP_ROUTE_SCAN.md), preserving its research-not-integration boundary. Public documentation below was re-read on October 5 without login, account probes, client registration, package execution or paid calls. A fact's October 5 document verification is not an authenticated connection check. The scan's community examples remain leads dated October 1 unless independently rechecked.

### 11.1 LinkedIn: member and Company Page

| Account / purpose | Routes to describe | What the user must see |
|---|---|---|
| Personal member / publish | API with member OAuth; manual human posting; community MCP candidate. | `w_member_social` is member posting authorization. No built-in personal publisher is implemented here. Ron's personal LinkedIn is not a Desk destination unless he explicitly selects that scope. |
| Company Page / publish | API with OAuth and organization authorization; human manual Page posting; community MCP only as an unreviewed custom choice. | Community Management product review, actual Page role, target organization and `w_organization_social`; signing in or choosing an MCP does not grant any of them. |
| Member / read own posts, mentions, replies, metrics | Named browser sign-in; restricted API capabilities; community MCP claim if supported by its underlying permissions. | Member posting consent is not read access. `r_member_social` is closed to new requests; separate analytics coverage must be checked by capability. |
| Company Page / read own activity and metrics | Named browser profile in the Page's context; reviewed Community Management API with appropriate read/analytics scopes; MCP wrappers retain upstream requirements. | An administrator's signed-in member identity and the selected Page are distinct. Post reads, analytics and a general mentions inbox cannot be treated as one verified capability. |
| Either / broad listening | Browser sign-in for the Desk; API/MCP discoveries may be informational. | No claimed unrestricted LinkedIn search API; browser coverage and automation eligibility have limits. Password possession proves neither API access nor complete metrics. |

[Share on LinkedIn](https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin) documents member OAuth with `w_member_social`. It establishes a member posting route, not Company Page authority or own-account read entitlement. Its older endpoint examples must not override the current [Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api), which documents `/rest/posts`, version headers, separate member/organization write and read permissions, and permitted organization roles.

[Community Management overview](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/community-management-overview) describes reviewed access tiers, organization activity/analytics and a closed `r_member_social` permission. [App review requirements](https://learn.microsoft.com/en-us/linkedin/marketing/community-management-app-review) require registered legal organizations for commercial use cases, verified business identity/Page-associated application and further Standard-tier evidence. OAuth consent alone cannot satisfy review. Shared profiles must store these prerequisites without asserting this install or every user's app has passed them.

LinkedIn API tariff/partner fees remain `unknown/account_specific` in this example, not assumed zero. Exact endpoint quotas are also unverified here. The [recent changes](https://learn.microsoft.com/en-us/linkedin/marketing/integrations/recent-changes) page is a central watch source. The October 5 documentation warns of the October 15 sunset of version `202510`; record version deprecations with dates, not an undifferentiated “LinkedIn supported” flag.

The October 1 scan found no applicable official LinkedIn action MCP and named a community member-posting wrapper. Preserve `none_verified`, search scope and date rather than assert no MCP exists. A user can supply that wrapper or any other MCP/API without central approval of the package; the card labels it user supplied and the selected scopes still need provider authorization. The scan's LinkedIn terms questions remain unresolved; a passcode or a confirmation button is not legal clearance.

**Ron/Clayrune configuration example:** the ron voice targets X; the clayrune voice targets the LinkedIn Company Page. The Page connection can combine approved API publication with a named browser profile for visible own activity, and optionally API analytics when eligible. Each capability has its own readiness. Adding Ron's member sign-in neither creates a member publishing destination nor replaces the Company Page. `LINKEDIN_ORG_POSTING_APPROVED=False` in the checked code remains a runtime gate; this document and custom Save do not flip it.

### 11.2 X

| Purpose | Routes to describe | Coverage/limits |
|---|---|---|
| Publish ordinary posts/replies/media | Current API/OAuth account adapter; manual human posting; action-MCP candidate. | Keep API publication and approval. The MCP guide's Article publication is not evidence of ordinary post/media/reply parity. |
| Own posts/mentions/replies/metrics | User-selected API/OAuth or named browser profile, default pane; user-supplied action MCP can supply independently checked capabilities. | Read eligibility, pagination/cursors, rate limits and requested metrics are separate; do not promise all capabilities from an account login. |
| Broad listening | Pane for the current Desk; API/MCP search described as alternative platform capabilities. | API search is metered and is not silently selected for the Desk listener. |
| Documentation | Public docs MCP. | Documentation lookup alone supplies no account publish/read capability. |

The [X MCP guide](https://docs.x.com/tools/mcp) distinguishes `https://api.x.com/mcp` from `https://docs.x.com/mcp`. User context uses an owned app and OAuth PKCE via the xurl bridge, with no DCR/native MCP OAuth discovery. App-only bearer supports a separate read context. An official endpoint is evidence of a route, not a ready Desk adapter. The guide's bridge storage must be adapted to the vault-reference contract; copying it into global config is insufficient.

[X pricing](https://docs.x.com/x-api/getting-started/pricing), checked October 5, lists $0.015/post creation, $0.200/creation with URL and $0.005/general post resource read. Eligible owned reads are $0.001/resource when the authenticated user is the developer-app owner and the documented endpoint qualifies; it is not every own-account metrics request. This qualifies the older blanket read-price premise without changing the chosen API/pane policy. Store endpoint/eligibility and units; an MCP-specific tariff remains unverified. X Premium is not established as payment for this route.

**Combined account example:** `publish → API/OAuth`, `read_own.mentions → pane` and `read_own.post_metrics → API/OAuth` after explicit paid-read selection. If metrics permission is missing or the pane is signed into another user, only the relevant capability is unavailable. No fallback reads another account's singleton token or charges API because a pane read failed. A custom MCP connection remains possible independently of central route completeness.

## 12. Module boundaries, slices and acceptance

### 12.1 Independent files

| Unit | Proposed owner/file and reuse |
|---|---|
| Profile schema, loading and v1 projection | `mc/desk_connect/profile_schema.py`, `profile_loader.py`, `profile_compat.py`; data-only index and one JSON per service. Thin existing registry wiring. |
| Purpose bindings and readiness | `mc/desk_connect/purpose_bindings.py`, `route_readiness.py`; reuse account refs, verification and store lock. |
| Parameter extraction | `mc/desk_connect/parameter_classifier.py`, `parameter_schema.py`, `parameter_sources.py`; reuse slice 3 transform, display scrub and guarded pane. |
| Custom approval/storage | `mc/desk_connect/custom_connections.py`, `connection_approval.py`; reuse commit/passcode/undo, never broaden the existing route monolith. |
| npm and PyPI provisioning | `mc/desk_connect/npm_packages.py`, `pypi_packages.py`, `dependency_manifest.py`; extract/generalize slice 4 verified archive utilities without growing the installer monolith. |
| Remote MCP and generic API execution | `mc/desk_connect/remote_mcp.py`, `api_connection.py`; reuse MCP normalization, vault execution and OAuth callbacks. |
| Central maintenance | `mc/desk_connect/profile_recheck.py`, `profile_evidence.py`, `tools/desk-service-profiles/recheck.py`; reuse scheduler/backlog/journals and no merge authority. |
| Proposal export/submission | `mc/desk_connect/profile_proposal.py` and its own blueprint; redacted export first, chosen receiving adapter later. |
| UI | `static/js/desk-v1-connect-purpose.js`, `desk-v1-connect-custom.js`, `desk-v1-connect-risk.js`, `desk-v1-connect-proposal.js`, with their own CSS; reuse shared secret form and approval card. |

New blueprint files own custom connection and proposal routes. Existing `desk_routes.py`, `mcp_installer.py` and other audited monoliths only wire extracted modules. Registries hold references/data, not implementation bodies. New Python modules pass pyright basic. Final filenames can follow an existing convention discovered during implementation, but independent concerns cannot be bundled to avoid a wiring edit.

### 12.2 Shippable slices

| Slice | Deliverable and dependencies | Acceptance tests required before merge |
|---|---|---|
| P1: profile data and migration | Versioned schema/loader/compatibility; one file per service; LinkedIn/X evidence and faithful conversion of all existing rows. No account writes. | Exact host/name recognition; duplicate/cross-reference/path rejection; every v1 row maps; unknown timestamps stay null; generation purposes survive; imported LinkedIn does not become available; invalid snapshot cannot partially load. |
| P2: purposes and combined bindings | Purpose groups, capability coverage and stable per-account binding; P1. | API publish plus pane own reads, two routes within `read_own`, partial verification, separate member/Page and two X identities; preserved legacy refs/read settings; no writes before Save; wrong-passcode/idempotency; credential change clears only affected verification; 1440/390px smokes. |
| U1: isolated detection and editable proposals | Extract the legacy unsafe README fallback; detect npm/PyPI/remote/API parameter drafts; no activation. Can start alongside P1. | Hostile README/page/registry/OpenAPI fixtures produce zero tool calls, MCP writes or credential access; certified denial unavailable never launches a fallback; provenance/duplicate/extra-field controls; mocked sources cover transport/command/env/auth/scopes; unsupported evidence is labelled editable/incomplete, not replaced. |
| U2a: common approval and self-contained npm | U1, shared fingerprint/passcode/vault-reference path, custom store and slice 4 verified artifacts. No profile membership dependency. | An unknown self-contained package saves without a catalogue entry and its approved bytes run; exact normalized command/pin/reach shown; archive traversal/links/bombs/deadline/concurrent Save checks; changed command/pin/scope re-asks; legacy/catalogue writes cannot bypass the gate; passphrase/frozen limitations allow pending Save with truthful state. |
| U2b: npm dependency and script support | U2a; exact dependency closure, staged installation, explicit script approvals and fixed launch environment. | A non-bundled unknown package runs from approved dependency artifacts; malicious `.npmrc`, semver ranges and ambient loaders cannot replace bytes; scripts are off unless their exact bodies are approved; missing dependency returns to Review without service secrets or an unpinned launch. |
| U2c: PyPI packages and builds | U2a common approval; own environment, wheel/source provisioning and build manifest. | Unknown wheel and source-only package each save/run; package/build/runtime dependency pins displayed; source build has no connection secret; new build dependencies require approval; wheel archive escapes rejected; changed artifacts re-ask; no global pip/environment mutation. |
| U2d: remote MCP HTTP/SSE | U1/common approval from U2a; owned vault-reference delivery wrapper and MCP registration. Independent of npm dependency/PyPI work. | Both protocols can save without catalogue entries; exact URL/issuer/scopes/recipient/global-project reach and mutable-server risk displayed; no pre-Save consent/initialize; no token in headers on disk; redirects/changed scopes re-ask; observed schema changes clear affected checks; handshake alone never claims purpose verification. |
| U3: user-added API executor | Editable spec/request templates, approved operations, credential delivery and checks; U1/common approval, no MCP dependency. | API absent from profiles can save and perform an explicitly chosen harmless request; local/private target needs explicit post-Save approval; no pre-Save probe; no secret in config/log/model; redirects cannot move credentials; malformed schema leaves manual entry; unknown billing stays unknown; no POST/DELETE/spend used as a connection test; generic connection cannot flip Desk publisher readiness. |
| P3: central recheck and release cascade | Main-install opt-in, chosen cadence, typed evidence diff, branch/backlog/journal workflow and atomic release loading; P1. | Ordinary install schedules none; changed scopes/prices/deprecation/package publisher yield distinct reviewed diffs; failed fetch leaves verification date; no-change cycle proposes date refresh; duplicate/base-change handling; agent cannot merge; source and frozen artifact load same profile revision; update/offline rollback preserves custom choices and approvals. |
| P4: unknown profile view and proposal export | Multi-route profile proposal, redacted human export; U1/P1, optional purpose UI from P2. | Sign-in wall/timeouts/caps and original TCP/UDP/CDP protections retained; all findings marked untrusted; custom flow remains available; forged evidence cannot become central review; no outbound request on lookup/Save/export; no account/secret/profile/path leakage. |
| P5: optional human-sent suggestion | Only after receiving-channel decision and its review; P4. | Exact outbound payload and recipient preview; explicit Send and existing human action gate; cancelled/failed Send reports truth; accepted suggestion cannot update catalogue or install anything. |
| P6: optional signed feed | Only after separate delivery decision; P1/P3. Not required by U1-U3. | Bad signature/hash/schema, expiry, replay, key rotation/revocation and partial download retain last-good snapshot; local custom records and approvals unchanged; no feed payload can execute or make a package centrally reviewed without review. |

The user-added MCP goal is complete only when U2a-U2d provide all specified package/remote execution paths, including source builds that require staged approvals. A demo of the one curated Notion package does not satisfy it. Each slice can ship independently with its supported variants stated. Missing MC-1047/runtime prerequisites are visible pending states, not false “Connected” claims; the slice's release report must state which execution paths remain pending.

Run existing Desk connect, discovery, MCP and vault/passcode regressions for touched seams. Run relevant UI smokes after each merge before merging the next slice, and the real-Chromium network confinement proof when discovery changes. Use the main checkout's smoke dependencies in a worktree; do not install a second browser stack. Test packages/API operations with local harmless fixtures, not live OAuth consent, production publishing or paid calls. Installer/bundling changes require clean Windows/Ubuntu install tests and frozen artifact inspection. Documentation tests alone do not prove runtime support.

### 12.3 Open choices for Dave

These do not reopen Ron's right to choose any service. The deciding session records its picks in the project journal; this dispatched specification selects none.

1. **Delivery beyond releases.** A: repo/release only, existing trust/operations with update latency. B: signed profile feed, lower latency with signing/rotation/hosting/client security work. Recommendation: A initially; reserve P6 until measured release delay warrants B. Initial repo cascade is required by the brief regardless.
2. **Maintenance cadence/freshness.** A: weekly full profile checks plus explicit checks for known deprecations, lower verification lag and more research cost. B: monthly full checks, lower recurring cost and up to a month of stale guidance. Recommendation: A with a fixed cycle budget and a visible overdue status, without turning age alone into a block on user-chosen connections. No schedule/time/TTL is installed by this spec.
3. **Default scope for new custom MCP.** A: selected project, narrower exposure and sometimes repeated setup. B: global, matches current slice 4/workspace convenience but exposes it to every project. Recommendation: A with explicit global opt-in. Existing configurations keep their scope; both options remain user-selectable and approved.
4. **Suggestion receiving channel.** A: human export only first, no hosting/abuse/retention infrastructure. B: human-sent submission to a maintainer inbox or intake endpoint, easier contributions but needs an actual recipient, retention/rate controls and isolated ingestion. Recommendation: A for P4, choose/authorize B before P5. No agent invents a destination or sends the proposal on the user's behalf.

Other provider facts remain evidence gaps, not choices to guess: LinkedIn account eligibility and terms, capability-specific reads/metrics, X MCP parity/tariff and custom-server runtime claims. Resolve through evidence or approved checks; retain explicit unknown values until then.

## U1 as built (2026-10-05)

Detection only. Nothing in this slice activates, registers, saves, probes a target, opens the vault or starts a process.

- **Route.** `POST /api/desk/connect/detect` (`mc/blueprints/desk_connect_detect_routes.py`, wired by three lines in `server.py`): `{kind?, input, text?, docs_url?}`. 403 for an unattended agent session, one detection at a time (429 `busy`), 60 seconds in total. `kind` is `npm | pypi | remote_mcp | api_spec | api_base | pasted`; without it only unambiguous input (an `npm:` / `pypi:` prefix, an npmjs.com or pypi.org link, JSON or multi-line text) is classified and anything else is 422 `needs_kind`. No search for a different service is ever made.
- **Files (one unit each).** `parameter_schema.py` (enumerations, bounds, hidden-character and credential-like tests, `risk_flags`, `field`, dedupe/merge), `parameter_parsers.py` (deterministic: package specs, pasted MCP config, OpenAPI, npm and PyPI documents), `parameter_sources.py` (bounded readers), `parameter_classifier.py` (the proposal schema and the one model call), `parameter_detect.py` (orchestrator the route calls), `readme_servers.py` (the installer's README fallback).
- **Sources.** npm and PyPI: ONE metadata document from the fixed hosts `registry.npmjs.org` / `pypi.org`, over HTTPS to the address just vetted by `net_guard` (certificate checked against the name), no redirect followed, 2 MB, 10 s, no credential sent; only an exact version or an npm dist-tag is accepted (a range is `version_range_unsupported`, because resolving it would need the release list). No tarball, wheel or sdist is downloaded, no archive opened, no `npm`/`pip` run. OpenAPI: JSON only, from a public https address the person gave (the `url_check` and `net_guard` rules of slice 3; anything private, local or plain http is `source_blocked` with a pointer to pasting the text), `$ref` followed only inside the same document, external references counted and never fetched, operations capped at 50 and said so. Documentation page (optional `docs_url`, remote MCP and API kinds only): the guarded signed-out pane of slice 3; a failed read is a problem entry, with no curl/requests/archive path. A typed remote MCP or API base address is never contacted.
- **Model.** Only README, documentation-page and pasted PROSE reaches a model, only through `agent_runtime.run_text_transform` after the certified tool-free authorization; registry and OpenAPI data go to the deterministic parsers alone and their description fields are never shown. A pasted configuration the parsers settle skips the model. The reply must be EXACTLY `{version, outcome, alternatives[<=4]}` with a fixed set of keys per alternative: an extra field (`approved`, `adapter_id`, a credential `value`), a duplicate key, a wrong type, a fifth alternative, `none` with alternatives or `found` with none fails the whole answer; an alternative whose command, argument, URL, credential name or scope is not found character for character in the evidence text for the evidence id it cites, or that holds a hidden character, a credential-like literal or a URL with a user name, is dropped on its own and counted. Confidence is assigned in code. Certified denial unavailable, a timeout or an invalid answer returns the deterministic findings and a `problems` entry; no retry, no other provider, no `claude -p`.
- **Proposal.** `{ok, schema: "desk-connect-proposal/1", kind, status: complete|incomplete|none, warning, evidence[], alternatives[], problems[], notes, classifier, approved: false}`. Evidence is described (kind, label, source, size, whether the reader saw it all), never echoed. Each alternative carries `route_type`, `transport`, `fields` (command, args, url, auth_type, authorization/token/issuer URLs, each `{value, provenance, evidence_id, confidence}`), `credentials` (names and placement only), `scopes`, `purposes`, `operations`, an optional `package` record (registry facts: resolved version, install-script names, source-build flag, claimed integrity, `bytes_fetched: false`), `missing`, `risk_flags`, `editable: true`, `incomplete`, `approved: false`. Values are copied, never completed: a command is only inferred from `bin` when the package has exactly one, an auth type is `unknown` unless the evidence states it. Duplicates across sources collapse into the first (typed or deterministic) alternative with the later one's credential names, scopes and auth type folded in under their own provenance. `risk_flags` (`runs_arbitrary_code`, `unpinned_package`, `unencrypted_connection`, `local_or_private_target`, `remote_server_can_change`, `install_scripts`, `source_build_required`, `credential_like_text_not_copied`, `detected_from_untrusted_evidence`) are computed in code from the final values.
- **README fallback.** `mcp_installer._extract_via_claude` no longer runs `claude -p` with tools on the README; it calls `readme_servers.servers_from_readme`, which uses the same classifier. Credential names come back as EMPTY env or header entries so `detect_secrets` asks for them. When certified denial is unavailable it returns nothing.
- **Tests.** `tests/test_desk_connect_parameters.py`, 148 tests, fixtures only (a poisoned `Popen`, `os.system` and `socket.create_connection` fail any test that starts a process or opens a connection): schema and risk labels, parsers (config, OpenAPI, npm, PyPI, bounds), classifier shape and grounding, source readers (redirect, size, deadline, private address), end-to-end npm/PyPI/remote/OpenAPI/paste/docs-page, hostile README/registry/OpenAPI, certified denial unavailable, the removed installer fallback (fails against the previous `mcp_installer.py`), the route, and an AST check that the new modules import no process, network-client, archive, MCP-config, vault or activation code.
- **Not in this slice.** UI, approval, activation, npm/PyPI dependency inspection (no archive is read), YAML OpenAPI (JSON only), version ranges, `mcp_installer.security_scan` (still `claude -p` on cloned source; reported separately).
