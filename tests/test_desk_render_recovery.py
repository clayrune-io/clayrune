"""Recovery polls existing jobs and acknowledges only; no missing scene submits."""
from dataclasses import asdict

import pytest

from mc import desk_engines as eng, desk_render_worker as worker
from mc.blueprints import desk_render_recovery_routes
from tests.test_desk_engines import (client, uploads, vault, vendor, board, gated, unattended,
    _sc, _render_body, _two_clip_vendor, _job_posts, HIGGS_T2V, _campaign, _own, _video_piece, CID)  # noqa: F401
from tests.test_desk_render_worker import queued, start  # noqa: F401


@pytest.fixture
def recovery_client(client):
    client.application.register_blueprint(desk_render_recovery_routes.bp)
    return client


def accepted_child(client, board, *, hold='submit_interrupted'):
    _campaign(_own(1))
    pid = _video_piece(client)
    board([_sc('s1'), _sc('s2')], 'piece', pid)
    rid = start(client, owner={'kind': 'piece', 'id': pid})
    rec = eng._read_store()['renders'][rid]
    plan = eng._plan_render(rec['request'], unattended=False, _board=rec['board'])
    body = asdict(plan['rows'][0]['req'])
    body.update(campaign_id=CID, desk={'scene_id': 's1', 'idempotency_key': 'already-accepted'})
    job, _ = eng.submit(body, _skip_limit=True, _render_id=rid, _estimate=plan['rows'][0]['est'])
    assert job['status'] == 'queued'
    if hold == 'submit_interrupted':
        worker._finish(rid, status='working', submission_state='working', worker_run='previous-server')
    else:
        worker._finish(rid, status='held', submission_state='held', reserved_usd=0,
                       hold={'kind': hold, 'message': 'Check the engine dashboard.'})
    return rid, job['job_id'], pid


@pytest.mark.parametrize('hold', ['submit_interrupted', 'submit_uncertain'])
def test_submission_hold_downloads_accepted_child_without_sending_missing_scene(recovery_client, queued, board, vendor, uploads, hold):
    rid, jid, _ = accepted_child(recovery_client, board, hold=hold)
    out = recovery_client.get(f'/api/desk/engines/renders/{rid}').get_json()['render']
    assert out['status'] == 'held' and out['hold']['kind'] == hold
    assert out['progress'] == {'ready': 1, 'total': 2}
    assert out['scenes'][0]['job_id'] == jid and out['scenes'][0]['status'] == 'ready'
    assert (uploads / out['clips'][0]['path']).read_bytes() == b'CLIP-a'
    assert eng._read_store()['jobs'][jid]['status'] == 'ready'
    assert eng.budget_state(CID) == {'amount': 1, 'spent': 0.21, 'remaining': 0.79, 'source': 'own'}
    assert eng._read_store()['renders'][rid]['reserved_usd'] == 0
    assert len(_job_posts(vendor, HIGGS_T2V)) == 1


def test_linked_free_failure_releases_cost_but_does_not_start_missing_scene(recovery_client, queued, board, vendor):
    rid, jid, _ = accepted_child(recovery_client, board)
    vendor.on('GET', '/requests/ra/status', vendor.json(200, {'status': 'nsfw'}))
    out = recovery_client.get(f'/api/desk/engines/renders/{rid}').get_json()['render']
    assert out['status'] == 'held' and out['scenes'][0]['status'] == 'failed'
    assert eng._read_store()['jobs'][jid]['cost_usd'] == 0
    assert eng.budget_state(CID)['remaining'] == 1
    assert len(_job_posts(vendor, HIGGS_T2V)) == 1


@pytest.mark.parametrize('hold', ['submit_interrupted', 'submit_uncertain'])
def test_acknowledge_keeps_children_and_allows_new_owner_enqueue(recovery_client, queued, board, vendor, hold):
    rid, jid, pid = accepted_child(recovery_client, board, hold=hold)
    before = len(vendor.calls)
    reply = recovery_client.post(f'/api/desk/engines/renders/{rid}/acknowledge')
    assert reply.status_code == 200
    acknowledged = reply.get_json()['render']
    assert acknowledged['status'] == 'failed' and acknowledged['hold'] is None
    assert acknowledged['failure']['kind'] == 'submission_acknowledged'
    assert acknowledged['scenes'][0]['job_id'] == jid and len(vendor.calls) == before
    # Even after acknowledgement, free polling must still finish linked jobs.
    settled = recovery_client.get(f'/api/desk/engines/renders/{rid}').get_json()['render']
    assert settled['status'] == 'failed' and settled['failure']['kind'] == 'submission_acknowledged'
    assert settled['scenes'][0]['status'] == 'ready' and settled['clips']
    another = start(recovery_client, owner={'kind': 'piece', 'id': pid})
    assert another != rid and len(queued) == 2
    assert len(_job_posts(vendor, HIGGS_T2V)) == 1  # new queued worker has not run


@pytest.mark.parametrize('state', ['pending', 'rendering', 'ready', 'failed', 'price', 'ffmpeg'])
def test_acknowledge_refuses_non_submission_holds(recovery_client, queued, state):
    rid = start(recovery_client)
    if state in ('price', 'ffmpeg'):
        worker._finish(rid, status='held', submission_state='held',
                       hold={'kind': 'render_price_changed' if state == 'price' else 'ffmpeg_missing'})
    elif state != 'pending':
        worker._finish(rid, status=state, submission_state='submitted')
    before = eng._read_store()
    reply = recovery_client.post(f'/api/desk/engines/renders/{rid}/acknowledge')
    assert reply.status_code == 409 and reply.get_json()['code'] == 'submission_not_held'
    assert eng._read_store() == before


def test_acknowledge_refuses_unattended_without_mutation(recovery_client, queued, unattended):
    # No POST Render here: that gate correctly refuses this unattended fixture.
    rec, _ = eng.render(_render_body())
    worker._finish(rec['render_id'], status='held', submission_state='held',
                   hold={'kind': 'submit_interrupted'})
    before = eng._read_store()
    reply = recovery_client.post(f'/api/desk/engines/renders/{rec["render_id"]}/acknowledge')
    assert reply.status_code == 403 and eng._read_store() == before


def test_acknowledge_missing_and_repeat_are_refused(recovery_client, queued):
    assert recovery_client.post('/api/desk/engines/renders/missing/acknowledge').status_code == 404
    rid = start(recovery_client)
    worker._finish(rid, status='held', submission_state='held', hold={'kind': 'submit_uncertain'})
    assert recovery_client.post(f'/api/desk/engines/renders/{rid}/acknowledge').status_code == 200
    assert recovery_client.post(f'/api/desk/engines/renders/{rid}/acknowledge').status_code == 409


def test_acknowledge_requires_no_passcode_but_next_render_still_does(recovery_client, queued, gated):
    from tests.test_desk_engines import PASSCODE
    record, _ = eng.render(_render_body())
    worker._finish(record['render_id'], status='held', submission_state='held', hold={'kind': 'submit_uncertain'})
    assert gated.post(f'/api/desk/engines/renders/{record["render_id"]}/acknowledge').status_code == 200
    assert gated.post('/api/desk/engines/renders', json=_render_body()).status_code in (401, 403)
    assert gated.post('/api/desk/engines/renders', json=_render_body(passcode=PASSCODE)).status_code == 201


def test_acknowledge_during_poll_is_not_overwritten(recovery_client, queued, board, vendor, monkeypatch):
    from mc.desk_render_recovery import acknowledge
    rid, _, _ = accepted_child(recovery_client, board)
    original = eng.poll
    def simultaneous(job_id, **kw):
        acknowledge(rid)
        return original(job_id, **kw)
    monkeypatch.setattr(eng, 'poll', simultaneous)
    out = recovery_client.get(f'/api/desk/engines/renders/{rid}').get_json()['render']
    assert out['status'] == 'failed' and out['failure']['kind'] == 'submission_acknowledged'
    assert out['scenes'][0]['status'] == 'ready' and out['clips']
    assert len(_job_posts(vendor, HIGGS_T2V)) == 1
