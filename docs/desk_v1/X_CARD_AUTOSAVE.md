# X card re-sign-in auto-save

Branch candidate from `1c9d2fb4`. No merge, push, restart or authenticated acceptance.

## Contract

The existing held-sign-in commit path checks the claim, account, app and saved-entry
replacement policy. It does **not** verify which X user granted the new token.
`mc/desk_connect/x_card_signin.py` adds that check for the saved-account card only.

The card sends `auto_save: true` and an explicit existing `account_id` to
`POST /api/desk/connect/x/start-held`. The server first refuses unattended callers,
checks the dashboard passcode and validates the target through `x_account_attach`.
Only then does it stamp the flow with the auto-save account id. A request without
an existing id cannot opt in; nested caller-supplied flow flags are not accepted.

After the code exchange, one bounded `GET https://api.x.com/2/users/me` uses the
new access token. It does not use the saved token, refresh, retry or follow redirects.
The response body's handle is validated and compared case-insensitively against
the saved handle (an optional leading `@` is ignored). No provider body, token or
exception text from that lookup is returned or logged. The endpoint and user-context
authentication follow [X's authenticated-user sample](https://github.com/xdevplatform/samples/blob/main/python/users/get_users_me.py).

| Callback outcome | Saved state | Card controls |
| --- | --- | --- |
| Handle matches | Claims and commits immediately | Says “The X sign-in is saved.” and reads `/api/desk/accounts` again |
| Different handle | Held, never committed | Shows both handles and Discard only; direct manual commit also refused |
| Lookup fails, quota/HTTP refusal, malformed response | Held | Explains identity could not be confirmed; passcode-gated manual Save or Discard |
| Automatic write/app/replacement failure | Held, undo restores any write | Reports failed automatic save; manual Save or Discard |
| Saved handle changes during lookup | Held, never committed | Explains the identity changed; Discard only |

The auto-save revalidates the target and saved app before calling `commit_held` with
the original claim and an `UndoStack`. It retains `store_with_undo`'s dead-versus-live
replacement and exact encrypted-record rollback, including scope and unattended-use
policy. It never grants Read or Post. A cancel during identity lookup prevents saving.
On success, `consume_held(keep_result=True)` keeps a value-free done result for the
poll and disarms deletion of the newly saved profile.

The new-account wizard omits the flag and account id, performs no identity lookup,
and keeps its explicit Review/Save. Non-auto held callers also keep their existing
contract. The manual fallback keeps the existing dashboard-passcode gate.

## Card layout

`desk-v1-account-signin.js` projects messages separately from buttons. The account
detail places waiting, held, failed, saved, browser lookup/pane and manual-Save errors
above the shared action row. Buttons retain Check now, Edit, route sign-ins, Remove
order. A narrow card scrolls the single action row horizontally; the page does not
overflow. Smokes check both row ends, including access to Remove, at 390 pixels.

The module header, user guide and changelog describe this behavior. README, generated
agent instructions and rules need no changes: installation and agent conventions
are unchanged. New backend behavior lives in its own module; `desk_oauth.py` has
only lifecycle hooks and the optional done-result retention.

## Verification

Commands ran from the branch worktree. The scratch wrappers register their own PID
with Clayrune, then run pytest or the requested Node entry in the foreground. No
dependencies were installed: `tools/smoke/node_modules` is temporarily linked from
the main checkout. Cleanup was blocked by the unattended fence; the junction still
exists, so the required pre-commit cleanup and commit are blocked. No commit,
merge, push or restart has been performed.

| Exact command | Result |
| --- | --- |
| `python _scratch/run_pytest.py tests/test_desk_oauth.py tests/test_desk_oauth_hold.py tests/test_desk_oauth_hold_cleanup.py tests/test_desk_oauth_saved_guard.py tests/test_desk_oauth_replace.py tests/test_desk_connect_x_account_attach.py tests/test_desk_x_card_signin.py -o addopts= -q` | 134 passed, rc 0; final added handle-change regression also runs in the full Desk set |
| `python -u _scratch/run_pytest.py --desk -o addopts= -q` | 3,777 passed, 2 failed in 855.84 s, rc 1; all 122 `tests/test_desk*.py`, no exclusions |
| `python -u _scratch/run_pytest.py tests/test_desk_oauth_hold_cleanup.py::test_a_sign_in_minted_for_another_x_app_is_refused_before_anything_is_written tests/test_desk_oauth_saved_guard.py::test_a_held_sign_in_from_another_app_still_revokes tests/test_desk_x_card_signin.py -o addopts= -q` | 21 passed in 17.10 s, rc 0: both unchanged retries plus all 19 final card cases |
| `node _scratch/run_node.mjs tools/smoke/desk-v1-account-signin.mjs` | 100 checks passed across 1440/390, rc 0 |
| `node _scratch/run_node.mjs tools/smoke/desk-v1-connection-status.mjs` | Both viewport runs passed, rc 0 |
| `node _scratch/run_node.mjs tools/smoke/boot-smoke.mjs` | 7 boot scenarios plus guards passed, rc 0 |
| `node _scratch/run_node.mjs tools/smoke/desk-v1-connect-api-step.mjs` | 134 supported native wizard setup checks passed at 1440/390, rc 0 |
| `pyright mc/desk_connect/x_card_signin.py mc/desk_oauth.py mc/blueprints/desk_held_signin_routes.py` | 0 errors, 0 warnings, rc 0 |
| `git diff --check` | Passed |

The full Desk run is **not green**. Both failures are callback-port fixture
failures before any exchange or auto-save:

- `tests/test_desk_oauth_hold_cleanup.py::test_a_sign_in_minted_for_another_x_app_is_refused_before_anything_is_written`:
  start returns `409 port_busy` while trying to bind port 53682.
- `tests/test_desk_oauth_saved_guard.py::test_a_held_sign_in_from_another_app_still_revokes`:
  its initial fixture receives `port_busy`, then indexes the missing `auth_url`.

Both cases passed in the earlier unchanged OAuth/held run and in the final unchanged
retry together with the final card regression file; this retry does not
replace the full-run result. The known UDP-discovery flake did not fail here.

The historical `desk-v1-connect-held.mjs` alias imports the supported wizard entry,
but that entry's direct-execution guard means the alias executes no checks. It is
not counted as a pass above; the supported `desk-v1-connect-api-step.mjs` was run
directly instead. The new card smoke separately pins the wizard's held-state and
explicit-Save contract through the shared controller.

The initial new tests failed because the duplicate-callback assertion retried an
already-closed HTTP listener and the rollback retry reused a completed fixture's
request id. They now call the callback state machine directly for duplication and
give the new manual Save a distinct request id. No production gates were changed
to make these tests pass.

Screenshots are saved under `_scratch/x_card_autosave/c9a89734fb28/` in the main
checkout so worktree cleanup does not lose them:

| State | 1440 | 390 |
| --- | --- | --- |
| Held after lookup failure | `x_held_1440.png` | `x_held_390.png` |
| Automatically saved | `x_saved_1440.png` | `x_saved_390.png` |
| Callback error | `x_error_1440.png` | `x_error_390.png` |
| Manual Save error | `x_save_error_1440.png` | `x_save_error_390.png` |
| Known handle mismatch | `x_mismatch_1440.png` | `x_mismatch_390.png` |

Phone screenshots ending in `_actions_end_390.png` also show the row scrolled to
its final Remove button. Images were visually inspected at both widths. Completed
check logs and registration wrappers are archived alongside them.

All provider responses and browser panes in these checks are scripted. No named
profile is opened, no real X request/sign-in is made, and live integration remains
unverified. The supplied screenshots are real renders of this branch's static assets
against those stub responses.
