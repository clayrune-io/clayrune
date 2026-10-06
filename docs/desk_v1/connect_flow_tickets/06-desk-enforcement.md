# MC-1062 / 06: Enforce Desk permission before read or publish

Status: split approved by Dave; 06a consent-only compatibility correction
verified on its branch, awaiting Dave integration (2026-10-06). Remaining-spend guard is a separate
ticket by Dave's decision; 06b-d remain separate.
[Parent design](../CONNECT_FLOW_SIMPLIFY.md).

The separately committed browser-account default-deny pre-step is complete.
See [06a's publisher contract](06a-publisher-enforcement.md) and the
[consumer inventory and proposed owner split](06-consumer-inventory.md) for
the independent read paths that bypass engagement polling and publishing.
06b-d remain separate work; the complete Read/Post enforcement is not shipped.

## Depends on

05.

## Ownership

New `mc/desk_connect/permission_check.py` with focused `tests/test_desk_connect_permission_execution.py`; narrow calls at existing Desk reader/publisher dispatch boundaries. No feature bodies appended to the execution monoliths.

Future workers are not alone in the repository: do not revert others' edits; keep wiring narrow and adapt to concurrent changes.

## Work

Apply explicit account Read/Post policy before the respective network operation or spend. Keep campaign approval, budget, provider scopes, account token selection, LinkedIn closed gate and no-fallback behavior as additional constraints. Unbinding a route cannot stand in for revocation. Reject rather than silently choosing another connection when access is denied.

## Acceptance

Mock actual read/publish clients: explicit deny causes zero outbound calls, even with a valid token, ready route and approved campaign. Allow still fails a closed campaign/provider gate. Removing Read stops covered legacy reader activity. Legacy/unset behavior unchanged. Enumerate Desk call sites reached by direct/manual approval and scheduled execution; no new checks are credited without consumer tests.

## Boundary

One enforcement seam, not a new scheduler or publisher. If source inventory reveals more independent consumers, split them into separate owner tickets before implementation.
