"""Claim a Desk proposal before running the human-approved writer dispatch."""

from __future__ import annotations

import uuid
from concurrent.futures import Future
from typing import Callable

from flask import Response

from mc import desk, desk_brief
from mc.core import _log, now_iso

Result = tuple[dict, int]
Prepare = Callable[[dict], Callable[[str], str] | tuple[Response, int]]

# Guarded by the Desk store lock. Only same-process requests can join a live
# dispatch. On restart the stored session id still latches the accepted claim.
_dispatches: dict[str, Future[Result]] = {}


def _claim(proposal_id: str) -> tuple[dict | None, Future[Result] | None, bool]:
    with desk._store_lock:
        store = desk._read_store()
        prop = store['proposals'].get(proposal_id)
        if prop is None:
            return None, None, False
        if prop['state'] == 'accepting':
            return prop, _dispatches.get(prop['draft_dispatch']), False
        if prop['state'] != 'proposed':
            return prop, None, False

        dispatch_id = uuid.uuid4().hex[:12]
        prop.update(state='accepting', draft_dispatch=dispatch_id)
        desk._write_store(store)
        pending: Future[Result] = Future()
        _dispatches[dispatch_id] = pending
        return prop, pending, True


def _finish(prop: dict, result: Result, dispatch_error: str | None) -> None:
    body, status = result
    with desk._store_lock:
        store = desk._read_store()
        row = store['proposals'].get(prop['id'])
        if row is None or row['state'] != 'accepting' or row['draft_dispatch'] != prop['draft_dispatch']:
            raise RuntimeError('proposal dispatch claim was lost')
        if status == 202:
            row.update(state='accepted', decided_at=now_iso(), draft_dispatch=body['session_id'],
                       draft_platform=body['platform'])
            row.pop('dispatch_error', None)
        else:
            row.update(state='proposed', draft_dispatch=None)
            if dispatch_error is not None:
                row['dispatch_error'] = dispatch_error
        desk._write_store(store)


def accept(proposal_id: str, prepare: Prepare) -> Result:
    prop, pending, owner = _claim(proposal_id)
    if prop is None:
        return {'error': 'proposal not found'}, 404
    if not owner:
        if pending is not None:
            return pending.result()
        if prop['state'] in ('accepting', 'accepted'):
            return {'ok': True, 'session_id': prop['draft_dispatch'],
                    'platform': prop.get('draft_platform') or desk_brief.platform_for(prop['voice'])}, 202
        return {'error': f"already {prop['state']}"}, 409

    assert pending is not None
    dispatch_id = prop['draft_dispatch']
    dispatch_error = None
    try:
        launch = prepare(prop)
        if isinstance(launch, tuple):
            response, status = launch
            result = response.get_json(), status
        else:
            platform = desk_brief.platform_for(prop['voice'])
            session_id = launch(dispatch_id)
            result = {'ok': True, 'session_id': session_id, 'platform': platform}, 202
    except Exception as e:
        dispatch_error = str(e)
        _log(f'[desk] accept dispatch failed for {proposal_id}: {e}')
        result = {'error': f'dispatch failed: {e}'}, 502

    try:
        _finish(prop, result, dispatch_error)
    except Exception as e:
        # A successful launch still owns its durable claim. A replay must
        # never try the dispatcher again because finalizing the row failed.
        _log(f'[desk] could not finish proposal {proposal_id} dispatch {dispatch_id}: {e}', level='error')
        if result[1] != 202:
            result = {'error': f'could not save the proposal after dispatch failed: {e}'}, 503
    finally:
        pending.set_result(result)
        with desk._store_lock:
            _dispatches.pop(dispatch_id, None)
    return result


def dismiss(proposal_id: str) -> Result:
    with desk._store_lock:
        store = desk._read_store()
        prop = store['proposals'].get(proposal_id)
        if prop is None:
            return {'error': 'proposal not found'}, 404
        if prop['state'] != 'proposed':
            return {'error': f"cannot dismiss a proposal that is {prop['state']}"}, 409
        prop.update(state='dismissed', decided_at=now_iso())
        desk._write_store(store)
        return {'ok': True, 'proposal': prop}, 200
