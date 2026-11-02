---
name: mc-hivemind-routing
description: Decide whether a multi-part ask goes out as ordinary agent dispatches or as ONE Clayrune hivemind, by writing an explicit decomposition and validating it with mc.hivemind_routing.plan_routing. TRIGGER when you are coordinating work for other agents and the ask splits into two or more deliverables, when you are about to dispatch several agents for one goal, or when the user asks you to "use a hivemind" / "fan this out". Do NOT trigger for a single lookup or a single unit of work you can hand one agent.
---

# Clayrune: routing a decomposed ask (ordinary vs hivemind)

You are coordinating, not building. Before you send work to more than one
agent, you write the decomposition down explicitly and let
`plan_routing` decide where it goes. The validator is the gate; your own
count of "how many pieces" is not.

## 1. Decompose explicitly

Write one plan object. Independence is YOUR declaration: a unit with an
empty `depends_on` claims it can start from the current base without
waiting for any other unit. Only declare that when it is true.

Plan (top-level) fields, all required unless marked:

- `project_id`: the Clayrune project id.
- `goal`: one line describing the authorized deliverables.
- `max_concurrent_workers`: an explicit positive integer. Use the capacity
  the user or the project configuration authorized. Never raise it to make
  a plan go faster; `1` is valid and is respected.
- `reviewer_character` (required when the result is a hivemind; validated
  whenever supplied): the agent that reviews the integrated result, e.g.
  `global:code-reviewer`. The reviewer is never a unit and never counts as
  a worker.
- `units`: the list of work units below.

Unit fields:

- `id` (required): short id, letters/digits/`_`/`-`, unique in the plan.
- `title` (required): one line.
- `description` (optional): the brief the worker will read.
- `character` (required): a real roster reference, `global:<type>`, for an
  agent type that exists right now. Check the roster first
  (`GET /api/characters?project_id=<id>`); do not invent one.
- `owned_files` (required, nonempty): concrete repository-relative FILE
  paths this unit alone may change. No directories, globs, absolute or
  `..` paths. Two units may not own the same file.
- `depends_on` (required, may be empty): ids of units that must land first.
- `checks` (required, nonempty): acceptance checks as text, e.g. the exact
  test command a reviewer should see pass.
- `smokes` (optional): bare `.mjs` smoke suite names, e.g. `boot-smoke.mjs`.
- `role` (optional): may only be `worker`.
- `work_type` (optional): `code`, `review`, `research`, `ui`, `analysis` or
  `other`.
- `complexity` (optional): `simple`, `standard` or `hard`.

Any other field is refused, including engine or model overrides.

## 2. Validate

`plan_routing` is a Python API in a Clayrune source checkout (there is no
HTTP route or CLI for it). Run it from the Clayrune checkout with an
explicit character lookup, the app configuration and the project record:

```bash
python - <<'EOF'
import json, os
from pathlib import Path
from mc import characters
from mc.hivemind_routing import plan_routing

root = Path(os.environ.get('MC_DATA_DIR', '.'))
config = json.loads((root / 'config.json').read_text(encoding='utf-8'))
project = json.loads((root / 'data' / 'projects' / 'PROJECT_ID.json').read_text(encoding='utf-8'))
plan = json.loads(Path('_scratch/plan.json').read_text(encoding='utf-8'))
result = plan_routing(
    plan, app_config=config, project=project,
    read_character=lambda name: characters.read_character('global', name, include_body=False))
print(json.dumps(result, indent=2))
EOF
```

Write the plan to a scratch file first (`_scratch/` is gitignored). If the
checkout or these files are not available, stop and say so; do not guess a
route by counting units yourself.

## 3. Act on `route`, exactly once

The result has `route`, `independent_count`, `independent_units`,
`ordinary_units`, `held_units`, `payload`, `errors` and `warnings`. The
destinations are mutually exclusive:

- `ordinary` (0, 1 or 2 independent units): dispatch each entry of
  `ordinary_units` as its own agent session, with its `owned_files` and
  `checks` in the brief. There is no payload.
- `hivemind` (3 or more independent units): POST `payload` ONCE, unchanged,
  to `/api/hivemind/create`. Do NOT also dispatch any of those units
  yourself; the hivemind owns them. `ordinary_units` is empty.
- `invalid`: dispatch nothing. Read `errors` (each has `code`, `units`,
  `message`, plus `index` for a malformed unit), fix the plan, and validate
  again. Never dispatch the part of an invalid plan that looked fine.
  Typical codes: `ownership_overlap`, `missing_dependency`,
  `dependency_cycle`, `duplicate_id`, `invalid_unit`, `invalid_reviewer`,
  `invalid_plan`, `invalid_roster`.

Report any `warnings` (for example a missing character that will fall back
at attempt time) to whoever you report to.

## 4. Keep the held units

`held_units` are the dependent units (including transitive dependents),
each with its full brief and a `hold_reason`. They were NOT sent anywhere.
They stay your responsibility: list them in your report as remaining work,
and arrange them only after the units they depend on have landed and been
reviewed. A created hivemind does not complete a mixed ask.

## Rules

- One route per validated plan. Re-validate after any edit to the plan.
- Starting several agents at once is a cost decision. A valid hivemind
  result says the plan is well formed, not that fan-out was authorized;
  make sure the user's ask covers it.
- The validator does not run checks, read files or judge whether two
  units are really independent. That judgment stays with you.
