"""Bounded validation of captured MCP shapes; no external refs or executable schemas."""
from __future__ import annotations

import math
import re
import uuid
from typing import Any

_KEYS = {'$schema', 'title', 'description', 'default', 'examples', 'type', 'properties',
         'additionalProperties', 'propertyNames', 'required', 'items', 'anyOf', 'oneOf',
         'enum', 'const', 'minimum', 'maximum', 'minLength', 'maxLength', 'minItems',
         'maxItems', 'pattern', 'format'}


_UUID_PATTERN = '^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$'


def finite_number(value: Any) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def matches(value: Any, schema: dict, depth: int = 0) -> bool:
    """Only the vocabulary used by the captured contract is supported. Unknown fails closed."""
    if depth > 16 or not isinstance(schema, dict) or set(schema) - _KEYS:
        return False
    if 'anyOf' in schema or 'oneOf' in schema:
        key = 'anyOf' if 'anyOf' in schema else 'oneOf'
        variants = schema[key]
        if not isinstance(variants, list) or not 1 <= len(variants) <= 32:
            return False
        hits = sum(matches(value, child, depth + 1) for child in variants)
        if not (hits >= 1 if key == 'anyOf' else hits == 1):
            return False
    typ = schema.get('type')
    kinds = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str), 'boolean': isinstance(value, bool),
             'null': value is None, 'integer': type(value) is int,
             'number': finite_number(value)}
    if typ is not None and (not isinstance(typ, str) or not kinds.get(typ, False)):
        return False
    if 'const' in schema and (value != schema['const'] or type(value) is not type(schema['const'])):
        return False
    if 'enum' in schema and not any(value == v and type(value) is type(v) for v in schema['enum']):
        return False
    if isinstance(value, dict):
        props = schema.get('properties', {})
        required = schema.get('required', [])
        if not isinstance(props, dict) or not isinstance(required, list) or any(k not in value for k in required):
            return False
        extra = schema.get('additionalProperties', {})
        for k, v in value.items():
            if 'propertyNames' in schema and not matches(k, schema['propertyNames'], depth + 1):
                return False
            child = props.get(k, extra)
            if child is False or (child is not True and not matches(v, child, depth + 1)):
                return False
    if isinstance(value, list):
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 10000):
            return False
        if any(not matches(v, schema.get('items', {}), depth + 1) for v in value):
            return False
    if isinstance(value, str):
        if not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 4 * 1024 * 1024):
            return False
        if schema.get('format') == 'uuid':
            try:
                if str(uuid.UUID(value)) != value.lower():
                    return False
                if schema.get('pattern') and (schema['pattern'] != _UUID_PATTERN or not re.fullmatch(_UUID_PATTERN, value)):
                    return False
            except (ValueError, AttributeError):
                return False
        elif schema.get('format') or schema.get('pattern'):
            return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not finite_number(value) or not schema.get('minimum', -math.inf) <= value <= schema.get('maximum', math.inf):
            return False
    return True


def tool(doc: dict, name: str) -> dict:
    hits = [t for t in doc.get('tools', []) if isinstance(t, dict) and t.get('name') == name]
    if len(hits) != 1:
        raise ValueError('missing or ambiguous tool')
    return hits[0]


def validate(doc: dict, name: str, field: str, value) -> None:
    schema = tool(doc, name).get(field)
    if not isinstance(schema, dict) or schema.get('type') != 'object' or not matches(value, schema):
        raise ValueError('response or input does not match the captured contract')
