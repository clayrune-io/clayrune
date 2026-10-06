# MC-1062 / 06a: account permission at the publisher

Dave approved the four-consumer split on 2026-10-06. This slice owns the shared
`mc/desk_connect/permission_check.py`, the narrow call in `desk_publish.publish`,
and `tests/test_desk_connect_permission_execution.py`. No other executor is
wired here; 06b-d follow separately. The branch first fast-forwarded to local
master `b1e5db8f`, incorporating the reviewed pre-step and consumer inventory.

## Execution contract

`require_permission(account_id, service, operation, *, purpose, capability,
route_id, account_kind=None)` reads the current workspace account and calls
ticket 05's tri-state `decision`. It returns `True` for exact explicit Allow,
`None` for a genuine legacy account/caller, or raises `PermissionDenied` before
credential resolution. Supplying a missing, malformed or wrong-service id
refuses execution instead of selecting the singleton. An absent (`None`)
account id is compatible with pre-account callers, but the publishing adapter
checks a known X singleton owner first: omitting the id cannot bypass that
account's consent. A tombstone for a deleted owner is refused, not reassigned.

Consumers must pass the **original workspace account id**, service and the
route/capability they actually execute, not user-supplied claims. Read consumers
can reuse this helper in 06b-d without modifying the publishing adapter.

`require_publish(item, unattended=False)` derives `x-oauth` or
`linkedin-oauth` from the actual platform. An `in_reply_to` field marks a reply,
even if empty, so it cannot borrow explicit `publish/post` consent. Ticket 05
has no supported explicit reply scope. Legacy replies keep their current human
path. Explicit Allow also retains actual API readiness, preview/manual
restrictions and the credential's attended-only setting; it never opens the
LinkedIn provider gate or grants an unsupported executor.

The publisher dispatch calls the helper after its receipt lookup, campaign
approval check and durable-store gate, then resolves its account's OAuth
argument. Thus the first account can retain `oauth.x` without losing its own
consent identity. A denial is translated to the existing `PublishError` shape:
human replies remain unsent; the tick records the failed send without a network
call. A duplicate receipt still returns the fact of the earlier post after
revocation; no new dispatch or credential use occurs.

## Consumer coverage

Focused tests invoke the real publisher and real Flask reply/approval routes,
plus the scheduled tick, with token and platform calls recorded. They cover:

- Explicit Deny with valid credentials, ready routing and an approved campaign:
  zero OAuth mapping, refresh, platform sends, verification or billed ledger use.
- Exact Post scope, account-specific tokens, read-only policy, corrupt/lost
  consent, unsupported routes/capabilities and caller-supplied approval claims.
- Reply refusal under explicit Post Allow (including empty target fields),
  unchanged legacy replies and unchanged pre-account calls.
- Current campaign approval/budget-bound widening, provider readiness, manual
  or preview API refusal, attended-only credentials and the closed LinkedIn gate.
- Manual approval and scheduled execution, denial failure states, revocation
  after receipt persistence and no fallback to another account/token.

No engagement polling, connection verification or post-readback enforcement
is claimed. Explicit Read Deny does not yet block the verification GET after
an allowed post; that path belongs to 06d. No UI is added or enabled; the
User Guide update for the complete wizard remains ticket 14.

## Budget discovery requiring Dave's scope decision

Source inventory found that publishing and `desk_tick._refusal` enforce
budget-bound approval changes, but have no remaining-spend pre-send check.
`desk_engines.budget_state` calculates campaign amount, engine job spend and
ledger post costs; importing it does not invent a scheduler or publisher.
Dave was asked whether 06a should add that check for explicit-policy sends or
preserve the existing budget-bound gates and defer spending enforcement.
Recommendation: keep the approved consent slice scoped to existing gates and
give remaining-spend accounting its own owner ticket. That option changes no
06a behavior and adds a separate implementation/review; adding it here requires
a pre-send check plus budget-accounting tests, and careful treatment of job
reservations, post costs and legacy calls. This recommendation is not a decision.
The check must not be silently added to legacy accounts: legacy/unset behavior
is an explicit acceptance constraint. No remaining-spend enforcement is claimed
until that decision is recorded and the selected path is tested.

## Verification

Focused final selection: `python -m pytest
tests/test_desk_connect_permission_execution.py -o addopts='' -q`:
`52 passed in 2.27s` (exit 0). Basic `pyright
mc/desk_connect/permission_check.py`: `0 errors, 0 warnings, 0 informations`
(exit 0; available-version notice only).

Final expanded pass with the complete 52-case suite and singleton-owner
correction: **`1 failed, 1327 passed in 259.06s (0:04:19)` (exit 1)**.
The failure is the unchanged real-Chromium test
`test_the_webrtc_policy_flag_alone_closes_the_udp_route` (line 218):
`body.get('ok')` was false with `error: own_origin_blocked` and detail
`the browser pane may not read Clayrune's own origin`.

Running the unchanged Chromium file alone then produced **`1 failed, 4 passed
in 24.79s` (exit 1)**, with the same error in
`test_a_devtools_websocket_needs_the_pane_own_origin` (line 248). A diagnostic
plugin that logs origin-guard refusals, without changing the guard's verdict or
any test definition, subsequently ran all five unchanged cases successfully:
`5 passed in 26.06s` (exit 0); no refusal occurred to trace. The failing URL and
cause remain unconfirmed. That diagnostic pass does **not** replace or erase the
failed expanded result, and the full selection is not claimed green.

No new permission/publisher consumer failed. Earlier expanded passes before the
last compatibility cases were `1312 passed in 226.83s` and `1320 passed in
237.03s`; they likewise do not validate the final code in place of the final run.
The scratch harness prints the full expanded command and registers its own
foreground PID before invoking pytest; no background worker is launched.

The test selection is every existing `tests/test_desk_publish*.py`,
`tests/test_desk_tick*.py` and `tests/test_desk_connect*.py`, plus
`tests/test_desk_conversations.py` for the existing human reply gates.
No tests were filtered, skipped by this harness, replaced by a nearby suite,
or excluded because of an earlier failure. All tokens and platform transports
in the new tests are fakes. A fresh-process import of the new helper passed,
confirming that its lazy publisher/Accounts integration does not introduce an
import cycle. New functionality lives in its own module; the existing publisher
receives only the dispatch call and relocation of its OAuth account mapping.

No browser security guard or browser test was changed to obtain a pass. No
reader wiring, UI smoke, live platform operation, merge, push, or restart was
performed. USER_GUIDE/README/rules remain unchanged because this is a backend
slice; ticket 14 documents/enables the complete user-facing wizard. The shared
CHANGELOG states only the publishing behavior this slice actually enforces.
