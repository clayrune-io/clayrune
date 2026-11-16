# Rollover thresholds by model and character (MC-1072, MC-1178)

Backlog: a2ac49d0. Status: local implementation; not merged, pushed or live.

`mc/rollover_threshold.py:threshold_for(session_or_character, config)` is the
shared resolver. It accepts a character reference (`scope:name`), character
metadata (`name`, optional `scope`, default `global`), or a session holding
that metadata under `character`. It uses the stored character name, never
the displayed agent name or a Floor label.

`context_rollover_by_character` is a map of character refs to token counts.
No configured override means `context_rollover_tokens` (default 200000).
Invalid map/entry values fall back to that global value. Invalid global
values fall back to 200000. Integer strings and whole numeric counts work;
booleans, fractional counts, non-finite numbers and other shapes do not.
Active thresholds clamp to 60000 without rewriting config. A nonpositive global
value disables the token trigger for every character, including pinned ones.
Remove an override to restore global behavior.

The map is exposed by the existing `/api/config` GET/PUT allowlist. The
existing config-edit passcode and unattended-caller guards still apply.
There is no new UI editor or repository config entry; values are install data.

All token-trigger call sites carry their session/character identity: Claude
dispatch/resume, live/dead follow-up, Mode A follow-up, interrupt, and mid-turn
tool boundaries. Mid-turn durable logs record the effective threshold.
`mc/agent_runtime.py` normalizes usage only; it does not read a threshold.
Unknown token counts still use the existing transcript-byte backstop.
Mid-turn enablement, pending-tool gates, retry growth/backoff, and handoff
behavior remain governed by their existing code.

## Model-aware resolution (MC-1178 v1)

Status: branch candidate, not merged or deployed. The same resolver accepts a
session with `observed_model`, or `model` (falling back to `agent_model` only
when `model` is absent), and `_mt_fresh_floor` from the segment's first usage
reading. Dated Claude IDs and a trailing `[1m]` normalize to canonical IDs.
Unknown models or unknown/invalid floors preserve legacy character/global
resolution. A present empty `model` means native default until observed.

`mc/rollover_model_table.py` holds reviewed inputs, separately from the resolver.
The v1 decision uses `F` (fresh floor), mean growth per model call `g`, and the
cache-write/cache-read price ratio `w/r`:

```text
T* = F + sqrt(2 * F * (w/r) * g)
lo = max(global threshold, F + 60000)
hi = min(window / 2, optional price step, optional usable character pin)
threshold = min(hi, max(lo, int(T*)))
```

Hard upper caps win when `hi < lo`. A pin below `F + 60000` is ignored with
one diagnostic per character/pin (bounded process-local cache); a higher pin
caps `hi`. No config is rewritten and no new config setting or API is added.
A global value <= 0 disables token rollover before model or character logic.

| Model | Window | Input/read/write $ per MTok | w/r | g | Growth source |
| --- | ---: | --- | ---: | ---: | --- |
| Fable 5.2 | 1M | 8 / 0.20 / 10 | 50 | 3900 | measured |
| Fable 5.1 | 1M | 10 / 0.25 / 12.5 | 50 | 3900 | family |
| Fable 5 | 1M | unknown | 12.5 | 3900 | family |
| Opus 5.5 | 1M | 4 / 0.20 / 5 | 25 | 1600 | measured |
| Opus 5 | 1M | unknown | 12.5 | 1600 | family |
| Sonnet 5.5 | 1M | 2 / 0.10 / 2.5 | 25 | 2600 | measured |
| Sonnet 5 | 1M | unknown | 12.5 | 2600 | family |
| Haiku 5.5 | 1M | 0.10 / 0.01 / 0.125 | 12.5 | 2600 | provisional |
| Haiku 4.5 | 200k | 1 / 0.10 / 1.25 | 12.5 | 2600 | provisional |

These are reviewed plan inputs dated 2026-11-16, not a live price feed.
Family growth uses the newest measured member of that family. Legacy absolute
prices stay null; the formula uses the explicit ratio. Haiku 5.5's price-step
cap is 100000 tokens. All cache writes above use the five-minute price.

At F=90000 and global=200000, Fable 5.2 resolves to 277349, ignoring a 100000
pin; Opus/Sonnet 5.5 stay at 200000; Haiku 5.5 and 4.5 cap at 100000.
`threshold_with_reason` returns `(int, reason)` and `threshold_for` stays an
integer API. Reasons: `global`, `pin`, `cost_optimal`, `window_cap`,
`price_step`, `pin_ignored_below_floor`. A binding upper cap takes reason
precedence; otherwise an ignored pin takes precedence over `cost_optimal`.
`cost_optimal` also covers the global/fresh-floor lower clamp.

Mid-turn rows add `threshold_reason`, `mean_growth_tokens`, `growth_source`.
The threshold and working room use the outgoing segment's saved floor after
`begin_roll` resets the live floor. Resume dispatch uses an available live
session for model/floor inputs; unknown durable-only state keeps fallback.

The client context meter still divides by global config. The server does not
send a per-session threshold, so v1 intentionally leaves the frontend unchanged.
No browser smoke is required because no static assets changed. Follow-up after
review/merge belongs to the integrating session; no restart is part of this unit.
Rollback: revert this branch's commit to restore legacy resolution.

## MC-1178 validation

- Focused suite: **183 passed** (resolver/table 76, mid-turn reader 69,
  log fields 13, agent-route context trigger 19, live follow-up 6), exit 0.
- Agent routes and dispatch-save regression suite: **143 passed**, exit 0;
  one pre-existing invalid-escape SyntaxWarning in test_agent_routes.py.
- `pyright mc/rollover_model_table.py mc/rollover_threshold.py`:
  zero errors/warnings. `git diff --check`: clean.
- Test launcher reported `telemetry_queue_full_progress_incomplete` on the
  focused run; test exit status, terminal counts and JUnit XML independently
  confirm results. No live provider calls, browser, deployment or restart.

Commands from this worktree (using the installation's absolute launcher path):

```powershell
python <installation>/tools/test-run.py --project mission_control --port 5199 -- python -m pytest -o addopts=-ra tests/test_rollover_threshold.py tests/test_midturn_rollover.py tests/test_midturn_rollover_log_fields.py tests/test_context_rollover_trigger.py tests/test_context_rollover_live_followup.py -q --junitxml=_scratch/mc1178-focused.xml
python <installation>/tools/test-run.py --project mission_control --port 5199 -- python -m pytest -o addopts=-ra tests/test_agent_routes.py tests/test_dispatch_spawn_save_race.py -q --junitxml=_scratch/mc1178-routes.xml
```

## MC-1072 historical checkpoint

1. Read backlog, rollover memory, token-limit readers and character shapes.
2. Add the resolver and wire only threshold call sites in agent_routes.py,
   mid-turn checks/logging, and the settings allowlist.
3. Verify resolution, Flask follow-up, stream-reader mid-turn, other-provider
   checks, config persistence, existing rollover suites and module typing.
4. Record docs/changelog/guide, review explicit paths, commit on this branch.

Implementation, documentation and verification are complete. The home-directory plan
write was blocked by the unattended fence (global ~/.claude scope); this
project document carries the recovery checkpoint instead.

## Validation

- Resolution: hit, miss, scope distinction, session/metadata/ref inputs,
  clamp, invalid entries/maps/global values and the legacy disable setting.
- Auto-fresh decision and Mode A checks for Codex, Gemini and Qwen.
- Flask live-followup and real mid-turn stream-reader regressions: 130000
  tokens rolls under a 120000 character limit while global stays 200000.
- Settings map persistence and effective-threshold audit log assertion.
- Existing context-trigger, live-followup, mid-turn, settings and Codex
  usage-carry suites: 133 passed, rc=0. A separate collection confirmed 133 cases.
- Live-process byte backstop, conversation lineage, full-buffer rollover
  links and unattended settings guards: 25 passed, rc=0.
- `pyright mc/rollover_threshold.py`: rc=0, zero errors/warnings.
- `git diff --check`: rc=0.

Commands from the worktree root:

```powershell
python -m pytest tests/test_rollover_threshold.py tests/test_context_rollover_trigger.py tests/test_context_rollover_live_followup.py tests/test_midturn_rollover.py tests/test_settings_routes.py tests/test_codex_usage_carry.py -q
python -m pytest -o addopts=-ra tests/test_auto_fresh_live_process.py tests/test_conversation_rollover_lineage.py tests/test_rollover_link_full_buffer.py tests/test_settings_routes_unattended_gate.py -q
pyright mc/rollover_threshold.py
git diff --check
```

The first attempt at the second pytest command returned rc=1 (10 passed,
15 setup errors). Running it concurrently with collection exposed existing
`tests/conftest.py` cleanup: it deletes other PID-suffixed `data-root-*`
directories in the same checkout's test temp parent. The concurrent collection
removed the run's root before `server.py` created config.json. The identical
test command run sequentially passed all 25 cases. No fixture/product changes
were made for this unrelated cleanup issue; run this checkout's tests serially.

## Integration handoff

The running server is unchanged; post-merge/restart validation and local
override selection belong to Dave. No live provider calls or real model
processes were used in these tests. Commit locally only; no merge/push/restart.
Rollback: remove the override map entries to restore global thresholds;
revert the commit to remove the feature completely.
