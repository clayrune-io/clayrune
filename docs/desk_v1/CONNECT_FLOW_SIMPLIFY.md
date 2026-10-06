# MC-1062: a simpler Connect flow

2026-10-06 · Kestrel · Design proposal only · Backlog `c4d2e802`

**Recommendation:** one address, one connection type, that type's setup, then a separate Permissions step. Retain the existing human authorization boundaries. No implementation is approved by this document; Dave selects the two open product choices in section 8 before build starts.

Source baseline: `a1228a0ec220` on the assigned worktree. This proposal changes no running behavior. Inspection sequence: read the requested specifications and modules; inspect both screenshots; trace selection, saving, sign-in and permission consumers; specify screens; split build tickets; validate references and commit locally. All artifacts stay in this worktree.

## 1. Findings: why the current flow is confusing

The supplied X screenshot (`data/uploads/agent_026e07956a.png`) shows a routes-by-purpose editor before the method list, an unavailable MCP row, a fallback record, and two independent MCP forms. The LinkedIn screenshot (`data/uploads/agent_730089b90b.png`) repeats route descriptions, cost, coverage and evidence for Publish and Read. Both screenshots were opened and inspected; they are input evidence, not mockups of this proposal.

The implementation confirms that these are several independently saving flows on one screen:

| Finding | Source at the baseline | Consequence |
|---|---|---|
| Method embeds Purpose, discovery, ordinary methods, custom npm and remote MCP | `static/js/desk-v1-connect-flow.js:164` (`_methodHTML`) | Picking a connection competes with permissions, research and three Save paths. |
| The original service list remains below the URL form | `static/js/desk-v1-add-service.js:98` (`panelHTML`) | Two entrances express the same task differently. |
| Route cards render every capability, including unsupported ones | `static/js/desk-v1-connect-purpose.js:186` (`_routeHTML`) | The user must interpret an integration matrix before signing in. |
| Account tile status follows publishing readiness | `static/js/desk-v1-connections.js:65` (`_status`) | A useful browser read connection can still say Not connected. |

### The missing X username/password choice is a wiring gap

1. `mc/desk_connect/profiles/x.json:354` defines `x-browser`, with the sign-in URL and allowed host, but no `connect_method`. Only `x-oauth` has `connect_method: oauth` (`:222`).
2. `profile_compat.project_service()` (`mc/desk_connect/profile_compat.py:131`) omits routes without that field. `methods.is_connectable()` (`mc/desk_connect/methods.py:22`) also requires an available method and a provider. `providers/x.py` supports OAuth only and requires an X app Client ID. Adding a label alone cannot create password-only setup.
3. `desk-v1-connect-signin.js:165` attaches login controls to a selected provider method. At `:173` it matches `connect_method`, so choosing OAuth exposes a saved password as help completing OAuth; it does not remove the app requirement.
4. The alternative browser login controls live inside the purpose editor, after selecting a capability. `purpose_bindings.py:52` allows that editor to create **LinkedIn accounts only**. A new X user with no account therefore sees “Add it first” and cannot reach the browser route there.

The existing fill engine is reusable: `signin_fill.py` accepts `x-browser` and `linkedin-browser`, checks declared HTTPS hosts and form destinations, uses isolated-world typing, and hands CAPTCHA/2FA to the human. What is missing is the first-class browser setup path and an X account creation path that requires no developer app.

### Do not mistake a route binding for a permission

`purpose_bindings.py:328` synchronizes a route choice to legacy `read_via`; removing the last binding does not disable the legacy reader. `desk_publish.py` and `desk_engagement.py` do not consume the capability binding map as a deny policy. A UI that merely renames those checkboxes “Permissions” would be false.

There is a separate, real browser-agent permission: `PUT /api/browser/profiles/<name>/agent-read` checks the human passcode and stores allowed domains; `POST /api/browser/read-digest` enforces them and returns a laundered answer. LinkedIn digest reading exists even though `route_readiness.EXECUTORS` still has only X entries and the LinkedIn engagement reader is gap-only. Preserve this distinction: **an agent can answer a question about a page; an automatic LinkedIn engagement feed is not thereby implemented.**

The newer arbitrary-MCP position and U2 implementation supersede the older “curated only” passages in `DESK_CONNECT_BY_URL_SPEC.md`. npm and remote HTTP/SSE approval already exist. U1 parameter detection supports API/PyPI descriptions, but detection is not execution; this baseline has no generic API executor or PyPI installer. None of these gaps should be hidden by optimistic wording.

## 2. Screen rules

The following is the recommended design, pending section 8. Titles, copy and controls below are the user-facing specification; implementation commentary is not UI copy.

- One view at a time: **Service → Connection → Setup → Permissions → Review → Result**. Account selection and authentication are Setup substeps, not parallel panels.
- At most **four visible alternatives** and **25 words of explanatory copy** per screen. Navigation labels, field labels and entered data are not explanation. No extra hint paragraphs. Long account lists use a search picker showing at most three matches plus New account (four matches where no New action appears), with paged results when needed.
- One `Details` disclosure per view. It contains evidence, scope identifiers, optional naming, unsupported alternatives and advanced configuration. No nested disclosures. Safety facts necessary for approval become their own short review pages, never hidden consent.
- Back preserves non-secret selections and the existing form DOM; Close clears typed secrets. A change of service/type/account drops incompatible selections, held sign-ins and approvals. It never carries one account's credentials to another.
- No permission grants from choosing a connection, successful sign-in, a check, or clicking Continue. New grants are off until explicitly chosen; existing grants are shown faithfully and retained unless changed. No automatic paid route selection or fallback.
- One primary action and Back. Desktop uses the existing Connections panel; phone uses one full-width view with actions pinned above the keyboard/safe area. A compact “Setup · 2 of 3” replaces a six-item step strip on phone.

These recommendations follow the supplied design skill's Layout guidance (group related controls; progressively disclose secondary information), Entering data (ask only for necessary input), Accessibility (keyboard access, larger text, labelled controls), and Color/Typography guidance (legible text and status words rather than color alone). Reuse Desk tokens; no visual rebrand. Verify 390px and 1440px, 200% text, visible focus, labelled errors, 44px touch targets and contrast in the supported themes. These are acceptance requirements, not measurements of the supplied screenshots.

## 3. Known service: x.com

Every Setup branch returns to Permissions. A browser login and an API connection can later coexist on one account; adding a second type must update that account, not create a duplicate or change its existing read route.

| Screen | Title | Explanation (entire visible copy) | Controls / next |
|---|---|---|---|
| X1 | Add service | “Enter a service name or web address.” | One input; at most four name suggestions; Continue. `x.com` resolves locally to X. |
| X2 | Connect X | “Choose a connection type.” | Three radio rows: **Sign in (username/password)**; **API / developer app**; **MCP server**. Continue. Details contains the reference-only fallback and unavailable documented methods. |
| X3 | Which X account? | “Choose an account or add another.” | Existing-account search picker, or New account. New account asks Handle; optional display label is in Details. |
| X4a, Sign in | Sign in to X | “Use a saved login, enter a new one, or sign in yourself.” | Three alternatives: Saved login; Username and password; In browser. Only the chosen form appears. |
| X5a, saved | Saved X login | “Your password stays in Secrets. Finish any security check in the browser.” | Matching-login picker; named-browser-profile picker; Open sign-in; Sign in. Fill retains its own passcode prompt. |
| X5b, new | Your X login | “Stored when you save. You can finish signing in afterwards.” | Username and password fields; Continue. Suggested vault name in Details, editable on collision. No early vault write. |
| X5c, manual | Sign in yourself | “Sign in in the browser pane. Clayrune keeps the browser session.” | Use an existing profile or a dedicated new profile; Open sign-in; Continue. Never silently adopt another account's profile. |
| X4b, API | X developer app | “Use an app you own. API use may cost money.” | Client ID, optional Client Secret; existing stored entries need not be retyped. Continue. Details contains callback address and app instructions. |
| X5d, API | Authorize X | “Sign in and approve your app in X.” | Open sign-in; optional saved-login help; Continue. Existing `start-held` passcode, token expiry, cancel and final claim behavior remain. |
| X4c, MCP | Add an MCP server | “Use a package or a remote server address.” | Two choices: Package; Server address. Continue to the selected shared MCP subflow in section 5. |
| X6 | Permissions for X | “Choose what agents may do through this connection. Posting still needs campaign approval.” | Supported **Read** / **Post** choices only. For browser sign-in: Read; no Post switch. For API: Post; API Read is an explicit paid choice. Details exposes exact coverage and existing advanced bindings. |
| X7 | Review X | “Save these connection details. Sign-in and permission changes keep their own passcode checks.” | Service/account/type facts; Save. No password, token or passcode echoed. Additional permission authorization pages appear only for changes needing a separate existing gate. |
| X8 | X saved | “Your connection is saved. Its status is shown below.” | Actual status; Finish sign-in if needed; Check if a free supported check exists; Done. A newly stored password uses the existing separately gated fill here. |

**Coverage, not implied scope:** Read means the selected account's supported own reads or the explicitly named browser-site digest permission. On the browser permission page, show the site and selected profile as facts and use the copy “Agents can read pages on x.com through this browser sign-in. They cannot click or post.” That grant covers the site, not only the typed handle. It must never be described as account-isolated access. Shared-profile effects are disclosed before approval.

For API Read, use “API reads may cost money. Choose this explicitly; a failed browser read never switches to the API.” Preserve the browser default on an account that already has it. Choosing the API connection for posting must not silently switch reads to the API.

`Post` grants only supported post operations, not Reply, media upload, Article or broad listening by implication. Existing detailed route bindings remain editable under Details; absent/unknown coverage stays absent. If a capability changes cost or authority, it gets an explicit later permission choice rather than being folded silently into Read/Post.

## 4. Known service: linkedin.com

LinkedIn uses the same connection types and shell. The person signing in and the destination being managed are separate identities. A Company Page uses a member's login; it never asks for a fictional Page password.

| Screen | Title | Explanation (entire visible copy) | Controls / next |
|---|---|---|---|
| L1 | Add service | “Enter a service name or web address.” | Input `linkedin.com`; Continue. |
| L2 | Connect LinkedIn | “Choose a connection type.” | **Sign in (username/password)**; **MCP server**. Continue. Built-in API setup is unavailable in this baseline; its explanation and supplied-API entry are under Details, not disabled route cards. |
| L3 | Which LinkedIn account? | “Choose what you want to manage.” | **Personal profile**; **Company Page**. Continue. No destination is created from recognition alone. |
| L4a | Personal profile | “Choose an existing profile or name a new one.” | Existing-account picker or New profile; profile name/address. Continue. No automatic addition to a campaign's Where board. |
| L4b | Company Page | “Choose the Page. You sign in with a member account that can access it.” | Existing Page picker or New Page; Page name/address. Optional organization ID is an advanced identity field, never inferred from the member. |
| L5 | Sign in to LinkedIn | “Use a saved login, enter a new one, or sign in yourself.” | Same three sign-in substeps as X, bound to `linkedin-browser` and its declared host. Username/password belong to the member in both cases. |
| L6 | Permissions for LinkedIn | “Allow agents to read through this sign-in. Posting is not enabled by signing in.” | **Read** only for browser setup, with named site/profile facts. For Page and member alike, disclose site-wide digest scope before approval. Details explains the missing engagement feed and posting routes. |
| L7 | Review LinkedIn | “Save these connection details. Sign-in and permission changes keep their own passcode checks.” | Account kind, destination, login reference and browser profile; Save, then separately gated permissions where needed. |
| L8 | LinkedIn saved | “Your connection is saved. Its status is shown below.” | Finish sign-in / supported read check / Done. Distinguish browser answers from automatic feed coverage. |

**API branch, when explicitly requested through Details:** title “LinkedIn API”; copy “Built-in API setup is unavailable. You can save API details; this does not enable posting.” Controls: API address/specification, credential reference, Save details via the reference-only branch, Back. This is not an executable integration. U3 must supply the executor before it can become a normal API connection choice. The same restriction applies to a personally supplied API; the catalogue is not an approval gate.

**MCP branch:** after L3/L4, use the shared package/remote setup. Registration does not prove it can read this member or publish to this Page. Whole-server approval versus granular permission presentation is decision Q2; neither may change LinkedIn's publisher gate.

`LINKEDIN_ORG_POSTING_APPROVED=False` remains untouched. Personal profile selection does not enable a personal publishing adapter. Existing human share/copy routes remain on the post's delivery action, with the human clicking Post; they are not authentication methods. An unavailable connector must not remove those manual routes.

## 5. Unknown URL and shared setup branches

An unknown hostname is not a failure and is never silently substituted with a similarly named service. Recognition is local; network discovery remains a separate user action. No guessed login endpoint or automatic password fill is offered for an unreviewed domain.

| Screen | Title | Explanation (entire visible copy) | Controls / next |
|---|---|---|---|
| U1 | Add service | “Enter a service name or web address.” | Input; Continue. An unknown name asks for its address without searching for a guessed domain. |
| U2 | Connect this service | “This service is new to Clayrune. Choose how to add it.” | Four actions: **Find connection options**; **MCP server**; **API details**; **Save a reference**. API details is visibly marked setup-only until U3 exists. |
| U3a | Find connection options | “Look up public connection information. No sign-in or credential is used.” | Look it up; Back. Running state has Cancel and status, no other forms. |
| U4a | Connection options | “Found information is a suggestion. Choose a setup path you can review.” | At most four deduplicated actionable setup types. Findings without an executable adapter appear under Details; always retain Back and Save a reference. Explicitly label incomplete discovery. |
| U3b | Add an MCP server | “Use a package or a remote server address.” | Package; Server address. Only the selected branch appears. |
| U3c | API details | “Save API information for later. This version cannot run a new API connection.” | API address or OpenAPI input; Detect parameters; Continue. Editable detection results, then reference-only save. No Connected/Verified state. |
| U3d | Save a reference | “Save the address and optional credential for agents. This does not connect the service.” | Name, optional existing credential reference or new credential form; Continue. Show one credential kind/form at a time. |
| U5, executable MCP | Permissions | “Choose who can use this server. Its tools may read or change data.” | One project / All projects; project picker when needed. Q2 determines whether whole-server consent or real granular tool permissions follow. |
| U5, reference/API details | Permissions | “Saving information grants no connection permissions. Credential access follows its existing vault policy.” | No Read/Post switches. If storing a credential, show its existing unattended-use control here; keep the saved record explicitly reference-only. |
| U6 | Review service | “Review what will be saved before entering your passcode.” | Selected branch's review facts and approvals; Save. |
| U7 | Service saved | “Your saved service and its status are shown below.” | Exact result: Saved reference / Registered, not checked / Saved, setup failed / Saved, cannot run yet; Check only when supported; Done. |

### Shared MCP substeps (both known and unknown services)

Each row below is a separate view, replacing simultaneous npm and remote sections. Advanced settings remain reachable, not removed.

| Screen | Title | Explanation (entire visible copy) | Controls / facts |
|---|---|---|---|
| M1p | MCP package | “Enter the package you want to use.” | Package identifier; Detect parameters; Continue. Detected values remain editable and unapproved. npm works now; PyPI detection is not a runnable installer. |
| M2p | Package details | “Review the detected settings and name any credentials in Secrets.” | Entry point, arguments, credential-reference editor. Optional server name and provenance under Details. |
| M1r | MCP server address | “Enter the server address. Nothing contacts it before approval.” | URL; proposed protocol; Continue. Protocol override in Details. |
| M2r | Server sign-in | “Choose how this server authenticates.” | None; Token from Secrets; OAuth, marked “Save only”. Only the chosen fields follow on the next view. |
| M3r | Server credentials | “Credentials go only to the recipient you approve.” | Header and prefix plus vault reference, or issuer and scopes for OAuth; Continue. No credential values in MCP config. |
| M4 | Permissions | “Choose who can use this server. Its tools may read or change data.” | Project/global reach; Q2 permission contract; Continue. This is after authentication setup, never on type selection. |
| M5p | Review package | “This runs local code. Review the exact command and package before approving.” | Exact command, version/hash, source and local reach as facts. Details holds publisher/license/size/dependency inventory. Continue. |
| M6p, if scripts | Install steps | “Approved install steps can fetch or write files that are not pinned.” | Up to four script choices per page, each full body visible before selection; Continue through remaining pages. Never bulk-approve unseen scripts. |
| M5r | Review server | “This remote server can change. Review its address and credential recipient before approving.” | Exact URL/origin, protocol, credential names/recipient and reach as facts. Details holds launch line/issuer/scopes and remaining evidence. |
| M6r, if exposures | Connection risks | “Approve each additional exposure to continue.” | Existing unencrypted/private-network acknowledgements, unchanged; each causes re-review. At most four; never default checked. |
| M7 | Approve connection | “Save exactly what you reviewed. Changes require a new approval.” | Existing approval checkbox, Save and dashboard passcode. Request uses the unchanged server-held fingerprint, never a client command. |

Review pagination changes presentation only: the immutable card remains complete and the final approval covers its exact contents. Show material warnings on the page they concern. Any edit invalidates all acknowledgements affected by the changed fingerprint. A held OAuth token, mutable remote server, unreviewed code, paid use or broad reach is never concealed to meet the word limit.

For stored credentials used on reference-only services, preserve the current ability to name or store them and the existing vault policy. Do not claim that a new checkbox prevents every agent from using a separately accessible vault secret. Reference-only status is not a permission sandbox.

## 6. Remove, merge and move

| Existing UI | Recommended treatment |
|---|---|
| Old service/account/engine list below URL input | Remove the duplicate entrance. Retain name suggestions; existing engine adapters remain reachable by recognition. Blog/manual reference use remains available through the fallback. |
| “Choose how each purpose is done” on Method | Remove from Connection. Route selection becomes internal setup data; supported capability choices appear later in Permissions. Preserve mixed/custom bindings under that view's one Details disclosure. |
| Publish/Read route-card walls | Replace with connection-type rows, then one selected setup. Retain route IDs, evidence, costs and coverage in the data model. |
| “Information only” / “Cannot be chosen here” rows | Q1 recommendation: hide from the primary picker; retain a short explanation under Details when useful. Never style an unavailable route like an enabled option. |
| Blanket “MCP is coming” and “no way to connect” text | Remove. Derive messages from the actual adapter and variant; arbitrary npm and remote registration already exist. |
| Two “Add your own MCP” buttons | Merge into MCP server, then Package or Server address. Keep both approval paths. |
| Repeated cost/evidence/status rows | Evidence and technical requirements go to Details. A material charge remains visible beside the relevant API choice/permission. Unknown cost never becomes free. |
| Save routes beside Continue and custom Save | Each branch has one active final action at a time. Keep separate existing passcode operations explicit; no pretend atomic Save across independent endpoints. |
| Raw profile/vault naming for everyone | Use named pickers and derived defaults. Editable identifiers and conflict repair live in Details. Do not guess which saved identity is intended. |
| Read via control in account detail | Move its editing into the same setup/permission editor; show a concise summary in the tile detail. Keep existing values and no-fallback behavior. |
| Approved-server list inside a new package form | Move to Connections management/result, including remote records. Keep drift, re-review and failed-setup recovery reachable. |
| Generic “Connected” toast | Replace with server-derived, per-connection status. Keep Saved reference, Saved/needs sign-in, Registered and Verified distinct. |
| Human share-intent/manual delivery routes | Retain in per-post delivery, not Connect. No browser-driven agent posting is introduced. |

## 7. Backend mapping and build contract

No registry flag is promoted to executable just to expose a button. The new type projection joins setup adapters, profile routes and installed capabilities; it must not use the legacy method projection alone.

| New step | Existing seam | Reuse / necessary work |
|---|---|---|
| Service | `GET /api/desk/connect/suggest`, `POST .../inspect`; `resolve.py`, `registry.py`, `methods.py` | Keep exact-host recognition and name matching. Cap suggestions in the view. |
| Connection types | `profile_loader.py`, `profile_compat.py`, `route_readiness.py`, `providers/__init__.py` | New read-only projection by type and variant. Distinguish setup support, runtime support, provider restrictions and evidence. |
| Browser setup | `signin/options`, `signin/fill`, `signin/store-login`; `signin_fill*.py`, `signin_login_store.py`; named browser profiles | New account/profile setup bridge for X and LinkedIn, no app required. Persist credential reference separately from permission bindings, so zero permissions is valid. Do not use `start-held` for cookie-only login: that route is OAuth. |
| API/provider setup | `providers/x.py`, `providers/higgsfield.py`, key providers; `provider_commit.py`; `POST .../commit` | Reuse provider fields and compensating rollback. X OAuth still requires the app. Retain generation and curated Notion adapters; no shared monolith growth. |
| Held OAuth | `POST .../<service>/start-held`, `GET .../flows/<id>`, `POST .../flows/<id>/cancel`; `desk_oauth_hold.py` | Preserve passcode start, expiring server-held token, claim, cancel/revoke and changed-app invalidation. |
| Permissions: Desk | `purpose/commit`, `purpose_bindings.py`, campaign/publisher gates | Binding data is reusable, but a deny policy is missing. Add a separately tested policy checked before read/publish execution; do not infer denial from missing legacy bindings. Existing accounts retain existing behavior until the human edits permission. |
| Permissions: browser agent | `GET /api/browser/agent-read`, `PUT /api/browser/profiles/<name>/agent-read`; `browser_agent_read.py` | Reuse the exact profile/site policy and passcode gate. Merge/remove only the chosen domain without overwriting other sites. Warn that other connections using this profile/site share this grant. |
| Permissions: MCP | `custom_connection_operation.py`, `custom_connection_guard.py`, launch wrappers | Existing approval covers exact code/endpoint, credential recipients and project/global reach, not a general Read/Post filter. Q2 must be resolved before those switches are designed for custom servers. |
| Package setup/review | `POST .../detect`, `POST .../custom/review`, `POST .../custom/commit` | Keep npm closure, script approval, launch gate, vault refs, stored fingerprint and reapproval rules. Detection never approves. |
| Remote setup/review | `POST .../custom/remote/review`, shared custom commit | Keep target/credential-recipient approval, exposure acknowledgement, redirect refusal and mutable-server warning. OAuth remains pending runtime. |
| Unknown lookup | `POST .../discover`, `.../discover/cancel`; `POST .../detect` | Retain explicit opt-in, bounded lookup, network confinement, toolless transform and no downgrade fallback. A finding may prefill an editable draft; never activate it. |
| Fallback | `POST .../commit` with `save_for_agents`; `mc/desk_services.py` | Preserve optional credential, create-only semantics, rollback and `publish:false`. Generic API/PyPI setup-only records require a clearly separate draft representation, not a falsely registered connection. |
| Result/check | `POST .../verify`, `.../purpose/verify`, `.../custom/remote/check`, `.../custom/remote/adopt` | Check only on human request. Handshake proves reachability, not purpose; paid X reads and publishing are not verification probes. Adoption keeps its passcode. |

### Permission semantics that must be implemented, not merely illustrated

- **Desk permission:** new Read/Post consent must gate the actual Desk executor before a network call or spend. Post remains an additional restriction on top of campaign approval, budget and LinkedIn's existing closed gate. Unchecking Read must actually stop the covered reader; an empty route map is insufficient.
- **Browser permission:** Read controls the existing domain grant on the selected profile. It does not enable raw page access, click/type, automatic login or posting. Preserve one-time approvals and their existing scope. Signing out or deleting a profile is not a permission toggle.
- **MCP permission:** Q2 is unresolved. Under the recommended whole-server option, the screen says “Use this server's tools”, shows reach and risks, and has no fake granular Read/Post switches. General local code cannot be made read-only by filtering the names of its tools.
- **Credential permission:** preserve vault scope and `allow_unattended` exactly. They govern credential use, not all possible actions. Password fill always remains human-started even when the credential permits unattended use elsewhere.
- **Existing configuration:** never migrate an absent permission into Deny or Allow by guesswork. Show legacy behavior as existing; first explicit edit records the new consent. Keep advanced/split bindings intact and report their current runtime limits rather than dropping them on Save.

### Authorization and failure sequencing

“One screen at a time” does not mean “one reusable authorization.” Existing provider save, purpose save, profile permission update, held sign-in start, password fill, MCP save, changed-server adoption and vault unlock keep their current independent gates. No passcode is cached or replayed across endpoints. A new operation uses the same human-only/passcode discipline; there is no ungated account/secret shortcut.

The proposed shell gathers connection details first, then displays Permissions as a distinct step. It records no permission on selection. At Review it performs the selected branch's existing connection save. If the branch needs a separate existing permission write, a subsequent authorization page reads **“Allow reading”** / **“Apply permissions”**, names the exact scope, and invokes that endpoint's own passcode check. Do not promise one passcode for the whole journey. A new secret typed in Setup is stored only with the applicable save; filling it afterwards still has its own prompt.

Failure after connection save says **“Connection saved; permissions unchanged.”** Retry only the failed operation, using its original request identity where supported. If profile creation/sign-in is still needed before its allowlist can be saved, show **“Finish sign-in to apply Read permission.”** No green completion while any requested permission is pending. Cancelling a browser sign-in does not claim cookies were rolled back or delete an existing profile. Cancelling a held OAuth flow retains its existing revoke/discard behavior.

New account setup may save with no permissions. The current purpose API requires non-empty bindings, so it must not be misused as a connection-only store. A dedicated setup module is required. API scope consent may be broader than the user's Desk permission: show this in review facts and retain the narrower execution restriction. No new login or grant is performed by this design task.

### Build order: independent ticket files

Tickets are specifications, not dispatches or backlog mutations. Each owns one concern in its own new module; existing large files receive imports/dispatch calls only. No ticket may implement a disputed option before Dave records Q1/Q2.

| Order | Ticket | Dependency |
|---|---|---|
| 01 | [Connection-type projection](connect_flow_tickets/01-type-projection.md) | Q1 |
| 02 | [Single-view wizard shell](connect_flow_tickets/02-wizard-shell.md) | 01 |
| 03 | [Browser connection setup](connect_flow_tickets/03-browser-setup.md) | 01 |
| 04 | [Sign-in setup screen](connect_flow_tickets/04-signin-screen.md) | 02, 03 |
| 05 | [Permission policy contract](connect_flow_tickets/05-permission-policy.md) | Dave confirms Q2 scope |
| 06 | [Desk execution enforcement](connect_flow_tickets/06-desk-enforcement.md) | 05 |
| 07 | [Browser permission adapter](connect_flow_tickets/07-browser-permission.md) | 03, 05 |
| 08 | [Permissions screen](connect_flow_tickets/08-permissions-screen.md) | 02, 06, 07 |
| 09 | [Provider API setup screen](connect_flow_tickets/09-api-screen.md) | 02, 08 |
| 10 | [Package MCP setup screen](connect_flow_tickets/10-package-screen.md) | 02, 08, Q2 |
| 11 | [Remote MCP setup screen](connect_flow_tickets/11-remote-screen.md) | 02, 08, Q2 |
| 12 | [Unknown service and detection](connect_flow_tickets/12-unknown-service.md) | 09–11 |
| 13a | [Review and result](connect_flow_tickets/13a-review-result.md) | 04, 08–12 |
| 13b | [Connection management status](connect_flow_tickets/13b-management-status.md) | 13a |
| 14 | [Entrypoint integration and acceptance](connect_flow_tickets/14-integration.md) | 01–13b |

If Q2 selects granular custom-server permissions, create a separate execution-control design and smaller implementation tickets before enabling that presentation. It is not a frontend checkbox task and must not be folded into ticket 08.

## 8. Open product choices for Dave

These are recommendations only. User-mandated type-first ordering, later Permissions, arbitrary service support and unchanged human gates are already constraints, not questions to reopen.

| ID | Options | Recommendation | Relative cost / tradeoff |
|---|---|---|---|
| Q1: unavailable methods | A: hide them from the primary picker, retain explanation/setup-only paths in Details. B: show disabled methods alongside working ones. | **A.** Keep choices actionable; preserve discoverability in one disclosure. | Both small. A needs a concise empty-state/Details index; B is cheaper to render but spends the four-choice budget on dead ends. Reversible display change. |
| Q2: custom MCP Permissions | A: whole-server approval with project/global reach; Read/Post controls only on routes with actual enforcement. B: require genuinely enforceable granular Read/Post for arbitrary MCP before releasing its redesigned flow. | **A.** Reuse the actual approval contract and say exactly what is granted; no false restriction. | A is small UI work on existing approval. B is a large security/runtime project, especially for local code with arbitrary network/file access; tool-name filtering alone cannot meet it. Approval never changes campaign/LinkedIn gates. |

## 9. Validation and limits

The source investigation and the supplied screenshots support the structural diagnosis. Local source checks confirm the projection exclusion, X-only OAuth provider, LinkedIn-only new purpose account, absence of binding-map checks in the inspected execution modules, and separate browser-domain enforcement. These findings describe this commit, not every future server version.

This deliverable is a Markdown design and ticket set, not a runnable prototype. No real sign-in, credential use, paid request, publish, install, server restart or permission change was performed. No frontend behavior is claimed tested. Build acceptance must include the exact fresh-X/no-app path, both LinkedIn kinds, unknown-service failure/cancellation, zero-permission save, permission-denied execution, shared-profile effects, Back/change cleanup, every existing passcode refusal, and honest partial-success states. Run the affected smokes after each merge, using the main checkout's existing browser dependencies.

Documentation verification: all 18 relative links resolve; 46 tabulated screen-copy entries contain at most 14 words each; all 15 ticket files include dependencies, ownership, work, acceptance and scope. Source assertions confirmed the X route/projection and new-account restrictions; path/line references were checked. This is evidence for the proposal's traceability, not a runtime test result.

Related source specifications: [connect by URL](../DESK_CONNECT_BY_URL_SPEC.md), [Desk IA revision 2, section 11.8](../THE_DESK_V1_IA_REVISION_2.md), [service profiles and U1/U2 state](../DESK_SERVICE_PROFILES_SPEC.md). Older as-built passages in these documents are history; this proposal does not silently amend their shipped status. After implementation, ticket 14 updates those passages and the User Guide.

## 10. Decided (Dave, 2026-10-06, without Ron; Ron may overrule)

- **Q1 = A.** Unavailable methods are hidden from the primary picker; their explanation and setup-only paths live under Details. Why: Ron's complaint was too many choices, and a disabled row is a choice that leads nowhere. Reversing it is a display change in ticket 01/02.
- **Q2 = A.** Custom MCP servers get truthful whole-server approval with project or global reach; Read/Post controls appear only on routes that actually enforce them. Why: granular enforcement on arbitrary MCP code is a separate security project, and a checkbox that restricts nothing is worse than none. Reversing it means the execution-control design described in section 7 before ticket 08 changes.
