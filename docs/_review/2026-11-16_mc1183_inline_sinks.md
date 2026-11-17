# MC-1183: MC-1179 markup and inline-handler fixes

Branch candidate only. No merge, push, server restart or live data mutation.

## Scope and implementation

Dave selected a shared escaping helper and incoming field validation. Existing
handlers stay inline; existing stored invalid values stay on disk.

`static/js/js-attr.js` defines `window.jsAttr(value)`: contents of a
single-quoted JavaScript string, followed by HTML attribute escaping. It handles
backslashes, quotes, control characters and Unicode line separators. The early
classic script tag in `static/index.html` makes it available to the page's
classic script as well as ES modules, through `window`, before any renderer runs.

| Finding | Fix |
|---|---|
| FE13 | Escape backlog classes, status/priority handler arguments and badge text; project/domain colors; GitHub issue numbers; workstream priority text. Validate incoming store fields separately. |
| FE20 | Escape project-ID string arguments at inline sinks and prebuilt handler fragments across dashboard modules; validate URL/body project IDs on project writes. Existing human routing checks retain precedence for body edits. |
| FE2 | Floor rename preserves the figure's name as data. |
| FE3 | Hivemind Runs preserves the workstream title as data. |
| FE4 | Folder breadcrumbs, voice/secret action names, provider sign-in URLs and mobile pairing labels preserve their original strings. |
| CV1 | Un-hire escapes the raw persona name separately from its display text. |
| SK1 | Skill row/browser/editor handlers escape names/scope/project IDs; editor radio names HTML-escape the derived modal ID and selectors use CSS escaping. |
| SK2 | Folder/Git candidate handlers preserve Windows separators and quotes. |
| SK3 | HTML-escape the complete retry source after its arguments have been serialized as JavaScript; this keeps the Git/folder force button intact without double-escaping arguments. |
| MC1 | Repository env keys are escaped in the preview's input handler. |
| MC2 | Server names and derived editor IDs are escaped in row actions and editor handlers. |
| DK4 | Platform badge and derived platform-rules modal handlers escape their arguments. |
| WB11 | Replace raw-JS-only escaping at the cited inline attribute sinks with the shared helper. Other builder code is unchanged. |
| ST1 | Named-session rename uses an escaped single-quoted label argument. |
| BK1 | Restore uses an escaped single-quoted archive path argument in a double-quoted attribute. |

`mc/ui_field_validation.py` owns the new validation concern. Route glue is in
`mc/blueprints/project_routes.py`, `settings_routes.py` and `hivemind_routes.py`:

- Backlog status: `open`, `in_progress`, `blocked`, `done`, `wontdo`.
- Backlog priority: `low`, `normal`, `high`.
- Project IDs: `^[a-z0-9][a-z0-9_-]{0,63}$`; safe human-owned ID edits remain
  supported. The routing/passcode gate has not been weakened.
- Colors: `^(#[0-9a-fA-F]{3,8}|var\(--[a-z0-9-]+\))$`; project modal color
  may also be cleared with `null`.
- Workstream priority: an integer, rejecting strings, floats, booleans and
  containers, at inline-create, standalone-create and update routes.

## Verification

Before any production source edit, ran the new smoke through the installation's
test-run launcher:

```text
python <installation>/tools/test-run.py --project mission_control --port 5199 -- node tools/smoke/inline-handler-escaping.mjs
```

It exited **1**, with **16 !== 0** at the stored-markup assertion. The exact
production renderers emitted injected elements/attributes from hostile status,
priority, issue-number and color fields. The records existed only in the browser
fixture; nothing was written to a live store. After the fix, the same assertion
passes and the existing item remains visible. The final smoke also executes
real inline handlers against harmless capture stubs and checks exact round trips.

Final observed results:

- New regression smoke: **34 checks passed, 0 failed**.
- Route regression suite: **377 passed, 0 failed** across
  `test_ui_field_validation.py`, `test_project_routes.py`,
  `test_backlog_links.py`, `test_settings_routes.py`, `test_hivemind_routes.py`,
  `test_project_routing_guard.py` and `test_project_safety_settings.py`.
- Covering smokes: **76 distinct scripts: 73 passed, 3 failed**. The explicit
  74-script batch initially had 70 passes / 4 failures; loading the real helper
  in the provider-neutral VM fixture corrected one failure and its rerun passed.
  `mid-turn-send-ui.mjs` and `steward-role-editor.mjs` each passed separately.
- `boot-smoke.mjs`, `workflow-builder.mjs`, `skills-passcode.mjs`,
  `mcp-panel-gate.mjs`, `hivemind-roster.mjs` and the isolated real-PTY smoke
  all passed. The PTY smoke owned/registered its temporary server on its own
  port and data directory; the live server was not restarted.
- `pyright mc/ui_field_validation.py`: **0 errors, 0 warnings**.
- Syntax checks of all **35** new/modified JavaScript files: **35 passed**.
- `git diff --check`: clean.

All three remaining failures were reproduced with the **original, unchanged
source** archived from base `3f51b0e0f5129ea97f1ce1ba03a56dc0db8aa662` (both `static/`
and `tools/smoke/`), using the same original script argv:

| Script | Candidate and original-source result |
|---|---|
| `onboarding-multiselect-browser.mjs` | Exit 1: timed out waiting for the `Get started` button at line 150. |
| `onboarding-skip-rule.mjs` | Exit 1: VM cannot execute the existing import of `setup-model-preferences.js`. |
| `secrets-engine-labels.mjs` | Exit 1: the single `google buttons` expectation fails; the other checks pass. |

Those unrelated tests and their production paths were not changed for this
scope. The two helper-related harness failures were fixed by loading the real
shared asset in `delivery-status-visibility.mjs` and
`provider-neutral-rail-cache.mjs`; both corrected scripts pass.

Four live-data-dependent probes are unverified for this branch:

- `mobile-dave-chats-live.mjs`: uses the operator's running frontend.
- `floor-poll-survives-render-error.mjs`: attempted before discovering its live
  dependency; stalled on a live API read and the launcher timed out the child.
  Its partial output is not branch verification.
- `rail-no-empty-flash.mjs`: would dispatch a real paid agent against live data.
- `stop-hook-draft-collapse.mjs`: hybrid fixture depends on live API reads.

Test reporting printed capture/transport gaps. The counts above use observed
child exit codes and direct assertion output, rather than dashboard telemetry.
The explicit selection and per-script exit-code records/logs are retained in
`_scratch/final-smokes/` for review. The production changes are not deployed.

One initial route run also hit a raw-backlog-ID 404 which did not recur in the
final 377-test run. A separate in-memory check confirmed that an all-numeric
raw ID such as `12345678` is interpreted as a ticket number and returns `None`
in **both** the original helper and the candidate. This existing lookup issue
was left outside the named findings; it may explain that non-repeating failure.

No new user controls or workflow were added, so `docs/USER_GUIDE.md`, README and
agent instructions do not need changes. This is a correction to existing paths.

## Operational note

The hook refused the required plan write under the human-owned `.claude/plans`
directory in this unattended session. That path was not retried. The scoped
implementation plan and crash-recovery context are recorded in this report.

## Changed files

- `CHANGELOG.md`
- `docs/_review/2026-11-16_mc1183_inline_sinks.md`
- `mc/blueprints/hivemind_routes.py`
- `mc/blueprints/project_routes.py`
- `mc/blueprints/settings_routes.py`
- `mc/ui_field_validation.py`
- `static/index.html`
- `static/js/agent-console.js`
- `static/js/agent-log.js`
- `static/js/backup-panel.js`
- `static/js/beacon.js`
- `static/js/composer-extras.js`
- `static/js/conversation.js`
- `static/js/cross-backlog.js`
- `static/js/cross-hivemind.js`
- `static/js/cross-social.js`
- `static/js/desk.js`
- `static/js/documents-search.js`
- `static/js/engine-preferences.js`
- `static/js/floor.js`
- `static/js/hivemind.js`
- `static/js/js-attr.js`
- `static/js/mcp.js`
- `static/js/mobile-pairing.js`
- `static/js/modal-manager.js`
- `static/js/project-actions.js`
- `static/js/project-forms.js`
- `static/js/provider-auth.js`
- `static/js/render-core.js`
- `static/js/resume-preview.js`
- `static/js/schedule-banner.js`
- `static/js/schedule-calendar.js`
- `static/js/scheduler.js`
- `static/js/search-chats.js`
- `static/js/secrets-panel.js`
- `static/js/send-intent.js`
- `static/js/settings-sections.js`
- `static/js/skills-panel.js`
- `static/js/social-actions.js`
- `static/js/steward-role-editor.js`
- `static/js/steward.js`
- `static/js/workflow-builder.js`
- `tests/test_settings_routes.py`
- `tests/test_ui_field_validation.py`
- `tools/smoke/delivery-status-visibility.mjs`
- `tools/smoke/inline-handler-escaping.mjs`
- `tools/smoke/provider-neutral-rail-cache.mjs`
