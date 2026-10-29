# Saving a replacement for a dead X sign-in

Branch candidate from `0c23f67c`, session `426ece4d0773` (Sol_Tobin).
No merge, push, server restart, real vendor sign-in, or operator vault access.

## Behavior

`desk_oauth.commit_held` previously refused every existing vault entry. A saved
account could say Needs sign-in, complete another sign-in, then fail Save with
`already_signed_in` and instructions to delete its saved entry in Secrets.

`mc/desk_oauth_replace.py` now owns the replacement check and compensating undo.
Only X entries whose hint says `state=needs_signin`, or a nonzero elapsed `exp`
without `refresh=yes`, can be replaced. Missing/malformed expiry alone is not
proof of a dead sign-in. An expired entry with a refresh token stays protected,
as does a live entry. Other services retain their existing-entry refusal.

The account refresh lock serializes token refresh against this Save. The vault
lock covers the existing-entry check, encrypted snapshot, write and undo
registration. The ordinary `_store_record` remains the token write path and
preserves scope and unattended policy. No browser or HTTP contract changes.

Undo holds the previous encrypted vault record only in memory, then restores
every field exactly. An entry created by this Save is removed instead. It
compares the current record against this Save's write before restoring, ignoring
usage counters only, following `secret_field_refs.create`. A later write is
left in place and reported through the existing UndoStack failure label.

Neither replacement nor Undo revokes the old or new tokens. MC-1117's cancel /
expiry / failed-write guard stays intact: a held X token sharing the saved
entry's app must not revoke the saved grant, even when the saved entry is dead.
Existing independent human/passcode gates, held claim and app checks remain.

## Verification

`tests/test_desk_oauth_replace.py` uses the real temporary vault and Desk store,
a scripted vendor and real loopback callback. Its callback port is ephemeral to
avoid Windows fixed-port TIME_WAIT across parameterized cases. Production X's
fixed callback configuration is unchanged.

New cases cover rejected and expired replacements, unchanged account/vault name,
retained policy, idempotent Save, live/refreshable/unknown-entry refusal, exact
rollback after a later Save failure, failed vault write with retryable hold,
shared-grant cancel protection, and refusal to overwrite an intervening change.

Final checks (run from this branch's worktree):

| Exact command | Result | rc |
| --- | --- | --- |
| `python _scratch/run_registered_pytest.py tests/test_desk_oauth_replace.py -o addopts= -q` | 11 passed in 6.67 s | 0 |
| `python _scratch/run_registered_pytest.py --oauth-connect -o addopts= -q` | 44 files, 1,723 passed, 2 failed in 661.44 s | 1 |
| `python _scratch/run_registered_pytest.py tests/test_desk_connect_real_chromium.py::test_the_discovery_pane_sends_no_udp_to_local_listeners tests/test_desk_connect_signin_reuse.py::test_without_the_reuse_the_same_save_is_refused_already_signed_in -o addopts= -q` | Unchanged isolated retry: 2 passed in 12.57 s | 0 |
| `python _scratch/run_registered_pytest.py --base-oauth tests/test_desk_connect_real_chromium.py::test_the_discovery_pane_sends_no_udp_to_local_listeners tests/test_desk_connect_signin_reuse.py::test_without_the_reuse_the_same_save_is_refused_already_signed_in -o addopts= -q` | Base OAuth control: 2 passed in 12.33 s | 0 |
| `pyright mc/desk_oauth.py mc/desk_oauth_replace.py` | 0 errors, 0 warnings | 0 |
| `git diff --check` | No whitespace errors | 0 |

The scratch runner registers its own PID with Clayrune, then calls pytest in the
foreground. `--oauth-connect` selects all `tests/test_desk_oauth*.py` and
`tests/test_desk_connect*.py` files, including `test_desk_connection_removal.py`.
It does not skip or alter tests. Equivalent selection without the registration
wrapper (not the command executed above):

```powershell
python -c "import glob,pytest; raise SystemExit(pytest.main(sorted(set(glob.glob('tests/test_desk_oauth*.py')+glob.glob('tests/test_desk_connect*.py')))+['-o','addopts=','-q']))"
```

For the separate `--base-oauth` control only, the runner loads the exact
`git show 0c23f67c:mc/desk_oauth.py` source into the OAuth module before pytest.
The two test cases and all other modules stay unchanged; this is not a full
baseline-branch suite run. Both cases also pass against that base connector.

The full run is **not green**. The Chromium UDP-discovery test failed with
`own_origin_blocked` before it could read its probe page; it does not call the
OAuth replacement path. The existing live-entry refusal test failed while its
fixture was creating its initial sign-in (`port_busy` on 53682, followed by
`KeyError: auth_url`), before reaching the held Save. Both pass in an unchanged
isolated retry. A port snapshot during the full run showed TIME_WAIT entries and
no listener on that port. The exact cause of the Chromium full-run failure and
the full-suite isolation issue are not established or repaired in this change.
The isolated retry is additional evidence, not a replacement for the rc=1 run.

The first run of the new tests used the fixed callback port: 9 passed and 2
failed during fixture setup with `port_busy` (rc=1). Their ephemeral callback
fixture then produced the 11-pass run above, and all 11 also passed in the full
run. No existing test file or production port policy was changed.

No frontend files changed; frontend boot/browser smoke is not applicable.
Authenticated vendor behavior is not exercised. No open product/design choice.
README and agent rules need no edits: installation and engineering rules did not
change. CHANGELOG and USER_GUIDE document the visible recovery behavior.
