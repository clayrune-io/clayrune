# Agent skill assignments (proposal, 2026-09-18, Dave)

Apply in the character editor (Skills field). Character writes are human-only
(`_refuse_if_agent_caller`, character_routes.py:102), so an agent cannot apply these.

Under `agent_skill_scoping_enabled`, a character's declared list is UNIONED with the
project's own `.claude/skills/`; every other GLOBAL skill drops to name-only.

Baseline for every character (Clayrune plumbing): `mc-clayrune-apis`, `mc-memory-search`, `mc-skill-broker`.

| Character | Type | Add to baseline |
|---|---|---|
| Dave | dave | mc-project-status, mc-position-review, document-commit-deploy, mc-changelog-update, mc-distill |
| Tobin | builder | document-commit-deploy, mc-changelog-update, verify-feature-schema-readiness, claude-cli-resume-system-prompt-isolation |
| Rusk | marlow-q | same as Tobin |
| Fenn | code-reviewer | audit-silent-exception-handlers, verify-feature-schema-readiness, validate-deployment-change |
| Bram | silent-failure-diagnostician | diagnose-silent-async-failure, diagnose-silent-async-worker, frontend-render-hang-diagnostic, committee-startup-diagnostics, audit-silent-exception-handlers, claude-cli-resume-system-prompt-isolation |
| Tilda | ui-fixer | apple-design, frontend-render-hang-diagnostic |
| Wren | security-privacy-auditor | gcp-billable-resource-enumeration, verify-feature-schema-readiness |
| Kestrel | generalist | mc-project-status, document-commit-deploy, mc-distill |
| Claydo | claydo | mc-project-status |
| Marlow | prd-writer | mc-project-status |
| Posy | social-media-strategist | video-shotcraft |
| Quill | market-researcher | (baseline only) |
| Halloway | market-scout | (baseline only) |
| Vance | us-stock-investor | engulfing-diagnostic, validate-deployment-change |

Left unassigned on purpose: `mc-steward` (loaded by the steward task marker, not a
character), `model-web-viewer` (3D projects only; stays name-only and loadable).

## Global `preference-*` skills

Never scoped: `effective_full_set` keeps every `preference-*` skill at full
description (Ron, 2026-09-18). They are conduct rules, not a toolkit, so no
character needs to list them.
