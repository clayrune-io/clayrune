"""Make the test-run store behave like a freshly started server (MC-1179 SF30).

Since SF30 a run this process just wrote is served from memory, and a finished
run is trusted from the validated ledger, so neither is read back from disk.
Tests that count reads, validations or cache admissions are about the cold path
(first use after a restart, or after an outside edit); they call ``cold`` to get
it: no open-run state is kept and no run is trusted, so every first sight of a
file replays it, exactly as before SF30.
"""
from mc import test_run_store as runs
from mc.test_run_ledger import ValidatedLedger
from mc.test_run_open import OpenRegistry


class NoLedger(ValidatedLedger):
    def get(self, path, fingerprint):
        return None

    def add(self, path, fingerprint, snapshot):
        return None


def cold(monkeypatch):
    monkeypatch.setattr(runs, '_OPEN', OpenRegistry(max_entries=0))
    monkeypatch.setattr(runs, '_LEDGER', NoLedger())
