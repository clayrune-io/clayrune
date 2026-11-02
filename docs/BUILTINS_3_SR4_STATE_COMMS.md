# BUILTINS-3 SR-4: role state, journal and decision lifecycle

Branch-only. Modules: `mc/steward_role_state.py`, `mc/steward_role_comms.py`.
Tests: `tests/test_steward_role_state.py`, `tests/test_steward_role_comms.py`
(fake mailer and fake digest; no mail, IMAP or model call).

## State (`RoleStateStore(data_root)`)

File: `<data_root>/steward/roles/<project>/<schedule>.json`, gitignored via
`data/steward/` in `.gitignore`, outside `data/projects/`. A present but
damaged file (NUL, bad JSON, wrong version/ids/keys) raises `StateError`; it is
never replaced by a default.

| Call | Purpose |
|---|---|
| `read(pid, sid)` / `update(pid, sid, mutate)` | read; locked read-modify-validate-fsync-replace transaction |
| `lock(pid, sid)` | the per-assignment RLock |
| `acquire_claim(pid, sid, claim_id, trigger, now)` | `(won, existing)`; claim starts `preparing`; never stolen by age |
| `set_claim_state(pid, sid, claim_id, 'delivery_unknown'\|'accepted')` | explicit transition |
| `accept_delivery(pid, sid, claim_id, delivered_at)` | claim -> `accepted` AND watermark forward, one write; the only way the watermark moves |
| `release_claim(pid, sid, claim_id)` | clear the claim by exact id |
| `record_session`, `record_attempt`, `record_completion`, `set_result` | session ids, timestamps, hashes, last result |

## Comms

* `append_journal(project, relative_path, event, body, schedule_id=, now=)` -
  confined to the registered root's `docs/_journal/`, append-only, fsynced.
* `record_lifecycle(project, schedule, event, detail, store=, now=)` - never
  raises; events: started, completed, skipped_busy, skipped_disabled,
  blocked_charter, blocked_guard, blocked_channel, dispatch_failed.
* `check_pending_replies(pid, sid, store=, digest_reader=)` - cycle-start read
  of each open emailed ask through `tools/mail-mcp/read_digest.py` only.
* `raise_decision(project, schedule, DecisionRequest, RouteContext, store=,
  mailer=, digest_reader=)` - route, dedupe, cap, send.
  Routing: spawner id present -> `reported` to spawner; attended -> chat;
  else email. Spawner/chat never read or send mail.
* `default_mailer` / `default_digest_reader` wrap `send_mail.py` /
  `read_digest.py`; both refuse under pytest.

Send outcomes: exit 0 `sent`; 1/2/4/5 `held` (retryable, visible); 3
(accepted but not in mailbox) `held: not_delivered`, counts toward the cap; any
other code, timeout or OSError `delivery_unknown` (never resent; a crash
mid-send is reconciled to the same). Cap 1/day (local date), at most 5
unanswered asks. A reply digest is untrusted: `ReplyCheck.grants_authority` is
always False and the reply text is never persisted.

## Seams for the fire path (not wired here)

1. Before dispatch: `store.acquire_claim(...)`; if not won, `record_lifecycle(..., 'skipped_busy')`.
2. After task built, before hand-off: `record_attempt(..., snapshot_hashes=)`.
3. Delivery ack: `store.accept_delivery(...)`; failure/ambiguity: `set_claim_state(..., 'delivery_unknown')`.
4. Cycle end: `record_lifecycle(..., 'completed')` then `release_claim`.
5. Cycle start (role session): `check_pending_replies(...)`.
6. Decisions: `raise_decision(..., RouteContext(spawner_session_id=, human_attended=))`.
