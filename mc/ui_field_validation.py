"""Incoming fields interpolated by dashboard renderers (MC-1183).

Validate submitted values only; legacy records remain readable and unchanged.
"""
import re

_PROJECT_ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}', re.ASCII)
_COLOR = re.compile(r'(?:#[0-9a-fA-F]{3,8}|var\(--[a-z0-9-]+\))', re.ASCII)
_STATUSES = ('open', 'in_progress', 'blocked', 'done', 'wontdo')
_PRIORITIES = ('low', 'normal', 'high')


def validate_project_fields(project_id, data):
    if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
        return 'project id must match ^[a-z0-9][a-z0-9_-]{0,63}$'
    if 'id' in data and (not isinstance(data['id'], str) or not _PROJECT_ID.fullmatch(data['id'])):
        return 'id must match ^[a-z0-9][a-z0-9_-]{0,63}$'
    if 'modal_color' in data and data['modal_color'] is not None:
        colors = data['modal_color']
        if not isinstance(colors, dict):
            return 'modal_color must be an object or null'
        return validate_colors(colors)
    return None


def validate_colors(data):
    for key in ('color', 'bg'):
        if key in data and (not isinstance(data[key], str) or not _COLOR.fullmatch(data[key])):
            return f'{key} must be a hex color or CSS variable'
    return None


def validate_backlog_fields(data):
    for key, allowed in (('status', _STATUSES), ('priority', _PRIORITIES)):
        if key in data and data[key] not in allowed:
            return f'{key} must be one of: {", ".join(allowed)}'
    return None


def validate_workstream_priority(data):
    if 'priority' in data and type(data['priority']) is not int:
        return 'priority must be an integer'
    return None
