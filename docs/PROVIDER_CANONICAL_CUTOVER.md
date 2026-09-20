# Canonical conversation read/cutover seam

> **Status (W0, 2026-09-17): implementation NOT on the merge branch.** It had no
> production caller, so it was removed from what merges (plan §9 DoD #4). The
> code and its tests are preserved at branch `archive/provider-neutral-canonical-dormant`
> (restore with `git revert` of the W0 removal commit) for the workstream that wires it.

Status: implemented offline and default-off; consumer seams are wired for
dependency injection, but startup does not compose or enable the policy.

`mc/conversation_cutover.py` is the bounded handoff between the provider-neutral
SQLite journal and existing transcript/Agent Log consumers. It is deliberately a
read policy, not another writer or provider adapter.

## Selection rules

- `CutoverPolicy()` is disabled by default. Callers continue using their legacy
  transcript/log reader and no canonical database is opened by this seam.
- With `enabled=True`, a canonical source is selected only when its fixed
  privacy-fenced history boundary has complete source coverage and the protocol
  projection has no gaps, conflicting identities, or unfinished content.
- An incomplete canonical source is never presented as complete Scribe input. If
  the caller supplies a legacy reader, the richer legacy source wins. If no
  legacy source exists, the gapped canonical history remains available for
  inspection with `partial` or `capture_gap` coverage.
- Canonical reads do not infer provider IDs, user messages from provider prompt
  echoes, success from transport EOF, or data from the legacy JSON sidecar.

## Consumer projections

`read_canonical_history()` reads a fixed boundary through
`ConversationStore.read_history_snapshot()`. Unlike derivation snapshots, this
reader permits known gaps so the history rail can show what was captured while
Scribe remains gated.

`select_scribe()` renders structured user/assistant/tool/thinking blocks. It
preserves full message text, labels partial assistant content, retains tool call
IDs/error state, and ignores lifecycle/provider-observation diagnostics. The
caller still owns model isolation, token budgets, privacy generation, memory
publication, and fallback telemetry.

`canonical_agent_log()` exposes lifecycle attempts as read-only provider-neutral
rows. It carries requested engine JSON and observed native handle separately;
it never copies or mutates the legacy `*_agent_log.json` sidecar.

## Activation gates still open

This increment adds inactive consumer hooks for Agent Log, read-only history,
conversation listing/search, privacy deletion, terminal Scribe, and checkpoint
Scribe. With no injected `ConversationCutover`/reader, all existing behavior is
unchanged. Resume/export and native runtime capture are not cut over. Before
enabling the policy in production, the composition root must provide:

1. an authorized store path and project/conversation mapping;
2. raw native capture for every reader/provider, including durable source-cursor
   recovery and backpressure;
3. startup composition and configuration for the existing canonical-first
   history, rail, search, Agent Log, Scribe, checkpoint, and privacy hooks, plus
   the still-missing revival/export integrations;
4. receipt-aware, cross-store memory publication and deletion/coverage fencing;
5. native-provider certification beyond repository fixtures, with no operator
   transcripts, credentials, live providers, or restart required by this seam.
