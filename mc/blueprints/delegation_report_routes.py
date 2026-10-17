"""Explicit report handling, separate from transport/retry routes."""
from typing import Callable

from flask import jsonify, request

from mc.delegation_delivery import DeliveryStore


def register_routes(bp, *, store_fn: Callable[[], DeliveryStore | None]):
    @bp.route('/api/project/<project_id>/agent/delegation/mark-handled', methods=['POST'])
    def delegation_mark_handled(project_id):
        store = store_fn()
        if store is None:
            return jsonify({'error': 'delegation delivery unavailable'}), 503
        data = request.get_json(silent=True)
        event_id = data.get('event_id') if isinstance(data, dict) else None
        if not isinstance(event_id, str) or not event_id.strip():
            return jsonify({'error': 'event_id required'}), 400
        try:
            if not store.mark_handled(event_id.strip(), project_id):
                return jsonify({'error': 'event not found'}), 404
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 409
        return jsonify({'ok': True, 'event_id': event_id.strip()})
