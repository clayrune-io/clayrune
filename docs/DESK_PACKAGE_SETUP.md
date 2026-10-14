# Package connection setup (MC-1095)

The package and GitHub paths share Connection details, Permissions, Review and Result. Read-only lookup shows a spinner and status and disables Find/Continue. Changing the address invalidates late answers. The public vocabulary layer only rewrites UI copy, preserving rendered URLs, package names, commands and saved names. Fixed interpolated prose must mark values separately; `data-connect-value` is always verbatim, and `data-connect-copy` opts fixed prose into public vocabulary.

## GitHub start inference

`mc/desk_connect/github_start_command.py` reads package.json without running code. A single Node bin wins; otherwise a plain two-token `node <local-file>` scripts.start can be used. Paths must be local JavaScript files with no traversal. An absent dist/build/lib file is suggested only when a build script exists. No registry launcher, shell expression, arbitrary start script or ambiguous bin is inferred. An unrecognized package keeps manual entry.

Stage projects the program, argument file, source, build_required and required_steps. A build notice tells the human to select dependency/build steps in Review. All steps still start unchecked and run only after passcode Save. The npm-only optional start-file input is omitted on GitHub; explanatory sentences and labels live in the shared copy set.

## GitHub Save steps (MC-1097)

`github_install_steps.declared_scripts` offers only preinstall, install, postinstall, prepare, build, compile, build:* and compile:*. Dependencies precede lifecycle scripts, then base build followed by its alphabetically ordered variants, then base compile and its variants. Declaration/checkbox selection order cannot change execution order. Other package scripts are omitted and cannot be selected by a forged review request. This policy applies only to GitHub; custom_npm_* keeps its own registry lifecycle rules.

Inferred required_steps is retained in staged metadata and copied into the fingerprinted operation and card. Initial Review is available with every step unchecked. The UI labels a required build and disables Save with a plain explanation while it is unselected; check_submission refuses missing approval before the human passcode gate, and provision also checks. No auto-selection, automatic execution or weaker human gate is introduced.

GitHub provisioning reports a step failure as one sentence naming dependency setup or the declared script, leaving detailed output in the server log. Package Result is rendered by `desk-v1-connect-package-result.js`; setup_failed says Not saved and suppresses saved-only vault/start notes. The approval record can retain a failed attempt for recovery, but no MCP server is registered by that failure. The shared passcode dialog reuses the lookup spinner above its disabled controls. Generic status is used: the synchronous commit endpoint has no inexpensive per-step progress feed.

Regression coverage: `tests/test_desk_connect_github_install_steps.py`, the existing GitHub/connect suites, and `tools/smoke/desk-v1-connect-package-save.mjs` (required selection, duplicate/cancel refusal while busy, cleared input, failed Result and 1440/390 captures). The isolated browser harness uses actual shipped modules/CSS and stub API replies; it never sends a real passcode or Save.

Read-only live metadata verification: ZubeidHendricks/youtube-mcp-server at `36142e30de15841139ae9613c568e0bd09aef372` produced `node` + `dist/cli.js`, build_required true, required step script:build, without installing or executing package code. Source: <https://github.com/ZubeidHendricks/youtube-mcp-server/blob/36142e30de15841139ae9613c568e0bd09aef372/package.json>.

## Help me fill this in

Separate modules own the feature: `package_setup_helper.py`, its route blueprint and `desk-v1-connect-package-helper.js`. The package screen only supplies draft fields and accepts suggestions.

`POST /api/desk/connect/custom/setup-help` is human-requested/read-only. Request: exactly one stage_id or npm package, question (1200 characters), optional previous_answer (3000), fields {command, entry, args, credentials: [ENV_NAME]}. Unknown keys, secret-like values and invalid field shapes are refused. No vault name/value, passcode, user-chosen README or arbitrary URL is accepted. Credential contents are never read.

GitHub evidence comes from an unexpired, unchanged staged clone; inventory checks run before and after bounded reads. package.json is capped at 16,000 characters and root README at 18,000. npm uses the existing guarded fixed-registry metadata reader, with no repository/page/tarball fallback. Only this isolated transform sees raw evidence.

Production uses `run_text_transform('claude', ...)`, the authorized seam for `ClaudeRuntime.oneshot`, matching mail laundering. That runtime denies tools/MCP/plugins/hooks, uses a neutral working directory and has no skip-permissions. No dispatch or alternate runtime is used. Model timeout is 60 seconds; one help request at a time. Invalid JSON/shapes/values, missing Claude or source/transform failures return a visible error, never raw model output or a fallback reader. Exceptions log their type without evidence. Suggestions remain untrusted data and carry a warning.

The response is `content: {warning, answer, suggestions: {command, entry, args, credentials}}`. GitHub proposals cannot use registry launchers; npm's command is chosen by Review. Reserved loader/environment variables and nonlocal entry paths are refused. Suggested commands/arguments are never executed by help. A human reviews all proposed values, then explicitly applies them into empty draft fields. Existing edits and vault references remain intact; missing credential names can be added with blank saved-credential references. The helper invalidates a prior review card if its suggestions are accepted. Package changes/close discard late answers. Follow-up questions include only the previous explanation as untrusted data, with no durable conversation storage.

No commit, install, approval, credential write, browser sign-in, or passcode bypass is exposed by help. Review still builds an immutable operation; script selection and the existing human/passcode Save remain unchanged.

A successful explanation is paired with the deterministic GitHub program/file proposal when the model omits it. “No extra options” must not drop Node's required start-file argument. This does not replace a failed/unavailable model call; failures still return an error with no proposals.

## Validation and integration

Five ordered branch commits, no merge/push/restart. Backend suite: all `tests/test_desk_connect*.py`; frontend: every `tools/smoke/desk*connect*.mjs` and boot-smoke. New focused smoke: `node tools/smoke/desk-v1-connect-package-progress.mjs`, delayed Find/Continue/npm, error recovery, stale answers, verbatim values, helper acceptance/edit preservation/follow-ups and no writes at 1440/390. The full simplify smoke also captures progress/helper in the actual Desk shell.

Live tool-free helper was exercised on bounded public YouTube metadata/README without install or Save. Authenticated YouTube operation, a build of the external package, and live dashboard integration are not claimed. Dave owns merge and review; server restart is a separate human-only step. Verification matrix and screenshots are recorded in the task journal under docs/_journal/24049aa7-package-setup.md and scratch artifacts for this branch.

Final checks: 1,448 backend tests passed in the full desk_connect selection (rc=0), followed by 27 focused helper tests after the final program/file and multiline-prose adjustments (rc=0). All 28 named Desk connect/connection smoke entries and boot-smoke passed. Two historical smoke aliases initially failed an old Vault wording assertion; its expected public wording was corrected and both reruns passed. The original aggregate runner preserves its rc=1 and first-attempt logs; rerun logs record the resolved checks. Final simplify rerun also verifies helper acceptance can be reached above mobile's pinned actions. Pyright on the three new Python modules: zero errors (rc=0). The only server/index changes register the separate blueprint/stylesheet; feature bodies do not grow those monoliths.
