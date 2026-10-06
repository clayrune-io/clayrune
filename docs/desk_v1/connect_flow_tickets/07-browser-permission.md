# MC-1062 / 07: Adapt the existing site/profile Read grant

Status: proposed, not dispatched. [Parent design](../CONNECT_FLOW_SIMPLIFY.md).

## Depends on

03, 05.

## Ownership

New `static/js/desk-v1-connect-browser-permission.js`; `tools/smoke/desk-v1-connect-browser-permission.mjs`. Reuse existing browser-agent-read routes and DeskV1ConnectAgentRead; no replacement permission store.

Future workers are not alone in the repository: do not revert others' edits; keep wiring narrow and adapt to concurrent changes.

## Work

Show Read for the detected site on the selected named profile. Read the existing policy, preserve other domains, and submit only the intended change through the existing PUT and its own humanProofFetch. Show shared profile/site consequences. A profile that does not yet exist defers this grant until sign-in; no early implicit launch/grant. Keep one-time approval behavior outside this editor.

## Acceptance

Grant/revoke the target domain while preserving unrelated sites; two connections sharing a profile display the same grant. Cancel/wrong passcode leaves policy unchanged. Saved connection plus failed permission shows partial success. Browser Read never becomes raw-page access, posting, click/type or a claim of automatic LinkedIn feed coverage.

## Boundary

Small adapter/UI slice. It is a profile-domain permission, not a new per-account browser sandbox.
