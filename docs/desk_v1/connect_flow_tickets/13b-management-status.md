# MC-1062 / 13b: Show each connection's actual status

Status: proposed, not dispatched. [Parent design](../CONNECT_FLOW_SIMPLIFY.md).

## Depends on

13a.

## Ownership

New `static/js/desk-v1-connection-status.js` and `tools/smoke/desk-v1-connection-status.mjs`; minimal delegation in connections.js and its tile module.

Future workers are not alone in the repository: do not revert others' edits; keep wiring narrow and adapt to concurrent changes.

## Work

Project the actual state of each browser/API/MCP/reference connection onto Connections management. Keep publishing readiness separate from browser access. Include approved remote and npm records with their existing drift/re-review/setup-failure recovery actions. Reopen the same setup/permission editor instead of maintaining duplicate Read via controls.

## Acceptance

A functioning browser connection is not labelled disconnected solely because publishing is unavailable. API plus browser on one account retains both identities and routes. Registered, needs sign-in, cannot run yet, setup failed and saved reference remain distinct. Existing custom records and observed remote changes remain visible. A status refresh opens no browser and performs no paid or mutating probe.

## Boundary

Small status/management unit. No capability is promoted to Verified by registry metadata or a successful MCP handshake.
