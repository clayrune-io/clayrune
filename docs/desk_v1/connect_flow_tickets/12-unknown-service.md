# MC-1062 / 12: Give unknown URLs an honest short path

Status: built on the assigned branch; wizard remains disabled until ticket 14. [Parent design](../CONNECT_FLOW_SIMPLIFY.md). Backend prerequisite: [12b reference-draft contract](12b-reference-draft.md).

## Depends on

09, 10, 11.

## Ownership

New `static/js/desk-v1-connect-unknown-step.js`; `tools/smoke/desk-v1-connect-unknown-step.mjs`. Reuse discover.js, suggest.js and the existing detect endpoint.

Future workers are not alone in the repository: do not revert others' edits; keep wiring narrow and adapt to concurrent changes.

## Work

Offer four paths: Find connection options, MCP server, API details, Save a reference. Keep lookup explicit/cancellable and distinguish failed, empty and incomplete answers. Map executable variants to editable setup drafts; put unsupported evidence in Details. Preserve safe fallback with optional credential. API/PyPI detection output is only draft information until their independent executors exist.

## Acceptance

Unknown name requests an address; no domain guess. Service edit invalidates prior lookup; stale answers ignored. Hostile lookup data is escaped and never becomes instructions or approved settings. Existing discovery timeout/network/toolless/no-fallback tests remain. Generic API details save visibly as reference-only; no silent substitute, test request or spend.

## Boundary

Small discovery presentation slice. No U3 or PyPI executor implementation is bundled into this ticket.

## Implementation

`desk-v1-connect-unknown-step.js` owns U2, explicit/cancellable public discovery,
and the Package/Server-address chooser shared by known and unknown services.
Its single entry script imports the independent `desk-v1-connect-reference-step.js`
registration unit; both consume existing window bridges. Index wiring is one
stylesheet and one script. The generic Connection matcher dispatches unknown
projections (`service.id:null`, `recognised:false`) here, and exposes unavailable
API details under its one Details disclosure (including LinkedIn).

Findings are escaped text, never links, instructions or approved configuration.
Lookup reuses Discover's bounded human-only route and cancellation. Failed,
empty/sign-in-wall and incomplete outcomes stay distinct; actionable types are
deduplicated, unsupported evidence is paged four items at a time in Details, and
reference fallback remains. MCP findings lead to editable npm/remote Setup;
the existing package/remote Review and whole-server approval paths still own
installation/registration. No guessed login or domain is introduced.

The reference unit owns API/PyPI/reference Setup, Permissions, Review and Result.
Detection uses the existing U1 endpoint only after the person's click. Sources,
parameters and transport remain editable, edited values receive user-input
provenance, source changes invalidate prior suggestions, and late answers are
ignored. OpenAPI JSON remains only in kept DOM for detection; it is never saved.
PyPI source selection lives in Details; detected package metadata stays
reference-only and never reaches npm registration. Stopping a detection says
explicitly that it stops waiting locally (U1 has no cancellation route).

Reference Setup shows one credential form at a time: none, existing entry NAME,
or new credential. Password/token values and username/Key ID stay in kept DOM,
survive Back, and clear on credential kind/type/service change, successful Save
and Close. Existing credential policy is shown faithfully; new credential
unattended-use choice lives on Permissions. No Read/Post controls or connection
permission writes exist on this branch. Final Save uses 12b through the existing
human-proof modal, with unchanged request identity on cancel/refusal/retry and a
new identity after edits. Wrong passcodes keep the guard's real re-prompt.
Result separates saved reference, vault storage/reference, no connection
permission and not checked; it offers no probe or Connected/Verified state.

Service input edits invalidate the prior type response, branch, kept secrets and
lookup immediately. Discover cancellation drops request identity before awaiting
the cancel response, preventing late answers from becoming a success. Existing
lookup error boundaries and server confinement/toolless/no-fallback behavior
are unchanged. Cancellation failure is observable in the console.

## Validation and handoff

New UI smoke drives the real modules/type projection through mocked public
lookup, detection and Save endpoints at 1440x900 and 390x844, including 200%
text, one Details/form/primary, no horizontal scroll, reachable actions,
hostile evidence, cancellation/late answers, API/PyPI/reference saves, separate
passcode behavior, retry identity, credential policy and DOM cleanup. Backend
12b regression uses the real route/passcode/temp vault/store, not the fake UI
server. No live vendor lookup, credential use, installation or paid operation
was performed. UI cannot be claimed enabled or live.

Fixture exclusions: `desk-v1-connect-wizard.mjs` and
`desk-v1-connect-permissions-step.mjs` blank the new entry script with one-line
rules because their fixture Connection/Setup/reference-permission screens must
own those independent tests. Other existing smoke assertions remain intact.

Screenshots: `../screens/connect_unknown_step_{choices,lookup,api,result}_{1440,390}.png`.
User Guide/README activation copy belongs to ticket 14, as the wizard is off.
No new project rule, permission gate, route, provider or dependency was added.
Rollback: revert ticket 12 UI/wiring without discarding saved 12b user data.
Final full-suite summary lines are recorded after verification below.

Checkpoint: `1343 passed in 311.33s (0:05:11)`, exit 0, for the expanded
`tests/test_desk_connect*.py` selection plus `tests/test_desk_services.py`,
with `-o addopts=''` and no filters. Required smoke selection initially
reported `SMOKES: 23/23 passed; failures=[]`; after final detection/source
cleanup additions the new smoke was exercised again at both widths. The full
23-script selection is repeated against frozen frontend files before handoff.
Boot checkpoint: `PASS` for seven boot scenarios plus all listed guards.
Backend basic pyright: `0 errors, 0 warnings, 0 informations`.

Final frozen-file verification: **23/23 required smokes, all exit 0**. Exact
summary lines (full logs are local test scratch, not committed):

```text
desk-v1-connect-api-step: rc=0; All connect-api-step checks passed
desk-v1-connect-browser-permission: rc=0; ✅ PASS — browser Read permission editor.
desk-v1-connect-custom: rc=0; all checks passed
desk-v1-connect-discover: rc=0; all checks passed
desk-v1-connect-flow: rc=0; all checks passed
desk-v1-connect-held: rc=0; all checks passed
desk-v1-connect-login-step: rc=0; All connect-login-step checks passed
desk-v1-connect-mcp: rc=0; all checks passed
desk-v1-connect-package-step: rc=0; all checks passed
desk-v1-connect-permissions-step: rc=0; all passed
desk-v1-connect-purpose: rc=0; all checks passed
desk-v1-connect-remote-step: rc=0; all checks passed
desk-v1-connect-remote: rc=0; all checks passed
desk-v1-connect-signin-details: rc=0; all checks passed
desk-v1-connect-signin-fill: rc=0; all checks passed
desk-v1-connect-slice2: rc=0; all checks passed
desk-v1-connect-summary-step: rc=0; All checks passed
desk-v1-connect-unknown-step: rc=0; All connect-unknown-step checks passed
desk-v1-connect-wizard: rc=0; All connect-wizard checks passed
desk-v1-connections: rc=0; All checks passed.
human-proof-guard: rc=0; ALL PASS
boot-smoke: rc=0; ✅ PASS — 7 boot scenarios + dispatch, first-run-migration, model-picker, identity-prefill, backlog, backlog-links, memory-panel, calendar, scheduler-layout, question-repaint, floor & agent-face guards all green.
inline-handler-scope-check: rc=0; ✅ inline-handler scope check: 134 modules clean — no inline handler references an unbridged module binding.
```

Python: **1343 passed in 311.33s (0:05:11)**, exit 0, all 28 expanded
Connect/service paths, `-o addopts=''`, no filters. Basic pyright on all four
touched Python modules: **0 errors, 0 warnings, 0 informations**, exit 0.
New-module/bridge JS syntax and three document links pass. Eight screenshots
were captured and visually inspected; layouts wrap, mobile actions stay pinned,
and controls remain reachable at 200% text. Fixture servers provide discovery,
detection and Save answers; no live vendor run is claimed. Final whitespace
cleanup removes only an extra blank line in the smoke file.

Branch checkpoints: 12b `6ddc49cf`, 12 UI `adef20fd`. No open product choice.
The final reference-only correction preserves U1's supplied transport,
credential-parameter and scope provenance instead of flattening it to
classifier. The Save smoke asserts OpenAPI provenance at both widths. The
final full selection ran in three disjoint groups (8 + 8 + 7), covering all
23 paths exactly once; each group exited 0. No production/test change
followed that run.
Ticket 14 can wire this branch without enabling an API/PyPI executor; keep the
new entry after wizard/pick/permissions/package/remote/summary scripts. The
reference branch owns its own Review/Result and uses 12b's existing Connect Save.
The shared MCP chooser sets `mcp/custom-npm` or `mcp/custom-remote`; known
service account selection remains ticket 14's responsibility. No merge, push,
restart, wizard activation or live integration was performed.
