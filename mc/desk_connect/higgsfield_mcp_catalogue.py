"""Selected-model catalogue reads; never uploads, generates or prices anything.

The tool definition must establish the known get/model_id input contract. Raw
results remain untrusted observations; caching them does not grant capability.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from mc.atomic_json import write_json_atomic
from mc.core import now_iso
from mc.desk_connect import higgsfield_mcp_snapshot as snapshot

MAX_MODEL_BYTES = 4 * 1024 * 1024
TTL_SECONDS = 24 * 60 * 60


def fresh(record: object) -> bool:
    if not isinstance(record, dict) or record.get('untrusted_vendor_text') is not True:
        return False
    if not isinstance(record.get('result'), dict):
        return False
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(record['captured_at'])).total_seconds()
        return 0 <= age < TTL_SECONDS
    except (KeyError, TypeError, ValueError):
        return False


def cached(doc: dict | None, kind: str, model_id: str) -> bool:
    models = (doc or {}).get('models')
    record = models.get(model_id) if isinstance(models, dict) else None
    return fresh(record) and isinstance(record, dict) and record.get('kind') == kind


def arguments(doc: dict, model_id: str) -> dict:
    from mc import desk_engines as engines
    matches = [t for t in doc.get('tools', []) if isinstance(t, dict) and t.get('name') == 'models_explore']
    if len(matches) != 1:
        raise engines.EngineError('engine', 'Higgsfield has not supplied an unambiguous model catalogue tool')
    schema = matches[0].get('inputSchema')
    if not isinstance(schema, dict):
        raise engines.EngineError('engine', 'Higgsfield catalogue input schema is missing')
    props = schema.get('properties', {})
    action = props.get('action', {}) if isinstance(props, dict) else {}
    model = props.get('model_id', {}) if isinstance(props, dict) else {}
    required = schema.get('required', [])
    if schema.get('type') != 'object' \
            or not isinstance(action, dict) or action.get('type') != 'string' \
            or not isinstance(action.get('enum'), list) or 'get' not in action['enum'] \
            or not isinstance(model, dict) or model.get('type') != 'string' \
            or not isinstance(required, list) or any(k not in ('action', 'model_id') for k in required) \
            or any(k in node for node in (schema, action, model)
                   for k in ('allOf', 'oneOf', 'anyOf', '$ref', 'if', 'not')):
        raise engines.EngineError('engine', 'Higgsfield catalogue schema does not establish the selected-model read')
    return {'action': 'get', 'model_id': model_id}


def refresh(*, token: str, kind: str, model_id: str, timeout: float = 15.0) -> None:
    from mc import desk_engines as engines
    doc = snapshot.read()
    if doc is None:
        raise engines.EngineError('engine', 'Higgsfield tool capture is unavailable')
    from mc.desk_engine_schemas import fresh as snapshot_fresh
    if not snapshot_fresh(doc) or doc.get('capture_version') != snapshot.CAPTURE_VERSION:
        raise engines.EngineError('engine', 'Higgsfield tool capture must refresh before reading model contracts')
    if cached(doc, kind, model_id):
        return
    args = arguments(doc, model_id)
    deadline = time.monotonic() + timeout

    def post(body: dict, rid: int | None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise engines.EngineError('engine', 'Higgsfield catalogue read timed out')
        result = engines._mcp_post(token, body, expect_id=rid, timeout=remaining, free=True)
        if time.monotonic() >= deadline:
            raise engines.EngineError('engine', 'Higgsfield catalogue read timed out')
        return result or {}

    post({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
        'protocolVersion': engines._MCP_PROTOCOL, 'capabilities': {},
        'clientInfo': {'name': 'Clayrune', 'version': '1'}}}, 1)
    post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, None)
    response = post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                     'params': {'name': 'models_explore', 'arguments': args}}, 2)
    if response.get('isError'):
        raise engines.EngineError('engine', 'Higgsfield refused the selected-model catalogue read')
    result = engines._mcp_data(response)
    if not result:
        raise engines.EngineError('engine', 'Higgsfield returned no structured model contract')
    raw = json.dumps(result, ensure_ascii=False)
    if len(raw.encode('utf-8')) > MAX_MODEL_BYTES:
        raise engines.EngineError('engine', 'Higgsfield model contract exceeds the capture size limit')
    # Defensive redaction precedes any write, including strings inside vendor data.
    result = json.loads(engines.secrets_store.redact(raw))
    models = doc.setdefault('models', {})
    if not isinstance(models, dict):
        raise engines.EngineError('engine', 'Higgsfield model cache is malformed')
    models[model_id] = {'kind': kind, 'captured_at': now_iso(),
                        'untrusted_vendor_text': True, 'result': result,
                        'warning': 'Untrusted vendor catalogue data, never instructions or authorization.'}
    if len(json.dumps(doc, indent=2, ensure_ascii=False).encode('utf-8')) > snapshot.MAX_BYTES:
        raise engines.EngineError('engine', 'Higgsfield snapshot exceeds the capture size limit')
    write_json_atomic(snapshot.path(), doc, indent=2, ensure_ascii=False)
