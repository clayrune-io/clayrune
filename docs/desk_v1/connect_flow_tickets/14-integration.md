# MC-1062 / 14: Replace the old entrypoint after branch acceptance

Status: implemented on the ticket branch; review/merge/release remain with Dave. [Parent design](../CONNECT_FLOW_SIMPLIFY.md).

## Depends on

01–13b, both decisions recorded.

## Ownership

Only wiring/removal in desk-v1-add-service.js, desk-v1-connect-flow.js, desk-v1-connections.js and static/index.html; end-to-end `tools/smoke/desk-v1-connect-simplify.mjs`; update relevant URL/profile specs and docs/USER_GUIDE.md after implementation.

Future workers are not alone in the repository: do not revert others' edits; keep wiring narrow and adapt to concurrent changes.

## Work

Activate the new shell; remove old list duplication, stacked purpose editor, stale blanket MCP warnings and concurrent forms. Preserve old account settings and all supported engine/manual/reference paths. Every UI concern lives in its ticket's own module, not the entrypoint. Update documentation to distinguish shipped behavior from this proposal.

## Acceptance

Fresh X with no app: URL → Sign in → username/password → later Read permission → truthful result. LinkedIn member and Page: distinct destination, same member-auth model, gate unchanged. Unknown URL: discovery, manual MCP and reference-only failures are honest. 1440/390 and keyboard/zoom; complete existing connect/vault/MCP/browser-policy smokes plus boot smoke when index.html changes. Source denial tests prove switches, not just DOM labels.

## Boundary

Integration only. Run relevant smokes after each merge; use existing main-checkout dependencies, never install a second browser stack. No deployment/restart/push is authorized by this proposal.


## Binding additions (Ron, 2026-10-06)

Section 11 of the parent supersedes the separate known/unknown and legacy wizard experiences. Every service uses Add service, Connection options, Connection details, Permissions, Review and Result, with titles/instructions/actions/field vocabulary from one shared copy module. Unknown URLs investigate automatically on Continue. Suggestions lacking adapters remain information only. A saved supported connection has a human-clicked free Check it now in its own tile/detail, not only immediately after Save.

The integration smoke covers Higgsfield key and sign-in, LinkedIn member and Page, unknown URL, YouTube information and fresh X without an app, at 1440 and 390. It asserts common copy, no internal terminology outside Details, no grants/writes on navigation, new grants off, explicit destination, separate sign-in/Save proof, saved tile check success/failure, keyboard action reach and 200% text fit. Detailed detection/reference and native adapter/approval smokes remain separate. Backend denial tests are required, not replaced by DOM assertions.
