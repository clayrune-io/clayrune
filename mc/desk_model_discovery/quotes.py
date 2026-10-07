"""Free text-only catalogue quotes; literal get_cost, no media or caller tool args."""
from __future__ import annotations

import math
from typing import Any

from mc import desk_engines as engines
from mc.desk_higgsfield_mcp import HiggsfieldMcpAdapter
from mc.desk_model_discovery.catalogue import discover


def quote(engine_id: str, body: Any, *, unattended: bool = False) -> dict:
    allowed = {'owner', 'model_id', 'aspect_ratio', 'project_id', 'kind', 'prompt'}
    if not isinstance(body, dict) or set(body) - allowed:
        raise engines.Refused('invalid_input', 'Only model, owner, shape and text quote fields are accepted', 400)
    kind = body.get('kind', 'video')
    ratio = body.get('aspect_ratio', '16:9')
    if not isinstance(body.get('model_id'), str) or not body['model_id']:
        raise engines.Refused('invalid_input', 'A quote needs a discovered model ID', 400)
    if kind not in ('video', 'image') or ratio not in ('16:9', '9:16', '1:1'):
        raise engines.Refused('invalid_input', 'Invalid quote kind or aspect ratio', 400)
    pid = body.get('project_id')
    if pid is not None and not isinstance(pid, str):
        raise engines.Refused('invalid_input', 'project_id must be a string', 400)
    scenes: list[dict] = []
    board_rev = None
    if kind == 'video':
        from mc import desk_storyboard
        owner_kind, oid, _, pid = engines._owner_scope(body.get('owner'), body)
        board = desk_storyboard.get_storyboard(owner_kind, oid)
        board_rev = board.get('rev')
        scenes = board['scenes']
        if not scenes or len(scenes) > 100:
            raise engines.Refused('invalid_input', 'A quote needs 1 to 100 saved storyboard scenes', 400)
    else:
        prompt = body.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
            raise engines.Refused('invalid_input', 'An image quote needs a description of at most 4000 characters', 400)
        scenes = [{'id': 'image', 'label': '', 'line': prompt.strip()}]
    doc = discover(engine_id, kind=kind, project_id=pid, unattended=unattended)
    model = next((m for m in doc['models'] if m['model_id'] == body.get('model_id')), None)
    if model is None:
        raise engines.Refused('unknown_model', 'This model is absent from the discovered catalogue', 400)
    if engine_id != 'higgsfield_mcp':
        return {'price': {'state': 'unknown'}, 'note': 'Price unknown: this service offers no verified free quote.'}
    creds = engines._creds(engine_id, pid, unattended)
    descriptor = engines.ModelDescriptor(model_id=model['model_id'], kind=kind, label=model['label'],
                                        durations_sec=model['durations_sec'])
    adapter = HiggsfieldMcpAdapter()
    from mc.desk_connect.higgsfield_mcp_snapshot import read as snapshot
    from mc.desk_higgsfield_preset_decline import quote as literal_quote
    contract = snapshot() or {}
    requests = []
    for scene in scenes:
        prompt = engines._scene_prompt(scene)
        if not prompt or len(prompt) > 4000:
            raise engines.Refused('invalid_input', 'Each saved scene needs a description of at most 4000 characters', 400)
        duration = engines._snap_duration(descriptor, scene.get('duration_sec')) if kind == 'video' else None
        params = {'model': descriptor.model_id, 'prompt': prompt, 'count': 1,
                  'aspect_ratio': ratio, 'get_cost': True, 'use_unlim': False}
        if duration is not None:
            params['duration'] = duration
        requests.append((scene, params, duration))
    quoted = []
    for scene, params, duration in requests:
        # Deliberately bypass prepare(): it uploads pictures. This route never
        # copies vendor recovery args, follows suggested tools or submits a job.
        out, _, literal_note = literal_quote(
            contract, lambda tool, args: engines._mcp_data(adapter._call(creds, tool, args, free=True)),
            'generate_' + kind, {'params': params})
        from mc.desk_higgsfield_quote_response import refusal
        reason = refusal(out)
        if reason:
            raise engines.Refused('quote_unavailable', reason, 409)
        price = adapter._quote(out)
        credits = price.credits
        if credits is None or not math.isfinite(credits) or credits < 0:
            raise engines.EngineError('engine', 'The service returned an invalid free quote')
        quoted.append({'scene_id': scene['id'], 'requested_sec': scene.get('duration_sec'),
                       'sent_duration_sec': duration, 'credits': credits,
                       'adjusted': bool(price.adjustments), 'preset_declined': bool(literal_note)})
    total = sum(s['credits'] for s in quoted)
    if not math.isfinite(total):
        raise engines.EngineError('engine', 'The service returned an invalid total quote')
    return {'engine_id': engine_id, 'model_id': descriptor.model_id,
            'board_rev': board_rev,
            'price': {'state': 'quoted', 'currency': 'credits', 'credits': total},
            'scenes': quoted, 'text_only': True, 'pictures_omitted': any(s.get('picture') for s in scenes),
            'note': 'Text-only quote; pictures are omitted. No upload or generation was performed.'
                    + (' The vendor adjusted at least one request; sent durations are not a confirmed output length.'
                       if any(s['adjusted'] for s in quoted) else '')}
