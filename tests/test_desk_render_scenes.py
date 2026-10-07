"""MC-1070 independent subset seam; production route wiring waits for MC-1068."""
from copy import deepcopy

import pytest

from mc import desk_engines as eng
from mc import desk_storyboard as sb
from mc.desk_render_scenes import (selected_board, subset_request, price_subset,
                                   replacement_children, rebuild_outputs, validate_previous)
from tests.test_desk_engines import (client, uploads, vault, vendor, board, gated,
                                    _sc, _render_body, _two_clip_vendor, FORGED)  # noqa: F401
from tests.test_desk_render_price_confirmation import picture_board, body  # noqa: F401
from tests.test_higgsfield_picture_render import contract, mcp, generations  # noqa: F401


@pytest.fixture
def subset_planner(monkeypatch):
    """Exercise the board-aware production planner seam."""
    def plan(request, narrowed):
        return eng._plan_render(request, unattended=False, _board=narrowed)
    return plan


@pytest.mark.parametrize('chosen,count', [(['s2'], 1), (['s3', 's1'], 2), (['s3', 's1', 's2'], 3)])
def test_subset_pricing_calls_only_selected_scenes(client, board, vendor, subset_planner, chosen, count):
    stored = board([_sc('s1'), _sc('s2'), _sc('s3')])
    before = deepcopy(stored)
    _two_clip_vendor(vendor)
    plan = price_subset(_render_body(scene_ids=chosen), stored, plan_board=subset_planner)
    assert plan['total_usd'] == pytest.approx(0.21 * count)
    assert len(vendor.to('/estimate/')) == count
    assert [r['scene']['id'] for r in plan['rows']] == sorted(chosen)
    assert stored == before
    if count == 3:
        full = eng._plan_render(_render_body(), unattended=False)
        assert plan['total_usd'] == full['total_usd']
        assert plan['total_amount'] == full['total_amount']


def test_unselected_invalid_picture_does_not_block_or_upload(client, board, vendor, subset_planner):
    stored = board([_sc('s1'), _sc('s2')])
    # A saved library picture can later disappear. Simulate that post-save
    # state without asking the storyboard writer to accept a missing asset.
    stored['scenes'][1]['picture'] = {'path': 'desk/library/image/no.png'}
    _two_clip_vendor(vendor)
    plan = price_subset(_render_body(scene_ids=['s1']), stored, plan_board=subset_planner)
    assert [r['scene']['id'] for r in plan['rows']] == ['s1']
    assert len(vendor.to('/estimate/')) == 1
    assert all('/estimate/' in c['url'] for c in vendor.calls)


@pytest.mark.parametrize('chosen', [[], 's1', [True], [''], ['s1', 's1'], ['removed'], None])
def test_bad_explicit_selection_refuses_before_pricing(chosen):
    calls = []
    with pytest.raises(eng.Refused):
        price_subset({'scene_ids': chosen}, {'scenes': [_sc('s1')]},
                     plan_board=lambda *a: calls.append(a))
    assert calls == []


def test_full_request_and_canonical_subset_keep_human_approval_fields():
    data = _render_body(scene_ids=['s3', 's1'], project_id='alpha')
    stored = {'scenes': [_sc('s1'), _sc('s2'), _sc('s3')]}
    out = subset_request(data, stored)
    assert out == {**data, 'scene_ids': ['s1', 's3']}
    assert data['scene_ids'] == ['s3', 's1']
    del data['scene_ids']
    assert subset_request(data, stored) == data
    assert selected_board(stored, None) == stored


def child(sid, jid):
    return {'scene_id': sid, 'job_id': jid, 'label': sid}


def job(name, status='ready'):
    return {'status': status, 'outputs': [{'path': f'{name}.mp4', 'local_path': f'/library/{name}.mp4'}]}


def test_replacement_preserves_untouched_jobs_and_joins_in_current_order():
    stored = {'scenes': [_sc('s3'), _sc('s2'), _sc('s1')]}
    previous = {'children': [child('s1', 'old1'), child('s2', 'old2'), child('s3', 'old3')]}
    before = deepcopy(previous)
    jobs = {k: job(k) for k in ('old1', 'old2', 'old3', 'new2')}
    assembly = replacement_children(stored, ['s2'], previous, [child('s2', 'new2')], jobs)
    assert [c['job_id'] for c in assembly['children']] == ['old3', 'new2', 'old1']
    assert assembly['reused_job_ids'] == ['old3', 'old1']
    assert assembly['generated_job_ids'] == ['new2']
    assert assembly['complete_storyboard'] and not assembly['missing_scene_ids']
    joined = []
    result = rebuild_outputs(assembly, jobs, join=lambda clips: joined.extend(clips) or [{'path': 'rebuilt.mp4'}])
    assert [c['path'] for c in joined] == ['old3.mp4', 'new2.mp4', 'old1.mp4']
    assert result == [{'path': 'rebuilt.mp4'}] and previous == before


def test_no_prior_render_generates_only_selected_and_labels_missing_scenes():
    stored = {'scenes': [_sc('s1'), _sc('s2'), _sc('s3')]}
    assembly = replacement_children(stored, ['s2'], None, [child('s2', 'new2')], {})
    assert assembly['children'] == [child('s2', 'new2')]
    assert assembly['missing_scene_ids'] == ['s1', 's3'] and not assembly['complete_storyboard']
    assert rebuild_outputs(assembly, {'new2': job('new2')}, join=lambda clips: clips) == job('new2')['outputs']


def test_deleted_and_unready_prior_scenes_are_not_reused():
    stored = {'scenes': [_sc('s1'), _sc('s2'), _sc('added')]}
    previous = {'children': [child('s1', 'bad'), child('s2', 'old'), child('removed', 'gone')]}
    jobs = {'bad': job('bad', 'failed'), 'old': job('old'), 'gone': job('gone')}
    out = replacement_children(stored, ['s2'], previous, [child('s2', 'new')], jobs)
    assert [c['job_id'] for c in out['children']] == ['new']
    assert out['missing_scene_ids'] == ['s1', 'added']


@pytest.mark.parametrize('replacements', [[], [child('s2', 'wrong')], [child('s1', 'a'), child('s1', 'b')], [child('s1', '')]])
def test_replacements_cannot_expand_the_paid_selection(replacements):
    with pytest.raises(eng.Refused):
        replacement_children({'scenes': [_sc('s1'), _sc('s2')]}, ['s1'], None, replacements, {})


def test_rebuild_waits_for_selected_jobs_without_invoking_join():
    calls = []
    with pytest.raises(eng.Refused) as error:
        rebuild_outputs({'children': [child('s1', 'new')]}, {'new': job('new', 'running')},
                        join=lambda clips: calls.append(clips))
    assert error.value.code == 'clips_not_ready' and calls == []


def test_subset_request_cannot_bypass_existing_passcode_gate(gated, board, vendor):
    board([_sc('s1'), _sc('s2')])
    out = gated.post('/api/desk/engines/renders', json=_render_body(scene_ids=['s1']), headers=FORGED)
    assert out.status_code == 403 and not vendor.calls


@pytest.mark.parametrize('change,code', [({'owner': {'kind': 'studio', 'id': 'other'}}, 'invalid_input'),
    ({'status': 'queued'}, 'render_busy'), ({'status': 'preparing'}, 'render_busy'),
    ({'status': 'submitting'}, 'render_busy'), ({'status': 'rendering'}, 'render_busy'),
    ({'aspect_ratio': '9:16'}, 'render_shape_changed')])
def test_reuse_preflight_refuses_wrong_owner_busy_or_mixed_shape(change, code):
    request = _render_body(scene_ids=['s1'])
    previous = {'owner': request['owner'], 'aspect_ratio': '16:9', 'status': 'ready', **change}
    with pytest.raises(eng.Refused) as error:
        validate_previous(request, previous)
    assert error.value.code == code


def test_reuse_preflight_allows_first_render_and_settled_same_shape():
    request = _render_body(scene_ids=['s1'])
    validate_previous(request, None)
    for status in ('ready', 'held', 'failed'):
        validate_previous(request, {'owner': request['owner'], 'aspect_ratio': '16:9', 'status': status})


def test_subset_picture_plan_stops_on_higher_price_before_spend(client, board, uploads, mcp, subset_planner):
    from mc.desk_generation_preflight import prepare_plan
    from mc.desk_render_price_confirmation import check
    picture_board(board, uploads)
    stored = sb.get_storyboard('studio', body()['owner']['id'])
    request = body(scene_ids=['s2'], shown_total=2)
    plan = price_subset(request, stored, plan_board=subset_planner)
    assert plan['total_amount'] == 2 and len(plan['rows']) == 1
    prepare_plan(plan, unattended=False)
    with pytest.raises(eng.Refused) as error:
        check(2, plan, estimate=eng._plan_estimate(plan), public_plan=eng._plan_public(plan))
    assert (error.value.code, error.value.status) == ('render_price_changed', 409)
    assert error.value.extra['prepared_total'] == 7
    assert len(error.value.extra['plan']['scenes']) == 1
    assert sum(n == 'media_upload' for n, _ in mcp[0]) == 1
    assert not generations(mcp[0])
