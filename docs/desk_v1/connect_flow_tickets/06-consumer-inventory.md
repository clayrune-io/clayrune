# MC-1062 / 06: execution consumers and required split

2026-10-06, Sol_Tobin. Source inventory at `444d9e45`; no live requests,
publishes, permission writes, or execution-enforcement changes.

## Boundary finding

There is no single account-aware execution dispatcher through which all covered
Desk reads and posts pass. A shared `permission_check.py` can express the policy,
but checking only `poll_project` and `publish` leaves two independent readers
running: connection verification and scheduled post readback. Checking only the
X reader classes also misses post readback. Ticket 06 therefore stops under its
explicit boundary; Dave must record a split before implementation.

## Actual consumer inventory

| Entry point | Dispatch and outbound path | Identity, route, capability | Existing constraints to retain |
|---|---|---|---|
| Human engagement poll: `POST /api/desk/engagement/poll` (`desk_routes.py:1417`) | `desk_engagement.poll_project` -> `_poll_platform` -> `reader.fetch_mentions` / `fetch_metrics`. X API calls `_urllib_transport`; X pane reads `_read`; other pane sites use `PaneDigestReader` and may invoke a digest model. | First presence account per platform; `channel_id` names the workspace account. Legacy inline/no-account presence is supported. `read_via` chooses API or pane; exact `mentions` and `post_metrics` calls need separate decisions. | Human-only poll route, selected route with no fallback, paid-read budget check, credential scope, pane/profile restrictions, read ledger and honest coverage gaps. |
| Human connection check: `POST /api/desk/connect/purpose/verify` (`desk_connect_purpose_routes.py:73`) | `purpose_verification.check` -> `CHECKERS` -> `_pane_mentions` (`purpose_verification.py:85`) -> a directly constructed `PaneXReader.fetch_mentions`. Never calls `poll_project`. | Workspace account id and exact bound group already available; X `x-browser`, `read_own/mentions`. | Human-only trigger; no paid API or publishing probe; per-capability binding/stamp verification; reader close and failed-check reporting. |
| Human version approval: `POST /api/desk/pieces/<piece_id>/versions/<version_id>/approve` (`desk_routes.py:727`); scheduled send: `desk_tick.run_once` | `approve_and_send` or scheduled `_process` -> `desk_publish.publish` (`desk_tick.py:349`) -> `_send_x` / `_send_linkedin`. A successful X send also performs the permalink username lookup; it belongs to this publishing transaction. | Version's actual workspace account id; X `x-oauth`, `publish/post`. LinkedIn organization uses its existing publishing path, but cannot receive an Allow through the current executor table. | Passcode/human version approval, current campaign bounds and kill switches, cadence, budget, provider readiness, LinkedIn closed gate, account-specific token, durable receipts and unknown-outcome handling. |
| Human engagement reply: `POST /api/desk/engagement/<item_id>/reply` (`desk_routes.py:1324`) | `send_engagement_reply` -> `desk_publish.publish` (`desk_routes.py:1366`), bypassing the campaign tick by design. Shares the outbound publisher with version sends. | Presence `channel_id`; no channel retains the legacy singleton path. Reply is `publish/reply`, **not** `publish/post`. | Retyped passcode, human-only route, taken-over/sent/platform checks, existing idempotency. Ticket 05 deliberately grants no explicit reply scope, so explicit Post Allow must not authorize replies. Legacy/unset behavior remains unchanged. |
| Immediate post verification after send/recovery, and later scheduled verification of submitted versions | `desk_tick._complete_from_receipt` / recovery -> `_verify`, or `run_once`'s separate submitted-version loop -> `desk_publish.verify_post` (`desk_tick.py:274`) -> token resolution -> `_get_tweet` (`desk_publish.py:348`). Calls neither `publish` nor an engagement reader. | Workspace account id; X `x-oauth`, `read_own/own_posts`. LinkedIn returns unsupported without a network call. | Submitted-state/receipt/retry limits, token scope, honest unsupported/unconfirmed status. This paid read is outside engagement polling's budget/ledger checks; the split must account for the existing campaign budget before spending, without inventing a new scheduler. |

Paths were traced by searching all `mc/*.py` consumers of `publish`,
`verify_post`, `poll_project`, `XReader`, `PaneXReader`, `PaneDigestReader`,
`fetch_mentions`, and `fetch_metrics`, then reading the named entry points.
No scheduled engagement poll consumer exists in this source inventory; scheduled
reads here are post-verification attempts. Manual copy/share tasks and
`report_posted` are human statements/actions and send no platform request.

## Proposed owner tickets, for Dave's decision

These are recommendations, not dispatched work or new backlog items.

| Slice | Owned concern/files | Required consumer tests |
|---|---|---|
| 06a | Shared `permission_check.py` contract and narrow publisher dispatch call in `desk_publish.publish`; `test_desk_connect_permission_execution.py` plus publisher/reply/tick tests. | Explicit Deny with valid token and approved campaign produces zero token refresh/platform calls; exact Post scope only; explicit reply refusal; Allow cannot bypass campaign/provider/LinkedIn/budget gates; manual and scheduled paths; legacy unchanged. |
| 06b | Engagement read dispatch in `desk_engagement`, reusing 06a's helper; dedicated execution-read tests. | Denied mentions and metrics each make zero calls/spend; Read revocation stops legacy-selected readers even without bindings; exact route/capability restrictions; no fallback; preserve budget and account identity. Include generic pane/digest paths wherever an explicit account policy applies, without claiming unsupported Allow. |
| 06c | Human purpose-verification dispatch in `purpose_verification`; dedicated probe-consumer tests. | Deny/revocation prevents direct pane construction/read; correct `mentions` scope only; denied check never records verification; human gate and legacy behavior retained. |
| 06d | Post-readback dispatch in `desk_publish.verify_post` and narrow budget/ledger integration in `desk_tick`; dedicated verification-consumer tests. | Read Deny makes zero token refresh/GET calls on immediate, recovery and later scheduled verification; Post Allow does not imply Read; budget exhausted means no billed read; receipt remains submitted/unconfirmed truthfully; legacy policy behavior retained. |

Only 06a owns the shared helper. Other slices add dispatch calls, not feature
bodies in existing monoliths. Merge and validate each slice before the next;
ticket 08 must not advertise enforcement until all relevant consumers pass.

Account consent must be checked using the original workspace account id before
`desk_account_refs.oauth_arg_for` maps the first X account to `None` for its
legacy credential name. That mapping also treats a missing id as the singleton:
it is not a safe permission identity resolver. A supplied missing/wrong account
must not silently choose another account or token. Genuine pre-account legacy
execution needs an explicit compatibility path, not an invented Allow policy.

Browser-domain grants remain ticket 07; discovery panes and general browser-agent
reads have their own authorization and are not Desk account executor consumers.
Custom MCP approval remains whole-server, as already decided in Q2.

## Completed pre-step and verification limits

`444d9e45` initializes explicit default Read/Post Deny only on newly created
browser-setup accounts, under the existing locked Save. Seven added cases cover
X, LinkedIn member/Page with and without organization id, preserved existing
legacy/explicit Allow policy, and failed-initializer account/login rollback.

`python -m pytest tests/test_desk_connect_browser_setup.py
tests/test_desk_connect_permission_policy.py -q` passed (exit 0; all 145 collected
cases reached 100%). `pyright mc/desk_connect/browser_setup.py` reported 0
errors, 0 warnings, 0 informations. `git diff --check` passed.

These tests verify persisted policy initialization and the existing policy
contract; they do **not** prove execution enforcement. No consumer checks were
implemented, no enforcement suite was created, no full Desk suite or UI smoke
was run, and nothing was merged, pushed, restarted, or deployed by this session.
