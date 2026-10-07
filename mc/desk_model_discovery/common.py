"""Bounded projections and pagination; vendor data never authorizes actions."""
from __future__ import annotations

import math
import re
from typing import Any, NoReturn

from mc import desk_engines as engines

MAX_PAGES = 50
MAX_MODELS = 5000
MAX_BYTES = 4 * 1024 * 1024


def fail(message: str) -> NoReturn:
    raise engines.EngineError('engine', message)


def rows(out: dict, field: str) -> list[dict]:
    value = out.get(field)
    if not isinstance(value, list) or any(not isinstance(v, dict) for v in value):
        fail('The service returned a malformed model list')
    return value


def cursor(value: Any, seen: set[str]) -> str:
    if value is None or value == '':
        return ''
    if not isinstance(value, str) or len(value) > 2048 or value in seen:
        fail('The service returned invalid or repeated model pagination')
    seen.add(value)
    return value


def project(model_id: Any, kind: Any, label: Any, *, raw: dict | None = None) -> dict | None:
    if not isinstance(model_id, str) or not re.fullmatch(r'[A-Za-z0-9_.\-/]{1,200}', model_id) \
            or '..' in model_id or kind not in ('image', 'video'):
        return None
    if engines._safe(model_id, 200) != model_id:
        return None
    raw = raw or {}
    durations = raw.get('durations', [])
    durations = [n for n in durations if isinstance(n, (int, float)) and not isinstance(n, bool)
                 and math.isfinite(n) and 0 < n <= 600] if isinstance(durations, list) else []
    ratios = raw.get('aspect_ratios', [])
    ratios = [r for r in ratios if isinstance(r, str) and re.fullmatch(r'\d{1,2}:\d{1,2}', r)] \
        if isinstance(ratios, list) else []
    parameters = raw.get('parameters')
    medias = raw.get('medias')
    picture = required_picture = audio = None
    resolutions = []
    if isinstance(medias, list):
        picture = any(isinstance(m, dict) and m.get('type') == 'image' for m in medias)
        required_picture = any(isinstance(m, dict) and m.get('type') == 'image'
                               and m.get('required') is True for m in medias)
    if isinstance(parameters, list):
        audio = any(isinstance(p, dict) and p.get('name') in ('audio', 'generate_audio') for p in parameters)
        for p in parameters:
            if isinstance(p, dict) and p.get('name') == 'resolution' and isinstance(p.get('options'), list):
                resolutions = [v for v in p['options'] if isinstance(v, str) and
                               re.fullmatch(r'[A-Za-z0-9]{1,16}', v)][:30]
    return {'model_id': model_id, 'kind': kind,
            'label': engines._safe(label if isinstance(label, str) else model_id, 128),
            'durations_sec': durations[:100], 'aspect_ratios': ratios[:30],
            'catalogue_inputs': {'picture': picture, 'required_picture': required_picture, 'audio': audio},
            'resolutions': resolutions,
            'price': {'state': 'unknown'}, 'untrusted_vendor_text': True}
