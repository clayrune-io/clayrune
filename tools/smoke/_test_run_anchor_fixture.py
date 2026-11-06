"""Hermetic P14 enrollment fixture for the real in-chat browser smoke."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mc import state
from mc.test_run_anchor import FLAG, post
from mc.test_run_ingest import OwnerBinding, TestRunIngest
from mc.test_run_store import TestRunStore

with tempfile.TemporaryDirectory(prefix='test-run-anchor-') as folder:
    state.CONFIG = {FLAG: True}
    session = dict(project_id='smoke_trc', session_id='trc_sid')
    state.agent_sessions = {'trc_sid': session}
    store = TestRunStore(Path(folder))
    ingest = TestRunIngest(store, resolve_owner=lambda p, c: OwnerBinding(p, 'trc_sid', False, False))
    run, _ = ingest.enroll('smoke_trc', None, {'runner': 'pytest', 'display_label': 'Server-enrolled anchor'})
    assert not post(TestRunStore(Path(folder)), 'smoke_trc', run['run_id'])
    assert session['log_lines'] == [f"[test-run:{run['run_id']}]"]
    print(json.dumps({'run': run, 'lines': session['log_lines']}))
