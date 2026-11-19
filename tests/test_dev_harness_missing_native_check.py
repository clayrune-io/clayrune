from types import SimpleNamespace

import pytest

from mc import dev_harness_native_plan, dev_harness_runner
from mc.dev_harness_store import StoreError


def test_approved_run_refuses_an_unknown_check(monkeypatch):
    snapshot = {'mode': 'preview', 'policy': {}, 'native_plan': {'payload': {'steps': []}}}
    store = SimpleNamespace(read=lambda _: snapshot)
    monkeypatch.setattr(dev_harness_native_plan, 'validate_plan', lambda *args: None)

    with pytest.raises(StoreError, match='Requested check is not in the approved native plan'):
        dev_harness_runner.run_approved(
            store=store, project={'id': 'p'}, snapshot=snapshot, frozen={}, check_id='missing',
            register=lambda *_: None, unregister=lambda _: None,
        )
