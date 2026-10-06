# MC-1062 / 10: Move package MCP into selected setup/review pages

Status: proposed, not dispatched. [Parent design](../CONNECT_FLOW_SIMPLIFY.md).

## Depends on

02, 08; Q2.

## Ownership

New `static/js/desk-v1-connect-package-step.js`; `tools/smoke/desk-v1-connect-package-step.mjs`. Reuse custom.js/custom-deps.js and current backend review/commit/launch gate.

Future workers are not alone in the repository: do not revert others' edits; keep wiring narrow and adapt to concurrent changes.

## Work

Show package and editable detected settings first; permissions/reach later; immutable review facts and scripts last. At most four script approvals per page. Preserve full command, pins, credential refs, reach, unreviewed-code notice, full script bodies and changed-card invalidation. Dependencies/license/size/evidence are under one Details disclosure. No hidden or automatically approved install steps.

## Acceptance

Unknown npm package and dependency/script fixtures use existing exact approval fingerprint. Changed args/pins/reach/scripts require new review. Missing passcode/unattended rejection, checksum and launch-gate regressions remain green. Pending runtime and setup failure are honest. Detection of PyPI never invokes npm or pretends installation succeeded.

## Boundary

Small frontend move. Do not change dependency resolution or soften the recently landed Node search-path gate.
