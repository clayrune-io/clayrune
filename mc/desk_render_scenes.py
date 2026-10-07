"""Scene selection and clip reuse for MC-1070; no transport or paid submission.

The caller selects the board BEFORE planning/pricing/uploading. A subset uses
the normal render worker and approval gates; this module cannot submit jobs.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Callable


def selected_board(board: dict, scene_ids: object) -> dict:
    """Validate an explicit selection and return it in storyboard order.

    None means the full board. Empty/duplicate/unknown IDs are errors rather
    than an accidental full render. The stored board is never changed.
    """
    from mc.desk_engines import Refused

    scenes = board.get('scenes') or []
    if scene_ids is None:
        return deepcopy(board)
    if not isinstance(scene_ids, list) or not scene_ids or any(
            not isinstance(sid, str) or not sid.strip() for sid in scene_ids):
        raise Refused('invalid_input', 'scene_ids must be a non-empty list of scene IDs', 400)
    if len(set(scene_ids)) != len(scene_ids):
        raise Refused('invalid_input', 'scene_ids must not repeat a scene', 400)
    known = {s['id'] for s in scenes}
    unknown = [sid for sid in scene_ids if sid not in known]
    if unknown:
        raise Refused('scene_not_found', 'A selected scene was removed. Select scenes again.', 409,
                      scene_ids=unknown)
    chosen = set(scene_ids)
    out = deepcopy(board)
    out['scenes'] = [s for s in out['scenes'] if s['id'] in chosen]
    return out


def subset_request(data: dict, board: dict) -> dict:
    """Canonical request for price/submit; retain scope and all approval fields."""
    out = deepcopy(data)
    if 'scene_ids' in data:
        if data['scene_ids'] is None:
            from mc.desk_engines import Refused
            raise Refused('invalid_input', 'omit scene_ids for the full storyboard', 400)
        out['scene_ids'] = [s['id'] for s in selected_board(board, data['scene_ids'])['scenes']]
    return out


def price_subset(data: dict, board: dict, *, plan_board: Callable[[dict, dict], dict]) -> dict:
    """Feed ONLY selected scenes into the existing planner's board-aware seam.

    The seam is supplied at integration time. Do not call a full-board planner
    and subtract prices: even free pricing must not inspect unselected pictures.
    """
    request = subset_request(data, board)
    selected = selected_board(board, request.get('scene_ids'))
    return plan_board(request, selected)


def validate_previous(data: dict, previous: dict | None) -> None:
    """Check the server-selected reuse record BEFORE preparing or paying.

    Shapes cannot be mixed into one final video, and an unfinished snapshot
    cannot be replaced while its worker is still adding scene jobs.
    """
    from mc.desk_engines import Refused

    if previous is None:
        return
    if previous.get('owner') != data.get('owner'):
        raise Refused('invalid_input', 'the previous render belongs to a different storyboard', 400)
    if previous.get('status') in ('pending', 'working', 'queued', 'preparing', 'submitting', 'rendering'):
        raise Refused('render_busy', 'Wait for the current render before replacing selected scenes.', 409)
    if previous.get('aspect_ratio') != data.get('aspect_ratio'):
        raise Refused('render_shape_changed', 'Use the existing video shape to replace scenes, or render the full storyboard in the new shape.', 409)


def replacement_children(board: dict, scene_ids: list[str], previous: dict | None,
                         replacements: list[dict], jobs: dict) -> dict:
    """Assemble a new snapshot from selected jobs and untouched READY jobs.

    A prior render is a server-resolved record for the same owner. Its children
    identify reusable clips (flat output arrays do not identify their scenes).
    Deleted scenes drop out; added/unrendered scenes remain explicit gaps. No
    job is resubmitted, no previous record/output is modified.
    """
    from mc.desk_engines import Refused

    selected = {s['id'] for s in selected_board(board, scene_ids)['scenes']}
    fresh = {c['scene_id']: c for c in replacements}
    if len(fresh) != len(replacements) or set(fresh) != selected:
        raise Refused('invalid_input', 'replacement clips must match the selected scenes exactly', 400)
    if any(not isinstance(c.get('job_id'), str) or not c['job_id'] for c in replacements):
        raise Refused('invalid_input', 'every replacement scene needs a job ID', 400)
    prior = {c['scene_id']: c for c in (previous or {}).get('children') or []}
    children, missing, reused = [], [], []
    for scene in board.get('scenes') or []:
        sid = scene['id']
        if sid in fresh:
            children.append(deepcopy(fresh[sid]))
            continue
        old = prior.get(sid)
        job = jobs.get(old['job_id']) if old else None
        if old and job and job.get('status') == 'ready' and job.get('outputs'):
            child = deepcopy(old)
            child['label'] = scene.get('label') or ''
            children.append(child)
            reused.append(old['job_id'])
        else:
            missing.append(sid)
    return {'children': children, 'missing_scene_ids': missing, 'reused_job_ids': reused,
            'generated_job_ids': [fresh[s['id']]['job_id'] for s in board.get('scenes') or [] if s['id'] in selected],
            'complete_storyboard': not missing}


def rebuild_outputs(assembly: dict, jobs: dict, *, join: Callable[[list[dict]], list[dict]]) -> list[dict]:
    """Rebuild from downloaded clips via the existing local join/crop function.

    Missing storyboard scenes are allowed: a first single-scene render yields
    that scene's clip(s). complete_storyboard lets the caller label the result
    accurately. No generator callback exists here.
    """
    from mc.desk_engines import Refused

    clips = []
    for child in assembly['children']:
        job = jobs.get(child['job_id']) or {}
        if job.get('status') != 'ready' or not job.get('outputs'):
            raise Refused('clips_not_ready', 'Wait until the selected clips have finished rendering.', 409)
        clips.extend(deepcopy(job['outputs']))
    if not clips:
        raise Refused('clips_missing', 'There are no downloaded clips to rebuild.', 409)
    return join(clips)


def select_request_board(data: dict, board: dict) -> dict:
    """Shared quote/worker seam; explicit null never means render everything."""
    return selected_board(board, subset_request(data, board).get('scene_ids'))


def _saved_scenes(previous: dict | None, jobs: dict) -> dict:
    # Keep the previous snapshot even when its selected generation was held
    # before submission. Only downloaded READY jobs replace a saved clip.
    saved = {c['scene_id']: deepcopy(c) for c in (previous or {}).get('base_scenes') or []}
    for child in (previous or {}).get('children') or []:
        job = jobs.get(child['job_id']) or {}
        if job.get('status') == 'ready' and job.get('outputs'):
            saved[child['scene_id']] = {**deepcopy(child), 'outputs': deepcopy(job['outputs']),
                                      'crop_square': bool((previous or {}).get('crop_square'))}
    return saved


def submission_context(data: dict, board: dict, store: dict) -> tuple[dict, dict]:
    """Freeze subset and ready prior outputs under the worker's enqueue lock.

    `children` always remains ONLY newly submitted jobs: MC-1068 holds,
    acknowledgement and accounting do not change. Prior outputs are server
    snapshots, never accepted from the browser.
    """
    selected = select_request_board(data, board)
    if 'scene_ids' not in data:
        return selected, {}
    chosen = [s['id'] for s in selected['scenes']]
    mine = [r for r in store['renders'].values() if r['owner'] == data['owner']]
    previous = max(mine, key=lambda r: r.get('created_at') or '') if mine else None
    saved = _saved_scenes(previous, store['jobs'])
    reused = [s['id'] for s in board['scenes'] if s['id'] not in chosen and s['id'] in saved]
    if reused:
        validate_previous(data, previous)
    missing = [s['id'] for s in board['scenes'] if s['id'] not in chosen and s['id'] not in saved]
    return selected, {
        'scene_ids': chosen, 'scene_order': [{'id': s['id'], 'label': s.get('label') or ''} for s in board['scenes']],
        'base_scenes': [saved[s['id']] for s in board['scenes'] if s['id'] in saved],
        'reuse_crop_square': any(saved[sid].get('crop_square') for sid in reused),
        'reused_scene_ids': reused, 'missing_scene_ids': missing, 'complete_storyboard': not missing,
    }


def render_clips(record: dict, jobs: dict) -> list[dict]:
    """Local finalizer inputs; reused jobs never enter the paid child list."""
    if 'scene_ids' not in record:
        return [o for c in record['children'] for o in jobs[c['job_id']].get('outputs') or []]
    generated = {c['scene_id']: c for c in record['children']}
    saved = {c['scene_id']: c for c in record.get('base_scenes') or []}
    clips = []
    for scene in record['scene_order']:
        sid = scene['id']
        if sid in record['scene_ids']:
            child = generated.get(sid)
            outputs = (jobs.get(child['job_id']) or {}).get('outputs') if child else None
        else:
            outputs = (saved.get(sid) or {}).get('outputs')
        clips.extend(deepcopy(outputs or []))
    return clips


def public_selection(record: dict) -> dict:
    fields = ('scene_ids', 'reused_scene_ids', 'missing_scene_ids', 'complete_storyboard')
    return {k: deepcopy(record[k]) for k in fields if k in record}
