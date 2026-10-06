# MC-1062 / 11: Move remote MCP into selected setup/review pages

Status: proposed, not dispatched. [Parent design](../CONNECT_FLOW_SIMPLIFY.md).

## Depends on

02, 08; Q2.

## Ownership

New `static/js/desk-v1-connect-remote-step.js`; `tools/smoke/desk-v1-connect-remote-step.mjs`. Reuse remote.js and the existing remote review, shared commit, check and adopt routes.

Future workers are not alone in the repository: do not revert others' edits; keep wiring narrow and adapt to concurrent changes.

## Work

Separate address, authentication, permissions/reach and review. Keep HTTP/SSE proposed protocol editable; token uses a vault reference. OAuth is visibly Save only/pending runtime. Show exact recipient and mutable-server risk before final approval. Existing exposure acknowledgements remain explicit and fingerprint-bound; paginate rather than conceal if more than four choices.

## Acceptance

Review contacts no target. Changed address/scope/header requires new approval. Same-origin recipient rule and redirect refusal remain. Handshake status says reached, not Verified. Changed tools require the existing passcode adoption; unsupported OAuth does not offer a runnable check. Both supported protocols retain acceptance coverage.

## Boundary

Small remote UI slice, independent of package UI. No OAuth implementation or capability-enforcement claim added.
