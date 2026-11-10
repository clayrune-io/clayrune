# Continuation-model failure audit

Audited base: `9f73231fdf538fd543735ca042a71a5f870e9155` (MC-1168 r14 merge).
Task: classify nine continuation-test failures before updating any fixtures.

Outcome: **stop for a real source regression**. The model-choice storage-failure
contract still requires HTTP 503 and an unchanged live pin. The ownership
snapshot added in `6f808001` executes outside that route's persistence error
handler. A failed snapshot read or save now escapes the route. No production
source, existing test, or original assertion was changed during this audit.
This report is a local handoff, not a fix or deployment claim.

## Per-test verdict

All names below are in `tests/test_continuation_model.py`. "Stale" describes
fixture setup, never permission to discard the behavior asserted. These eight
classifications follow the source contracts; corrected fixtures were not run
because the task requires stopping on a real regression.

| Failing case | Verdict | Evidence and required fixture correction |
| --- | --- | --- |
| `test_set_model_choice_is_durable_before_success[claude-opus-5]` | Stale fixture | Loader double rejects `strict=True` before the route can persist. Accept the actual strict-read interface; retain every durability, identity and pin assertion. |
| `test_set_model_choice_is_durable_before_success[]` | Stale fixture | Same loader mismatch for explicit clear. Retain the `model_auto_requested` and empty-pin assertions. |
| `test_set_model_storage_failure_is_not_acknowledged` | **Real source regression; also stale fault injection** | `_agent_log_is_readable=False` no longer injects a mutation failure, so the original test receives a success Response and fails unpacking it. Injecting a real strict-read or snapshot-save fault exposes an uncaught `PermissionError` instead of the required 503. Do not change the expected status or pin preservation. |
| `test_manual_pending_preserves_engine_and_native_id` | Stale fixture | Pending writer logs the loader's unexpected `strict` keyword and never reaches the fake save. Loader signature must change; all three provider/model/native-ID assertions stay. |
| `test_native_reader_persists_id_before_process_exit` | Stale fixture | Fake pending row and live session have no matching delivery owner. `_runtime_note_init` deliberately refuses unowned rows via `DeliveryAttempt.owns_row`. Supply a matching nonempty token; preserve native-ID and prior usage assertions. |
| `test_manual_claude_init_backfills_native_id` | Stale fixture | Both the unsupported strict-loader keyword and absent matching owner prevent backfill. Supply the current loader interface and owner; retain the backfill assertion. |
| `test_late_claude_init_preserves_completed_row` | Stale fixture | Identity-only backfill deliberately refuses the fixture's unowned row. Supply a matching owner and include its unchanged token in the complete-row equality; preserve every existing status, summary, usage, Scribe and identity expectation. |
| `test_parallel_pending_transactions_cannot_lose_another_session` | Stale fixture | Loader rejects `strict=True` before signalling `first_loaded`. Additionally, `_log_agent_dispatch_pending` now takes the manager lock before the leaf lock; the second writer cannot reach the fixture's leaf-attempt signal while the first holds the manager lock. A future fixture correction must observe the actual manager-to-leaf order and retain both writers, completion checks and exact session-ID set. |
| `test_runtime_start_failure_updates_only_pending_state` | Stale fixture | Failure writer requires a matching nonempty owner as well as pending status. Supply owned rows/sessions; keep the pending-to-error, failure-summary and completed-row-preservation assertions. |

The ownership requirement is explicit in `mc/delivery_attempt.py`:
`owns_row` requires a nonempty attempt ID and the same durable row token.
`tests/test_runtime_delivery_truth_r8.py` separately verifies that native INIT,
identity backfill and startup-failure writers refuse absent, empty and successor
owners. Restoring writes to unowned legacy rows would undo that protection.

## Regression evidence

At the audited base:

- `mc/blueprints/agent_routes.py:13632` calls
  `agent_log_maintenance.snapshot(project_id)` before the `try` at line 13663.
- `mc/agent_log_maintenance.py:11` implements that snapshot using
  `_update_agent_log(project_id, claim_rows)`, so it can fail on reads **or saves**.
- `mc/blueprints/agent_routes.py:7406` and line 7409 perform the initial strict
  read and its single bounded reread. Persistent read failure propagates.
- Line 7411 performs the snapshot save, whose failure also propagates.
- The existing 503 handler covers only the later `save_choice` transaction.
  Failures before it bypass the handler. Neither failure changes the live pin.

`git log -S 'agent_log_maintenance.snapshot(project_id)'` identifies
`6f808001` as introducing the route snapshot. The first parent of the merge,
`1c3d9479`, has no snapshot before the existing error handler. Thus this is a
regression introduced by the merged MC-1168 work, not solely the final r14
strict-loader signature change.

The diagnostic below uses current loader signatures, real snapshot/mutation
functions, the real route and explicit storage faults. Its four cases produce
**2 failed, 2 passed**, exit **1**: strict read and snapshot save raise
`PermissionError('storage unavailable')`; later choice-save failure returns
503 with the original pin; the no-fault control returns 200 and applies the pin.
The first diagnostic version selected only persistent read and snapshot-save
faults and produced 2 failed, exit 1; the final version adds the two controls.

Save this diagnostic as `_scratch/continuation_model/test_storage_contract.py`
to reproduce without modifying the original test:

```python
from threading import RLock
from types import SimpleNamespace

from flask import Flask
import pytest

from tests.test_revive_non_claude_character_ref import ar


@pytest.mark.parametrize('fault', ['read', 'snapshot-save', 'choice-save', 'none'])
def test_storage_failure_returns_503_and_keeps_pin(ar, monkeypatch, fault):
    session = dict(project_id='p1', session_id='s1', provider='claude',
                   pinned_model='sonnet')
    ar.agent_sessions['s1'] = session
    monkeypatch.setattr(ar, 'get_manager', lambda _: SimpleNamespace(lock=RLock()))
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a: None)

    def load(_, *, strict=False):
        assert strict is True
        if fault == 'read':
            raise PermissionError('storage unavailable')
        return []

    saves = []

    def save(*args):
        saves.append(True)
        if fault == 'snapshot-save' or (fault == 'choice-save' and len(saves) == 2):
            raise PermissionError('storage unavailable')

    monkeypatch.setattr(ar, '_load_agent_log', load)
    monkeypatch.setattr(ar, '_save_agent_log', save)
    app = Flask(__name__)
    with app.test_request_context(json={'model': 'opus'}):
        try:
            response = app.make_response(ar.agent_set_model('p1', 's1'))
        finally:
            if fault != 'none':
                assert session['pinned_model'] == 'sonnet'
    assert response.status_code == (200 if fault == 'none' else 503)
    if fault == 'none':
        assert response.get_json()['ok'] is True
        assert session['pinned_model'] == 'opus'
```

The narrow source repair would include snapshot acquisition and owner setup
inside the same persistence-error handling boundary, preserving strict reads,
owner comparison and persistence-before-pin ordering. It was **not applied**:
the task explicitly forbids editing `agent_routes.py` and requires reporting
source regressions instead of rewriting tests.

## Verification

Every executed pytest command used the installation's `tools/test-run.py`, the
project's Python interpreter, the task worktree as cwd and unchanged environment.
The installation and interpreter paths were absolute in the executed commands.
The portable notation below substitutes only machine-specific path prefixes:

```text
<project-python> <installation>/tools/test-run.py --project mission_control --port 5199 -- <project-python> -m pytest <selection> -o addopts= -q --tb=short
```

| Selection | Exact result | Child exit |
| --- | --- | ---: |
| `tests/test_continuation_model.py` (original file, unchanged) | 9 failed, 22 passed | 1 |
| All ten `tests/test_runtime_delivery_truth*.py` files plus `tests/mc1168_r14` | 171 passed | 0 |
| The same ten runtime-delivery files alone | 157 passed | 0 |
| `tests/mc1168_r14` alone | 14 passed | 0 |
| `-p tests.conftest _scratch/continuation_model/test_storage_contract.py` (final four-case diagnostic) | 2 failed, 2 passed | 1 |

The explicit runtime selection, in execution order:

```text
tests/test_runtime_delivery_truth.py tests/test_runtime_delivery_truth_r2.py tests/test_runtime_delivery_truth_r3.py tests/test_runtime_delivery_truth_r4.py tests/test_runtime_delivery_truth_r5.py tests/test_runtime_delivery_truth_r6.py tests/test_runtime_delivery_truth_r7.py tests/test_runtime_delivery_truth_r8.py tests/test_runtime_delivery_truth_r9.py tests/test_runtime_delivery_truth_r11.py
```

The combined run appended `tests/mc1168_r14` to that exact list. Counts overlap;
they are not additive across runs. The diagnostic explicitly loads
`tests.conftest` to retain data isolation and the live-state/real-CLI guards.
No real provider process, production route request, merge, push or restart was
used. Framework stdout and child exits establish the reported counts; launcher
transport, parent enrollment, pytest enrollment and queue-overflow progress gaps
were printed on some runs. An initial relative-interpreter launcher invocation
returned `child_start_failed_FileNotFoundError` before starting pytest and is
not counted as a test run. It was corrected to the same interpreter's absolute
path with the same selection, cwd and environment.

## Handoff

This commit changes documentation only. Original tests and all production
modules, including `mc/agent_mail_*` and `agent_routes.py`, remain untouched.
Changelog, user guide, README and agent rules need no updates because no product
behavior changed. After the source regression is repaired in its owning branch,
the eight stale fixtures can be updated without weakening their assertions.
