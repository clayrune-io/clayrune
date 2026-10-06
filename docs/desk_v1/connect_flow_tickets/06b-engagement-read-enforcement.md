# MC-1062 / 06b: Read consent at engagement dispatch

Approved and dispatched by Dave on 2026-10-06; branch first fast-forwarded to
local master `624c2919`, which includes reviewed 06a. This slice owns only
`mc/desk_engagement.py`, new `mc/desk_engagement_poll.py`, dedicated
`tests/test_desk_engagement_permission_execution.py`, this contract and the
changelog entry. No shared-helper, Accounts, purpose-verification, publisher or
tick changes. Parallel 06c/06d/Accounts owners remain independent.

## Consumer and execution contract

`poll_project` still selects the first presence account per platform, honors
its API/pane choice, checks the reader's existing capability, and always closes
the reader after a connected poll. `_poll_platform` now delegates to a dedicated
module rather than adding the new consent concern to the engagement monolith.
The existing per-platform polling unit moved with its budgets, cursor handling,
ingestion, metrics window/daily deduplication, ledger and error handling.

The new unit reuses 06a `permission_check.require_permission` with the original
presence `channel_id`, operation `read`, purpose `read_own`, and two independent
capabilities: `mentions` and `post_metrics`. It checks just before mentions and
each metrics batch, before token resolution/refresh, pane reads or digest calls.
X routes derive from the actual reader transport: API is `x-oauth`, pane is
`x-browser`; OAuth's first-account-to-None mapping never supplies the consent id.
Generic pane routes use the service registry's unique browser route; a missing
or ambiguous route cannot grant explicit Allow. No route is chosen from a
permission record, no executor or coverage is enabled, and no fallback occurs.
Legacy generic accounts need no registry lookup for permission route selection.

Explicit denial, lost/corrupt policy or mismatched identity makes zero reader,
refresh, platform, browser or digest-model calls for that capability. Each refusal
has a zero-resource/zero-cost failed read-ledger entry, a coverage error, and a
`permission_denied[capability]` reason in the poll result. No synthetic successful
read or zero-valued feed outcome is recorded. Denied mentions do not prevent
independently allowed metrics; denied metrics do not undo ingested mentions.
Revocation is checked again between operations and between metrics batches.

No account id and genuine unset workspace policy retain previous behavior;
consent is not inferred from singleton credentials. Existing read failures
(including sign-in walls), provider/profile readiness and paid-read budget
refusals retain their prior stop/report behavior. Existing `_afford` and actual
read costing remain: metrics-only consent costs only its allowed metrics read,
without reserving denied mentions. This is no new spending guard or accounting
system; MC-1064 remains separate.

The touched engagement module had a pre-existing pyright error for a nullable
engagement state dictionary key. The lookup now normalizes absent state to an
unmatched empty-string key; valid states and resulting lane counts are unchanged.

## Current support limits

The real X OAuth profile marks `read_own/mentions` and `post_metrics` coverage
`unknown`; ticket 05 currently refuses API Read Allow. Real consumer tests cover
Deny and forged/stale API Allow failing closed. Tests using the named
`supported_api_scope` fixture simulate future reviewed coverage to verify API
independent decisions, identities, revocation and retained budget gates. Those
simulations do not prove production API Read Allow is available. No profile,
permission-policy validator or executor registration changes in 06b.

X browser mentions and post_metrics have supported exact consent scopes today;
their independent Allow tests use the real profile. The generic X digest reader
also exercises these supported scopes with fake page/model seams. LinkedIn's
pane executor is unregistered, so a new explicit pane Read Allow is still
unsupported; a forged policy fails closed. Its legacy generic reader continues
to run, but explicit Deny/revocation stops it. Unknown generic sites remain
legacy-compatible; invalid explicit policy is refused. No unsupported generic
Allow is claimed.

## Consumer verification

Tests invoke the real project poll, reader factory, XReader, PaneXReader,
PaneDigestReader and human poll route. Tokens, platform responses, page factories
and toolless digest calls are fakes; no live browser, credential or platform
operation is performed. Coverage includes:

- Separate mentions/metrics Deny and supported pane scopes; exact route mismatch,
  no fallback, Post Allow never authorizes Read, original first/second identity.
- Revocation without bindings, between capabilities and between metrics batches;
  no denied refresh/page/discovery/model calls or billed reads.
- Legacy/unset and inline/no-id reads, costs and reply attribution; invalid
  identity, lost policy and unsupported forged consent never use another token.
- Actual generic legacy reads, explicit Deny/revocation, supported X generic
  scope checks, existing profile agent-read/domain gates and model read budget.
- Human-only poll route; unchanged existing credential/profile and budget gates.

Focused final run: `python -m pytest
 tests/test_desk_engagement_permission_execution.py tests/test_desk_engagement.py
 tests/test_desk_engagement_pane.py tests/test_desk_engagement_pane_digest.py
 -o addopts='' -q`: **`120 passed in 4.10s`** (exit 0; 46 new + 74 existing).

Basic pyright `mc/desk_engagement.py mc/desk_engagement_poll.py`:
**`0 errors, 0 warnings, 0 informations`** (exit 0, version notice only).

Final combined run: exactly the requested publish/tick/engagement/connect files,
deduplicated, with `-o addopts=''`, no filters, exit 0. The foreground runner
printed every path and asserted base publisher and new execution tests included.
It selected 30 files, collected 1449 tests, and registered PID 40900 successfully
(HTTP 200, `ok: true`). All 46 new cases, 74 existing engagement cases, 19 base
publisher tests and five unchanged Chromium cases passed within this run.

Exact summary:

```text
====================== 1449 passed in 237.41s (0:03:57) =======================
```

Fresh-process import of the new poll module and whitespace/reference checks
passed. No browser security guard or test was changed to obtain a pass.

Exact expanded command:

```text
python -m pytest tests/test_desk_connect.py tests/test_desk_connect_browser_setup.py tests/test_desk_connect_custom.py tests/test_desk_connect_custom_audit.py tests/test_desk_connect_custom_manifest.py tests/test_desk_connect_discovery.py tests/test_desk_connect_display_text.py tests/test_desk_connect_mcp.py tests/test_desk_connect_mcp_followups.py tests/test_desk_connect_names.py tests/test_desk_connect_npm_closure.py tests/test_desk_connect_npm_launch_gate.py tests/test_desk_connect_npm_node_paths.py tests/test_desk_connect_npm_scripts.py tests/test_desk_connect_npm_semver.py tests/test_desk_connect_parameters.py tests/test_desk_connect_permission_execution.py tests/test_desk_connect_permission_policy.py tests/test_desk_connect_providers.py tests/test_desk_connect_real_chromium.py tests/test_desk_connect_remote.py tests/test_desk_connect_signin_login_save.py tests/test_desk_connect_types.py tests/test_desk_engagement.py tests/test_desk_engagement_pane.py tests/test_desk_engagement_pane_digest.py tests/test_desk_engagement_permission_execution.py tests/test_desk_publish.py tests/test_desk_publish_linkedin.py tests/test_desk_tick.py -o addopts=''
```

No UI change: USER_GUIDE/README/rules and SPA smoke are inapplicable. No shared
consumer inventory/status edits during parallel work; this owned contract carries
06b's result. No merge into master, push, restart, live reads or remaining-spend
implementation. The full Read/Post feature still depends on the other slices.
