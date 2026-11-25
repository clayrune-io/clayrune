# Rollover limits by character, model and thinking effort

**Recommendation for review, 2026-11-25.** Use 200,000 tokens as the base fallback. At the observed median fresh floor, the model-wide candidates are **260,000 for Fable 5.2, 200,000 for Opus 5.5, and 210,000 for Sonnet 5.5**. Use the character/effort rows below when their sample supports a separate estimate. Treat these numbers as reference thresholds at the measured floors. A live segment with a different floor warrants a fresh calculation. The policy choices below are open for review.

| Character or scope | Model | Effort | Reference tokens | Distinct chats with a floor | Measured call deltas |
| --- | --- | --- | ---: | ---: | ---: |
| All | Fable 5.2 | All | 260,000 | 40 | 3,750 |
| All | Opus 5.5 | All | 200,000 | 33 | 5,776 |
| All | Sonnet 5.5 | All | 210,000 | 57 | 3,209 |
| Build | Fable 5.2 | High | 240,000 | 13 | 1,530 |
| Build | Sonnet 5.5 | High | 200,000 | 37 | 2,336 |
| Build | Sonnet 5.5 | Medium | 210,000 | 7 | 303 |
| Product guide | Fable 5.2 | High | 360,000 | 18 | 433 |
| Coordinator | Opus 5.5 | High | 190,000 | 6 | 1,388 |
| Coordinator | Opus 5.5 | Medium | 190,000 | 11 | 702 |
| UI repair | Sonnet 5.5 | High | 220,000 | 6 | 260 |
| Any cell with an uncalibrated model and no usable calculation | Unknown | Any | 200,000 | 0 for unseen model | 0 for unseen model |

The base retains the existing configuration default. Across 438 observed starts the p90 floor was 85,890, leaving 114,110 tokens at 200,000. At 200,000 the estimated read-plus-rewrite cost is 2.4% above the Fable optimum, 1.1% above the Opus optimum, and 0.8% above the Sonnet optimum (3,750 / 5,776 / 3,209 deltas). There are zero cost observations for a wholly unknown model. Model prices, windows and floor guards still matter when a model is known.

### What the incident establishes

The coordinator had 142 logged mid-turn rolls in the calibration window. The raw model string was `opus` on 132 pin-resolved rolls. All 132 used 100,000. Their logged floors ranged from 72,476 to 107,890, leaving −7,890 to +27,524 tokens. The other 10 rolls named Fable 5.2. The live global setting read 200,000 and the coordinator pin read 100,000. The dynamics of the resolver and its alias/floor fix are a separate unit of work.

**All three flagged Fable rolls shrank context.** The outgoing trigger and the first input in the directly linked successor are visible in different native transcripts. The supposed floors match outgoing calls **146, 173 and 40**, respectively. In date order, their true first inputs were **85,140, 86,701 and 84,183**. The linked successors started at **86,701, 87,086 and 83,812**. One additional case on November 17, outside the calibration window, had a logged floor of 303,817 at call 115, a true first input of 84,949, and a successor at 85,102.

`note_call_tokens` saves the first input it *observes* while its in-memory floor is empty. A reader attached to a resumed transcript can therefore label a later call as fresh. `begin_roll` parks that outgoing value before clearing it, and `record_roll` writes it on the outgoing row. Across all 290 comparable rows, 52 logged floors differ from the first input of the named transcript. The matches establish the sampling error. They do not identify which lifecycle action cleared each in-memory value. The first input after a roll cannot be read from that roll's outgoing row. See [the observer, reset and writer](../mc/midturn_rollover.py).

**Continuity changes the choice.** Of 290 mid-turn rolls, 94 followed another logged mid-turn roll in the same chat by under five minutes. There were 225 rolls with a logged predecessor. Ten of 290 had pending messages, including a documented case where the carried steer displaced an in-flight decision. These logs count pending steers and queued follow-ups. A zero can coexist with an unanswered opening prompt. Across directly linked intervals the median was 13 calls at 100,000 (111 intervals) and 44 calls at 200,000 (71 intervals). These cohorts mix workloads. The incident journal also identifies a message-boundary roll approximately 40 seconds before the mid-turn interruption. A full all-trigger denominator and the probability of losing a decision at each candidate threshold cannot be recovered from the mid-turn ledger.

### Measured inputs and interpretation

| Model | Fixed growth in existing table | Measured mean per call | p50 / p90 per call | Call deltas | Earlier two-day mean (deltas) |
| --- | ---: | ---: | --- | ---: | --- |
| Fable 5.2 | 3,900 | 3,205 | 1,738 / 7,009 | 3,750 | 4,205 (4,018) |
| Opus 5.5 | 1,600 | 2,369 | 1,571 / 4,859 | 5,776 | 2,113 (1,745) |
| Sonnet 5.5 | 2,600 | 3,214 | 1,604 / 7,219 | 3,209 | 1,921 (3,979) |

The earlier window, November 16–18 UTC, yields model candidates of 290,000 / 200,000 / 180,000 on 114 / 78 / 84 floors. The later window is the one used for the recommendations. The 30,000-token shifts for Fable and Sonnet show how much the workload mix can move a rounded estimate. Effort is observational. For example, build work on Sonnet medium averaged 5,333 tokens/call over 303 deltas, while high averaged 2,596 over 2,336. A universal multiplier that increases with effort would predict the reverse ordering.

- **Cohort.** Scan every project roll ledger and retained agent log. Select roll timestamps and run-row timestamps in the half-open UTC interval. Collect native IDs from the roll, the run's primary ID and its native lineage, including runs that never rolled. Read uniquely located top-level native transcripts. Ownership requires a consistent character for that ID. Exclude nested agent transcripts, sidechains, synthetic model messages, duplicate blocks sharing a message ID and observations after cutoff. Run records are retention-limited and mutable. This is a census of records reachable from these indexes, not of all provider requests on the machine. A run's cumulative cost or `num_turns` spans a different unit and can recur across persisted snapshots. Neither is apportioned to a roll.
- **Floor and growth.** Use the first real input in a rooted transcript that starts with a prompt. Input includes direct, cache-read and cache-creation tokens. Output is excluded from the current input and contributes when reread on a later call. Floor statistics count each eligible native segment once. Call growth is the signed difference across successive distinct calls in the same transcript, model and context epoch. Declared compactions and new roots break the series. Turn growth is first-input to next first-input across consecutive prompt-bearing turns. Tool responses and metadata are excluded as prompts. Unfinished final turns are censored, and one-prompt workers can have many calls and zero turn deltas. The tables give medians and nearest-rank p90s. Means weight calls, so the largest chat's share is displayed.
- **Effort.** Each of 261 exact trigger matches has the actual request's `params.output_config.effort`, joined by the assistant block's `requestRef` to `api-request.id`. For historical calls without that field the script checks the native block's `perTurnEffort`, then `effort`. Missing observations stay unknown. The run log persists `requested_effort` and `effort_support`. In the calibration window 225/315 run rows have a non-null request field. Only seven roll rows have an unambiguous, supported, nonempty requested effort on a retained run with the same project, chat and primary native ID. A request setting on a later snapshot or descendant cannot establish what an older call sent. The one effort field to add to every roll is **`effective_effort`** from the triggering request. See [run snapshot and continuation code](../mc/blueprints/agent_routes.py).
- **Cadence.** Match the UTC roll time and unique exact trigger input in its named transcript. Require the current first prompt to name the previous logged native ID as the handoff source and its first call to occur after that roll (one-second timestamp precision). Count distinct calls through the trigger and distinct prompt turns with calls, including the triggering turn. The table groups by the recorded outgoing threshold. The logger recomputes it on write, so only a future decision-time field can independently verify it. Omitted intervals have insufficient linkage. Counts describe work between two mid-turn rolls and can span more than one user turn.
- **Cost.** For working room `R = T − F > 0`, model call count as `R/g`, average context as `F + R/2` and one cache rewrite of `F` per cycle. The resulting USD/call is `[r(F + R/2) + wFg/R] / 1,000,000`. Its continuous minimum is `T* = F + sqrt(2Fgw/r)`. Inputs are the cohort's median observed floor, arithmetic mean signed call growth and the five-minute cache prices in [the reviewed model table](../mc/rollover_model_table.py). This measures the two requested cost components under sustained work. Output charges, cache expiration, suffix writes, short jobs that finish before rolling, and human rework are outside the estimate. No billing savings or candidate loss rates have been observed.
- **Confidence and inheritance.** Provisional fit requires 10 floors, five distinct chat IDs with a floor, 100 call deltas, and at most 50% of growth observations from any one chat (unidentified chats share a bucket). These are proposed coverage criteria. They are not confidence intervals. If a cell fails, inherit a qualified model/effort estimate, then a model estimate, then the base when the inputs for computation are missing. The table gives the source and counts even for zero usable calls. Any unlisted character/effort combination has zero observations as a joint cell in this window and follows the same hierarchy. Haiku, earlier model versions and non-Claude providers have zero eligible call-growth deltas in this window. For known windows, even an inherited base is subject to the guards below.
- **Continuity premium.** On a 10,000-token grid, propose up to 30,000 extra room above the optimum if it costs at most 2% more, reserving at least p90 floor + 60,000 for the reference table. Fable's model-wide curve moves +1.48% at −30,000 and +1.01% at +30,000. Both directions cost under 2%, so continuity justifies the higher point. Opus moves +3.72% / +1.97%, and Sonnet +3.19% / +1.80%. There is no measured dollar value for an interrupted decision. The product-guide sample has zero complete turn deltas. Its 360,000 proposal is supported by call growth only.

### Cross-check against all retained history

All 26 project run ledgers and 10 roll ledgers yielded 2,872 mid-turn rolls, 5,718 rooted starts and 131,369 call deltas through the same cutoff. Of 2,306 rolls with a logged predecessor, 525 followed it within five minutes. Pending messages were present on 96 of 1,763 rows carrying that field. The all-history pass matched 2,753 triggers, found 362 indexed native IDs without a transcript, and skipped two malformed transcript lines, breaking the growth series at each gap. Floor construction has also changed with the model profile and conversation-versus-dispatched-task rules. Measuring the first input captures the actual prefix in each era. There is no separate causal estimate for job shape here.

| Model | All-history floors / call deltas | Historical reference tokens | Use in a current decision |
| --- | ---: | ---: | --- |
| Fable 5.2 | 194 / 7,874 | 270,000 | Current sample gives 260,000 |
| Opus 5.5 | 2,110 / 57,930 | 190,000 | Current sample gives 200,000 |
| Sonnet 5.5 | 600 / 24,219 | 200,000 | Current sample gives 210,000 |
| Opus 5 | 106 / 10,632 | 140,000 | Historical fit at median floor 58,279. Zero current-window deltas. |
| Sonnet 5 | 473 / 26,933 | 170,000 | Historical fit at median floor 83,676. Zero current-window deltas. |
| Fable 5 / 5.1 | 6 / 122 and 8 / 257 | Insufficient starts | Use reviewed family inputs with live floor when available |
| Haiku 4.5 / 5.5 | 2,186 / 12 and 3 / 12 | Insufficient growth observations | Use reviewed provisional inputs and window/price guard |
| Opus 4.7 / 4.8 | 2 / 239 and 30 / 3,139 | No reviewed price ratio | Use base with known runtime window and floor guards |

Legacy Opus 5 and Sonnet 5 have a reviewed write/read ratio of 12.5 but no absolute cache prices. Their historical cost minima were 109,925 and 149,681 tokens. The working-room floor helps set the higher reference values. A dollar cost for either is unavailable. Reuse of historical growth for retired model versions is a review choice. The current-window table prioritizes recent calibration. The all-history command also emits the complete character/effort splits for historical versions.

### Resolver proposal

Keep the existing global disable convention. Proposed configuration uses tokens for explicit targets. This example describes types and scopes. Angle-bracket keys and `N` are placeholders. Store actual character references in installation configuration.

```text
context_rollover_tokens = 200000
context_rollover_by_character[character_ref] = N                 # existing format
context_rollover_overrides.by_model[canonical_model] = N
context_rollover_overrides.by_effort[effective_effort] = N
context_rollover_overrides.by_model_effort[canonical_model][effective_effort] = N
context_rollover_overrides.by_character_model[character_ref][canonical_model] = N
context_rollover_overrides.by_cell[character_ref][canonical_model][effective_effort] = N
```

1. Read global disable first. Resolve observed provider/model, then a provider-confirmed mapping of its requested alias. Read the effective effort on the triggering call and a verified first input for the current native segment. Leave each unknown dimension unset.
2. **Explicit targets** take precedence, in this proposed order: character/model/effort, character/model, model/effort, model, character, effort. An invalid entry falls through. A legacy character pin below live floor + 60,000 falls through to the next source, which prevents a stale 100,000 pin from silently becoming the normal threshold.
3. **Computed default** uses live verified floor and reviewed price ratio with measured growth from the first supported cell, model/effort pool or model pool. Apply the same continuity premium as the reference table. With a reviewed model but insufficient local measurements, use the table's provenance-tagged family/provisional growth. Without price ratio, growth or a verified floor, use the **200,000 base**. It is a fallback, so a computed 190,000 remains expressible. When the first verified input arrives, recompute. An unknown alias never bypasses the live floor guard.
4. **Working room** applies to every active source. Recommend at least 60,000 above the live floor. This covers the pooled p90 observed turn growth of 41,433 (499 complete prompt intervals), though Sonnet's p90 was 85,775 (36) and a build/Fable turn can exceed the reserve. Preserve the half-window and price-step as policy caps subject to the same existing 60,000-room exception. A known physical input ceiling after reserving output takes precedence. If the verified floor plus working room cannot fit, record the conflict and compact/reduce the prefix through an appropriate existing path. Repeatedly rolling to an equally large prefix cannot provide room.
5. **Observability** should capture one decision record for every trigger type, including message boundaries, with outgoing and incoming native IDs, trigger call ID/time, applied threshold, reason, alias and canonical model, effective effort, pending count and whether an opening prompt still awaits an answer. Attach the successor's first main-call ID/input/time and freshness evidence when available. Store segment call and user-turn counts. Keep the existing run-level requested effort separately. These fields allow an end-to-end check that higher thresholds reduce handoff frequency and that an unfinished decision actually survives.

| Decision | Recommendation | Other option and cost |
| --- | --- | --- |
| Optimize cost alone or buy continuity | Choose the upper grid point within the proposed 2% / 30,000 allowance. | Use the mathematical optimum. Saves the small premium shown per cell and causes more interruptions on sustained workloads. |
| Resolve conflicting one-axis overrides | Model before character before effort. Honor an explicit joint target first. | Character first preserves more legacy pin behavior but requires a joint exception for every model that needs more room. Cap-only overrides preserve v1 semantics but cannot express a larger continuity target. |
| Fix 60,000 room or learn it by task | Keep the 60,000 minimum and learn growth within each evidence-qualified cell. | Raising the minimum to each cell's turn p90 would protect more long turns, but some cells have zero or one completed interval. For build/Fable high the observed p90 is 164,376 on 11 intervals. |
| Static table or continuous calibration | Use reviewed cohort growth with a dated snapshot and recompute against the live first-call floor. | Learning growth from each live session adapts sooner, but short or resumed segments lack stable samples. Flat token pins are simpler and require manual updates as floors and workloads move. |

### Reproduction

Run from a checkout containing the script and price table. Point `--data` at the installation data or a frozen copy. The script reads native transcripts under the user's Claude projects directory by default. Use `--transcripts PATH` for an exported tree. Add `--local-labels` to print actual character names locally. The published labels below follow sorted character references for this snapshot. C01 = build, C02 = product guide, C03 = code review, C04 = coordinator, C05 = market research, C06 = research, C07 = failure diagnosis, C08 = social strategy, C09 = UI repair. Asterisk means pooled. Unknown means unobserved. Anonymous labels can change between windows.

```text
python -X utf8 tools/analyze-rollover-tuning.py --data DATA --since 2026-11-18T00:00:00Z --until 2026-11-25T17:00:00Z
python -X utf8 tools/analyze-rollover-tuning.py --data DATA --since 2026-11-16T00:00:00Z --until 2026-11-18T00:00:00Z
python -X utf8 tools/analyze-rollover-tuning.py --data DATA --since 1970-01-01T00:00:00Z --until 2026-11-25T17:00:00Z
```

The first command produces the following tables. The second reproduces the earlier-window comparison and the third covers all retained history. The original context-economy spec was read from its tracked revision `7948fe37`. The [existing threshold design](ROLLOVER_THRESHOLDS.md) describes the policy in force in this sample.

Calibration [2026-11-18T00:00:00Z, 2026-11-25T17:00:00Z). 10 roll files, 26 run files, 2872 historical mid-turn rolls through cutoff, 290 in calibration. Retained run rows by provider: {'claude': 255, 'codex': 33, 'unknown': 27}. Non-null run fields: {'requested_effort': 225, 'cost_usd': 315, 'num_turns': 315}. 460 indexed native IDs. Audit: {'logged floors compared to native start': 290, 'logged floors differing from native start': 52, 'matched triggers': 261, 'request effort at trigger': 261, 'rolls with direct run requested effort': 7}.

### Recommended thresholds

| Character / model / effort | Tokens | Source | Chats with floor | Floors | Call deltas | Largest chat share |
| --- | --- | --- | --- | --- | --- | --- |
| * / * / * | 200,000 | base | 129 | 438 | 12735 | 20% |
| * / fable-5-2 / * | 260,000 | model | 40 | 79 | 3750 | 22% |
| * / fable-5-2 / high | 260,000 | model/effort | 38 | 77 | 3547 | 23% |
| * / fable-5-2 / medium | 260,000 | model | 2 | 2 | 203 | 64% |
| * / fable-5-2 / unknown | 260,000 | model | 0 | 0 | 0 | — |
| * / opus-5-5 / * | 200,000 | model | 33 | 252 | 5776 | 45% |
| * / opus-5-5 / high | 190,000 | model/effort | 15 | 128 | 1845 | 37% |
| * / opus-5-5 / max | 200,000 | model | 2 | 61 | 3059 | 85% |
| * / opus-5-5 / medium | 200,000 | model/effort | 16 | 63 | 872 | 38% |
| * / opus-5-5 / unknown | 200,000 | model | 0 | 0 | 0 | — |
| * / sonnet-5-5 / * | 210,000 | model | 57 | 107 | 3209 | 5% |
| * / sonnet-5-5 / high | 210,000 | model/effort | 47 | 88 | 2700 | 6% |
| * / sonnet-5-5 / medium | 200,000 | model/effort | 10 | 19 | 509 | 20% |
| * / sonnet-5-5 / unknown | 210,000 | model | 0 | 0 | 0 | — |
| * / opus / * | 200,000 | base | 0 | 0 | 0 | — |
| * / opus / unknown | 200,000 | base | 0 | 0 | 0 | — |
| C01 / fable-5-2 / high | 240,000 | cell | 13 | 27 | 1530 | 23% |
| C01 / fable-5-2 / unknown | 260,000 | model | 0 | 0 | 0 | — |
| C01 / sonnet-5-5 / high | 200,000 | cell | 37 | 67 | 2336 | 7% |
| C01 / sonnet-5-5 / medium | 210,000 | cell | 7 | 15 | 303 | 29% |
| C01 / sonnet-5-5 / unknown | 210,000 | model | 0 | 0 | 0 | — |
| C02 / fable-5-2 / high | 360,000 | cell | 18 | 30 | 433 | 8% |
| C02 / fable-5-2 / unknown | 260,000 | model | 0 | 0 | 0 | — |
| C02 / opus-5-5 / high | 190,000 | model/effort | 1 | 1 | 11 | 100% |
| C03 / fable-5-2 / high | 260,000 | model/effort | 2 | 5 | 242 | 55% |
| C04 / fable-5-2 / high | 260,000 | model/effort | 3 | 12 | 1142 | 72% |
| C04 / fable-5-2 / medium | 260,000 | model | 2 | 2 | 203 | 64% |
| C04 / fable-5-2 / unknown | 260,000 | model | 0 | 0 | 0 | — |
| C04 / opus-5-5 / high | 190,000 | cell | 6 | 119 | 1388 | 50% |
| C04 / opus-5-5 / medium | 190,000 | cell | 11 | 50 | 702 | 47% |
| C04 / opus / unknown | 200,000 | base | 0 | 0 | 0 | — |
| C05 / opus-5-5 / max | 200,000 | model | 2 | 61 | 3059 | 85% |
| C05 / opus-5-5 / unknown | 200,000 | model | 0 | 0 | 0 | — |
| C06 / opus-5-5 / high | 190,000 | model/effort | 1 | 1 | 4 | 100% |
| C06 / opus-5-5 / medium | 200,000 | model/effort | 1 | 2 | 50 | 100% |
| C06 / sonnet-5-5 / high | 210,000 | model/effort | 4 | 10 | 104 | 39% |
| C06 / sonnet-5-5 / medium | 200,000 | model/effort | 3 | 4 | 206 | 49% |
| C07 / fable-5-2 / high | 260,000 | model/effort | 2 | 3 | 200 | 78% |
| C07 / fable-5-2 / unknown | 260,000 | model | 0 | 0 | 0 | — |
| C07 / opus-5-5 / high | 190,000 | model/effort | 2 | 2 | 100 | 63% |
| C08 / opus-5-5 / high | 190,000 | model/effort | 1 | 1 | 139 | 85% |
| C08 / opus / unknown | 200,000 | base | 0 | 0 | 0 | — |
| C09 / sonnet-5-5 / high | 220,000 | cell | 6 | 11 | 260 | 25% |
| C09 / sonnet-5-5 / unknown | 210,000 | model | 0 | 0 | 0 | — |
| unknown / opus-5-5 / high | 190,000 | model/effort | 4 | 4 | 203 | 20% |
| unknown / opus-5-5 / medium | 200,000 | model/effort | 4 | 11 | 120 | 51% |
| unknown / opus / unknown | 200,000 | base | 0 | 0 | 0 | — |

### Measured growth

| Character / model / effort | Calls | Floor p50 / p90 | Call Δ p50 / p90 | Call Δ mean | Turn deltas | Turn Δ p50 / p90 |
| --- | --- | --- | --- | --- | --- | --- |
| * / * / * | 13192 | 78,063 / 85,890 | 1,627 / 5,847 | 2,828 | 499 | 7,734 / 41,433 |
| * / fable-5-2 / * | 3831 | 78,463 / 84,183 | 1,738 / 7,009 | 3,205 | 207 | 10,636 / 49,982 |
| * / fable-5-2 / high | 3625 | 78,463 / 83,812 | 1,767 / 7,148 | 3,259 | 170 | 11,790 / 58,087 |
| * / fable-5-2 / medium | 206 | 78,111 / 86,694 | 1,483 / 4,507 | 2,258 | 37 | 8,365 / 26,453 |
| * / fable-5-2 / unknown | 0 | — | — | — | 0 | — |
| * / opus-5-5 / * | 6043 | 80,675 / 87,220 | 1,571 / 4,859 | 2,369 | 256 | 5,280 / 20,397 |
| * / opus-5-5 / high | 1984 | 80,854 / 82,876 | 1,170 / 3,412 | 1,771 | 115 | 5,106 / 15,560 |
| * / opus-5-5 / max | 3121 | 66,215 / 73,444 | 1,979 / 6,032 | 2,853 | 37 | 20,238 / 98,709 |
| * / opus-5-5 / medium | 938 | 86,432 / 88,674 | 1,146 / 3,051 | 1,937 | 104 | 4,860 / 12,526 |
| * / opus-5-5 / unknown | 0 | — | — | — | 0 | — |
| * / sonnet-5-5 / * | 3318 | 72,724 / 78,880 | 1,604 / 7,219 | 3,214 | 36 | 15,788 / 85,775 |
| * / sonnet-5-5 / high | 2790 | 74,190 / 79,468 | 1,584 / 6,800 | 3,076 | 35 | 14,278 / 85,775 |
| * / sonnet-5-5 / medium | 528 | 62,249 / 71,277 | 1,792 / 9,718 | 3,943 | 1 | 17,299 / 17,299 |
| * / sonnet-5-5 / unknown | 0 | — | — | — | 0 | — |
| * / opus / * | 0 | — | — | — | 0 | — |
| * / opus / unknown | 0 | — | — | — | 0 | — |
| C01 / fable-5-2 / high | 1557 | 78,840 / 80,765 | 1,582 / 4,697 | 2,405 | 11 | 109,975 / 164,376 |
| C01 / fable-5-2 / unknown | 0 | — | — | — | 0 | — |
| C01 / sonnet-5-5 / high | 2405 | 74,005 / 79,891 | 1,511 / 5,754 | 2,596 | 34 | 14,242 / 85,775 |
| C01 / sonnet-5-5 / medium | 318 | 61,326 / 67,090 | 2,287 / 14,007 | 5,333 | 1 | 17,299 / 17,299 |
| C01 / sonnet-5-5 / unknown | 0 | — | — | — | 0 | — |
| C02 / fable-5-2 / high | 463 | 75,809 / 79,424 | 6,072 / 17,572 | 9,025 | 0 | — |
| C02 / fable-5-2 / unknown | 0 | — | — | — | 0 | — |
| C02 / opus-5-5 / high | 12 | 72,978 / 72,978 | 1,784 / 5,167 | 5,460 | 0 | — |
| C03 / fable-5-2 / high | 247 | 78,095 / 78,915 | 2,316 / 5,506 | 2,857 | 0 | — |
| C04 / fable-5-2 / high | 1155 | 84,292 / 87,051 | 1,614 / 4,989 | 2,528 | 154 | 11,216 / 32,070 |
| C04 / fable-5-2 / medium | 206 | 78,111 / 86,694 | 1,483 / 4,507 | 2,258 | 37 | 8,365 / 26,453 |
| C04 / fable-5-2 / unknown | 0 | — | — | — | 0 | — |
| C04 / opus-5-5 / high | 1508 | 80,868 / 82,643 | 1,236 / 3,397 | 1,834 | 58 | 6,195 / 16,190 |
| C04 / opus-5-5 / medium | 753 | 86,909 / 88,794 | 1,090 / 2,641 | 1,412 | 86 | 4,992 / 12,080 |
| C04 / opus / unknown | 0 | — | — | — | 0 | — |
| C05 / opus-5-5 / max | 3121 | 66,215 / 73,444 | 1,979 / 6,032 | 2,853 | 37 | 20,238 / 98,709 |
| C05 / opus-5-5 / unknown | 0 | — | — | — | 0 | — |
| C06 / opus-5-5 / high | 5 | 72,180 / 72,180 | 1,568 / 3,403 | 1,764 | 0 | — |
| C06 / opus-5-5 / medium | 52 | 82,036 / 84,951 | 1,752 / 7,505 | 4,850 | 4 | 3,827 / 109,729 |
| C06 / sonnet-5-5 / high | 114 | 75,634 / 76,112 | 5,166 / 23,274 | 11,173 | 0 | — |
| C06 / sonnet-5-5 / medium | 210 | 66,055 / 71,277 | 1,462 / 4,093 | 1,897 | 0 | — |
| C07 / fable-5-2 / high | 203 | 73,909 / 80,116 | 1,414 / 3,623 | 1,969 | 5 | 1,995 / 97,992 |
| C07 / fable-5-2 / unknown | 0 | — | — | — | 0 | — |
| C07 / opus-5-5 / high | 102 | 71,358 / 71,372 | 1,202 / 3,911 | 1,748 | 0 | — |
| C08 / opus-5-5 / high | 142 | 67,378 / 67,378 | 1,013 / 3,788 | 1,584 | 18 | 9,294 / 24,439 |
| C08 / opus / unknown | 0 | — | — | — | 0 | — |
| C09 / sonnet-5-5 / high | 271 | 72,611 / 77,226 | 2,045 / 9,492 | 4,153 | 1 | 42,891 / 42,891 |
| C09 / sonnet-5-5 / unknown | 0 | — | — | — | 0 | — |
| unknown / opus-5-5 / high | 215 | 86,755 / 93,660 | 838 / 2,437 | 1,280 | 39 | 3,090 / 8,470 |
| unknown / opus-5-5 / medium | 133 | 82,251 / 88,131 | 1,776 / 5,296 | 3,794 | 14 | 2,872 / 30,520 |
| unknown / opus / unknown | 0 | — | — | — | 0 | — |

### Cost sensitivity

| Character / model / effort | T* | USD/call at T* | −30k | +30k | 150k USD | 200k USD | 250k USD | 300k USD | Proposed USD |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| * / fable-5-2 / * | 237032 | 0.04741 | 1.48% | 1.01% | 0.05799 | 0.04853 | 0.04750 | 0.04920 | 0.04770 |
| * / fable-5-2 / high | 238367 | 0.04767 | 1.45% | 0.99% | 0.05859 | 0.04888 | 0.04775 | 0.04939 | 0.04793 |
| * / opus-5-5 / * | 178428 | 0.03569 | 3.72% | 1.97% | 0.03685 | 0.03608 | 0.03871 | 0.04242 | 0.03608 |
| * / opus-5-5 / high | 165462 | 0.03309 | 4.98% | 2.37% | 0.03344 | 0.03409 | 0.03732 | 0.04135 | 0.03364 |
| * / opus-5-5 / medium | 177917 | 0.03558 | 4.11% | 2.08% | 0.03681 | 0.03601 | 0.03876 | 0.04256 | 0.03601 |
| * / sonnet-5-5 / * | 180826 | 0.01808 | 3.19% | 1.80% | 0.01870 | 0.01823 | 0.01943 | 0.02121 | 0.01839 |
| * / sonnet-5-5 / high | 181016 | 0.01810 | 3.24% | 1.82% | 0.01874 | 0.01824 | 0.01946 | 0.02124 | 0.01841 |
| * / sonnet-5-5 / medium | 173023 | 0.01730 | 3.22% | 1.85% | 0.01760 | 0.01757 | 0.01888 | 0.02069 | 0.01757 |
| C01 / fable-5-2 / high | 216530 | 0.04331 | 1.93% | 1.24% | 0.04953 | 0.04353 | 0.04396 | 0.04646 | 0.04365 |
| C01 / sonnet-5-5 / high | 172017 | 0.01720 | 3.85% | 2.04% | 0.01752 | 0.01751 | 0.01893 | 0.02083 | 0.01751 |
| C01 / sonnet-5-5 / medium | 189204 | 0.01892 | 2.43% | 1.51% | 0.01979 | 0.01896 | 0.01990 | 0.02149 | 0.01907 |
| C02 / fable-5-2 / high | 337375 | 0.06747 | 0.58% | 0.46% | 0.11480 | 0.08267 | 0.07186 | 0.06810 | 0.06766 |
| C04 / opus-5-5 / high | 166972 | 0.03339 | 4.80% | 2.32% | 0.03381 | 0.03431 | 0.03747 | 0.04147 | 0.03388 |
| C04 / opus-5-5 / medium | 165229 | 0.03305 | 5.64% | 2.51% | 0.03341 | 0.03411 | 0.03745 | 0.04157 | 0.03364 |
| C09 / sonnet-5-5 / high | 195406 | 0.01954 | 2.48% | 1.51% | 0.02087 | 0.01955 | 0.02038 | 0.02195 | 0.01975 |

### Continuity exposure

| Character / model / effort | Mid-turn | <5 min / with predecessor | Pending / recorded |
| --- | --- | --- | --- |
| * / * / * | 290 | 94/225 | 10/290 |
| * / fable-5-2 / * | 40 | 0/14 | 2/40 |
| * / fable-5-2 / high | 29 | 0/13 | 0/29 |
| * / fable-5-2 / unknown | 11 | 0/1 | 2/11 |
| * / opus-5-5 / * | 191 | 80/182 | 7/191 |
| * / opus-5-5 / high | 107 | 77/101 | 1/107 |
| * / opus-5-5 / max | 56 | 0/55 | 1/56 |
| * / opus-5-5 / medium | 26 | 3/24 | 5/26 |
| * / opus-5-5 / unknown | 2 | 0/2 | 0/2 |
| * / sonnet-5-5 / * | 52 | 9/23 | 1/52 |
| * / sonnet-5-5 / high | 34 | 6/16 | 0/34 |
| * / sonnet-5-5 / medium | 9 | 1/5 | 0/9 |
| * / sonnet-5-5 / unknown | 9 | 2/2 | 1/9 |
| * / opus / * | 7 | 5/6 | 0/7 |
| * / opus / unknown | 7 | 5/6 | 0/7 |
| C01 / fable-5-2 / high | 12 | 0/4 | 0/12 |
| C01 / fable-5-2 / unknown | 2 | 0/0 | 0/2 |
| C01 / sonnet-5-5 / high | 25 | 4/12 | 0/25 |
| C01 / sonnet-5-5 / medium | 8 | 1/5 | 0/8 |
| C01 / sonnet-5-5 / unknown | 6 | 2/2 | 0/6 |
| C02 / fable-5-2 / high | 5 | 0/0 | 0/5 |
| C02 / fable-5-2 / unknown | 7 | 0/0 | 1/7 |
| C03 / fable-5-2 / high | 3 | 0/1 | 0/3 |
| C04 / fable-5-2 / high | 9 | 0/8 | 0/9 |
| C04 / fable-5-2 / unknown | 1 | 0/1 | 0/1 |
| C04 / opus-5-5 / high | 104 | 77/99 | 1/104 |
| C04 / opus-5-5 / medium | 24 | 2/23 | 5/24 |
| C04 / opus / unknown | 4 | 4/4 | 0/4 |
| C05 / opus-5-5 / max | 56 | 0/55 | 1/56 |
| C05 / opus-5-5 / unknown | 2 | 0/2 | 0/2 |
| C06 / opus-5-5 / medium | 1 | 0/0 | 0/1 |
| C06 / sonnet-5-5 / high | 6 | 1/2 | 0/6 |
| C06 / sonnet-5-5 / medium | 1 | 0/0 | 0/1 |
| C07 / fable-5-2 / unknown | 1 | 0/0 | 1/1 |
| C08 / opus / unknown | 1 | 0/1 | 0/1 |
| C09 / sonnet-5-5 / high | 3 | 1/2 | 0/3 |
| C09 / sonnet-5-5 / unknown | 3 | 0/0 | 1/3 |
| unknown / opus-5-5 / high | 3 | 0/2 | 0/3 |
| unknown / opus-5-5 / medium | 1 | 1/1 | 0/1 |
| unknown / opus / unknown | 2 | 1/1 | 0/2 |

### Intervals with a verified handoff

| Character / model / effort | Actual end threshold | Intervals | Calls p50 / p90 | Prompts p50 / p90 |
| --- | --- | --- | --- | --- |
| * / * / * | 100,000 | 111 | 13 / 19 | 1 / 3 |
| * / * / * | 200,000 | 71 | 44 / 62 | 1 / 3 |
| * / * / * | 252,614 | 1 | 74 / 74 | 1 / 1 |
| * / * / * | 254,253 | 1 | 85 / 85 | 2 / 2 |
| * / * / * | 254,540 | 1 | 119 / 119 | 1 / 1 |
| * / * / * | 255,396 | 1 | 116 / 116 | 1 / 1 |
| * / * / * | 256,405 | 1 | 92 / 92 | 1 / 1 |
| * / * / * | 257,111 | 1 | 66 / 66 | 3 / 3 |
| * / * / * | 258,931 | 1 | 83 / 83 | 4 / 4 |
| * / * / * | 261,773 | 1 | 60 / 60 | 3 / 3 |
| * / * / * | 262,076 | 1 | 30 / 30 | 6 / 6 |
| * / * / * | 270,614 | 1 | 97 / 97 | 16 / 16 |
| * / * / * | 271,305 | 1 | 96 / 96 | 11 / 11 |
| * / * / * | 271,377 | 1 | 59 / 59 | 7 / 7 |
| * / * / * | 274,176 | 1 | 85 / 85 | 3 / 3 |
| * / * / * | 280,039 | 1 | 70 / 70 | 4 / 4 |
| * / * / * | 282,802 | 1 | 76 / 76 | 5 / 5 |
| * / * / * | 315,318 | 1 | 114 / 114 | 4 / 4 |
| * / * / * | 451,517 | 1 | 195 / 195 | 24 / 24 |
| * / * / * | 500,000 | 3 | 201 / 208 | 31 / 37 |
| * / fable-5-2 / * | 252,614 | 1 | 74 / 74 | 1 / 1 |
| * / fable-5-2 / * | 254,253 | 1 | 85 / 85 | 2 / 2 |
| * / fable-5-2 / * | 254,540 | 1 | 119 / 119 | 1 / 1 |
| * / fable-5-2 / * | 255,396 | 1 | 116 / 116 | 1 / 1 |
| * / fable-5-2 / * | 256,405 | 1 | 92 / 92 | 1 / 1 |
| * / fable-5-2 / * | 262,076 | 1 | 30 / 30 | 6 / 6 |
| * / fable-5-2 / * | 270,614 | 1 | 97 / 97 | 16 / 16 |
| * / fable-5-2 / * | 271,305 | 1 | 96 / 96 | 11 / 11 |
| * / fable-5-2 / * | 271,377 | 1 | 59 / 59 | 7 / 7 |
| * / fable-5-2 / * | 451,517 | 1 | 195 / 195 | 24 / 24 |
| * / fable-5-2 / * | 500,000 | 3 | 201 / 208 | 31 / 37 |
| * / fable-5-2 / high | 252,614 | 1 | 74 / 74 | 1 / 1 |
| * / fable-5-2 / high | 254,253 | 1 | 85 / 85 | 2 / 2 |
| * / fable-5-2 / high | 254,540 | 1 | 119 / 119 | 1 / 1 |
| * / fable-5-2 / high | 255,396 | 1 | 116 / 116 | 1 / 1 |
| * / fable-5-2 / high | 256,405 | 1 | 92 / 92 | 1 / 1 |
| * / fable-5-2 / high | 262,076 | 1 | 30 / 30 | 6 / 6 |
| * / fable-5-2 / high | 270,614 | 1 | 97 / 97 | 16 / 16 |
| * / fable-5-2 / high | 271,305 | 1 | 96 / 96 | 11 / 11 |
| * / fable-5-2 / high | 271,377 | 1 | 59 / 59 | 7 / 7 |
| * / fable-5-2 / high | 451,517 | 1 | 195 / 195 | 24 / 24 |
| * / fable-5-2 / high | 500,000 | 3 | 201 / 208 | 31 / 37 |
| * / opus-5-5 / * | 100,000 | 111 | 13 / 19 | 1 / 3 |
| * / opus-5-5 / * | 200,000 | 50 | 48 / 63 | 1 / 3 |
| * / opus-5-5 / * | 257,111 | 1 | 66 / 66 | 3 / 3 |
| * / opus-5-5 / * | 258,931 | 1 | 83 / 83 | 4 / 4 |
| * / opus-5-5 / * | 261,773 | 1 | 60 / 60 | 3 / 3 |
| * / opus-5-5 / * | 274,176 | 1 | 85 / 85 | 3 / 3 |
| * / opus-5-5 / * | 280,039 | 1 | 70 / 70 | 4 / 4 |
| * / opus-5-5 / * | 282,802 | 1 | 76 / 76 | 5 / 5 |
| * / opus-5-5 / * | 315,318 | 1 | 114 / 114 | 4 / 4 |
| * / opus-5-5 / high | 100,000 | 94 | 14 / 19 | 1 / 2 |
| * / opus-5-5 / high | 200,000 | 2 | 86 / 115 | 26 / 32 |
| * / opus-5-5 / max | 200,000 | 47 | 47 / 63 | 1 / 3 |
| * / opus-5-5 / max | 257,111 | 1 | 66 / 66 | 3 / 3 |
| * / opus-5-5 / max | 258,931 | 1 | 83 / 83 | 4 / 4 |
| * / opus-5-5 / max | 261,773 | 1 | 60 / 60 | 3 / 3 |
| * / opus-5-5 / max | 274,176 | 1 | 85 / 85 | 3 / 3 |
| * / opus-5-5 / max | 280,039 | 1 | 70 / 70 | 4 / 4 |
| * / opus-5-5 / max | 282,802 | 1 | 76 / 76 | 5 / 5 |
| * / opus-5-5 / max | 315,318 | 1 | 114 / 114 | 4 / 4 |
| * / opus-5-5 / medium | 100,000 | 17 | 13 / 23 | 3 / 4 |
| * / opus-5-5 / medium | 200,000 | 1 | 11 / 11 | 1 / 1 |
| * / sonnet-5-5 / * | 200,000 | 21 | 20 / 60 | 1 / 1 |
| * / sonnet-5-5 / high | 200,000 | 16 | 26 / 62 | 1 / 1 |
| * / sonnet-5-5 / medium | 200,000 | 5 | 16 / 21 | 1 / 1 |
| C01 / fable-5-2 / high | 254,253 | 1 | 85 / 85 | 2 / 2 |
| C01 / fable-5-2 / high | 254,540 | 1 | 119 / 119 | 1 / 1 |
| C01 / fable-5-2 / high | 255,396 | 1 | 116 / 116 | 1 / 1 |
| C01 / fable-5-2 / high | 256,405 | 1 | 92 / 92 | 1 / 1 |
| C01 / sonnet-5-5 / high | 200,000 | 12 | 43 / 62 | 1 / 1 |
| C01 / sonnet-5-5 / medium | 200,000 | 5 | 16 / 21 | 1 / 1 |
| C03 / fable-5-2 / high | 252,614 | 1 | 74 / 74 | 1 / 1 |
| C04 / fable-5-2 / high | 262,076 | 1 | 30 / 30 | 6 / 6 |
| C04 / fable-5-2 / high | 270,614 | 1 | 97 / 97 | 16 / 16 |
| C04 / fable-5-2 / high | 271,305 | 1 | 96 / 96 | 11 / 11 |
| C04 / fable-5-2 / high | 271,377 | 1 | 59 / 59 | 7 / 7 |
| C04 / fable-5-2 / high | 451,517 | 1 | 195 / 195 | 24 / 24 |
| C04 / fable-5-2 / high | 500,000 | 3 | 201 / 208 | 31 / 37 |
| C04 / opus-5-5 / high | 100,000 | 94 | 14 / 19 | 1 / 2 |
| C04 / opus-5-5 / medium | 100,000 | 17 | 13 / 23 | 3 / 4 |
| C05 / opus-5-5 / max | 200,000 | 47 | 47 / 63 | 1 / 3 |
| C05 / opus-5-5 / max | 257,111 | 1 | 66 / 66 | 3 / 3 |
| C05 / opus-5-5 / max | 258,931 | 1 | 83 / 83 | 4 / 4 |
| C05 / opus-5-5 / max | 261,773 | 1 | 60 / 60 | 3 / 3 |
| C05 / opus-5-5 / max | 274,176 | 1 | 85 / 85 | 3 / 3 |
| C05 / opus-5-5 / max | 280,039 | 1 | 70 / 70 | 4 / 4 |
| C05 / opus-5-5 / max | 282,802 | 1 | 76 / 76 | 5 / 5 |
| C05 / opus-5-5 / max | 315,318 | 1 | 114 / 114 | 4 / 4 |
| C06 / sonnet-5-5 / high | 200,000 | 2 | 10 / 10 | 1 / 1 |
| C09 / sonnet-5-5 / high | 200,000 | 2 | 16 / 20 | 1 / 1 |
| unknown / opus-5-5 / high | 200,000 | 2 | 86 / 115 | 26 / 32 |
| unknown / opus-5-5 / medium | 200,000 | 1 | 11 / 11 | 1 / 1 |

### Fable floors over the base

| UTC date | Logged floor | First input | Call # matching floor | Trigger | Linked successor first input(s) |
| --- | --- | --- | --- | --- | --- |
| 2026-11-18 | 389142 | 85140 | [146] | 502960 | [86701] |
| 2026-11-19 | 421330 | 86701 | [173] | 501033 | [87086] |
| 2026-11-24 | 232244 | 84183 | [40] | 520392 | [83812] |

### Logged pin observations

| Character | Logged model | Pin | Rolls | Floors | Floor range | Room range |
| --- | --- | --- | --- | --- | --- | --- |
| C04 | opus | 100000 | 132 | 132 | (72476, 107890) | (-7890, 27524) |
