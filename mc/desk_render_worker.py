"""Durable storyboard submission; only the human-gated render route enqueues.

The server owns these threads. Never replay an interrupted paid call on boot.
Unsent parent holds are released; linked child holds survive uncertain sends.
"""
from __future__ import annotations

import copy
import hashlib
import threading
import uuid
from dataclasses import asdict

from mc import desk_engines as eng
from mc import desk_storyboard
from mc.core import _log, now_iso
from mc.desk_generation_preflight import prepare_plan
from mc.desk_render_price_confirmation import check, shown_total

_RUN = uuid.uuid4().hex
_active: set[str] = set()  # protected by eng._lock, like the durable store
_FIELDS = ('engine_id', 'model_id', 'aspect_ratio', 'resolution', 'audio', 'project_id')


def _finish(rid: str, **fields) -> None:
    with eng._lock:
        store = eng._read_store()
        store['renders'][rid].update(fields, updated_at=now_iso())
        eng._write_store(store)


def recover(rid: str) -> None:
    """Read-side recovery only: never start a worker or retry a generation."""
    with eng._lock:
        store = eng._read_store()
        rec = store['renders'].get(rid)
        if not rec or rec.get('submission_state') not in ('pending', 'working'):
            return
        if rec.get('worker_run') == _RUN and rid in _active:
            return
        rec.update(status='held', submission_state='held', reserved_usd=0,
                   updated_at=now_iso(), hold={
                       'kind': 'submit_interrupted',
                       'message': 'Submission was interrupted. Nothing was retried. Check the engine dashboard '
                                  'before starting another render; any sent clip cost stays reserved.'})
        eng._write_store(store)
        _log(f'[desk_render_worker] {rid} interrupted; child holds retained, no paid retry', flush=True)


def enqueue(data: dict, *, unattended: bool = False) -> tuple[dict, bool]:
    if not isinstance(data, dict):
        raise eng.Refused('invalid_input', 'body must be a JSON object', 400)
    kind, oid, campaign_id, project_id = eng._owner_scope(data.get('owner'), data)
    key = data.get('idempotency_key')
    if not isinstance(key, str) or not key.strip() or len(key) > 120:
        raise eng.Refused('invalid_input', 'idempotency_key is required (a retried click must not spend twice)', 400)
    owner = {'kind': kind, 'id': oid}
    idem = f'{kind}:{oid}:{key}'
    with eng._lock:
        store = eng._read_store()
        previous = store['render_idem'].get(idem)
        if previous:
            # A held price needs a NEW click/key; replays never authorize spending.
            return eng._render_view(previous), True  # type: ignore[return-value]
        for rec in store['renders'].values():
            if rec['owner'] == owner and (rec['status'] in ('pending', 'working', 'queued', 'rendering')
                    or (rec.get('hold') or {}).get('kind') in ('submit_interrupted', 'submit_uncertain')):
                store['render_idem'][idem] = rec['render_id']
                eng._write_store(store)
                return eng._render_view(rec['render_id']), True  # type: ignore[return-value]
        displayed = shown_total(data)
        engine_id, model_id = str(data.get('engine_id') or '').strip(), str(data.get('model_id') or '').strip()
        if engine_id not in eng.ENGINES:
            raise eng.Refused('unknown_engine', f'unknown engine {engine_id!r}', 400)
        model = eng.get_model(engine_id, model_id)
        if model is None:
            raise eng.Refused('unknown_model', f'{engine_id} has no model {model_id!r}', 400)
        if model.kind != 'video':
            raise eng.Refused('invalid_input', f'{model_id} makes {model.kind}, not video', 400)
        ratio = data.get('aspect_ratio')
        if ratio not in eng._DESK_RATIOS:
            raise eng.Refused('invalid_input', 'Choose a supported video shape', 400)
        try:
            board = desk_storyboard.get_storyboard(kind, oid)
        except eng._pieces.PieceError as e:
            raise eng.Refused('storyboard_unavailable', str(e), e.status) from e
        if not board['scenes']:
            raise eng.Refused('no_scenes', 'this storyboard has no scenes to render: add one first', 409)
        # This approval is the largest allowed spend, reserved before any remote
        # work. Exact price and caps are checked again in the worker.
        eng.check_caps(displayed, engine_id, campaign_id, store)
        rid = f'rnd-{uuid.uuid4().hex[:10]}'
        request = {k: copy.deepcopy(data[k]) for k in _FIELDS if k in data}
        request.update(owner=owner, engine_id=engine_id, model_id=model_id, aspect_ratio=ratio)
        currency = eng.ENGINES[engine_id].currency
        now = now_iso()
        rec = dict(render_id=rid, status='pending', submission_state='pending', worker_run=_RUN,
                   hold=None, failure=None, owner=owner, campaign_id=campaign_id, project_id=project_id,
                   engine_id=engine_id, model_id=model_id, aspect_ratio=ratio, crop_square=False,
                   clip_count=len(board['scenes']), children=[], outputs=[], clips=[],
                   estimate={'usd': displayed if currency != 'credits' else 0,
                             **({'credits': displayed} if currency == 'credits' else {})},
                   reserved_usd=displayed if currency != 'credits' else 0,
                   request=request, board=copy.deepcopy(board), shown_total=displayed,
                   created_at=now, updated_at=now)
        store['renders'][rid] = rec
        store['render_idem'][idem] = rid
        eng._write_store(store)
        _active.add(rid)
        result = eng._public_render(rec, store['jobs'])
    try:
        launch(rid, unattended=unattended)
    except Exception as e:
        _log(f'[desk_render_worker] {rid} launch failed: {eng._safe(e)}', flush=True)
        with eng._lock:
            _active.discard(rid)
        _finish(rid, status='failed', submission_state='failed', reserved_usd=0,
                failure={'kind': 'worker', 'message': 'Could not start submission. Nothing was sent; press Render again.'})
        result = eng._render_view(rid)
    return result, False  # type: ignore[return-value]


def launch(rid: str, *, unattended: bool) -> None:
    threading.Thread(target=run, args=(rid,), kwargs={'unattended': unattended},
                     name=f'desk-render-{rid}', daemon=True).start()


def run(rid: str, *, unattended: bool = False) -> None:
    """Prepare every picture and price before the first generation call."""
    try:
        with eng._lock:
            rec = copy.deepcopy(eng._read_store()['renders'][rid])
        _finish(rid, status='working', submission_state='working')
        plan = eng._plan_render(rec['request'], unattended=unattended, _board=rec['board'])
        with eng._lock:
            before = eng._read_store()
            before['renders'][rid]['reserved_usd'] = 0
            eng.check_caps(plan['total_amount'], plan['engine_id'], plan['campaign_id'], before,
                           estimate=eng._plan_estimate(plan))
        prepare_plan(plan, unattended=unattended)
        with eng._lock:
            store = eng._read_store()
            parent = store['renders'][rid]
            parent['reserved_usd'] = 0  # exclude our own hold from the cap comparison
            eng.check_caps(plan['total_amount'], plan['engine_id'], plan['campaign_id'], store,
                           estimate=eng._plan_estimate(plan))
            check(rec['shown_total'], plan, estimate=eng._plan_estimate(plan), public_plan=eng._plan_public(plan))
            parent.update(reserved_usd=plan['total_usd'], estimate=eng._plan_estimate(plan),
                          crop_square=plan['crop_square'], updated_at=now_iso())
            eng._write_store(store)
        for n, row in enumerate(plan['rows']):
            sid = row['scene']['id']
            body = asdict(row['req'])
            body['desk'] = {'scene_id': sid,
                            'idempotency_key': 'r-' + hashlib.sha1(f'{rid}|{sid}'.encode()).hexdigest()[:32]}
            if plan['campaign_id']:
                body['campaign_id'] = plan['campaign_id']
            elif plan['project_id']:
                body['project_id'] = plan['project_id']
            job, _ = eng.submit(body, unattended=unattended, _skip_limit=True,
                                _prepared=row.get('prepared'), _estimate=row['est'], _render_id=rid)
            if job['status'] == 'failed':
                _finish(rid, status='held', submission_state='held', reserved_usd=0,
                        hold={'kind': 'submit_uncertain', 'message': f"Scene {n + 1}: "
                              + (job.get('failure') or {}).get('message', 'Check the engine dashboard before retrying.')})
                return
        _finish(rid, status='rendering', submission_state='submitted', reserved_usd=0)
    except eng.Refused as e:
        message = eng._safe(str(e))
        if e.code == 'render_price_changed':
            _finish(rid, status='held', submission_state='held', reserved_usd=0,
                    estimate=e.extra['estimate'],
                    hold={'kind': e.code, 'code': e.code, 'message': message, **e.extra,
                          'request': rec['request'], 'board_fingerprint': fingerprint(rec['board'])})
        else:
            _failed(rid, e.code, message)
    except eng.NotConnected as e:
        _failed(rid, 'auth', e.reason)
    except eng.EngineError as e:
        _failed(rid, e.kind, str(e))
    except Exception as e:
        _log(f'[desk_render_worker] {rid} crashed: {eng._safe(e)}', flush=True)
        _failed(rid, 'worker', 'Submission stopped unexpectedly. Check the engine dashboard before retrying.')
    finally:
        with eng._lock:
            _active.discard(rid)


def _failed(rid: str, kind: str, message: str) -> None:
    with eng._lock:
        rec = eng._read_store()['renders'][rid]
    sent = len(rec['children'])
    suffix = f' {sent} earlier clip(s) may already be billed; check the engine dashboard.' if sent else ''
    _log(f'[desk_render_worker] {rid} {kind}: {eng._safe(message)}', flush=True)
    _finish(rid, status='failed', submission_state='failed', reserved_usd=0,
            failure={'kind': kind, 'message': eng._safe(message) + suffix})


def fingerprint(board: dict) -> str:
    import json
    return hashlib.sha256(json.dumps(board['scenes'], sort_keys=True).encode()).hexdigest()
