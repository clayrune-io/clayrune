# Project routing guard (MC-1080 / f0d66b8e)

Branch build only. No merge, push, server restart, or live settings changes.

`POST /api/project/<project_id>` protects the exact body keys `id` and
`project_path` through `mc/project_routing_guard.py`. The route calls this
after the existing MC-1076 safety-key guard and record-existence check, before
record reads, workspace creation, activity logging, persistence or lifecycle
updates.

Any submitted routing key counts, including an unchanged value or `null`.
An agent receives HTTP 403 with an error naming the keys and a sorted
`protected_keys` array. Mixed bodies are refused in full. The guard reuses
`project_safety_settings.is_agent_caller`: absent Origin means agent; with
Origin, managed OS process-tree attribution still means agent. Attribution
runs before the passcode helper, so an attributed agent is refused even with
a valid passcode, `source: ui`, or `client: mobile`.

A human browser write containing either key on an **existing project** must pass
the existing `secrets_routes._require_human_passcode` check. No configured dashboard
passcode yields `403 passcode_required`; missing/wrong/malformed proof yields
`403 bad_passcode`; the existing shared guessing budget yields
`429 too_many_attempts`. Existing-record proof has no approval window or
alternate passcode store. The route removes `passcode` before saving any body,
even on ordinary writes; it never becomes project data.

**Creation exception (Ron, 2026-10-16):** if the project record does not exist,
a human browser request skips the passcode helper, including when no dashboard
passcode is configured. Agent attribution still runs first: absent Origin or an
attributed agent submitting either routing key receives 403, even for a new
project. If a concurrent request creates the record before the locked save,
the route rechecks the existing-project guard with the original request proof
before updating that record. It never persists that proof.

Ordinary body fields remain writable by agents and skip routing attribution
and passcode checks. New projects created without either routing key keep
their server-generated URL id and automatic workspace behavior. Human id
writes keep the route's previous semantics; this ticket does not impose an
id-equals-URL invariant. Direct disk edits, other project-mutating endpoints,
and the existing attribution limitations are outside this guard. With
unavailable attribution, forged Origin still reaches the passcode check for
existing records.

**Accepted residual risk:** a request with a forged browser Origin and no
process attribution can now create a new project pointing at any folder
without a passcode. The install-directory refusal still applies. Ron explicitly
accepted this creation-only risk on **2026-10-16**. Existing-project routing
edits still require passcode proof; every other human-only gate is unchanged.

## Dashboard callers

Creation uses plain `fetch` with the original JSON body and no proof prompt:

- `project-forms.js`: Create, including automatic workspace creation.
- `claude-import.js`: both first-run and standalone Claude project import.
  Selected projects are submitted in sequence; failed additions remain selected
  with the server's reason and successful additions leave the list.

Existing-project `saveProjectPath` (typed/on-blur edits) and `fpSelectCurrent`
(Browse / Use this folder) keep the shared `humanProofFetch` prompt and
per-request dashboard proof. Typed edit cancel restores the saved folder;
folder-picker cancel keeps the chosen folder open. Failed workspace saves
show the server error and do not report success. The existing prompt offers
first-use passcode setup; its host-only gate remains unchanged.

This Import is the Claude project-registration flow posting workspace routing
to the project CRUD endpoint. The legacy `/api/project/<id>/import` action
reads that project's existing CHANGELOG and does not write routing.

## Verification

Backend tests use the real blueprint, attribution seam and passcode helper
with temporary local-auth/project files. They verify absent and forged
Origin agent refusal, changed/unchanged/null values, mixed bodies, refusal
before lifecycle/workspace/save calls, missing/unconfigured/malformed/wrong/
throttled proof, human Create/Import without proof (configured and unconfigured
passcodes, unattributed and unavailable attribution), Edit acceptance with
proof, concurrent-create rechecks, ordinary agent writes, and proof absence
from records. Existing CRUD edits supply isolated human proof; new-project
regressions exercise the real guard without proof.

`project-routing-guard.mjs` loads real checkout HTML, static modules, handlers
and passcode DOM with a canned API. It uses real prompt button clicks at
1440 and 390 widths, checks Create/Import without proof or a prompt, original
request bodies, existing-workspace cancel/no-write, wrong/correct proof,
first-use passcode setup, folder refusal visibility, and partial import
failure. The unrelated optional diagram CDN is
stubbed; no real passcode, server, project data or network is used.
`claude-import.mjs` covers full first-run and standalone import and asserts
proof is absent at its mock wire boundary. Backend and UI are verified
separately; no human save against the running production server is claimed.

Validation on 2026-10-07, worktree `clayrune/agent/1e0ebb030a74`:

```powershell
python -c "import os,pytest; os.environ['PATH']=r'C:\Program Files\Git\bin'+os.pathsep+os.environ['PATH']; raise SystemExit(pytest.main(['tests/test_project_routing_guard.py','tests/test_project_safety_settings.py','tests/test_project_routes.py','tests/test_human_proof_guard.py','tests/test_merge_review_gate.py','tests/test_caller_attribution.py','-o','addopts=-ra','-q']))"
pyright mc/project_routing_guard.py
node tools/smoke/project-routing-guard.mjs
$env:SHOTS='0'; node tools/smoke/claude-import.mjs
node tools/smoke/human-proof-guard.mjs
node tools/smoke/boot-smoke.mjs
git diff --check
```

All commands returned **rc=0**. Backend: **280 passed in 65.38s**, no skips;
one pre-existing invalid-escape SyntaxWarning in `tests/test_agent_routes.py`.
Pyright: **0 errors, 0 warnings**. Routing smoke: all ten desktop/phone case
groups passed. Import: all eight scenarios passed. Shared human-proof:
ALL PASS. Boot: seven boot scenarios plus all dispatch/first-run/model/
identity/backlog/memory/calendar/scheduler/question/Floor guards passed.

The first focused backend command also returned 0:
`python -m pytest tests/test_project_routing_guard.py tests/test_project_safety_settings.py tests/test_project_routes.py -q`.
Initial routing smoke failures were harness defects: lexical `allProjects`
looked up on window, an unstubbed optional diagram CDN, and a hidden desktop
grid selected as visible on mobile. The first import run selected
`input[type=password]`, but the shared MaskedInput uses its own id/type.
The harnesses now observe the actual state/input ids and retain real clicks.
No application bypass or guard weakening resolved these failures.

Smoke dependencies came from a temporary junction to the main checkout's
already-installed `tools/smoke/node_modules`; no packages or browser builds
were installed. The junction is removed before committing. Integration and
live human acceptance remain Dave's handoff; this build does not claim them.

## Creation-exception validation (2026-10-16)

Sol_Tobin, branch `clayrune/agent/fea8724e8fc7`. Exact commands run from the
worktree root (PowerShell); every command returned **rc=0**:

```powershell
python -c "import os,pytest; os.environ['PATH']=r'C:\Program Files\Git\bin'+os.pathsep+os.environ['PATH']; raise SystemExit(pytest.main(['tests/test_project_routing_guard.py','tests/test_project_safety_settings.py','tests/test_project_routes.py','tests/test_human_proof_guard.py','tests/test_merge_review_gate.py','tests/test_caller_attribution.py','-o','addopts=-ra','-q']))"
pyright mc/project_routing_guard.py
node tools/smoke/project-routing-guard.mjs
$env:SHOTS='0'; node tools/smoke/claude-import.mjs
node tools/smoke/human-proof-guard.mjs
node tools/smoke/boot-smoke.mjs
git diff --check
```

- Backend: **295 passed**, no skips, in **79.18s**. One pre-existing
  invalid-escape SyntaxWarning in `tests/test_agent_routes.py:2358`.
- Pyright: **0 errors, 0 warnings** (separate CLI update notice only).
- Routing smoke: **10 case groups** at 1440/390, all passed.
- Import smoke: **8 scenarios**, all assertions passed, no screenshots written.
- Shared human-proof smoke: **19 checks**, ALL PASS.
- Boot smoke: **7 boot scenarios + 14 guards**, all passed.

Smoke dependencies used a temporary junction to the main checkout's installed
`tools/smoke/node_modules`, removed before commit. No npm install or browser
download. These browser checks load the branch's real HTML/modules against
canned APIs; backend checks use isolated local state. No live production save,
merge, push or restart was performed. No new dependencies or unrelated refactor.
The changelog, user guide and API reference were updated together; no new
agent rule, installation step or README instruction is needed.
