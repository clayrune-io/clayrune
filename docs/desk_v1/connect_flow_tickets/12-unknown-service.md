# MC-1062 / 12: Give unknown URLs an honest short path

Status: proposed, not dispatched. [Parent design](../CONNECT_FLOW_SIMPLIFY.md).

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
