"""Incoming fields interpolated by dashboard renderers (MC-1183).

Validate submitted values only; legacy records remain readable and unchanged.
"""
import re

# A leading underscore is allowed: `_ideas` and `_incognito` are real reserved
# workspaces, and refusing them would stop every write to those records.
_PROJECT_ID = re.compile(r'[a-z0-9_][a-z0-9_-]{0,63}', re.ASCII)
_COLOR = re.compile(r'(?:#[0-9a-fA-F]{3,8}|var\(--[a-z0-9-]+\))', re.ASCII)
_STATUSES = ('open', 'in_progress', 'blocked', 'done', 'wontdo')
# Priority is a shape rule, not a list. Agents and older records use words the
# dashboard does not offer ('medium', 'P1'); the rule only has to keep markup
# and script out, which the renderers also escape on their own.
_PRIORITY = re.compile(r'[A-Za-z0-9_-]{1,24}', re.ASCII)


def validate_project_fields(project_id, data):
    if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
        return 'project id must match ^[a-z0-9_][a-z0-9_-]{0,63}$'
    if 'id' in data and (not isinstance(data['id'], str) or not _PROJECT_ID.fullmatch(data['id'])):
        return 'id must match ^[a-z0-9_][a-z0-9_-]{0,63}$'
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
    if 'status' in data and data['status'] not in _STATUSES:
        return f'status must be one of: {", ".join(_STATUSES)}'
    if 'priority' in data and (not isinstance(data['priority'], str)
                               or not _PRIORITY.fullmatch(data['priority'])):
        return 'priority must be 1-24 letters, digits, _ or -'
    return None


def validate_workstream_priority(data):
    if 'priority' in data and type(data['priority']) is not int:
        return 'priority must be an integer'
    return None
