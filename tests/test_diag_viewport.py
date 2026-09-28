"""POST /api/diag/viewport (mc/blueprints/push_mobile.py, MC-988 part 3).

A raw, unauthenticated beacon from static/js/mobile.js's stuck-pane
watchdog — no session/project context, fired from a WebView that may itself
be malfunctioning. Guards the two things that make it safe to leave always-on:
the per-request body cap and the on-disk rolling cap, plus the
DATA_DIR-pollution rule (CLAUDE.md) that the file must live outside
data/projects/.
"""
import json
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import push_mobile  # noqa: E402


@pytest.fixture
def client(tmp_path):
    app = Flask(__name__)
    app.config['TESTING'] = True
    push_mobile.wire(
        data_root=tmp_path,
        load_project_fn=lambda _pid: None,
        cf_session_nonce_fn=lambda: '',
        get_remote_provider_fn=lambda: None,
    )
    app.register_blueprint(push_mobile.bp)
    return app.test_client()


def _snapshot(**overrides):
    body = {
        'ts': 1234567890000,
        'events': [{'ts': 1234567889000, 'source': 'apply', 'appVh': 400}],
        'current': {'innerHeight': 883, 'appVh': 400},
        'modalHeight': 400,
        'devicePixelRatio': 2.75,
        'ua': 'Mozilla/5.0 (Linux; Android 14) Clayrune',
        'capacitor': True,
    }
    body.update(overrides)
    return body


def test_post_writes_one_line_outside_data_projects(client, tmp_path):
    r = client.post('/api/diag/viewport', json=_snapshot())
    assert r.status_code == 200
    assert r.get_json()['ok'] is True

    diag_path = tmp_path / 'data' / 'diag' / 'viewport.jsonl'
    assert diag_path.exists()
    # Sibling to data/projects/, never inside it (CLAUDE.md DATA_DIR rule).
    assert 'projects' not in str(diag_path.relative_to(tmp_path / 'data'))

    lines = diag_path.read_text(encoding='utf-8').strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row['capacitor'] is True
    assert row['modalHeight'] == 400
    assert 'received_at' in row


def test_multiple_posts_append(client, tmp_path):
    client.post('/api/diag/viewport', json=_snapshot())
    client.post('/api/diag/viewport', json=_snapshot(modalHeight=200))
    diag_path = tmp_path / 'data' / 'diag' / 'viewport.jsonl'
    lines = diag_path.read_text(encoding='utf-8').strip().splitlines()
    assert len(lines) == 2


def test_oversized_body_rejected_413(client):
    huge = _snapshot(events=[{'pad': 'x' * 200}] * 400)
    assert len(json.dumps(huge)) > push_mobile._VIEWPORT_DIAG_MAX_BODY
    r = client.post('/api/diag/viewport', json=huge)
    assert r.status_code == 413
    assert r.get_json()['ok'] is False


def test_non_dict_json_body_is_a_clean_400(client):
    # A bare JSON array or scalar (malformed client) can't be merged into a
    # row — reject cleanly, never a 500 from unpacking assumptions downstream.
    r = client.post('/api/diag/viewport', data='[1,2,3]',
                    content_type='application/json')
    assert r.status_code == 400
    assert r.get_json()['ok'] is False


def test_missing_or_garbled_body_is_accepted_as_empty_snapshot(client):
    # sendBeacon is fire-and-forget and this is a best-effort diagnostic
    # beacon: an empty body or unparseable text must never 500 — it's
    # accepted as an empty snapshot rather than treated as an error.
    r = client.post('/api/diag/viewport', data=b'',
                    content_type='application/json')
    assert r.status_code == 200

    r2 = client.post('/api/diag/viewport', data='not json at all',
                     content_type='application/json')
    assert r2.status_code == 200


def test_file_is_size_capped(client, tmp_path):
    # Force many appends past the file cap and confirm it stays bounded and
    # keeps the NEWEST rows (oldest dropped first).
    orig_cap = push_mobile._VIEWPORT_DIAG_MAX_FILE
    push_mobile._VIEWPORT_DIAG_MAX_FILE = 2000
    try:
        for i in range(200):
            client.post('/api/diag/viewport', json=_snapshot(modalHeight=i))
        diag_path = tmp_path / 'data' / 'diag' / 'viewport.jsonl'
        size = diag_path.stat().st_size
        assert size <= push_mobile._VIEWPORT_DIAG_MAX_FILE + 2048  # one line of slack
        lines = diag_path.read_text(encoding='utf-8').strip().splitlines()
        last_row = json.loads(lines[-1])
        assert last_row['modalHeight'] == 199  # newest survives
    finally:
        push_mobile._VIEWPORT_DIAG_MAX_FILE = orig_cap
