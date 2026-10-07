# Studio render passcode replay investigation

2026-10-07. Follow-up to the shared prompt busy fix `3f7b689c`.

## Result

The old prompt's missing in-flight feedback and repeat-submission guard are
reproduced. The existing busy fix resolves both. A separate failure to close
on HTTP 200 is **not reproduced**, including on the source immediately before
that fix. No additional production change is justified by these results.

The new smoke covers the real Studio engine caller and real proof prompt
together. `desk-v1-live-render.mjs` replaces `window.humanProofFetch`, so its
existing coverage cannot detect regressions at that boundary.

## Source trace

- `static/js/human-proof-modal.js`: `_hpSubmit` previously cleared the input
  and waited for fetch without a busy guard or a visible in-flight state.
  Re-entering the passcode sent the same action body/idempotency key again.
  The existing `p.busy` check and `_hpShowBusy` now prevent that and display
  Working while the server finishes.
- `_hpSendWithPasscode` re-prompts only on the explicit guard error/status
  pairs (403 passcode required/bad passcode, 429 too many attempts). Both
  HTTP 200 and 201 reach `_hpCleanup`, which deletes the pending entry,
  clears the proof overlay class, closes the modal, and resolves the caller.
- `static/js/desk-v1-engines.js`: `_humanPost` accepts any successful HTTP
  status and returns the parsed body. Video `submit` assigns `out.render`
  and starts progress polling; there is no 201-only or replay-only branch.
- `mc/blueprints/desk_routes.py`: the render route returns
  `{render: out, replay: replay}` with 200 for replay and 201 for creation.
  `mc/desk_engines.py` reuses the render for the same idempotency key.
- A repeated `_hpSubmit` operates on the same modal ID. It does not call
  `humanProofFetch` and does not create another modal. Both historical and
  current browser runs confirm no stacking in this path.

## Regression

Run `node tools/smoke/desk-studio-passcode-replay.mjs`.

The smoke loads this checkout's page and modules, mounts the actual video
engine panel inside the Desk, and intercepts every network request. No live
service, credential, vendor, or paid action is used. At 1440px and 390px it
tests both delayed HTTP 201 creation and HTTP 200 replay replies:

1. A wrong passcode re-prompts, clears the field, and permits correction.
2. An accepted request displays busy feedback and retains the Studio body.
3. Re-entering during that slow request sends no additional POST and creates
   no additional prompt.
4. The answer removes the prompt and overlay class, displays the render,
   disables another start, and polls that render to ready without exceptions.

For historical reproduction, write the output of
`git show 3f7b689c^:static/js/human-proof-modal.js` to a UTF-8 scratch file,
then run the smoke with `--proof-source=<scratch-file>`.
That substitution is explicit and limited to the proof module; other assets
come from the current checkout. A second accepted POST gets an immediate
HTTP 200 replay while the original reply remains held.

Historical result: **rc=1, eight failures** (missing busy feedback and an
extra accepted POST in all four cases). In all four historical cases, the
immediate duplicate 200 leaves **zero prompts before the original reply**,
and the caller displays and polls the render correctly. Current result:
**rc=0, all 36 checks pass**.

## Evidence boundary

Server response logs establish that requests ran; they do not establish
whether the originating page received and processed the reply. The incident
has no captured client exception, pending-prompt inventory, or response-body
trace. The report therefore does not attribute the observed stuck UI to 200
handling or stacked prompts. Neither hypothesis matches this reproduction.

If the issue persists after the busy fix, capture the actual page's console
errors, modal IDs/count, Network response completion/body, and whether it
reloaded or navigated while the original render was pending. Inspect that
existing render rather than starting a paid render solely for diagnosis.

## Delivery and rollback

This follow-up contains test/documentation changes only. No merge, push, or
server restart is part of the handoff. The user guide, shared guard spec,
and agent rules need no change: no new behavior, gate, or convention is
introduced. Reverting the follow-up commit removes only its smoke and report;
the production busy fix remains independently revertible as `3f7b689c`.

## Verification matrix

All 33 smokes returned rc=0. The new smoke was rerun after its final edits;
the historical run was also repeated and returned the same expected eight
failures (rc=1). JavaScript syntax and `git diff --check` passed.

| Smoke | rc |
| --- | --- |
| `desk-studio-passcode-replay.mjs` | 0 |
| `human-proof-busy.mjs` | 0 |
| `desk-v1-live-render.mjs` | 0 |
| `agent-read-request-ui.mjs` | 0 |
| `boot-smoke.mjs` | 0 |
| `brainstorm-handoff.mjs` | 0 |
| `codex-sandbox-setting.mjs` | 0 |
| `desk-v1-connect-browser-permission.mjs` | 0 |
| `desk-v1-connect-signin-fill.mjs` | 0 |
| `desk-v1-connect-summary-step.mjs` | 0 |
| `desk-v1-library.mjs` | 0 |
| `desk-v1-live-review.mjs` | 0 |
| `desk-v1-vault-gate.mjs` | 0 |
| `desk-v1-vault-locked-card.mjs` | 0 |
| `engine-fallback-settings.mjs` | 0 |
| `first-run-backup.mjs` | 0 |
| `first-run-provider-chooser.mjs` | 0 |
| `first-run-setup-gate.mjs` | 0 |
| `human-proof-guard.mjs` | 0 |
| `masked-secret-prompts.mjs` | 0 |
| `mcp-panel-gate.mjs` | 0 |
| `onboarding-multiselect-browser.mjs` | 0 |
| `passkeys-assert.mjs` | 0 |
| `passkeys-settings.mjs` | 0 |
| `provider-choice-no-popup.mjs` | 0 |
| `secrets-editor-ux.mjs` | 0 |
| `secrets-engine-labels.mjs` | 0 |
| `settings-backup-schedule.mjs` | 0 |
| `settings-providers.mjs` | 0 |
| `settings-update-row.mjs` | 0 |
| `team-card.mjs` | 0 |
| `vault-unlock-link.mjs` | 0 |
| `workflow-builder.mjs` | 0 |
