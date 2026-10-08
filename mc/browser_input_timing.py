"""Bounded, numeric-only phone-side browser input diagnostics (opt-in client)."""
from collections import deque
import math
from threading import Lock
from typing import Any

from flask import Blueprint, jsonify, request

bp = Blueprint('browser_input_timing', __name__)
CAPACITY = 1000
BATCH_LIMIT = 50
BODY_LIMIT = 65536
_samples: deque[dict[str, Any]] = deque(maxlen=CAPACITY)
_lock = Lock()
_total = 0
_FIELDS = frozenset(('client_id', 'sample_id', 'request_id', 'queue_depth',
                     't_key', 't_sent', 't_ack', 't_frame', 'outcome',
                     'http_status', 'transport'))
_NULLABLE = frozenset(('t_ack', 't_frame'))
_INTEGER = frozenset(('client_id', 'sample_id', 'request_id', 'queue_depth',
                      'outcome', 'http_status', 'transport'))


def _sample(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ValueError('sample must contain only the documented numeric fields')
    for key, number in value.items():
        if number is None and key in _NULLABLE:
            continue
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ValueError('sample values must be numbers')
        if not 0 <= number <= 9007199254740991 or not math.isfinite(number):
            raise ValueError('sample values must be bounded finite nonnegative numbers')
        if key in _INTEGER and int(number) != number:
            raise ValueError('identifiers and codes must be integers')
    if value['outcome'] not in (0, 1, 2, 3) or value['transport'] not in (0, 1, 2):
        raise ValueError('unknown outcome or transport code')
    if value['http_status'] > 599 or value['queue_depth'] > 500:
        raise ValueError('status or queue depth outside bounds')
    if value['t_sent'] < value['t_key']:
        raise ValueError('send precedes input')
    ack, frame = value['t_ack'], value['t_frame']
    if ack is not None and ack < value['t_sent']:
        raise ValueError('ack precedes send')
    if frame is not None and (ack is None or frame < ack):
        raise ValueError('frame precedes ack')
    if value['outcome'] == 1 and (ack is None or frame is None):
        raise ValueError('shown frame requires ack and frame timestamps')
    if value['outcome'] != 1 and frame is not None:
        raise ValueError('non-frame outcome cannot contain a frame timestamp')
    return dict(value)


@bp.route('/api/browser/input-timing', methods=['POST'])
def record():
    global _total
    # Limit the read itself, including chunked bodies without Content-Length.
    if request.content_length is not None and request.content_length > BODY_LIMIT:
        return jsonify({'error': 'timing body too large'}), 413
    raw = request.stream.read(BODY_LIMIT + 1)
    if len(raw) > BODY_LIMIT:
        return jsonify({'error': 'timing body too large'}), 413
    try:
        import json
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {'user_agent', 'samples'}:
            raise ValueError('body requires only user_agent and samples')
        ua, values = data['user_agent'], data['samples']
        if not isinstance(ua, str) or len(ua) > 512 or any(ord(c) < 32 for c in ua):
            raise ValueError('user_agent must be at most 512 printable characters')
        if not isinstance(values, list) or not 1 <= len(values) <= BATCH_LIMIT:
            raise ValueError('samples must contain 1 to 50 entries')
        samples = [_sample(v) for v in values]
    except (ValueError, TypeError, OverflowError, RecursionError):
        # Do not echo the payload: a malformed request may contain typed text.
        return jsonify({'error': 'invalid input timing payload'}), 400
    with _lock:
        for sample in samples:
            _total += 1
            _samples.append({**sample, 'user_agent': ua, 'index': _total})
    return jsonify({'ok': True, 'accepted': len(samples)})


@bp.route('/api/browser/input-timing', methods=['GET'])
def read():
    try:
        after = int(request.args.get('after', '0'))
        if after < 0:
            raise ValueError
    except ValueError:
        return jsonify({'error': 'after must be a nonnegative integer'}), 400
    with _lock:
        samples = [dict(s) for s in _samples if s['index'] > after]
        total, retained = _total, len(_samples)
    response = jsonify({'samples': samples, 'capacity': CAPACITY,
                        'total_received': total, 'retained': retained,
                        'evicted': max(0, total - retained)})
    response.headers['Cache-Control'] = 'no-store'
    return response
