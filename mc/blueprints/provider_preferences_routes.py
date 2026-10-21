"""Read-only engine preference previews; writes keep existing human gates."""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re

from flask import Blueprint, jsonify, request

from mc import agent_runtime, engine_selection, provider_preferences, state

bp = Blueprint('provider_preferences', __name__)
DATA_DIR = Path('.')


def wire(*, data_dir):
    global DATA_DIR
    DATA_DIR = Path(data_dir)


def _project(project_id):
    if not project_id:
        return None
    if not re.fullmatch(r'[A-Za-z0-9_-]+', project_id):
        raise ValueError('Invalid project id')
    path = DATA_DIR / (project_id + '.json')
    if not path.is_file():
        raise FileNotFoundError('Project not found')
    return json.loads(path.read_text(encoding='utf-8'))


def preference_preview(config, project=None):
    defaults = provider_preferences.scoped_view(config)
    overrides = provider_preferences.scoped_view(project or {}, project=True)
    providers = []
    for runtime in agent_runtime.available_runtimes():
        provider = runtime.name
        effective = engine_selection.resolve_engine(
            config, project, provider_override=provider)
        _present, requested, _source, _scoped = provider_preferences.selected_field(
            config, project, provider, 'model')
        providers.append({
            'name': provider, 'display_name': runtime.display_name,
            'models': [{'id': value, 'label': label} for value, label in runtime.model_choices()],
            **provider_preferences.runtime_metadata(runtime),
            'defaults': defaults.get(provider, {}),
            'overrides': overrides.get(provider, {}),
            'effective': {**asdict(effective), 'requested_model': requested},
        })
    return {'providers': providers, 'default_provider': config.get('default_provider') or '',
            'project_provider': (project or {}).get('provider') or '',
            'project_streaming': (project or {}).get('use_streaming_agent'),
            'legacy_global': not isinstance(config.get('provider_defaults'), dict),
            'legacy_project': bool(project) and not isinstance((project or {}).get('provider_overrides'), dict)}


@bp.route('/api/engine/preferences')
def get_preferences():
    try:
        return jsonify(preference_preview(state.CONFIG, _project(request.args.get('project_id', ''))))
    except FileNotFoundError as exc:
        return jsonify({'error': str(exc)}), 404
    except (ValueError, TypeError) as exc:
        return jsonify({'error': str(exc)}), 400
    except OSError:
        return jsonify({'error': 'Could not read project preferences'}), 500


@bp.route('/api/engine/preview', methods=['POST'])
def preview_engine():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'error': 'Expected an object'}), 400
    try:
        project = _project(data.get('project_id') or '')
        engine = engine_selection.resolve_engine(
            state.CONFIG, project,
            provider_override=data.get('provider') or '',
            model_override=data.get('model'), effort_override=data.get('effort'),
            character=data.get('character'))
        return jsonify(asdict(engine))
    except FileNotFoundError as exc:
        return jsonify({'error': str(exc)}), 404
    except (ValueError, TypeError, AttributeError) as exc:
        return jsonify({'error': str(exc)}), 400
