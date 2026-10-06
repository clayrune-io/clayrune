# MC-1062 / 09: Isolate provider/API setup fields

Status: proposed, not dispatched. [Parent design](../CONNECT_FLOW_SIMPLIFY.md).

## Depends on

02, 08.

## Ownership

New `static/js/desk-v1-connect-api-step.js`; `tools/smoke/desk-v1-connect-api-step.mjs`. Reuse adapter.js, held.js and existing provider commit paths. If an existing account is selected, reuse 03's setup identity contract; its server-side support must be complete first.

Future workers are not alone in the repository: do not revert others' edits; keep wiring narrow and adapt to concurrent changes.

## Work

Show the chosen provider's required fields, then held OAuth where supported. Keep X Client ID/optional secret and callback instructions; retain Higgsfield/key-engine and curated-provider capabilities. Store typed login only with the existing commit. Preserve held-token expiry, claim and changed-app cancellation. LinkedIn built-in API remains unavailable; supplied API details stay explicitly setup-only until U3.

## Acceptance

Existing stored credential reuse; no duplicate account when attaching API to an existing browser account (coordinate 03's account identity contract). start-held and final Save each retain their own passcode boundary. Changed app invalidates held authorization. No paid or publishing verification probe. Existing connect/signin/held/vault regression smokes.

## Boundary

Small provider UI slice. The current X provider creates a new account: attaching API to an existing browser account is a required backend gap in 03, not functionality claimed to exist. No generic API executor and no LinkedIn gate change; a newly supplied API is not redirected to another integration.
