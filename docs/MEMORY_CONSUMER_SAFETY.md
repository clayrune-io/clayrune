# Memory consumer provenance (MC-1122)

Branch candidate, 2026-10-29. No deployment or live configuration change.

The unattended-loop rail applies to automatic RELEVANT MEMORY delivery as
well as exploration readback. Four callers now pass `consumer_unattended`
to the existing memory search gate. Lexical candidates and link expansion
share one origin predicate, `mc.memory._mem_unit_allowed`. Ranking, budgets,
and legacy-origin handling remain governed by `mc.memory._memory_search`.

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

## Link expansion and retained boundaries

Dave authorized closing the reproduced link bypass on this branch. The
existing lexical eligibility predicate is extracted without changing its
policy. `_memory_search` forwards the consumer flag to `_mem_expand_links`,
which builds its graph from eligible units only. An unattended topic cannot
return through an outbound link, backlink, or a composed second hop through
an attended intermediate. Filtering happens before the expansion cap, so
an excluded neighbor cannot consume a result slot. The scoring corpus and
its normalization are unchanged.

The scratch repro is now a regression: query `quartz calibration`, human
note `origin: interactive` with `[[loop_note]]`, and `loop_note.md` stamped
`origin: unattended`. With expansion 2, unattended consumers receive only
the human note; manual consumers receive both. Reversing the link tests
backlink delivery. Expansion 2 means **two extra results**, not recursive
depth; the chain regression explicitly composes two one-hop expansions.

Unstamped/legacy notes remain eligible under the existing policy.
`consumer_unattended=False` retains the original expansion path and result
bytes. Explicit search API attribution and non-marker initial dispatch
provenance remain outside this repair, as do archive/managed units and
per-turn cold transcript fallback. This closes the authorized lexical/link
delivery gap without claiming those other channels now enforce provenance.

## Verification

- New caller regressions: **10 failed / 3 passed before**, **13 passed after**.
  They execute the real prompt, steward-cycle, seeding, and refresh callers
  against a synthetic mixed-origin vault; manual consumers retain both notes.
- Link graph suite: **4 failed / 15 passed before**, **19 passed after**,
  including eight new outbound/backlink, second-hop and legacy/manual cases.
- Full memory, scheduler, steward-refresh, distiller-safety, position, and
  read-floor suites after both commits: **rc 0, 671 passed, 3 skipped**.
  Existing human-chat fixtures
  now state `trigger_type: manual`; unknown provenance is covered separately.
- Caller-commit pyright on its three production files: **rc 1, 15 errors**.
  The base revision has the same 15 errors in `agent_routes.py`; diagnostic
  messages/rules/columns match after line-offset mapping. **0 new errors**;
  `memory_turn.py` and `scheduler_routes.py` have none.
- Follow-up pyright on `mc/memory.py`: **rc 1, 2 existing errors, 0 new**.
- A paired, frozen live-vault comparison with the caller commit's functions
  produced byte-identical manual/default results across seven queries. The
  live charter retained eight hits with this follow-up gate enabled; no
  live-vault mutation or delivery telemetry was used.

Operator-specific live measurements and exact commands are in the requested
gitignored worktree journal. Copy that journal when integrating; merging the
branch does not carry it. No UI, settings, public API shape, or install flow
changed; the user guide and agent instructions need no update.
