# Desk: connect by URL

2026-10-03 · Implementation proposal; open decisions require Dave's selection.

## Contract and limits

Connections serves people who know their service's URL but not its integration terminology. Add service starts with that URL, explains available methods, and stores credentials inside the same flow. One final Save requests the dashboard passcode once. Before Save, configuration exists only as an unsaved browser draft.

Recognition does not prove integration support. Current source supports X/Higgsfield sign-in and engine key guides; LinkedIn has restricted guidance. YouTube, Google Drive/Photos are not working Desk connectors. The registry must distinguish recognized services from executable adapters.

Out of scope: generating connectors, arbitrary shell instructions, automatic publishing, paid verification calls, private-network discovery, and changes to existing human gates. Account-reading preferences remain per account; browser reading remains the default.

## Screen flow

1. **URL.** The existing last Add service tile opens a numbered flow: URL → Method → Details → Review. Accept a public HTTPS service/account URL; display the normalized hostname. Account paths suggest identity, never prove ownership. Editing the URL invalidates detection and method selection.
2. **Method.** Known hosts answer immediately. Unknown hosts show cancellable progress. Each option shows evidence, support status, and maintained guidance: MCP for agent tools; API key for supported direct operations; OAuth for delegated authorization; browser sign-in for interactive access. Unsupported methods say “Information only.” No predefined account is created.
3. **Details.** Draft label, account identity, existing credential reference or inline vault fields, scope, unattended-use policy, and named browser profile. Reuse login username/password pairing. Passwords/keys live only in the open form, never local/session storage, persisted drafts, logs, telemetry, or model input. Back preserves the form; closing/reloading clears it. No vault write, service record, install request, profile creation, or OAuth start occurs yet.
4. **Review.** Show the exact method, permissions, credential metadata, and any MC-1022 install card: source, pinned version/hash, licence, size, purpose. Selecting it records draft consent only. Final **Save** includes install approval where applicable, then requests the dashboard passcode once. Wrong passcode preserves the draft and permits retry within the existing shared rate limit; never retain the passcode.
5. **Result.** After accepted Save, show setup progress in the same panel. Open required provider consent/sign-in in the attached named browser pane, then return here. OAuth tokens reach the vault only through the existing human-started callback. Browser cookies stay in the profile; an optional saved password uses the inline vault form. Show “Key stored, not verified”, “Sign-in required”, “Setup failed”, or “Saved for agents”. “Verified” requires a successful supported read-only probe or authenticated account check, with method, capability, identity and timestamp. An open profile or stored token alone is insufficient. Credential changes invalidate verification.

Desktop keeps the flow beneath the tile grid. Phone uses a full-width step view with Back and a bottom action above keyboard/safe-area insets; no nested settings navigation. Preserve focus across renders; announce progress/errors. At 390px there must be no horizontal scrolling or hidden action. A missing dashboard passcode uses existing host-only setup. If the vault is locked, collect its distinct passphrase in Review and unlock inside the final batch, under the same dashboard-passcode check; no earlier unlock request.

## Detection and trust

Proposed read-only `POST /api/desk/connect/discover` accepts the normalized URL, never credentials. Limit URLs to 300 characters; reject userinfo, queries/fragments, localhost, private/link-local addresses and Clayrune origins, explaining how to submit a clean URL. Enforce network restrictions before navigation, redirects and subrequests, including DNS rebinding. Matching uses explicit host aliases, never substrings.

Tier 1 is a versioned registry of known hosts, maintained evidence, guidance keys and adapters, with no model call. Tier 2 runs page reading and public MCP lookup concurrently: 15 seconds for a temporary unsigned-in browser pane and `/api/browser/read` envelope; 10 seconds for registry lookup. Discovery keeps no account/configuration records and removes its temporary resources.

The [official MCP registry](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/api/official-registry-api.md) searches server-name substrings, not service hosts. Query host/brand-derived terms; cap at three pages/100 results. Compare metadata and official-service links; a name match or registry presence is not endorsement. Mark bounded results incomplete when capped.

Only the isolated transform receives the untrusted envelope and registry metadata: use `run_text_transform` as in `mc/mail_launder.py`, with certified tool denial, no hooks/plugins/skills, and strict empty MCP. `--allowedTools ''` alone is insufficient. Cap input at 20,000 characters, output at four options, model time at 30 seconds, whole discovery at 60 seconds; no automatic retry.

Validate exact JSON: `{version:1, outcome, options:[{method, evidence_id, usage_key}]}`; outcome is `found|none|signin_wall|incomplete`. Methods are `mcp|api_key|oauth|browser_signin`; usage keys select reviewed one-line templates. Resolve evidence IDs to fetched URLs server-side; reject unknown fields/IDs. Never execute generated URLs/configuration, display generated instructions as Clayrune advice, or pass raw content into a tool-enabled agent. The result remains untrusted evidence, not authority.

Unreachable/non-HTML page, sign-in wall, registry timeout, model unavailable/timeout and invalid schema each show a specific failure. Preserve independently validated options with “Discovery incomplete”. No options offers “Save for agents” with the inline credential form. Retry is explicit. No curl/requests fallback after pane-read failure; the fixed registry API is a separate source, never a page-reader substitute.

## One save, one authorization

New `POST /api/desk/connect/commit` receives `{request_id, draft, passcode}`. The server validates the draft, revalidates selected evidence and pinned install details, rejects unattended callers, and calls `_require_human_passcode` exactly once. It invokes internal services, not separately gated HTTP endpoints. Approval applies only to this immutable operation; no session unlock or reusable authorization token. Unknown executable configuration is rejected. Conflicting existing secret names require explicit replacement consent on Review.

Local vault changes, account/service linkage and install approval must commit together. Existing independent file writes are insufficient: add a transaction coordinator, common locking and encrypted recovery journal outside the repository/DATA_DIR. Stage encrypted vault material and non-secret records; commit visibility together; roll back records and any batch-acquired vault unlock on failure. Startup recovery precedes reads/workers. Failed recovery blocks affected operations rather than exposing partial state. Never store plaintext credentials in staging or request logs.

Only after durable commit may bounded provisioning start. Read-only `GET /api/desk/connect/operations/<id>` reports redacted progress through an opaque identifier; duplicate request IDs cannot repeat writes or installs, and changed payloads return 409. A lost response is reconciled there. Clear credential fields after confirmed acceptance; retries before acceptance use the still-open form.

**Explicit partial-success boundary:** external OAuth grants, browser sessions and installer effects cannot be transactionally undone. Their failure retains the committed credential/reference and approval, marked “Saved; setup failed”, never Verified. This preserves an auditable, repairable state rather than claiming rollback. The approved operation may finish its exact install/sign-in without another dashboard prompt; expired, changed or newly retried operations require another final Save. Post-save callbacks/jobs cannot add permissions, change packages, or create unrelated credentials. Time out installs after five minutes, sign-in after ten; retain failure state, not a hanging spinner.

## Reuse and file ownership

Reuse `desk-v1-connections*.js` tiles, `desk-v1-guides.js` guides/tests, `mc/desk_oauth.py`, named profiles, `secrets_routes.py`/`mc/secrets_store.py`, and `mc/desk_services.py` fallback records in `data/desk.json`. Fallback remains `saved_for_agents`, `publish:false`.

New independent modules: `mc/desk_connect/{discovery,classifier,transaction,verification}.py`, `providers/<service>.py`, data-only registry, and `mc/blueprints/desk_connect_routes.py`. Frontend: `desk-v1-connect-flow.js`, extracted `secret-form.js` and `addon-approval-card.js`; existing callers share them. New concerns must not grow `desk_routes.py`, `secrets-panel.js` or `mc/mcp_installer.py`.

MC-1022 currently catalogues ffmpeg, not arbitrary MCP packages. MCP activation therefore requires a new approved-package adapter plus existing MCP registration. Current `/api/mcp/url/install` lacks the add-on gate, applies raw secrets to config, and preview can invoke unsafe README extraction. Do not route around these gaps: require pinned approved inputs, isolated extraction, server-side vault references and an unbypassable activation gate, including deferred `npx` execution. Remote MCP also requires explicit capability approval.

## Shippable slices and acceptance

1. Shared inline draft form + final batch save + fallback records. Inject failure at every local write and restart boundary; prove rollback, wrong-passcode retention, duplicate-save safety and no pre-save mutations.
2. Known-host adapters + verification. Exercise supported OAuth/key/browser paths, unavailable connectors, mobile keyboard layout and separate accounts; stored keys never imply Verified.
3. Unknown discovery. Test hostile instructions, fake evidence, redirects/rebinding, sign-in walls, deadlines and malformed output; prove no tool execution or writes.
4. MCP approval/activation adapter. Test missing/wrong passcode, changed pins, deferred launch and provisioning failure; prove one check per Save and no plaintext config. Earlier slices show MCP as information-only.

## Open decisions for Dave

- **Unknown-service scope:** recommend information plus fallback until a reviewed adapter exists; generating adapters increases review and credential-exfiltration risk.
- **MCP rollout:** recommend curated packages first; arbitrary packages require substantially broader installer hardening. Remote endpoints still need approval.
- **Multiple accounts:** recommend per-account vault/profile references; current singleton OAuth names need migration. One-account-only is cheaper but cannot fulfill arbitrary account URLs.
- **Partial provisioning:** recommend the explicit durable-failure boundary above; strict external all-or-nothing would exclude installs and sign-ins from this feature.
