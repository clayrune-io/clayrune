# MC-1062 / 06a: account permission at the publisher

Dave approved the four-consumer split on 2026-10-06. This slice owns the shared
`mc/desk_connect/permission_check.py`, the narrow call in `desk_publish.publish`,
and `tests/test_desk_connect_permission_execution.py`. No reader is wired here;
06b-d remain separate. The branch incorporated reviewed master `b1e5db8f`
before the initial implementation.

## Execution contract

`require_permission(account_id, service, operation, *, purpose, capability,
route_id, account_kind=None)` returns `True` for exact explicit Allow, `None`
for legacy/unset, or raises `PermissionDenied`. Consumers pass the original
workspace account id before `desk_account_refs.oauth_arg_for` maps the first
X account to the singleton token name. A supplied malformed, missing or
wrong-service account id is refused instead of selecting another connection.
An existing account with neither policy nor version marker retains legacy
behavior without new account-kind restrictions. A version marker with lost
policy remains invalid and fails closed.

An absent (`None`) account id retains the pre-account publisher behavior.
The helper does not infer an owner from singleton credentials or tombstones.
This compatibility requirement applies even if a workspace account has an
explicit policy. Account-aware callers must supply their original account id.

`require_publish(item)` derives the actual `x-oauth` or `linkedin-oauth` route.
An `in_reply_to` field marks a reply even if empty; explicit Post Allow covers
only publish/post, never reply. Legacy replies retain their human path.
The helper checks consent only: it does not call `publish_state`, inspect vault
metadata, read credentials, refresh tokens, or contact a platform.

The publisher calls it after receipt lookup, campaign approval and the durable
receipt-store gate, before OAuth mapping and token resolution. Denial becomes
`PublishError` before outbound work. Credential errors still originate from
the existing resolver and keep `credential unavailable: ...` messages and
unattended/project/consumer arguments. An existing receipt remains the fact of
a prior post after revocation; returning it requires no new dispatch.

Provider readiness, manual/preview handling, LinkedIn's closed gate, human
approval and scheduled credential gates remain at the existing approval/tick
call sites. This helper does not add those gates to direct `publish` calls.
Explicit Allow cannot bypass the existing campaign or budget-bound checks.

## Consumer coverage

Tests exercise the real publisher, human reply/approval Flask routes and the
scheduled tick with recorded tokens/transports. Coverage includes:

- Deny/read-only with valid credentials and approved campaign: zero account
  mapping, token refresh, platform calls, verification or billed ledger use.
- Exact account, API route and post scope; corrupt/lost consent; denied replies
  including empty reply targets; idempotent receipts after revocation.
- Absent-id callers with explicit singleton policy, deleted/ambiguous owners,
  legacy account replies and unset policy with older account-kind metadata.
- No consent-time vault/provider probe, exact credential-resolution errors,
  and real token-resolver failures for missing and attended-only vault entries.
- Existing campaign/budget widening, provider and scheduled credential gates;
  preview refusal, manual copy task and LinkedIn refusal at approval.

No engagement polling, connection verification or post-readback enforcement is
claimed. Read Deny does not yet block verification GETs after allowed posts;
that belongs to 06d. No UI is added; ticket 14 owns the complete wizard guide.

## Spending decision

Dave agreed on 2026-10-06: remaining-spend enforcement is a separate ticket,
which he is filing. 06a stays consent only and preserves current budget-bound
approval checks. No remaining-spend guard or new budget accounting is claimed.

## Compatibility correction and verification

Dave rejected initial commit `18aaeca4`: his merge produced 14 failures in
`tests/test_desk_publish.py`, including success receipts and original missing/
denied credential errors. He backed out the merge. The helper had inferred a
singleton owner for absent-id callers and performed its own vault-readiness
check, which changed the accepted compatibility contract. Both are removed.
Tests that asserted those extra restrictions were replaced with compatibility
regressions and tests of the gates at their existing consumer call sites.

The prior claim that the expanded run established complete publisher coverage
and unchanged legacy behavior is withdrawn. Its reported `1 failed, 1327
passed in 259.06s` (Chromium `own_origin_blocked`) is historical evidence only;
it does not validate this correction or override Dave's observed failures.

Intermediate base publisher plus consent suite: `79 passed in 2.14s` (exit 0),
before six additional regression cases were added. Basic pyright on the final
`mc/desk_connect/permission_check.py`: `0 errors, 0 warnings, 0 informations`
(exit 0; version-availability notice only).

Final exact requested publish/tick/engagement/routes/connect selection:
30 files, deduplicated with no filtering, run with `-o addopts=''` (exit 0).
The 19 base publisher tests, 66 consent execution tests and all five unchanged
Chromium tests passed within this combined run. Exact summary:

```text
====================== 1456 passed in 227.28s (0:03:47) =======================
```

The foreground scratch runner printed every selected path, asserted
`tests/test_desk_publish.py` was included, and registered itself before pytest:
HTTP 200, `ok: true`, PID 43656. The prior Chromium failure did not recur in
this selection; its historical root cause remains unconfirmed.

No browser guard, browser test, reader wiring or remaining-spend code changed.
No live platform operation, merge, push or restart. USER_GUIDE, README and rules
need no changes: this corrects the backend consent boundary, adds no UI and
creates no new project convention. SPA smoke is inapplicable.

Exact expanded final command (no test filters):

```text
python -m pytest tests/test_desk_connect.py tests/test_desk_connect_browser_setup.py tests/test_desk_connect_custom.py tests/test_desk_connect_custom_audit.py tests/test_desk_connect_custom_manifest.py tests/test_desk_connect_discovery.py tests/test_desk_connect_display_text.py tests/test_desk_connect_mcp.py tests/test_desk_connect_mcp_followups.py tests/test_desk_connect_names.py tests/test_desk_connect_npm_closure.py tests/test_desk_connect_npm_launch_gate.py tests/test_desk_connect_npm_node_paths.py tests/test_desk_connect_npm_scripts.py tests/test_desk_connect_npm_semver.py tests/test_desk_connect_parameters.py tests/test_desk_connect_permission_execution.py tests/test_desk_connect_permission_policy.py tests/test_desk_connect_providers.py tests/test_desk_connect_real_chromium.py tests/test_desk_connect_remote.py tests/test_desk_connect_signin_login_save.py tests/test_desk_connect_types.py tests/test_desk_engagement.py tests/test_desk_engagement_pane.py tests/test_desk_engagement_pane_digest.py tests/test_desk_publish.py tests/test_desk_publish_linkedin.py tests/test_desk_routes.py tests/test_desk_tick.py -o addopts=''
```
