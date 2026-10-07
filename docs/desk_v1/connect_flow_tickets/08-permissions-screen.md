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

## MC-1062 follow-up: account reading settings

The production wizard hid the account card's old Read via editor, also hiding its activity-page block. The Permissions screen now has a section registry; `desk-v1-connect-reading-step.js` owns reading drafts, coverage loading, Review facts and account PATCH outcomes. `desk-v1-connect-read-pages.js` owns the activity-address editor as a separate draft factory. Shared labels and instructions live in `desk-v1-connect-copy.js`. Reopening a saved account uses `desk-v1-connect-account-reopen.js` and the same frame, Permissions, Review and Result; it preserves the selected account rather than starting an empty Add service request.

Browser reading is the default. Only X offers app reading; a stored app setting on another service requires an explicit pane choice. Activity addresses appear only when coverage says `pages_needed` or saved addresses exist. No permission/credential is inferred from the reading choice. Continue and address editing never write. Save PATCHes only changed reading fields through the existing human-only account route; errors retain the draft for Result's retry. The old unreachable card branch and caller-free `DeskV1Services.create` were removed.

Verification and Ron's UI walkthrough: [14-reading-settings.md](14-reading-settings.md).
