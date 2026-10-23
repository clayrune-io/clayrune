# Desk receipt outcomes (MC-968 step 9)

`mc/desk_outcomes.py` owns the pure low-reach rule. The caller supplies an aware
`now`, a published ledger receipt and a threshold. The named default
`POSSIBLY_SUPPRESSED_IMPRESSIONS` is 5; live config
`desk_suppression_impressions` overrides it. Settings persists finite,
non-negative numeric values behind its existing human/passcode gates. An
invalid/unset stored setting falls back to the default; zero disables positive
flags. No setting, campaign approval or publication authority is expanded.

For each metric, use the newest observation timestamp (last appended wins a
tie). A malformed timestamp makes ordering unknown. Missing, boolean, textual,
negative, non-finite or future values cannot establish a number. Observation
timestamps and publication timestamps must carry time zones. No missing metric
is filled with zero. No measurement before publication counts.

`possibly_suppressed` is true only when the receipt has an id and a valid
publication time, and its latest validly ordered impressions observation was
taken >=24 hours after publication, with value strictly below the threshold.
It is false for an eligible observation at or above the threshold and null
when unassessed. The receipt's present age never makes an early observation
eligible. The label is **possibly suppressed**, never a platform verdict.

`mc/desk_retro.py` adds `receipt_outcomes[]` to its existing GET response for
the selected campaign and term. This projection is read-only and uses the
existing engagement outcomes. `static/js/desk-v1-outcomes.js` renders these
cells inside v1 Results/retro, even when the interim retro is collapsed.
Legacy `desk.js` is unchanged. No new browser/API engagement calls are made.

`mc/desk_campaign_results.py` adds previous-campaign evidence to
`build_suggest_brief`. It selects the latest `done` campaign in the same
project, excluding the current campaign. The store has no completion timestamp:
ordering uses `updated_at`, falling back to `created_at`, then id. A later edit
to a completed campaign can affect that ordering. Running/paused campaigns do
not qualify just because their planned end date passed.

The digest contains one title (60 characters), receipt count, impressions and
replies sums with measured/total coverage, assessed/unknown counts and the
possibly-suppressed count. It has no post bodies or history list and remains
below 1,400 characters for normal finite recorded metrics, including an
all-escaped Unicode title. Daily readings are not summed: only the latest
measurement per receipt is included. Unknown coverage is separate from zero.

Verification commands (run from the checkout root):

```text
python -m pytest tests/test_desk_outcomes.py tests/test_desk_campaign_results.py tests/test_desk_retro_campaign.py tests/test_settings_routes.py -q
python -m pytest tests -k desk -q
python -m pytest tests/test_desk_v1_flag.py tests/test_desk_outcomes.py tests/test_desk_campaign_results.py tests/test_desk_retro_campaign.py tests/test_settings_routes.py -q
pyright mc/desk_outcomes.py mc/desk_campaign_results.py
node tools/smoke/desk.mjs
node tools/smoke/boot-smoke.mjs
node tools/smoke/desk-v1-retro.mjs
node tools/smoke/desk-v1-outcomes.mjs
```

The outcomes smoke boots the real v1 Results page against a hermetic API,
checks displayed measurements/unknowns, escaped post text, interim expansion
and viewport fit. It writes receipt-cell screenshots at widths 1440 and 390
to `_scratch/step9/results-{width}.png`. These are fixture-based UI checks;
authenticated live engagement collection is not exercised.

Validation on 2026-10-23: the focused backend set passed (90 checks), both new
modules passed pyright, and all four listed smokes passed. The full Desk run
reported one pre-existing stale source assertion in `test_desk_v1_flag.py`:
it expected the Desk toggle and timezone field in the old monolithic settings
file. The test now checks the owning modules and mounting hooks. The repaired
flag file plus focused backend set passed together (105 checks). The original
full-run exit code is 1; the repair recheck is 0. Passing full-suite cases were
not repeated after this test-only repair.
