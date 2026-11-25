# Context rollover limits (MC-1072, MC-1178)

`mc/rollover_threshold.py` resolves the token trigger for message-boundary and
mid-turn rollover. The integer `threshold_for(session_or_character, config)`
and the `(tokens, reason)` `threshold_with_reason` interfaces accept a live
session, a `scope:name` character reference, or character metadata.

## Resolution order

1. A nonpositive `context_rollover_tokens` disables the token trigger. The
   transcript-byte backstop still covers conversations with unknown token
   usage.
2. An explicit target can match `character`, `model`, `effort`, or any
   combination. The number of matched selectors breaks ties first. For
   equally specific entries, model takes precedence over character, then
   effort. Duplicate targets use the first valid entry. The legacy
   `context_rollover_by_character` map supplies character-only entries after
   the list. A target below the verified floor plus 60,000 tokens emits one
   diagnostic per distinct target and falls through.
3. The computed default uses the live verified floor and the first qualified
   growth estimate for character/model/effort, model/effort, or model. The
   reviewed model table supplies its own growth estimate when no measured
   cohort qualifies. The actual call model takes precedence over the requested
   model. Known Claude aliases, dated IDs and `[1m]` suffixes normalize to the
   corresponding model IDs. Effort comes from the triggering request when the
   native transcript links it to that call.
4. `context_rollover_tokens` supplies the base (default 200,000) when the floor,
   price ratio, or growth estimate is unavailable. A computed limit can be
   below the base. Active thresholds start at 60,000 tokens.

The config API accepts integer numbers and integer strings for token counts.
Fractional and non-finite values, booleans, and malformed overrides fall
through. `PUT /api/config` uses the existing passcode and unattended-caller
checks. The context meter continues to display the global setting.

```json
{
  "context_rollover_tokens": 200000,
  "context_rollover_overrides": [
    {"model": "sonnet", "effort": "high", "tokens": 220000},
    {"character": "global:builder", "model": "sonnet", "effort": "high", "tokens": 210000}
  ]
}
```

## Verified floors and computed limits

Clayrune verifies the first main-conversation input against the beginning of
the native Claude transcript. A reader that attaches during a later call can
only use a floor previously verified for that same segment. Changing the
native ID or rolling to fresh context clears the outgoing floor. When the
first call cannot be proved from available evidence, resolution uses the
no-floor path.

With fresh floor `F`, mean input growth per call `g`, ratio `w/r` of cache-write
to cache-read prices, and working room `R = T - F`, the relative cost per call
is `F + R/2 + (w/r) * F * g / R`. Its minimum is
`T* = F + sqrt(2 * F * g * (w/r))`.

The computed default chooses the highest 10,000-token grid point within 2% of
the minimum modelled cost and 30,000 tokens above `T*`. With a known floor,
every active threshold leaves at least 60,000 work tokens. Half-window and
price-step caps apply with that floor. If an existing cap would leave less
room, the effective cap becomes `F + 60000`.

The snapshot requires at least 10 floors, five distinct chats with a floor,
100 call deltas, and at most 50% of those deltas from any one chat. Only built-in
character references appear in exported character cells. The model pools
include all eligible call deltas. The estimates come from the November 18
00:00 UTC to November 25 17:00 UTC 2026 calibration window. [Calibration analysis](ROLLOVER_TUNING_ANALYSIS.md)

| Cohort | Median floor | Floors | Chats | Call deltas | Mean growth | Reference limit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Fable 5.2 | 78,463 | 77 | 38 | 3,690 | 3,214 | 260,000 |
| Opus 5.5 | 80,640 | 244 | 32 | 5,675 | 2,384 | 200,000 |
| Sonnet 5.5 | 72,724 | 105 | 56 | 3,192 | 3,184 | 210,000 |
| Builder / Sonnet 5.5 / high | 74,005 | 67 | 37 | 2,336 | 2,596 | 200,000 |

Regenerate the table from the same calibration window and an installation data
directory or frozen copy. `--transcripts` can point to an exported transcript
tree. The script otherwise uses the local Claude transcript location.

```text
python -X utf8 tools/analyze-rollover-tuning.py --data DATA --since 2026-11-18T00:00:00Z --until 2026-11-25T17:00:00Z --emit-table mc/rollover_growth_snapshot.py
```

## Decision log

Each accepted rollover writes one decision row to `data/midturn_rollover_log/`,
beside the project data directory. The row captures the outgoing native ID,
requested and canonical model, effective effort, verified floor, applied
threshold and reason, selected growth where used, and the opening prompt's
unanswered state. `trigger_type` identifies `midturn` or `message_boundary`.
`trigger_reason` identifies `tokens`, `bytes`, or `interrupted`. Unknown
fields and the unapplied token threshold on bytes and interruption rows use
JSON null.

Threshold reasons identify the winning source: `disabled`, `override:<selectors>`,
`computed:cell`, `computed:model_effort`, `computed:model`,
`computed:reviewed_<provenance>`, or `base`. A guard can add `/window_cap`,
`/price_step`, or `/fresh_floor`.
