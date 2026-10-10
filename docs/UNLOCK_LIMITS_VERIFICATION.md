# MC-1084: unlock limits

The dialog, guarded/open pill titles, and grant toast now say **eligible**
actions can pass. `static/js/attend-unlock-dialog.js` owns dialog markup;
`conversation.js` imports its opener and retains the existing submit/re-arm
handlers and server-status poll. No index script registration is required.

The dialog explains that browser-pane/HTTP calls made through transfer tools,
shell commands with chaining or special shell symbols, and shell/interpreter
wrappers cannot use either grant. The alternative is a fresh chat started by
the human. This follows `_is_plain_single_invocation`, `_PASS_META_CHARS`,
`_PASS_TRANSFER_HEADS`, and `_PASS_WRAPPER_HEADS` in `steward/fence.py`.
Quotes alone do **not** exclude a plain command: shlex permits, for example,
`git push origin "master"`. No classifier or grant policy was changed.

## Pending human-owned fence edit

The approval hook rejected editing `steward/fence.py`: "editing the steward
fence's own enforcement code (safety-rail supply chain ... human-owned)".
The current file is unchanged. The exact proposed change is
`docs/patches/fence-unlock-limits.patch`: a non-passable call says neither
mode covers the action and requires a fresh human-started chat; passable
calls retain their existing Allow-once guidance. It does not alter eligibility,
classification, consume behavior, or any human-only gate.

After the commit is integrated, a human in an attended environment can run:

```powershell
git apply --check docs/patches/fence-unlock-limits.patch
git apply docs/patches/fence-unlock-limits.patch
python -m pytest tests/test_fence_unlock_message.py tests/test_fence_unattended_arming.py tests/test_attend_once_window.py tests/test_attend_once_one_call_one_spend.py -q
```

Those apply commands were **not** executed against this checkout. The new
tests apply the exact patch to a pytest temporary copy, following MC-1055's
existing test pattern. They test passable versus non-passable messages,
zero pass-consumes for transfer/chaining/wrapper calls even with a grant,
plain quoted arguments, and steward/launcher eligibility. Eleven strict
expected failures against the current file expose the pending change; once
the human applies it, those same cases must pass normally.

## Verification on 2026-10-10

| Exact command (from the worktree root) | rc | Result |
| --- | --- | --- |
| `python -m pytest tests/test_fence_unlock_message.py tests/test_fence_unattended_arming.py tests/test_attend_once_window.py tests/test_attend_once_one_call_one_spend.py -q` | 0 | 330 collected: 319 passed, 11 strict expected failures for the unapplied current-file message. |
| `node tools/smoke/attend-handoff.mjs` | 0 | 1440x900 and 390x844; caveat visible and inside viewport, tooltip limits, wrong-passcode handling, Enter once, window, expiry poll, re-arm, no runtime errors. |
| `node tools/smoke/boot-smoke.mjs` | 0 | Seven boot scenarios plus dispatch, migration, model-picker, identity, backlog/links, memory, calendar, scheduler, question, Floor and face guards passed. |
| `node tools/smoke/inline-handler-scope-check.mjs` | 1 | Four existing scanner findings in untouched render-core/scheduler/workflow-builder files, from active/pending declarations in browser-input-timing.js. |
| `node _scratch/check-baseline-handler-scope.mjs` | 1 | Same four findings against every static/js source from base HEAD `0f37e492`; no new finding from this change. Scratch harness feeds HEAD sources into the unchanged checker. |
| `git diff --check` | 0 | Whitespace check passed. |

Smoke dependencies were a junction to the main checkout's existing
`tools/smoke/node_modules`; no install occurred. The hook rejected deletion;
the junction was moved intact to ignored `_scratch/mc1084-smoke-dependency-link`
before commit. `tools/smoke/node_modules` is absent and the main dependency
directory still exists. All browser tests use throwaway headless contexts and mocked API
responses, not named profiles or the live dashboard. Live delivery is unverified.

`docs/USER_GUIDE.md`, the guard spec, and CHANGELOG.md were updated. No new
standing rule or installation change warrants AGENTS.md/README changes. No
new/moved `mc/` module requires pyright. Branch-only handoff: no merge, push,
server restart, sign-in, or named-profile access. The task remains incomplete
until a human applies the fence patch and runs the current-file tests.
