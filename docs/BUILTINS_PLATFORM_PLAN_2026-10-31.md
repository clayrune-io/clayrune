# Claude Code built-ins, extended across the platform (plan, 2026-10-31)

Source: `docs/research/CLAUDE_CODE_FEATURES_2026-10-31.md`. Direction (Ron,
2026-10-31): the point of the comparison is to make Clayrune better, not to
defend positioning. Use as many Claude Code built-ins as fit, and extend each
one across the platform so it is not Claude-only.

**Principle.** When Claude Code ships a primitive, Clayrune (a) adopts it for
Claude sessions and (b) supplies the same behaviour for every other engine at
the Clayrune layer. The roster, the scheduler, hivemind and the steward are
where that behaviour lives, so it reaches all eight engines. Every item below is scoped to every engine; a Claude-only version is not done.

## Items, in build order

| # | Item | Vendor primitive | Platform-wide extension | Size |
|---|---|---|---|---|
| 1 | Unattended retry and usage-limit caps, every engine | `CLAUDE_CODE_RETRY_WATCHDOG_MAX_WAIT_MS`, `..._MAX_USAGE_LIMIT_WAIT_MS`, `..._USAGE_LIMIT_RECHECK_MS` (2.1.295 to 2.1.305) | One config block holds max wait, max usage-limit wait and recheck interval. Claude: map to the env vars beside `RETRY_WATCHDOG_ENV` in `mc/launch_marker.py`. Other engines: the engine's native knob where one exists, otherwise Clayrune enforces the same caps on the session. | 1 day |
| 2 | Fresh persona and rules on every engine's resume | `--system-prompt-snapshot off` (2.1.267) | Claude: behind a config key on resume paths (targets the "`claude -r` ignores `--append-system-prompt`" and persona-lost-on-revive classes); measure prompt-cache cost before defaulting on. Other engines: verify each resume path re-delivers the current persona and rules, fix any that replay a stale one. | 1 day |
| 3 | Steward becomes a role any character can hold | None; Claude Duet is the nearest concept | Today the steward machinery (charter, self-set next goal, unattended read-floor, cadence, decision email, reply check) runs only in steward mode, on 1 of 23 projects here. Scheduled named-agent runs (15 enabled schedules here) restate that behaviour in hand-written prose. A schedule or project can attach a charter to any roster character; the cycle is built by `steward.core.build_cycle_task` in that character's voice and engine. Migrate the Dave backlog run first. | Spec + 2 to 3 days |
| 4 | Hivemind workers are roster characters | Dynamic workflows, `/batch`, Projects coordinator | Today a workstream is provider + model. Add `character: global:<type>` per workstream (engine from the roster), a reviewer seat (Fenn) after each serial merge, and Dave routes any ask with 3+ independent workstreams to hivemind instead of single dispatches. A Claude worker may run a dynamic workflow inside its workstream; hivemind stays the multi-engine, smoke-gated integration layer. Replaces research recommendation 6. | Spec + 3 to 4 days |
| 5 | Fail-closed guard on every engine | `onFailure: "block"` (2.1.295), shipped for Claude in `8bae6685` | Engines whose CLI has no equivalent (Codex: `mc/guardrail_hooks.py:363-370`) get the same pre-spawn fence self-test and refuse to launch unattended when the guard cannot load. | 1 to 2 days |
| 6 | One owner for the memory index | Auto memory, 25,000-byte load cap | Research recommendation 2. Memory work runs on Fable. | Decide + 1 to 2 days |
| 7 | Cross-session messaging for every engine | `ListAgents` / `SendMessage` | Check whether non-Claude sessions can reach Clayrune's coordination and delivery store as tools; if not, expose them through an MCP server Clayrune already launches. | Investigate first |

## Sequence

1 and 2 now, one builder, one branch. 3 is specced next and built before 4;
both need a short spec because they change how Dave routes work. 5, 6 and 7
follow. Nothing here touches `steward/fence.py` (human-owned) or loosens a
human-only gate.
