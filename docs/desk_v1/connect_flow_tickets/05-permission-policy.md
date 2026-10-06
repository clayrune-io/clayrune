# MC-1062 / 05: Define a truthful connection permission contract

Status: proposed, not dispatched. [Parent design](../CONNECT_FLOW_SIMPLIFY.md).

## Depends on

Dave records Q2; follow CONNECT_FLOW_SIMPLIFY section 7.

## Ownership

New `mc/desk_connect/permission_policy.py`, its own narrowly scoped blueprint if a new write route is necessary, and `tests/test_desk_connect_permission_policy.py`. Store policy with the account or outside DATA_DIR; no new unexcluded sidecar.

Future workers are not alone in the repository: do not revert others' edits; keep wiring narrow and adapt to concurrent changes.

## Work

Define explicit Desk Read/Post consent independently of route selection and browser-domain/MCP grants. New connections can save with no allowed operations. Legacy records retain existing behavior until human editing; distinguish legacy/unset from explicit deny. Version and fingerprint the exact scope. Preserve detailed bindings; broad labels cannot silently add unsupported capabilities. All policy changes use human-only passcode protection.

## Acceptance

Policy truth table covers new/legacy/allow/deny, wrong account/kind, missing or corrupt explicit policy, unchanged unrelated grants and duplicate submission. No route preference or token existence may itself constitute new consent. No caller-supplied approval or reusable authorization token. Basic pyright.

## Boundary

Small data/validation slice. This alone does not enforce execution; do not expose live Permission switches until 06/07 pass. Granular arbitrary-MCP enforcement is excluded unless separately designed under Q2.
