# MC-1062 / 08: Put permissions in their own later step

Status: proposed, not dispatched. [Parent design](../CONNECT_FLOW_SIMPLIFY.md).

## Depends on

02, 06, 07; Q2.

## Ownership

New `static/js/desk-v1-connect-permissions-step.js`, matching CSS and `tools/smoke/desk-v1-connect-permissions-step.mjs`. Data-only capability label mapping; existing purpose editor receives delegation only.

Future workers are not alone in the repository: do not revert others' edits; keep wiring narrow and adapt to concurrent changes.

## Work

Show supported Read/Post choices and exact account/site facts after setup. New grants start off. Preserve existing selections and advanced split bindings in one Details view. Distinguish Desk policy, browser-site permission, vault unattended-use and MCP project/global reach. Show explicit paid API Read with material cost notice; never switch an existing browser read merely because API posting was added.

## Acceptance

Permissions absent from Connection/Setup screens. Denied capability is genuinely stopped by 06 or 07. Supported-only selections preserve exact coverage and no inferred Reply/Article/media authority. Under Q2-A, custom MCP says Use this server's tools and contains no fake Read/Post switches. Reference-only records grant no connection permission.

## Boundary

Small presentation slice once enforcement exists. A cosmetic relabel of purpose checkboxes does not satisfy this ticket.
