# MC-1062 / 05: Define a truthful connection permission contract

Status: contract implemented on the ticket branch; execution enforcement remains ticket 06. [Parent design](../CONNECT_FLOW_SIMPLIFY.md).

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

## Implemented contract (MC-1062/05)

- Account storage: `permission_policy_version: 1` plus `permission_policy` in the existing `data/desk.json` account. No sidecar in DATA_DIR. The policy contains `version`, `account_id`, `service`, `account_kind`, explicit boolean `read` / `post`, `scopes`, SHA-256 `fingerprint`, and `updated_at`. A scope is exactly `{purpose, capability, route_id}`. The fingerprint covers version, account/service/kind, both operations, and every exact scope; it is metadata, never an authorization token.
- `permission_policy.initialize_new_account(rec, account_kind)` adds explicit default Deny to a **new** account in memory; setup's own human-gated Save persists it. It does not reset an existing policy, create an account, choose routes, or authorize anything. Setup consumers must use it for new accounts. Legacy records are never bulk-migrated.
- `read_policy(rec, account_id=..., account_kind=...)` returns `state: legacy` with `read/post: null` only when both fields are absent on a valid account. Explicit Deny returns `state: explicit`, false operations, and an empty scope list. A missing explicit policy/marker, malformed schema/version, altered fingerprint, or wrong account/kind returns `state: invalid` with both operations denied. The separate version marker prevents a missing policy from reverting a newly created account to legacy behavior.
- Ticket 06's seam is `decision(rec, operation, purpose=..., capability=..., route_id=..., account_id=..., account_kind=...)`: `None` means retain legacy behavior; `False` denies; `True` permits only the exact scope as an additional restriction. Consumers still enforce campaign approval, budget, token selection, provider restrictions and browser policy. This ticket adds no execution call sites.
- Allow requires explicit scopes backed by a declared profile capability **and** an existing Desk executor. Read covers only `read_own`; Post covers only `publish.post`. Unknown coverage, Reply, media, Article, broad listening, reference-only routes, and arbitrary MCP are refused. Selecting an API route never approves an API read. LinkedIn browser digest consent remains the existing profile/site grant, not a claim that the Desk engagement executor exists.
- `GET /api/desk/connect/permissions/<account_id>` reads metadata without side effects. `POST /api/desk/connect/permissions/commit` accepts only `{request_id, draft, passcode}`; draft contains exactly `{account_id, service, account_kind, read, post, scopes}`. No caller-provided approval field or reusable token. Sequence: unattended refusal, shape/identity/scope validation, existing `_require_human_passcode` check, locked revalidation and account-local write. Every replay checks a fresh passcode; same request/draft returns the first result without another write, changed draft returns 409, failed writes may retry. Replay memory is bounded and process-local, following existing Desk Save behavior.
- Permission Save preserves detailed/split bindings, route preferences, browser profiles/site grants, credentials/vault policy, MCP approvals and all other account/store fields. Browser and MCP permission writes continue through their existing separately protected endpoints. No UI change, publishing enablement, merge, push or server restart in this ticket.

Validation: `python -m pytest tests/test_desk_connect_permission_policy.py` passed 69 tests; `pyright mc/desk_connect/permission_policy.py mc/blueprints/desk_connect_permission_routes.py` returned `0 errors, 0 warnings, 0 informations`.

Full regression: the foreground Python harness expanded the exact `tests/test_desk_connect*.py` pattern, registered its process, and called `pytest.main(paths)`. Output: `1106 passed in 198.00s (0:03:17)` (exit 0). The complete expanded command is recorded in the item's journal. No test was skipped. No live policy write was made; Flask test clients exercised the real passcode guard against isolated temporary stores.
