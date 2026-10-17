"""Lost-ack confirmation must never replay or infer delivery from age."""
import json
import os
import sqlite3
from pathlib import Path

import pytest
from flask import Flask

from mc.agent_runtime import ClaudeRuntime
from mc.delegation_delivery import DeliveryStore
import mc.delegation_transcript_reconcile as reconcile
from mc.delegation_transcript_reconcile import TranscriptReconciler, dry_run
from mc import delegation_waiting as dw
from mc.blueprints.delegation_report_routes import register_routes


HEADER = '[dispatched agent finished] Sol_Tobin (session child) ended with status=completed.'


@pytest.fixture
def setup(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.db', clock=lambda: 1000.0)
    payload = {'message': HEADER + '\n\nThe report', 'child_session_id': 'child', 'who': 'Sol_Tobin'}
    store.enqueue('child:turn:1', 'p', 'parent', payload)
    store.accept('child:turn:1', 'p', 'parent', payload)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE inbox SET state='uncertain',recovery_required=1")
    project_path = tmp_path / 'project'
    home = tmp_path / 'transcripts'
    rec = TranscriptReconciler(projects_home=home)
    return store, rec, project_path


def write_transcript(setup, *, content=HEADER, stamp='1970-01-01T00:16:41Z',
                     kind='user', shared=False, name='unrecorded-rollover', identity=''):
    store, rec, project = setup
    cwd = project if shared else project / '.clayrune' / 'agents' / 'parent'
    directory = rec.projects_home / ClaudeRuntime._encoded_dir_candidates(str(cwd))[-1]
    directory.mkdir(parents=True, exist_ok=True)
    records = []
    if identity:
        records.append({'type': 'user', 'message': {'content': identity}})
    records.append({'type': kind, 'timestamp': stamp, 'message': {'content': content}})
    path = directory / (name + '.jsonl')
    path.write_text('\n'.join(json.dumps(r) for r in records), encoding='utf-8')
    return path


def tick(setup, log=None):
    store, rec, project = setup
    return rec.tick(store, {}, lambda pid: {'project_path': str(project)}, lambda pid: log or [])


def state(store, table='inbox'):
    return store.status(table, 'child:turn:1', 'p')['state']


@pytest.mark.parametrize('parked', ['uncertain', 'blocked'])
def test_match_confirms_unrecorded_parent_rollover_without_replay(setup, parked):
    store, _, _ = setup
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE inbox SET state=?', (parked,))
    write_transcript(setup)
    assert tick(setup) == 1
    assert state(store) == state(store, 'outbox') == 'confirmed_in_transcript'
    assert store.claim_inbox() is None and store.claim_outbox() is None
    assert store.status('inbox', 'child:turn:1', 'p')['recovery_required'] == 0
    assert store.list_recovery_status('p')['total'] == 0
    dw.configure(store.path)
    try:
        assert dw.waiting_by_parent('p') == {}
    finally:
        dw.configure(None)


@pytest.mark.parametrize('case', ['missing', 'wrong_header', 'assistant', 'older', 'equal',
                                  'missing_timestamp', 'tool_result', 'inline_header', 'shared_unowned'])
def test_non_evidence_keeps_warning(setup, case):
    options = {
        'wrong_header': {'content': HEADER.replace('child', 'other')},
        'assistant': {'kind': 'assistant'},
        'older': {'stamp': '1970-01-01T00:16:39Z'},
        'equal': {'stamp': '1970-01-01T00:16:40Z'},
        'missing_timestamp': {'stamp': ''},
        'tool_result': {'content': [{'type': 'tool_result', 'content': HEADER}]},
        'inline_header': {'content': 'Quoted inline: ' + HEADER},
        'shared_unowned': {'shared': True},
    }
    if case != 'missing':
        write_transcript(setup, **options.get(case, {}))
    assert tick(setup) == 0
    assert state(setup[0]) == 'uncertain'


def test_text_blocks_and_newer_row_timestamp_match(setup):
    write_transcript(setup, content=[{'type': 'text', 'text': HEADER + '\nbody'}])
    assert tick(setup) == 1


@pytest.mark.parametrize('prefix', [
    '=== End of prior conversation (rollover) ===\n\n',
    '--- STANDING POSITIONS ---\nA saved position\n--- RELEVANT MEMORY ---\nA saved memory\n\n',
])
@pytest.mark.parametrize('blocks', [False, True])
def test_callback_whole_line_after_injected_prefix_matches(setup, prefix, blocks):
    text = prefix + HEADER + '\n\nIts final message follows.'
    content = [{'type': 'text', 'text': text}] if blocks else text
    write_transcript(setup, content=content)
    assert tick(setup) == 1


def test_prefixed_header_only_in_nested_tool_result_is_not_receipt(setup):
    write_transcript(setup, content=[
        {'type': 'text', 'text': 'A tool returned this historical excerpt.'},
        {'type': 'tool_result', 'content': [{'type': 'text', 'text': 'Preamble\n' + HEADER}]},
    ])
    assert tick(setup) == 0
    assert state(setup[0]) == 'uncertain'


def test_unowned_prefixed_receipt_and_tool_result_identity_do_not_clear(setup):
    write_transcript(setup, shared=True, content=[
        {'type': 'text', 'text': 'Preamble\n' + HEADER},
        {'type': 'tool_result', 'content': '/api/project/p/agent/parent/job'},
    ])
    assert tick(setup) == 0


def test_shared_directory_scanned_once_for_all_parent_groups(setup, monkeypatch):
    store, rec, project = setup
    header2 = HEADER.replace('child', 'child2')
    store.enqueue('child2:turn:1', 'p', 'parent2', {'message': header2})
    store.accept('child2:turn:1', 'p', 'parent2', {'message': header2})
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE inbox SET state='uncertain'")
    path = write_transcript(setup, shared=True, name='shared', content=HEADER + '\n' + header2)
    calls = []
    original = reconcile._scan_file

    def scan(path, *args):
        calls.append(path)
        return original(path, *args)

    monkeypatch.setattr(reconcile, '_scan_file', scan)
    project_reads = []
    log_reads = []

    def load_project(pid):
        project_reads.append(pid)
        return {'project_path': str(project)}

    def load_log(pid):
        log_reads.append(pid)
        return [{'session_id': p, 'claude_session_ids': ['shared']}
                for p in ('parent', 'parent2')]

    assert rec.tick(store, {}, load_project, load_log) == 2
    assert calls == [path]
    assert project_reads == log_reads == ['p']


def test_scan_skips_old_files_but_keeps_equal_mtime(setup, monkeypatch):
    old = write_transcript(setup, name='old')
    equal = write_transcript(setup, name='equal')
    os.utime(old, (999, 999))
    os.utime(equal, (1000, 1000))
    calls = []
    original = reconcile._scan_file

    def scan(path, *args):
        calls.append(path)
        return original(path, *args)

    monkeypatch.setattr(reconcile, '_scan_file', scan)
    assert tick(setup) == 1
    assert calls == [equal]


def test_dry_run_uses_readonly_db_and_real_ownership_without_resolve(setup, monkeypatch, tmp_path):
    store, rec, project = setup
    write_transcript(setup, shared=True, name='known', content='Rollover\n' + HEADER)
    data = tmp_path / 'project-records'
    data.mkdir()
    (data / 'p.json').write_text(json.dumps({'project_path': str(project)}), encoding='utf-8')
    (data / 'p_agent_log.json').write_text(json.dumps([
        {'session_id': 'parent', 'claude_session_ids': ['known']}]), encoding='utf-8')
    before = store.status('inbox', 'child:turn:1', 'p')
    original = sqlite3.connect
    connections = []

    def connect(database, *args, **kwargs):
        assert str(database).endswith('?mode=ro') and kwargs.get('uri') is True
        db = original(database, *args, **kwargs)
        connections.append(db)
        return db

    def cannot_resolve(*args, **kwargs):
        pytest.fail('dry run attempted to resolve')

    with monkeypatch.context() as m:
        m.setattr(reconcile.sqlite3, 'connect', connect)
        m.setattr(DeliveryStore, 'resolve_report', cannot_resolve)
        result = dry_run(store.path, data, projects_home=rec.projects_home)
    assert len(connections) == 1
    assert result['projects'] == {'p': {'parked': 1, 'eligible': 1, 'matched': 1}}
    assert result['dry_run'] and result['tick_seconds'] >= result['scan_seconds']
    assert store.status('inbox', 'child:turn:1', 'p') == before
    assert state(store, 'outbox') == 'pending'
    # Same whole-line evidence without a saved parent identity must remain unconfirmed.
    (data / 'p_agent_log.json').write_text('[]', encoding='utf-8')
    assert dry_run(store.path, data, projects_home=rec.projects_home)['projects']['p']['matched'] == 0


def test_shared_transcript_requires_recorded_id_or_parent_identity(setup):
    write_transcript(setup, shared=True, name='known')
    assert tick(setup, [{'session_id': 'other', 'claude_session_ids': ['known']}]) == 0
    setup[1]._next_check = 0
    assert tick(setup, [{'session_id': 'parent', 'claude_session_ids': ['known']}]) == 1


def test_shared_unrecorded_rollover_has_injected_parent_identity(setup):
    write_transcript(setup, shared=True, identity='/api/project/p/agent/parent/job')
    assert tick(setup) == 1


@pytest.mark.parametrize('tool_input', [
    {'notify_session': 'parent', 'task': 'Review the work'},
    {'command': 'curl -s localhost:5199/api/project/p/agent/dispatch -d \'{"notify_session":"parent","task":"Review the work"}\''},
    {'command': r'curl -d "{\"notify_session\":\"parent\"}" localhost:5199/api/project/p/agent/dispatch'},
    {'arguments': [{'command': 'dispatch(notify_session=parent, task="Review")'}]},
])
@pytest.mark.parametrize('owner_after_receipt', [False, True])
def test_unrecorded_shared_rollover_owned_only_by_assistant_dispatch(setup, tool_input, owner_after_receipt):
    path = write_transcript(setup, shared=True, name='unrecorded-rollover',
                            content='=== End of prior conversation ===\n\n' + HEADER)
    dispatch = json.dumps({'type': 'assistant', 'message': {'role': 'assistant', 'content': [
        {'type': 'tool_use', 'name': 'Bash', 'input': tool_input}]}})
    receipt = path.read_text(encoding='utf-8')
    path.write_text(receipt + '\n' + dispatch if owner_after_receipt else dispatch + '\n' + receipt,
                    encoding='utf-8')
    assert tick(setup, log=[]) == 1  # no recorded provider id or user identity marker
    assert state(setup[0]) == 'confirmed_in_transcript'


@pytest.mark.parametrize('record', [
    {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': '{"notify_session":"parent"}'}]}},
    {'type': 'user', 'message': {'content': [{'type': 'tool_use', 'input': {'notify_session': 'parent'}}]}},
    {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': '{"notify_session":"parent"}'}]}},
    {'type': 'assistant', 'message': {'content': [{'type': 'tool_result', 'content': '{"notify_session":"parent"}'}]}},
    {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'input': {'notify_session': 'parent-other'}}]}},
    {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'input': {'command': 'dispatch(notify_session=parent_other)'}}]}},
    {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'input': {'command': 'dispatch(other_notify_session=parent)'}}]}},
])
def test_notify_session_requires_assistant_tool_input_and_exact_parent(setup, record):
    path = write_transcript(setup, shared=True, content='Rollover preamble\n' + HEADER)
    path.write_text(json.dumps(record) + '\n' + path.read_text(encoding='utf-8'), encoding='utf-8')
    assert tick(setup) == 0
    assert state(setup[0]) == 'uncertain'


@pytest.mark.parametrize('owned_by_dispatch', [False, True])
def test_analysis_chat_quoting_header_is_not_a_user_receipt(setup, owned_by_dispatch):
    path = write_transcript(setup, shared=True, name='analysis-chat',
                            content='I found this header in a transcript: ' + HEADER)
    content = [{'type': 'text', 'text': HEADER}]  # assistant text is never a receipt
    if owned_by_dispatch:
        content.append({'type': 'tool_use', 'input': {'notify_session': 'parent'}})
    path.write_text(path.read_text(encoding='utf-8') + '\n' + json.dumps(
        {'type': 'assistant', 'timestamp': '1970-01-01T00:16:42Z',
         'message': {'content': content}}), encoding='utf-8')
    assert tick(setup) == 0


def test_assistant_dispatch_ownership_does_not_relax_receipt_timestamp(setup):
    path = write_transcript(setup, shared=True, stamp='1970-01-01T00:16:39Z')
    path.write_text(path.read_text(encoding='utf-8') + '\n' + json.dumps(
        {'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'input': {'notify_session': 'parent'}}]}}), encoding='utf-8')
    assert tick(setup) == 0


def test_rate_limit_includes_no_match_and_missing_transcripts(setup):
    assert tick(setup) == 0
    write_transcript(setup)
    assert tick(setup) == 0
    setup[0].clock = lambda: 1300.0
    assert tick(setup) == 1


def test_missing_project_and_bad_transcript_are_not_delivery(setup):
    path = write_transcript(setup)
    path.write_text('null\n[]\n{partial', encoding='utf-8')
    assert tick(setup) == 0
    setup[1]._next_check = 0
    assert setup[1].tick(setup[0], {}, lambda pid: None, lambda pid: []) == 0
    assert state(setup[0]) == 'uncertain'


@pytest.mark.parametrize('parked', ['uncertain', 'blocked', 'recovery_required'])
def test_mark_handled_is_terminal_idempotent_and_preserves_payload(setup, parked):
    store, _, _ = setup
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE inbox SET state=?', (parked,))
    original = store.status('inbox', 'child:turn:1', 'p')['payload']
    assert store.mark_handled('child:turn:1', 'p')
    assert store.mark_handled('child:turn:1', 'p')
    assert state(store) == state(store, 'outbox') == 'handled'
    assert store.status('inbox', 'child:turn:1', 'p')['payload'] == original
    store.retry('inbox', 'child:turn:1', 'p', reviewed=True)
    assert state(store) == 'handled'
    assert store.claim_inbox() is None and store.claim_outbox() is None


def test_mark_handled_scoped_and_inflight_refused(setup):
    store, _, _ = setup
    assert not store.mark_handled('child:turn:1', 'wrong-project')
    assert state(store) == 'uncertain'
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE outbox SET lease_until=2000')
    with pytest.raises(ValueError, match='in flight'):
        store.mark_handled('child:turn:1', 'p')
    assert state(store) == 'uncertain'


def test_handled_survives_reopen_source_recovery_and_duplicate_receipt(setup):
    store, _, _ = setup
    payload = json.loads(store.status('inbox', 'child:turn:1', 'p')['payload'])['payload']
    store.record_completion_source('child:turn:1', 'p', 'parent', payload)
    store.mark_handled('child:turn:1', 'p')
    reopened = DeliveryStore(store.path)
    assert reopened.recover_sources() == 0
    assert not reopened.accept('child:turn:1', 'p', 'parent', payload)
    assert state(reopened) == 'handled'
    assert reopened.claim_inbox() is None and reopened.claim_outbox() is None


def test_mark_handled_can_end_parked_outbox_without_receipt(setup):
    store, _, _ = setup
    with sqlite3.connect(store.path) as db:
        db.execute('DELETE FROM inbox')
        db.execute("UPDATE outbox SET state='blocked'")
    assert store.mark_handled('child:turn:1', 'p')
    assert state(store, 'outbox') == 'handled'


@pytest.mark.parametrize('new_state', ['pending', 'dispatch_intent', 'submitted', 'processed', 'handled'])
def test_confirmation_does_not_overwrite_concurrent_state(setup, new_state):
    store, _, _ = setup
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE inbox SET state=?', (new_state,))
    with pytest.raises(ValueError):
        store.resolve_report('child:turn:1', 'p', confirmed=True)
    assert state(store) == new_state


def test_mark_handled_route_validation_scoping_and_no_redelivery(setup):
    store, _, _ = setup
    app = Flask(__name__)
    from flask import Blueprint
    bp = Blueprint('reports', __name__)
    register_routes(bp, store_fn=lambda: store)
    app.register_blueprint(bp)
    client = app.test_client()
    url = '/api/project/p/agent/delegation/mark-handled'
    for body in ({}, [], {'event_id': 42}, {'event_id': ' '}):
        assert client.post(url, json=body).status_code == 400
    assert client.post(url.replace('/p/', '/q/'), json={'event_id': 'child:turn:1'}).status_code == 404
    assert client.post(url, json={'event_id': 'child:turn:1'}).status_code == 200
    assert state(store) == 'handled'
    assert client.post(url, json={'event_id': 'child:turn:1'}).status_code == 200


def test_mark_handled_route_refuses_active_report(setup):
    store, _, _ = setup
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE inbox SET state='dispatch_intent'")
    app = Flask(__name__)
    register_routes(app, store_fn=lambda: store)
    response = app.test_client().post('/api/project/p/agent/delegation/mark-handled', json={'event_id': 'child:turn:1'})
    assert response.status_code == 409
