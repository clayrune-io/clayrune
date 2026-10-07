# Per-character rollover thresholds (MC-1072)

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
Active thresholds clamp to 60000 without rewriting config. For compatibility,
the existing nonpositive global value still disables the token trigger;
an explicit character override clamps to the floor and can enable that
character's token trigger. Remove an override to restore global behavior.

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

## Plan and checkpoint

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
