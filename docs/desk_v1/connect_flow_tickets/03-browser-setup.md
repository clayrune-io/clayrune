# MC-1062 / 03: Save a browser connection without a developer app

Status: proposed, not dispatched. [Parent design](../CONNECT_FLOW_SIMPLIFY.md).

## Depends on

01.

## Ownership

New `mc/desk_connect/browser_setup.py` and `mc/blueprints/desk_connect_browser_setup_routes.py`; `tests/test_desk_connect_browser_setup.py`. Reuse account/vault/profile services; do not grow providers/x.py or server.py beyond registration.

Future workers are not alone in the repository: do not revert others' edits; keep wiring narrow and adapt to concurrent changes.

## Work

Create or reuse an X/LinkedIn account and bind its named browser-profile and optional login reference, independently of purpose permissions. Support zero-permission setup. Use the existing human-only/passcode gate order, create-only login semantics and compensating rollback; no password in records. Preserve a member login separately from a Company Page destination. Do not mint OAuth tokens or fake a browser probe. Any proposed new endpoint is documented as new, not assumed to exist.

## Acceptance

Fresh X account with no Client ID reaches browser setup; two X identities stay separate; LinkedIn member/Page never substitute for one another. Duplicate/replayed saves and account-name conflicts are explicit. Wrong/missing passcode and unattended callers write nothing. Save with no permissions starts no agent read/post. New/moved mc modules pass basic pyright.

## Integrated default-deny policy (2026-10-06, MC-1062/06 pre-step)

New browser-setup accounts call `permission_policy.initialize_new_account` after
the browser record and account kind are applied under the store lock. The same
setup store write persists explicit Read/Post Deny; no new authorization or
network operation is granted. Existing accounts, including legacy/unset and
explicit Allow accounts, retain their policy. Initialization failures use the
existing compensating account/login rollback. Consumer coverage lives in
`tests/test_desk_connect_browser_setup.py`.

Validation: browser-setup and permission-policy suites together passed all 145
collected cases (exit 0); basic pyright on `browser_setup.py` reported 0 errors.
This records consent only; execution enforcement remains ticket 06.

## Boundary

Small backend bridge. Define stable account reuse for 09: attaching a later OAuth connection must use the saved account ID with its own token/profile references, never create a duplicate or rewrite the existing browser read route. Put any necessary provider-attachment body in its own module (`mc/desk_connect/account_attach.py`) and split it as 03b if it exceeds a narrow helper. Reuse named-profile launching; do not introduce a generic unknown-domain password filler.
