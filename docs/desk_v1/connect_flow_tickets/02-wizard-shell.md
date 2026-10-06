# MC-1062 / 02: Render one setup screen at a time

Status: proposed, not dispatched. [Parent design](../CONNECT_FLOW_SIMPLIFY.md).

## Depends on

01.

## Ownership

New `static/js/desk-v1-connect-wizard.js` and matching CSS; new `tools/smoke/desk-v1-connect-wizard.mjs`. Existing flow receives only a delegation hook; activation waits for 14.

Future workers are not alone in the repository: do not revert others' edits; keep wiring narrow and adapt to concurrent changes.

## Work

Implement Service, Connection, Setup, Permissions, Review, Result with one active branch. Separate step state from DOM-owned secret inputs. At most four alternatives and 25 explanatory words per ordinary screen; one Details disclosure. Paginate long option sets. Keep desktop panel and phone full-screen action bar. Back preserves compatible drafts; service/type/account changes invalidate incompatible data and approval.

## Acceptance

Fixtures at 1440/390; keyboard and 200% text; no hidden primary action or horizontal scroll. No calls that write on Continue. Closing clears typed secrets and cancels owned held operations through their existing cancel path. No nested independent Save panels.

## Boundary

Small UI infrastructure slice. Retain the old entrypoint until all selected branches pass 14.
