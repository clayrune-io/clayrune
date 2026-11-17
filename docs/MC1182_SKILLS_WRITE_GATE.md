# MC-1182: Skills write gate

Branch candidate addressing MC-1179 finding ML1. No merge, push, live skill
mutation, deployment, or server restart was performed.

## Changed files

| File | Change |
|---|---|
| `mc/skill_mutation_gate.py` | Shared HTTP gate; calls `is_unattended_caller()` first, then the existing `_require_human_passcode(body)` |
| `mc/blueprints/skills_routes.py` | Gate at the start of all 11 mutations; restore translates authority refusal to HTTP 400 |
| `mc/skill_authority.py` | Reuses Distiller's `_authority_violation(description + '\n' + body)`; raises `ValueError` with the matching reason |
| `mc/skills.py` | Authority check before `write_skill`, folder/staging copying, archive restoration, and all plugin instruction copies |
| `static/js/skills-panel.js` | Reuses `humanProofFetch` for every Skills mutation, including cross-project copy, force retries and staging cancel |
| `tests/test_skills_mutation_gate.py` | 72 route and sink regressions using temporary stores |
| `tools/smoke/skills-passcode.mjs` | Hermetic browser checks of real Skills handlers and the existing confirmation modal |
| `docs/USER_GUIDE.md` | Describes confirmation, initial passcode setup, authority refusal and read access |
| `CHANGELOG.md` | Dated change entry |
| `docs/MC1182_SKILLS_WRITE_GATE.md` | Review record and verification |

## Gated routes

Every row uses the same ordered gate. No GET route was changed.

| Method | Route |
|---|---|
| POST | `/api/skills` |
| PUT | `/api/skills/<scope>/<name>` |
| DELETE | `/api/skills/<scope>/<name>` |
| POST | `/api/skills/archive/<name>/restore` |
| POST | `/api/skills/import/paste` |
| POST | `/api/skills/import/folder` |
| POST | `/api/skills/import/git` |
| POST | `/api/skills/import/plugin` |
| POST | `/api/skills/import/git/install` |
| POST | `/api/skills/import/git/cancel` |
| DELETE | `/api/skills/quarantine/<qid>` |

The route matrix also compares this inventory with Flask's registered mutating
Skills routes, so a new route cannot silently fall outside its coverage.
Each row exercises unattended refusal before the passcode check, forged Origin
with missing and wrong passcodes, correct-passcode mutation, and unconfigured
passcode refusal. Refused requests leave the temporary filesystem unchanged.

## Installation paths

`write_skill` is the sink for create, update, paste, cross-project copy and
Distiller promotion. Folder and staged Git imports use `_install_skill_dir`,
which copies a directory directly; that sink now checks the same authority
rail before copying or removing an existing destination. Archive restore
checks the archived instruction before moving it back into the active pool.
Plugin installation preflights every skill, command and agent instruction
before copying any component. `force` overrides only the existing scanner;
it cannot override this check. Authority refusal becomes a clear HTTP 400
with the matched reason on forced imports and normal writes; the scanner may
also reject unforced imports with its existing HTTP 422 quarantine response.

Startup is server-internal:
`skills_routes._install_builtin_skills()` calls `skills.install_builtins()`
directly, without an HTTP route or request context. It retains its checksum
install/update/preserve behavior without a configured passcode. A regression
exercises all three cases. No exemption flag or alternate agent write route
was introduced. Human-approved Distiller promotion still calls `write_skill`;
tests prove benign promotion succeeds and authority expansion refuses before
the proposal is marked promoted.

The authority phrase matcher and the unattended-caller heuristic were reused
unchanged. This patch does not resolve ML3's broader phrase coverage finding.

## Verification

All test commands used the project Python interpreter and the reported-tests
launcher. The portable equivalent of the launcher prefix is:

```text
python tools/test-run.py --project mission_control --port 5199 --
```

The exact child argv for the combined regression run:

```text
python -m pytest -o addopts=-ra tests/test_skills_mutation_gate.py tests/test_character_skills.py tests/test_skill_usage_stats.py tests/test_skill_scoping.py tests/test_skill_import_guard.py tests/test_skills_staging_path.py tests/test_distiller_safety.py tests/test_distiller_promote.py tests/test_distiller_promote_unattended_gate.py tests/test_distiller_reframe.py tests/test_steward_fence.py tests/test_codex_steward_fence.py
```

Observed output:

```text
collected 1190 items
1190 passed in 35.18s
```

No failed or skipped tests. Report capture emitted
`[test-run] telemetry_queue_full_progress_incomplete`; framework assertion
counts above come from pytest stdout, not the launcher's script-unit telemetry.
The initial focused 72-test run also exited 0; the combined run includes it.

Reported smoke commands:

```text
node tools/smoke/boot-smoke.mjs
node tools/smoke/skills-passcode.mjs
```

Boot smoke exited 0: seven boot scenarios and fourteen additional guard
functions returned true (21 checks, zero failures). Its printed summary:

```text
PASS — 7 boot scenarios + dispatch, first-run-migration, model-picker, identity-prefill, backlog, backlog-links, memory-panel, calendar, scheduler-layout, question-repaint, floor & agent-face guards all green.
```

The two Claydo guards also printed successful results but are omitted from
that harness's existing summary text.

Skills smoke's final output:

```text
19 passed, 0 failed
```

It checks create/edit, archive/delete/restore, paste quarantine and force
retry, folder import, staged Git install, plugin import, staging cancel,
cross-project copy, wrong-passcode retry, cancellation, first-time passcode
setup, and absence of browser runtime errors. Its first run exposed an
incorrect test expectation that setup leaves the next confirmation input
empty; the existing shared modal intentionally prefills the newly set code.
The corrected test preserves that behavior and passed in full.

`pyright mc/skill_mutation_gate.py mc/skill_authority.py`:
`0 errors, 0 warnings, 0 informations`. `node --check` on the edited panel
passed. `git diff --check` passed.

All browser API responses were intercepted; Python writes used temporary
stores. Real remote Git transport and live installed-user state were not
exercised. Existing clone transport was stubbed; folder/staging/plugin store
operations ran against real temporary files. Playwright was reused from the
main checkout through a temporary node_modules junction, without installing
dependencies; the junction is removed before committing.
