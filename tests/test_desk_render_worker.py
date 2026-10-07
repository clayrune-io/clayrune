"""Async startup, durable money holds and restart boundaries. No live spending."""
import json
import threading

import pytest

from mc import desk_engines as eng, desk_render_worker as worker
from tests.test_desk_engines import (client, uploads, vault, vendor, board, gated, unattended,
    _sc, _render_body, _two_clip_vendor, _job_posts, HIGGS_T2V, _campaign, _own, _video_piece, CID, FORGED)  # noqa: F401


@pytest.fixture
def queued(client, board, vendor, monkeypatch):
    board([_sc('s1'), _sc('s2')])
    _two_clip_vendor(vendor)
    eng.set_limit('higgsfield', 5)
    launched = []
    monkeypatch.setattr(worker, 'launch', lambda rid, unattended: launched.append(rid))
    yield launched
    with eng._lock:
        worker._active.difference_update(launched)


def start(client, **kw):
    response = client.post('/api/desk/engines/renders', json=_render_body(**kw))
    assert response.status_code == 201, response.get_json()
    return response.get_json()['render']['render_id']


def test_post_and_pending_poll_never_contact_a_vendor(client, queued, vendor):
    rid = start(client, passcode='never-persist-this')
    assert queued == [rid] and not vendor.calls
    out = client.get(f'/api/desk/engines/renders/{rid}').get_json()['render']
    assert out['status'] == out['submission_state'] == 'pending' and not vendor.calls
    assert 'never-persist-this' not in json.dumps(eng._read_store())
    assert not {'request', 'board', 'worker_run', 'reserved_usd'} & out.keys()


def test_different_click_keys_reuse_the_same_owner_while_starting(client, queued, vendor):
    rid = start(client)
    replay = client.post('/api/desk/engines/renders', json=_render_body())
    assert replay.status_code == 200 and replay.get_json()['render']['render_id'] == rid
    assert replay.get_json()['replay'] and len(queued) == 1 and not vendor.calls


def test_simultaneous_owner_clicks_launch_only_one_worker(client, queued, vendor):
    barrier = threading.Barrier(2)
    results, errors = [], []
    def click(key):
        try:
            barrier.wait(timeout=3)
            results.append(eng.render(_render_body(idempotency_key=key)))
        except Exception as e:
            errors.append(e)
    threads = [threading.Thread(target=click, args=(key,)) for key in ('one', 'two')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert not errors and len(results) == 2
    assert results[0][0]['render_id'] == results[1][0]['render_id']
    assert sorted(replay for _, replay in results) == [False, True]
    assert len(queued) == 1 and not vendor.calls


def test_pending_render_reservation_also_blocks_standalone_jobs(client, queued, board, vendor):
    from tests.test_desk_engines import _higgs_video
    _campaign(_own(0.5))
    pid = _video_piece(client)
    board([_sc('s1'), _sc('s2')], 'piece', pid)
    start(client, owner={'kind': 'piece', 'id': pid})
    with pytest.raises(eng.Refused) as refused:
        eng.submit(_higgs_video())
    assert refused.value.code == 'over_budget'
    assert not _job_posts(vendor, HIGGS_T2V)


def test_real_thread_answers_while_preparation_is_blocked(client, queued, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    threads = []
    def prepare(plan, unattended):
        entered.set()
        assert release.wait(5)
    def launch(rid, unattended):
        thread = threading.Thread(target=worker.run, args=(rid,))
        threads.append(thread)
        thread.start()
    monkeypatch.setattr(worker, 'prepare_plan', prepare)
    monkeypatch.setattr(worker, 'launch', launch)
    try:
        rid = start(client)
        assert entered.wait(2)
        assert eng.get_render(rid)['status'] == 'working'
        assert eng._read_store()['jobs'] == {}
    finally:
        release.set()
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
    assert eng.get_render(rid)['submission_state'] == 'submitted'


def test_click_snapshot_survives_storyboard_edits(client, queued, board, vendor):
    rid = start(client)
    board([_sc('changed', line='Changed after approval')])
    worker.run(rid)
    sent = _job_posts(vendor, HIGGS_T2V)
    assert len(sent) == 2
    assert 'Line s1.' in json.loads(sent[0]['body'])['prompt']
    assert 'Changed after approval' not in json.dumps(sent, default=str)


def test_parent_reservation_blocks_another_owner_and_transfers_atomically(client, queued, board, vendor, monkeypatch):
    _campaign(_own(0.5))
    pid = _video_piece(client)
    board([_sc('s1'), _sc('s2')], 'piece', pid)
    rid = start(client, owner={'kind': 'piece', 'id': pid})
    assert eng.budget_state(CID)['spent'] == 0.42
    other = _video_piece(client)
    board([_sc('other')], 'piece', other)
    refusal = client.post('/api/desk/engines/renders', json=_render_body(owner={'kind': 'piece', 'id': other}))
    assert refusal.status_code == 409 and refusal.get_json()['code'] == 'over_budget'
    adapter = eng.HiggsfieldAdapter.submit
    seen = []
    def inspect(self, model, req, creds, **kw):
        store = eng._read_store()
        parent = store['renders'][rid]
        assert len(parent['children']) == len(store['jobs'])
        seen.append((parent['reserved_usd'], eng.budget_state(CID)['spent']))
        return adapter(self, model, req, creds, **kw)
    monkeypatch.setattr(eng.HiggsfieldAdapter, 'submit', inspect)
    worker.run(rid)
    assert seen == [(0.21, 0.42), (0, 0.42)]
    assert eng.budget_state(CID)['spent'] == 0.42


@pytest.mark.parametrize('not_sent', [True, False])
def test_failed_submit_releases_only_provably_unsent_money(client, queued, monkeypatch, not_sent):
    def fail(*args, **kw):
        raise eng.EngineError('engine', 'Network failed', definitive=False, not_sent=not_sent)
    monkeypatch.setattr(eng.HiggsfieldAdapter, 'submit', fail)
    rid = start(client)
    worker.run(rid)
    store = eng._read_store()
    assert store['renders'][rid]['reserved_usd'] == 0
    if not_sent:
        assert store['jobs'] == {} and store['renders'][rid]['children'] == []
        assert eng.get_render(rid)['status'] == 'failed'
    else:
        assert len(store['jobs']) == 1 and sum(j['cost_usd'] for j in store['jobs'].values()) == 0.21
        assert eng.get_render(rid)['hold']['kind'] == 'submit_uncertain'
        replay = client.post('/api/desk/engines/renders', json=_render_body())
        assert replay.status_code == 200 and replay.get_json()['render']['render_id'] == rid


@pytest.mark.parametrize('phase', ['pending', 'working'])
def test_restart_holds_unsent_startup_without_any_remote_retry(client, queued, vendor, phase):
    rid = start(client)
    worker._finish(rid, status=phase, submission_state=phase, worker_run='previous-server')
    out = client.get(f'/api/desk/engines/renders/{rid}').get_json()['render']
    assert out['status'] == 'held' and out['hold']['kind'] == 'submit_interrupted'
    assert eng._read_store()['renders'][rid]['reserved_usd'] == 0 and not vendor.calls
    replay = client.post('/api/desk/engines/renders', json=_render_body())
    assert replay.status_code == 200 and len(queued) == 1


def test_restart_retains_a_linked_in_flight_child_reservation(client, queued, vendor):
    rid = start(client)
    store = eng._read_store()
    rec = store['renders'][rid]
    rec.update(status='working', submission_state='working', worker_run='old-server', reserved_usd=0.21)
    rec['children'] = [{'scene_id': 's1', 'job_id': 'interrupted-child'}]
    store['jobs']['interrupted-child'] = {'cost_usd': 0.21, 'status': 'rendering'}
    eng._write_store(store)
    latest = client.get('/api/desk/engines/renders?owner_kind=studio&owner_id=' + rec['owner']['id']).get_json()['render']
    assert latest['status'] == 'held' and latest['cost_usd'] == 0.21
    assert eng._read_store()['renders'][rid]['reserved_usd'] == 0 and not vendor.calls


def test_price_hold_does_not_poll_or_finalize_and_key_replay_cannot_accept(client, queued, vendor):
    rid = start(client, shown_total=0.4, idempotency_key='first')
    worker.run(rid)
    before = len(vendor.calls)
    hold = client.get(f'/api/desk/engines/renders/{rid}').get_json()['render']['hold']
    assert hold['code'] == 'render_price_changed' and hold['prepared_total'] == 0.42
    assert hold['board_fingerprint'] and hold['request']['owner']
    replay = client.post('/api/desk/engines/renders', json=_render_body(shown_total=0.42, idempotency_key='first'))
    assert replay.status_code == 200 and len(vendor.calls) == before and not _job_posts(vendor, HIGGS_T2V)
    accepted = start(client, shown_total=0.42, idempotency_key='second')
    worker.run(accepted)
    assert len(_job_posts(vendor, HIGGS_T2V)) == 2


def test_lowered_limit_still_blocks_prepared_spend(client, queued, vendor):
    rid = start(client)
    eng.set_limit('higgsfield', 0.2)
    worker.run(rid)
    assert eng.get_render(rid)['failure']['kind'] == 'over_job_limit'
    assert not _job_posts(vendor, HIGGS_T2V)


def test_worker_crash_and_launch_failure_remain_observable(client, queued, monkeypatch):
    monkeypatch.setattr(worker, 'prepare_plan', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('oops')))
    rid = start(client)
    worker.run(rid)
    assert eng.get_render(rid)['status'] == 'failed' and eng._read_store()['renders'][rid]['reserved_usd'] == 0
    monkeypatch.setattr(worker, 'launch', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('cannot start')))
    rid2 = start(client)
    assert eng.get_render(rid2)['failure']['kind'] == 'worker'
