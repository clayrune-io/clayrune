# MC-1062 / 06d: account Read consent on post readback

Owns the narrow dispatch call in `desk_publish.verify_post` and
`tests/test_desk_connect_verify_consent.py`. The shared helper is 06a's
`permission_check.require_permission`; nothing was added to it. Remaining-spend
budget is MC-1064 and is not touched here.

## What is enforced

`verify_post` (X only) calls `require_permission(account_id, 'x', 'read',
purpose='read_own', capability='own_posts', route_id='x-oauth',
account_kind='account')` before `desk_account_refs.oauth_arg_for` and before
`desk_oauth.x_token`. Denial raises `PublishError` ("could not check"), so
there is no token refresh and no GET.

Every caller already routes through it: `desk_tick._verify` (immediate verify
after send and after crash recovery, via `_complete_from_receipt`) and the
`verify` loop in `run_once`. No `desk_tick` change was needed: its existing
`PublishError` branch counts the attempt, records `verify_error`, and after
`VERIFY_MAX_ATTEMPTS` marks the receipt `unconfirmed`. A denied readback
therefore leaves the version `submitted` with no `verified_at`, which is the
truth: the post went out and nobody confirmed it.

- Exact scope only. Post Allow, Read for `mentions`, and Read through the pane
  (`x-browser`) do not satisfy `read_own/own_posts` via `x-oauth`.
- Legacy accounts (no policy), pre-account callers (`account_id=None`) and
  LinkedIn behave as before. LinkedIn returns `None` with no call, so no check
  is made there.
- A supplied missing, wrong-service or malformed account id is refused rather
  than falling back to the singleton token.
- Consent is read on each call: revoking Read stops the next attempt, granting
  it lets the later loop verify.

## Consequence for existing tests

An account with explicit Post Allow and no Read used to verify after posting.
It now stays `submitted`. `test_actual_manual_approval_and_scheduled_tick_consume_permission[allow]`
in the 06a file granted Post only and asserted `verified_published`; it now
also grants `read_own/own_posts` via `x-oauth`. Ticket 08's wizard must grant
Read alongside Post for an account that should reach `verified_published`.

## Not done

- A denied readback consumes one of the five verify attempts per tick pass,
  so a long-denied post ends `unconfirmed`, and granting Read afterwards does
  not reopen it (the loop skips `unconfirmed` receipts). Distinguishing
  "denied" from "could not reach X" would need a `desk_tick` change; left for
  Dave's decision.
- Verification spend is outside the engagement budget/ledger; unchanged
  (MC-1064).
