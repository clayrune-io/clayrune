"""DCW-C14: the status path reads the check record under the same lock the check writes it under.

`verify` stores a record and then adds the signed-out state to it, both under `_lock`. A status
read that does not take the lock can land between the two and report a signed-out account as
"not checked".
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mc.desk_connect import verification  # noqa: E402


class _Provider:
    def credential_state(self, method, account_id):
        return {'state': 'key_stored', 'entry': 'fixture-entry', 'stamp': 1}


def test_status_waits_for_a_check_that_is_writing_its_record():
    verification._forget_all_for_tests()
    done = threading.Event()
    got = {}

    def read():
        got['out'] = verification.status('fixture', 'api_key', provider=_Provider())
        done.set()
    with verification._lock:
        t = threading.Thread(target=read, daemon=True)
        t.start()
        assert not done.wait(0.3)               # the writer holds the lock: the read has not gone past it
    assert done.wait(5) and got['out']['state'] == 'key_stored'
    t.join(5)


def test_status_still_reports_a_matching_record():
    verification._forget_all_for_tests()
    key = ('fixture', 'api_key', '')
    with verification._lock:
        verification._records[key] = {'entry': 'fixture-entry', 'stamp': 1, 'at': 'then', 'identity': 'me',
                                      'capability': None, 'verified': True}
    out = verification.status('fixture', 'api_key', provider=_Provider())
    assert out['state'] == 'verified' and out['at'] == 'then'
    verification._forget_all_for_tests()
