"""Selected-scene routes through MC-1068's real durable worker; no vendor spend."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from mc import desk_engines as eng, desk_render_worker as worker
from mc.desk_render_scenes import submission_context
from tests.test_desk_engines import (client, uploads, vault, vendor, board, gated, ffmpeg,
    _sc, _render_body, _two_clip_vendor, _job_posts, HIGGS_T2V, FORGED)  # noqa: F401
from tests.test_desk_render_price_confirmation import picture_board, body
from tests.test_higgsfield_picture_render import contract, mcp, generations  # noqa: F401


@pytest.mark.parametrize('chosen', [['s2'], ['s3', 's1'], ['s3', 's1', 's2']])
def test_quote_route_prices_exact_selection(client, board, vendor, chosen):
    board([_sc('s1'), _sc('s2'), _sc('s3')])
    _two_clip_vendor(vendor)
    out = client.post('/api/desk/engines/render/estimate', json=_render_body(scene_ids=chosen))
    assert out.status_code == 200, out.get_json()
    quote = out.get_json()
    assert quote['estimate']['usd'] == pytest.approx(0.21 * len(chosen))
    assert quote['plan']['clips'] == len(chosen)
    assert [s['scene_id'] for s in quote['plan']['scenes']] == sorted(chosen)
    assert len(vendor.to('/estimate/')) == len(chosen)


@pytest.mark.parametrize('chosen', [None, [], ['missing'], ['s1', 's1']])
def test_bad_selection_refuses_enqueue_and_quote_without_vendor_or_record(client, board, vendor, chosen):
    board([_sc('s1'), _sc('s2')])
    for path in ('/api/desk/engines/render/estimate', '/api/desk/engines/renders'):
        out = client.post(path, json=_render_body(scene_ids=chosen))
        assert out.status_code in (400, 409), out.get_json()
    assert not vendor.calls and not eng._read_store()['renders']


def test_subset_has_durable_pending_snapshot_and_hold_before_remote_calls(client, board, vendor, monkeypatch):
    board([_sc('s1'), _sc('s2')])
    _two_clip_vendor(vendor)
    eng.set_limit('higgsfield', 2)
    launched = []
    monkeypatch.setattr(worker, 'launch', lambda rid, unattended: launched.append(rid))
    try:
        out = client.post('/api/desk/engines/renders', json=_render_body(scene_ids=['s2'], shown_total=0.21, passcode='fake-test-proof'))
        assert out.status_code == 201
        view = out.get_json()['render']; rid = view['render_id']
        assert view['status'] == 'pending' and view['scene_ids'] == ['s2'] and not vendor.calls
        record = eng._read_store()['renders'][rid]
        assert record['reserved_usd'] == 0.21
        assert [s['id'] for s in record['board']['scenes']] == ['s2']
        assert record['request']['scene_ids'] == ['s2']
        assert 'fake-test-proof' not in json.dumps(record)
        replay = client.post('/api/desk/engines/renders', json=_render_body(scene_ids=['s1']))
        assert replay.status_code == 200 and replay.get_json()['render']['render_id'] == rid
        board([_sc('s1'), _sc('s2', line='changed after approval')])
        worker.run(rid)
        sent = _job_posts(vendor, HIGGS_T2V)
        assert len(sent) == 1 and 'Line s2.' in json.loads(sent[0]['body'])['prompt']
        assert eng._read_store()['renders'][rid]['reserved_usd'] == 0
    finally:
        worker._active.difference_update(launched)


def test_ready_render_replaces_one_clip_and_rejoins_without_resubmitting_others(client, board, vendor, ffmpeg):
    board([_sc('s1'), _sc('s2')]); _two_clip_vendor(vendor); eng.set_limit('higgsfield', 5)
    original = client.post('/api/desk/engines/renders', json=_render_body(shown_total=0.42)).get_json()['render']
    old = client.get('/api/desk/engines/renders/' + original['render_id']).get_json()['render']
    assert old['status'] == 'ready'
    store = eng._read_store(); old_record = deepcopy(store['renders'][old['render_id']])
    old_child = old_record['children'][0]
    old_output = deepcopy(store['jobs'][old_child['job_id']]['outputs'][0])
    old_bytes = Path(old_output['local_path']).read_bytes()
    before_posts = len(_job_posts(vendor, HIGGS_T2V))
    subset = client.post('/api/desk/engines/renders', json=_render_body(scene_ids=['s2'], shown_total=0.21)).get_json()['render']
    new = client.get('/api/desk/engines/renders/' + subset['render_id']).get_json()['render']
    assert new['status'] == 'ready' and new['scene_ids'] == ['s2']
    assert new['reused_scene_ids'] == ['s1'] and new['complete_storyboard']
    assert new['cost_usd'] == 0.21 and len(_job_posts(vendor, HIGGS_T2V)) == before_posts + 1
    rec = eng._read_store()['renders'][new['render_id']]
    assert len(rec['children']) == 1 and rec['children'][0]['scene_id'] == 's2'
    assert new['clips'][0] == old_output and Path(old_output['local_path']).read_bytes() == old_bytes
    assert new['clips'][1]['local_path'] != old['clips'][1]['local_path']
    assert eng._read_store()['renders'][old['render_id']] == old_record
    assert len(ffmpeg) == 2  # one full join, then one local replacement join


def test_multiple_subsets_preserve_prior_replacements(client, board, vendor, ffmpeg):
    board([_sc('s1'), _sc('s2')]); _two_clip_vendor(vendor); eng.set_limit('higgsfield', 5)
    results = []
    for chosen, shown in [(None, 0.42), (['s2'], 0.21), (['s1'], 0.21)]:
        request = _render_body(shown_total=shown)
        if chosen: request['scene_ids'] = chosen
        r = client.post('/api/desk/engines/renders', json=request).get_json()['render']
        results.append(client.get('/api/desk/engines/renders/' + r['render_id']).get_json()['render'])
    assert all(r['status'] == 'ready' for r in results)
    assert results[2]['clips'][1] == results[1]['clips'][1]
    assert results[2]['clips'][0] != results[1]['clips'][0]
    assert len(_job_posts(vendor, HIGGS_T2V)) == 4


def test_no_prior_subset_is_ready_partial_and_does_not_attach_full_video(client, board, vendor, monkeypatch):
    board([_sc('s1'), _sc('s2')]); _two_clip_vendor(vendor); eng.set_limit('higgsfield', 5)
    attached = []
    monkeypatch.setattr(eng, '_attach_to_piece', lambda *a: attached.append(a))
    r = client.post('/api/desk/engines/renders', json=_render_body(scene_ids=['s2'], shown_total=0.21)).get_json()['render']
    out = client.get('/api/desk/engines/renders/' + r['render_id']).get_json()['render']
    assert out['status'] == 'ready' and not out['complete_storyboard']
    assert out['missing_scene_ids'] == ['s1'] and len(out['outputs']) == 1
    assert not any(owner for owner, _ in attached) and len(_job_posts(vendor, HIGGS_T2V)) == 1


def test_subset_picture_price_held_before_generation_then_accepted(client, board, uploads, mcp):
    picture_board(board, uploads); eng.set_limit('higgsfield_mcp', 30)
    request = body(scene_ids=['s2'], shown_total=2)
    assert client.post('/api/desk/engines/render/estimate', json=request).get_json()['estimate']['credits'] == 2
    out = client.post('/api/desk/engines/renders', json=request)
    assert out.status_code == 201
    rid = out.get_json()['render']['render_id']
    held = eng.get_render(rid)
    assert held['status'] == 'held' and held['hold']['prepared_total'] == 7
    assert held['hold']['request']['scene_ids'] == ['s2']
    assert len(held['hold']['plan']['scenes']) == 1 and not generations(mcp[0])
    assert sum(n == 'media_upload' for n, _ in mcp[0]) == 1
    accepted = client.post('/api/desk/engines/renders', json={**request, 'shown_total': 7, 'idempotency_key': 'accept-subset-price'})
    assert accepted.status_code == 201 and len(generations(mcp[0])) == 1


def test_subset_passcode_and_prepared_cap_are_still_required(gated, board, uploads, mcp):
    picture_board(board, uploads); eng.set_limit('higgsfield_mcp', 5)
    request = body(scene_ids=['s2'], shown_total=2)
    out = gated.post('/api/desk/engines/renders', json=request, headers=FORGED)
    assert out.status_code == 403 and not mcp[0]
    from tests.test_desk_engines import PASSCODE
    out = gated.post('/api/desk/engines/renders', json={**request, 'passcode': PASSCODE}, headers=FORGED)
    assert out.status_code == 201
    failed = eng.get_render(out.get_json()['render']['render_id'])
    assert failed['failure']['kind'] == 'over_job_limit' and not generations(mcp[0])


def test_interrupted_subset_never_retries_then_acknowledgement_retains_children(client, board, vendor, monkeypatch):
    from mc import desk_render_recovery as recovery
    board([_sc('s1'), _sc('s2')]); _two_clip_vendor(vendor); eng.set_limit('higgsfield', 5)
    def uncertain(*args, **kwargs):
        raise eng.EngineError('network', 'Unknown acceptance', definitive=False, not_sent=False)
    monkeypatch.setattr(eng.HiggsfieldAdapter, 'submit', uncertain)
    r = client.post('/api/desk/engines/renders', json=_render_body(scene_ids=['s2'], shown_total=0.21)).get_json()['render']
    r = eng.get_render(r['render_id'])
    assert r['status'] == 'held' and r['hold']['kind'] == 'submit_uncertain'
    children = deepcopy(eng._read_store()['renders'][r['render_id']]['children'])
    assert len(children) == 1 and r['cost_usd'] == 0.21
    again = client.post('/api/desk/engines/renders', json=_render_body(scene_ids=['s1'], shown_total=0.21))
    assert again.status_code == 200 and again.get_json()['render']['render_id'] == r['render_id']
    assert len(eng._read_store()['jobs']) == 1
    # Acknowledgement is the existing explicit human action; no generation is replayed.
    monkeypatch.setattr(eng, 'poll', lambda jid, **kw: eng.get_job(jid))
    ack = recovery.acknowledge(r['render_id'])
    assert ack['failure']['kind'] == 'submission_acknowledged'
    assert eng._read_store()['renders'][r['render_id']]['children'] == children


def test_square_reuse_keeps_crop_when_next_model_is_native_square():
    data = _render_body(scene_ids=['s2'], aspect_ratio='1:1')
    board = {'scenes': [_sc('s1'), _sc('s2')]}
    previous = {'owner': data['owner'], 'status': 'ready', 'aspect_ratio': '1:1', 'crop_square': True,
                'created_at': '1', 'children': [{'scene_id': 's1', 'job_id': 'old'}]}
    store = {'renders': {'old': previous}, 'jobs': {'old': {'status': 'ready', 'outputs': [{'path': 'old.mp4'}]}}}
    _, context = submission_context(data, board, store)
    assert context['reuse_crop_square']
