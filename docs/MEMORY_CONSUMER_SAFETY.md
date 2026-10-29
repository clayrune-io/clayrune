# Memory consumer provenance (MC-1122)

Branch candidate, 2026-10-29. No deployment or live configuration change.

The unattended-loop rail applies to automatic RELEVANT MEMORY delivery as
well as exploration readback. Four callers now pass `consumer_unattended`
to the existing memory search gate. Ranking, budgets, and legacy-origin
handling remain governed by `mc.memory._memory_search`.

## Caller audit

All eight production `_memory_search` calls were reviewed:

| Caller | Destination | Verdict |
| --- | --- | --- |
| `scheduler_routes._steward_cycle_task` | Charter RELEVANT MEMORY in cycle task | Repaired: always `True`, matching its exploration floor. |
| `agent_routes._build_agent_context` | Initial/rebuilt prompt: positions and RELEVANT MEMORY | Repaired: `distiller.is_unattended_task(task)`, matching the adjacent negation ledger and exploration floor. |
| `memory_turn.seed_delivered` | Dispatch suppression and position-compaction bookkeeping | Repaired: the same task classifier as dispatch; withheld notes are not marked delivered. |
| `memory_turn._refresh_for_turn` | Live-turn positions and RELEVANT MEMORY | Repaired: `distiller.is_unattended_session(session.task, session.trigger_type)`, shared with its negation ledger. |
| `agent_routes._newer_memory_unit_exists` | Boolean allowance re-probe decision | No memory text delivered. The later allowance-conflict block describes vendor state, not the search hit. No change. |
| `memory._note_retrievable` | Write-time/sweep reachability verdict and filenames | Audit output, not automatic prompt delivery. No change. |
| `memory_push._observe` | Report-mode would-send log and filenames | Observer only; no prompt injection. No change. A future delivery feature needs its own provenance gate. |
| `guide_routes.memory_search` | Explicit search API response, including agent-requested tool results | Origin-unfiltered; not an automatic read floor. This change does not add caller attribution to that API. |

Offline `tools/memory-eval` probes and test callers do not build production
agent prompts. No other production `_memory_search` call was found.

Per-turn provenance is available on the session; it is not inferred from
the latest message. A plain follow-up in a scheduled, dispatched, unknown,
or backfilled session therefore keeps the unattended gate. Only a literal
`manual` trigger with no unattended task marker is attended. Dispatch and
seeding deliberately retain their existing text-based classifier, as
requested; an unmarked scheduled task at initial context build is still a
limitation of that contract.

## Remaining gap requiring integration-owner decision

The four callers now enable the lexical gate, but this is **not a claim
that the complete rail is closed**. `_memory_search` filters unattended
topic/position candidates while scoring, then `_mem_expand_links` expands
against the original unfiltered corpus. An interactive note linking to an
unattended note reintroduces the latter when expansion is enabled.

Reproduced with a two-note synthetic vault: query `quartz calibration`,
human note `origin: interactive` with `[[loop_note]]`, and `loop_note.md`
stamped `origin: unattended`. With `consumer_unattended=True`, expansion 0
returns only the human note; expansion 2 returns both, with
`via: human.md` on the unattended hit. No live-vault mutation was used.

Recommended follow-up: extend the primitive's gate to link expansion and
add an outbound/inbound-link regression. This is a separate scope decision
for Dave rather than an unreviewed change to the primitive. Explicit search
API attribution and non-marker initial dispatch provenance also need a
decision if the rail is intended to cover those channels. Archive/managed
units and per-turn cold transcript fallback remain outside the existing
topic/position-origin gate; unstamped legacy notes remain eligible.

## Verification

- New caller regressions: **10 failed / 3 passed before**, **13 passed after**.
  They execute the real prompt, steward-cycle, seeding, and refresh callers
  against a synthetic mixed-origin vault; manual consumers retain both notes.
- Memory, scheduler, steward-refresh, distiller-safety, position, and
  read-floor suites: **663 passed, 3 skipped**. Existing human-chat fixtures
  now state `trigger_type: manual`; unknown provenance is covered separately.
- Pyright on all three touched production files: **rc 1, 15 errors**.
  The base revision has the same 15 errors in `agent_routes.py`; diagnostic
  messages/rules/columns match after line-offset mapping. **0 new errors**;
  `memory_turn.py` and `scheduler_routes.py` have none.

Operator-specific live measurements and exact commands are in the requested
gitignored worktree journal. Copy that journal when integrating; merging the
branch does not carry it. No UI, settings, public API shape, or install flow
changed; the user guide and agent instructions need no update.
