"""POST /api/project/<id>/topics/<topic>/state (mc/blueprints/topics_routes.py).

MC-1179 pass 3, ML36. The route built `<id>_topic_state.json` straight from
the URL without ever loading the project, so a mistyped id created a state
file for a project that does not exist. Its two file helpers swallowed every
error with no log, and a failed write still answered `{"ok": true}`.
"""
import json
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import topics_routes as tr  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    logs = []
    monkeypatch.setattr(tr, '_DATA_DIR', tmp_path)
    monkeypatch.setattr(tr, '_load_project',
                        lambda pid: {'id': 'p1'} if pid == 'p1' else None)
    monkeypatch.setattr(tr, '_log', lambda msg, *a, **k: logs.append(str(msg)))
    app = Flask(__name__)
    app.register_blueprint(tr.bp)
    return app.test_client(), tmp_path, logs


def _post(client, pid, topic, state):
    return client.post(f'/api/project/{pid}/topics/{topic}/state',
                       json={'state': state})


def test_a_known_project_still_saves(env):
    client, root, logs = env
    assert _post(client, 'p1', 'alpha', 'done').get_json() == {'ok': True, 'state': 'done'}
    saved = json.loads((root / 'p1_topic_state.json').read_text(encoding='utf-8'))
    assert saved['alpha']['state'] == 'done'
    assert _post(client, 'p1', 'alpha', 'open').status_code == 200
    assert json.loads((root / 'p1_topic_state.json').read_text(encoding='utf-8')) == {}
    assert logs == []      # a state file that does not exist yet is not an error


def test_an_unknown_project_is_404_and_writes_nothing(env):
    client, root, _ = env
    r = _post(client, 'no-such-project', 'alpha', 'done')
    assert r.status_code == 404
    assert list(root.iterdir()) == []


def test_a_failed_write_is_a_500_and_is_logged(env, monkeypatch):
    client, root, logs = env

    def refuse(*a, **k):
        raise PermissionError('simulated: disk says no')

    monkeypatch.setattr(tr, 'write_json_atomic', refuse)
    r = _post(client, 'p1', 'alpha', 'done')
    assert r.status_code == 500
    assert r.get_json().get('ok') is not True
    assert any('simulated: disk says no' in l and 'p1_topic_state.json' in l for l in logs)


def test_an_unreadable_state_file_is_logged(env):
    client, root, logs = env
    (root / 'p1_topic_state.json').write_text('{ not json', encoding='utf-8')
    assert _post(client, 'p1', 'alpha', 'done').status_code == 200
    assert any('p1_topic_state.json' in l for l in logs)
