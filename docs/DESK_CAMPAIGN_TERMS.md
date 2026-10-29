# Campaign term edits (MC-1125)

Branch fix, 2026-10-29. No live-data migration or production acceptance is
included. The policy lives in `mc/desk_campaign_term.py`; `mc/desk.py` imports
it and calls it once inside the existing locked update path, before bounds
comparison. Start, Approve and Renew remain human-only, passcode-gated routes.

## Stale-save root cause

1. `static/js/desk-v1-how.js` changes `plan.end.date` in the Ends field handler;
   it does not change the browser's `camp.term`.
2. Before this fix, `_approveBounds` in `static/js/desk-v1-campaign.js` called
   `deskV1CampaignAction(camp, 'approve', ['plan', 'how', 'goal', 'term'])`.
3. `deskV1CampaignAction` in `static/js/desk-v1-setup.js` first calls
   `deskV1PatchCampaign`, which copies every requested key directly into a PATCH.
4. `v1_campaign_in` and `update_campaign` in `mc/desk.py` both accepted `term`.
   The update replaced each allowed field verbatim. A stale term in that
   pre-approval save therefore overwrote a more recent server term; Approve
   subsequently snapshotted the mismatched plan/term.

The regression smoke drives the real Ends field and approval button, captures
both PATCH bodies, and verifies neither contains `term`. The backend regression
sends a stale term alongside a later end date through the real v1 API.

## Ownership and derivation

- Draft/proposed campaigns may propose a term; the existing Start validation
  still refuses terms over 90 days.
- After Start, ordinary PATCHes ignore `term`, including attempts to remove it,
  change its index/start/post cap, or save an old browser copy. The previous
  campaign state, `started_at` and approval record establish ownership, so a
  client cannot regain term writes by relabeling a started campaign as draft.
- A plan end-date edit derives the current end as the earlier of the requested
  date and current `term.starts + MAX_TERM_DAYS`. It keeps the term's index,
  start and post cap; any matching current history row follows it. Closed-term
  rows and approval snapshots stay unchanged.
- Saving an unchanged plan also repairs a previously inconsistent, shorter
  term, without rolling back a newer stored end. Removing the plan deadline
  leaves the current term ceiling intact.
- Derivation runs before the existing bounds tracker. Extending either bound
  leaves publishing blocked until an explicit human approval. Renew retains
  its existing goal-deadline and other-bounds checks.

Ignoring client terms rather than rejecting the entire PATCH keeps already-open
tabs able to save legitimate plan edits. Removing the source key in Launch
prevents new tabs from sending it at all. No blanket migration is required.

## Recovery in the UI

An expired plan takes priority over an expired term in the hold message: it
names **Brief > Limits > Ends**, followed by **Launch > Approve the new bounds**.
A term-only expiry names **Launch > Renew term**, now exposed for short and
long campaigns alike. Existing goal-deadline restrictions still apply. Held
posts retain their existing requirement for a fresh review/approval.

## Verification

All checks ran against isolated stores or synthetic browser APIs:

| Check | Result |
| --- | --- |
| Relevant pytest set below | 302 passed, rc=0 |
| `pyright mc/desk_campaign_term.py` | 0 errors, rc=0 |
| `node tools/smoke/desk-v1-campaign.mjs` | rc=0 |
| `node tools/smoke/desk-v1-campaign-content.mjs` | rc=0, 1440 and 390 widths |
| `node tools/smoke/desk-v1-campaign-chat.mjs` | rc=0 |
| `node tools/smoke/desk-v1-live-campaign.mjs` | rc=0, includes MC-1125 request regression |
| `node tools/smoke/boot-smoke.mjs` | rc=0 |

```text
python -m pytest tests/test_desk_campaign_term.py tests/test_desk_approval.py tests/test_desk_start_gate.py tests/test_desk_tick.py tests/test_desk_reapproval_parity.py tests/test_desk.py tests/test_desk_routes.py tests/test_desk_results.py -q
```

The first live-campaign smoke attempt failed in the pre-existing Resume
success case: its fixture end date was 2026-10-20, so the real clock refused
Resume before the intended PATCH. The harness now fixes that scenario's browser
clock at 2026-10-01, and explicitly uses 2026-10-29 for the new expiry regression.
Product expiry checks are unchanged. The rerun passed every scenario, including
wrong-passcode refusal, cancel rollback and preview mode sending no API writes.

The main checkout's smoke dependencies were linked into the worktree temporarily;
no dependency installation was performed. Live publishing and acceptance on a
real campaign remain unverified; integration owns deployment and that check.
