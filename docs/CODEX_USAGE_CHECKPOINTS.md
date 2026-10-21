# Codex source-observed usage checkpoints (MC-998)

The sample-tick writer reads native rollout usage through
`mc/codex_usage_checkpoint.py`. It never copies `session.usage` into a running
checkpoint: that field changes only at `turn.completed` and can describe an
earlier turn while the current turn is still producing tokens.

## Native contract and observation time

The adapter consumes these native JSONL records, with synthetic fixtures in
`tests/test_codex_usage_checkpoint.py`:

- `session_meta`: its envelope `timestamp` and payload `id` establish the native
  thread identity and its creation time.
- `event_msg` with payload type `token_count`: the envelope `timestamp` is the
  source observation time. `info.total_token_usage` is the native thread's running
  total across model requests and resumed turns; `info.last_token_usage` describes
  one model request. Neither represents a turn's standalone spend.
- `input_tokens` includes `cached_input_tokens`. Normalization reports processed
  input = input, fresh input = input minus cached, cache reads = cached, and output
  separately. Reasoning is an output subset, never an additional output charge.
  Unknown cache-write categories, TTLs and model splits remain NULL.
- A token-count event can have allowance data with `info: null`. Its allowance
  time is not token evidence. Missing fields are unknown, including missing cache
  counters; explicit source-confirmed zero values are valid observations.

The native shape and cumulative behavior were checked against local CLI 0.163.0
rollouts and the existing runtime fixtures. Public [non-interactive mode
documentation](https://learn.chatgpt.com/docs/non-interactive-mode) describes the
JSON event stream and turn-completed usage, but does not establish the rollout's
mid-turn cumulative contract. The native projection, not the documentation's
example values, is the evidence for that contract. No vendor call is required.

## Boundary and lifetime rules

The allowance sampler passes its original source timestamp into the existing
route writer. A Codex tick is complete only when the owning native rollout has a
valid token observation at **that exact instant**, comparing timezone-aware times.
Older or newer observations are not moved to the boundary. File mtime, poll time
and stale last-turn counters never become source time. Later native records do
not leak into a historical boundary.

This matters with concurrent sessions: the account-wide allowance reader chooses
one rollout. Another session without a token observation at that same instant
remains unavailable. No interpolation, freshness tolerance or fabricated zero
fills that gap. The ordinary aggregate consequently continues to refuse intervals
with incomplete coverage.

A complete cumulative reading also needs a durable MC baseline, matching native
identity, metadata no earlier than that baseline, and an observed native history
whose first total equals its first request. The reader checks counter and source
time ordering through the requested boundary. Negative, missing, non-integral,
inconsistent or unsupported counters are refused. A reset remains unavailable;
the reader does not clamp it or silently start another lifetime.

Resuming the same native thread under the same durable MC session preserves the
thread total, including earlier turns. Turn-start carries the stored completion
fact and completion consumes the runtime's thread-cumulative usage, so these paths
already share the supported tick scope. Their accounting was not changed.

Two cases remain explicitly unavailable:

1. A new MC session resumes native history older than its baseline, or a fork
   inherits unobserved totals. Charging that history to a zero dispatch baseline
   would misattribute old usage.
2. A session has `_codex_usage_carry`, or native metadata reveals replacement
   after earlier complete session evidence. The legacy carry is not an attested
   source history, so it cannot prove a live session-lifetime boundary. Supporting
   it requires a separate native-thread ledger and consistent lifecycle accounting.

Repeated native observations use the existing `(session, sample_tick, source time)`
dedup key. Missing evidence writes NULL counters and `token_coverage=unavailable`;
a plain reason is logged on reason transitions. No schema migration or production
backfill is part of this repair. Incognito still performs no source read or write.

## Verification and integration gate

The focused suite exercises the real writer, actual SQLite store and native-shaped
synthetic records. Coverage includes long turns and open tails, repeated source
observations, cached input counted once, same-thread resume, actual completion and
turn-start writers, actual runtime rollout resolution, malformed/stale/missing
source data, inherited history, reset/replacement, unknown categories and incognito.
The aggregate test proves eligible intervals can reach calibration while preserving
the existing independent-session gate.

Run focused tests with an isolated pytest `--basetemp`, the existing usage-breakdown
sampler/store/routes/transcript/pooled/turn-start tests, and affected Codex runtime
accounting tests. The new module must pass basic pyright. No static files changed,
so frontend smokes and a borrowed smoke dependency junction are unnecessary.

Dave owns independent review and integration. After separately authorized release
and restart, live validation must confirm source-aligned rows from a running Codex
thread, repeated-source deduplication, NULL coverage for unmatched concurrent
threads, and correct interval deltas. A passing isolated suite is not evidence that
the deployed database or live calibration has changed. No backfill, DB repair,
restart, merge or push is authorized by this task.
