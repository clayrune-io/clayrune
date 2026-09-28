"""POST /api/project/<id>/brainstorm/transfer -- the ONE server route that
performs the Brainstorm handoff (mc/blueprints/guide_routes.py::brainstorm_transfer,
MC-990, docs/BRAINSTORM_HANDOFF_SPEC.md).

Covers: agent-origin callers refused, idempotency per (session, version,
destination), rollback on partial failure, no model-supplied filesystem path,
Ideas-workspace provisioning stays out of load_projects(), and the happy
path for both "create a new project" and "add to an existing project".

Determinism: real JSON-file load_project/save_project against tmp_path (the
route calls both more than once per request, so a dict-based double would
hide bugs a real reload could catch); the transcript is provided by
monkeypatching `agent_routes.resolve_transcript_messages` -- the same seam
`brainstorm_transfer` imports at call time -- so no real Claude/Codex
transcript file or CLI is needed. Provider-agnostic on purpose: nothing here
assumes Claude.
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

UI_HEADERS = {'Origin': 'http://localhost:5199'}  # a real browser fetch always carries one

MARKER = '[clayrune:exploration-ready]'


def _brief(n):
    """An assistant message ending in the terminal marker."""
    return f'# Exploration brief v{n}\n\nThe idea, the frontier, the gaps.\n\n{MARKER}'


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import server  # noqa: F401 -- registers blueprints + runs wire() on import
    from mc.blueprints import guide_routes as gr
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(gr, 'DATA_DIR', data_dir)

    def _load(pid):
        f = data_dir / f'{pid}.json'
        if not f.exists():
            return None
        return json.loads(f.read_text(encoding='utf-8'))

    def _save(pid, doc):
        (data_dir / f'{pid}.json').write_text(json.dumps(doc), encoding='utf-8')

    monkeypatch.setattr(gr, 'load_project', _load)
    monkeypatch.setattr(gr, 'save_project', _save)
    monkeypatch.setitem(gr.state.CONFIG, 'auto_workspace_base', str(tmp_path / 'auto_ws'))

    server.app.config['TESTING'] = True
    c = server.app.test_client()
    c.tmp_path = tmp_path          # type: ignore[attr-defined]
    c.data_dir = data_dir          # type: ignore[attr-defined]
    c.save = _save                 # type: ignore[attr-defined]
    c.load = _load                 # type: ignore[attr-defined]
    return c


@pytest.fixture(autouse=True)
def _fake_transcript(monkeypatch):
    """Default: one clean brief at version 1. Individual tests override via
    `_set_messages`."""
    from mc.blueprints import agent_routes as ar
    state = {'messages': [
        {'role': 'user', 'text': 'brainstorm this'},
        {'role': 'assistant', 'text': _brief(1)},
    ]}

    def _resolve(p, csid, provider='claude'):
        return {'messages': state['messages'], 'size': 1, 'message_count': len(state['messages'])}, 200

    monkeypatch.setattr(ar, 'resolve_transcript_messages', _resolve)
    return state


def _seed_source(client, pid='_ideas'):
    src_path = client.tmp_path / 'src'
    src_path.mkdir(exist_ok=True)
    client.save(pid, {'id': pid, 'name': 'Ideas', 'project_path': str(src_path),
                       '_is_ideas_workspace': True, 'backlog': []})


def _seed_dest(client, pid='existing_proj'):
    dst_path = client.tmp_path / 'dst'
    dst_path.mkdir(exist_ok=True)
    client.save(pid, {'id': pid, 'name': 'Existing', 'project_path': str(dst_path), 'backlog': []})


def _create_body(**over):
    body = {
        'claude_session_id': 'csid-abc',
        'version': 1,
        'destination': {'mode': 'create', 'id': 'newproj', 'name': 'New Proj',
                         'domain': 'general', 'description': 'a fresh idea'},
        'backlog_text': 'Try the smallest version first',
    }
    body.update(over)
    return body


def _existing_body(**over):
    body = {
        'claude_session_id': 'csid-abc',
        'version': 1,
        'destination': {'mode': 'existing', 'project_id': 'existing_proj'},
        'backlog_text': 'Try the smallest version first',
    }
    body.update(over)
    return body


# ── agent-origin refusal ─────────────────────────────────────────────────────

def test_agent_caller_refused_no_origin(client):
    _seed_source(client)
    resp = client.post('/api/project/_ideas/brainstorm/transfer', json=_create_body())
    assert resp.status_code == 403
    assert not (client.data_dir / 'newproj.json').exists()


def test_agent_caller_cannot_override_via_body_field(client):
    """A `source: 'ui'`-style self-report in the body must not flip the check
    -- the workflow_routes precedent this route reuses is deliberately blind
    to anything the caller claims about itself."""
    _seed_source(client)
    resp = client.post('/api/project/_ideas/brainstorm/transfer',
                        json=_create_body(source='ui', client='browser'))
    assert resp.status_code == 403


def test_ui_caller_with_origin_is_allowed(client):
    _seed_source(client)
    resp = client.post('/api/project/_ideas/brainstorm/transfer', json=_create_body(), headers=UI_HEADERS)
    assert resp.status_code == 201


# ── happy paths ──────────────────────────────────────────────────────────────

def test_create_new_project_writes_doc_and_one_backlog_item(client):
    _seed_source(client)
    resp = client.post('/api/project/_ideas/brainstorm/transfer', json=_create_body(), headers=UI_HEADERS)
    assert resp.status_code == 201, resp.get_json()
    data = resp.get_json()
    assert data['ok'] is True
    assert data['destination_project_id'] == 'newproj'

    dest = client.load('newproj')
    assert dest is not None
    doc = Path(dest['project_path']) / data['doc_path']
    assert doc.exists()
    text = doc.read_text(encoding='utf-8')
    assert MARKER not in text                     # marker itself is stripped
    assert 'Exploration brief' in text
    assert len(dest['backlog']) == 1
    assert dest['backlog'][0]['id'] == data['backlog_item_id']
    assert dest['backlog'][0]['text'] == 'Try the smallest version first'
    assert dest['backlog'][0]['status'] == 'open'


def test_add_to_existing_project(client):
    _seed_source(client)
    _seed_dest(client)
    resp = client.post('/api/project/_ideas/brainstorm/transfer', json=_existing_body(), headers=UI_HEADERS)
    assert resp.status_code == 201, resp.get_json()
    dest = client.load('existing_proj')
    assert len(dest['backlog']) == 1


# ── idempotency ──────────────────────────────────────────────────────────────

def test_duplicate_submission_returns_prior_result_not_a_second_item(client):
    _seed_source(client)
    r1 = client.post('/api/project/_ideas/brainstorm/transfer', json=_create_body(), headers=UI_HEADERS)
    assert r1.status_code == 201
    d1 = r1.get_json()

    r2 = client.post('/api/project/_ideas/brainstorm/transfer', json=_create_body(), headers=UI_HEADERS)
    assert r2.status_code == 200
    d2 = r2.get_json()
    assert d2['idempotent'] is True
    assert d2['backlog_item_id'] == d1['backlog_item_id']
    assert d2['destination_project_id'] == d1['destination_project_id']

    dest = client.load('newproj')
    assert len(dest['backlog']) == 1  # not two


def test_same_brief_different_destination_is_a_distinct_transfer(client):
    _seed_source(client)
    _seed_dest(client, 'other_dest')
    r1 = client.post('/api/project/_ideas/brainstorm/transfer', json=_create_body(), headers=UI_HEADERS)
    assert r1.status_code == 201
    r2 = client.post('/api/project/_ideas/brainstorm/transfer',
                      json=_existing_body(destination={'mode': 'existing', 'project_id': 'other_dest'}),
                      headers=UI_HEADERS)
    assert r2.status_code == 201
    assert r2.get_json()['idempotent'] is False


# ── rollback on partial failure ─────────────────────────────────────────────

def test_backlog_write_failure_rolls_back_the_doc(client, monkeypatch):
    _seed_source(client)
    _seed_dest(client)

    from mc.blueprints import guide_routes as gr
    real_save = gr.save_project
    calls = {'n': 0}

    def _flaky_save(pid, doc):
        calls['n'] += 1
        # Seeding (`_seed_source`/`_seed_dest`) writes through the test fixture's
        # own `client.save`, not `gr.save_project` -- so the FIRST call this
        # counter ever sees is the route's post-doc-write backlog save on the
        # destination project. Fail exactly that one.
        if calls['n'] >= 1:
            raise RuntimeError('disk full (simulated)')
        return real_save(pid, doc)

    monkeypatch.setattr(gr, 'save_project', _flaky_save)
    resp = client.post('/api/project/_ideas/brainstorm/transfer', json=_existing_body(), headers=UI_HEADERS)
    assert resp.status_code == 500
    dest = client.load('existing_proj')
    assert dest['backlog'] == []
    doc = client.tmp_path / 'dst' / 'docs' / 'brainstorm' / 'csid-abc-v1.md'
    assert not doc.exists(), 'doc must be rolled back when the backlog write fails'


def test_source_record_failure_rolls_back_the_dest_backlog_item(client, monkeypatch):
    """MC-990 D2 (review finding): the destination save (mode=='existing')
    can succeed and THEN the source save (recording `_brainstorm_transfers`,
    which guards idempotency) can fail. Before this fix the except block only
    rolled back the doc + a newly-created project record, leaving the
    already-inserted backlog item behind in the destination -- a retry then
    passed the idempotency check (the key was never written) and inserted a
    SECOND item. Fail exactly the source's own save_project call."""
    _seed_source(client)
    _seed_dest(client)

    from mc.blueprints import guide_routes as gr
    real_save = gr.save_project

    def _flaky_save(pid, doc):
        if pid == '_ideas':  # the source record write, after the dest one succeeded
            raise RuntimeError('disk full writing source record (simulated)')
        return real_save(pid, doc)

    monkeypatch.setattr(gr, 'save_project', _flaky_save)
    resp = client.post('/api/project/_ideas/brainstorm/transfer', json=_existing_body(), headers=UI_HEADERS)
    assert resp.status_code == 500
    dest = client.load('existing_proj')
    assert dest['backlog'] == [], 'dest backlog item must be rolled back, not left for a retry to duplicate'

    # A retry now succeeds cleanly -- exactly one item, not two.
    monkeypatch.setattr(gr, 'save_project', real_save)
    resp2 = client.post('/api/project/_ideas/brainstorm/transfer', json=_existing_body(), headers=UI_HEADERS)
    assert resp2.status_code == 201, resp2.get_json()
    dest2 = client.load('existing_proj')
    assert len(dest2['backlog']) == 1


def test_create_mode_failure_removes_the_new_project_record(client, monkeypatch):
    _seed_source(client)
    from mc.blueprints import guide_routes as gr
    real_save = gr.save_project
    calls = {'n': 0}

    def _flaky_save(pid, doc):
        calls['n'] += 1
        if calls['n'] >= 2:  # first call creates 'newproj'; fail after that
            raise RuntimeError('simulated failure after project creation')
        return real_save(pid, doc)

    monkeypatch.setattr(gr, 'save_project', _flaky_save)
    resp = client.post('/api/project/_ideas/brainstorm/transfer', json=_create_body(), headers=UI_HEADERS)
    assert resp.status_code == 500
    assert not (client.data_dir / 'newproj.json').exists()


# ── path safety / no model-supplied path ────────────────────────────────────

def test_reserved_ideas_id_cannot_be_produced_via_create_mode(client):
    """The create-mode sanitizer (`re.sub` then `strip('_')`) strips every
    leading underscore from a caller-supplied id, so no input can ever
    sanitize to the reserved '_ideas' workspace id -- `_RESERVED_PROJECT_IDS`
    is defense in depth here, not the reachable guard (same as it always was
    for '_incognito'). Assert the actual guarantee instead: submitting
    '_ideas' creates an ordinary project at 'ideas', never touching or
    colliding with the real Ideas workspace record (MC-990 D1)."""
    _seed_source(client)
    resp = client.post('/api/project/_ideas/brainstorm/transfer',
                        json=_create_body(destination={'mode': 'create', 'id': '_ideas', 'name': 'x'}),
                        headers=UI_HEADERS)
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()['destination_project_id'] == 'ideas'
    dest = client.load('ideas')
    assert dest is not None and not dest.get('_is_ideas_workspace')


def test_path_traversal_id_sanitized_not_honored_verbatim(client):
    """The route never treats a caller-supplied id as a literal path segment --
    `_PROJECT_ID_RE`/`re.sub` collapse any run of non [a-z0-9_-] characters
    (dots, slashes) to a single '_', so '../../etc' becomes the harmless id
    'etc', not a traversal. Assert the safety property directly: the record
    lands at DATA_DIR/etc.json (nowhere else) and the workspace folder stays
    under auto_workspace_base -- not that the request is refused, which the
    route does not promise for input that sanitizes to something valid."""
    _seed_source(client)
    resp = client.post('/api/project/_ideas/brainstorm/transfer',
                        json=_create_body(destination={'mode': 'create', 'id': '../../etc', 'name': 'x'}),
                        headers=UI_HEADERS)
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()['destination_project_id'] == 'etc'
    assert (client.data_dir / 'etc.json').exists()
    assert not (client.data_dir / '..').resolve().joinpath('etc.json').exists()
    dest = client.load('etc')
    assert str(client.tmp_path / 'auto_ws') in dest['project_path']


def test_id_that_sanitizes_to_empty_is_refused(client):
    _seed_source(client)
    resp = client.post('/api/project/_ideas/brainstorm/transfer',
                        json=_create_body(destination={'mode': 'create', 'id': '///', 'name': ''}),
                        headers=UI_HEADERS)
    assert resp.status_code == 400


def test_existing_project_id_conflict_refused(client):
    _seed_source(client)
    _seed_dest(client, 'taken')
    resp = client.post('/api/project/_ideas/brainstorm/transfer',
                        json=_create_body(destination={'mode': 'create', 'id': 'taken', 'name': 'x'}),
                        headers=UI_HEADERS)
    assert resp.status_code == 409


def test_cannot_target_the_ideas_workspace_as_existing_destination(client):
    _seed_source(client)
    resp = client.post('/api/project/_ideas/brainstorm/transfer',
                        json=_existing_body(destination={'mode': 'existing', 'project_id': '_ideas'}),
                        headers=UI_HEADERS)
    assert resp.status_code == 400


def test_no_path_field_accepted_anywhere_in_the_body(client):
    """The spec forbids a model-supplied filesystem path. A `project_path` (or
    `folder`) smuggled onto the destination must not be honored verbatim --
    the route only ever derives a path itself (auto_workspace_base) or takes
    a folder the human typed into the review form, never one attributed to
    the model's own message content. This asserts the created project's path
    lands under the test's auto_workspace_base, not an attacker-chosen path
    slipped in as 'project_path'."""
    _seed_source(client)
    evil = str(client.tmp_path / 'somewhere_else')
    resp = client.post('/api/project/_ideas/brainstorm/transfer',
                        json=_create_body(destination={'mode': 'create', 'id': 'newproj', 'name': 'x',
                                                        'project_path': evil}),
                        headers=UI_HEADERS)
    assert resp.status_code == 201
    dest = client.load('newproj')
    assert dest['project_path'] != evil
    assert str(client.tmp_path / 'auto_ws') in dest['project_path']


# ── brief version resolution ─────────────────────────────────────────────────

def test_unknown_version_refused(client):
    _seed_source(client)
    resp = client.post('/api/project/_ideas/brainstorm/transfer',
                        json=_create_body(version=7), headers=UI_HEADERS)
    assert resp.status_code == 409


def test_missing_claude_session_id_refused(client):
    _seed_source(client)
    resp = client.post('/api/project/_ideas/brainstorm/transfer',
                        json=_create_body(claude_session_id=''), headers=UI_HEADERS)
    assert resp.status_code == 400


def test_marker_stripped_from_transferred_doc(client):
    _seed_source(client)
    resp = client.post('/api/project/_ideas/brainstorm/transfer', json=_create_body(), headers=UI_HEADERS)
    assert resp.status_code == 201
    data = resp.get_json()
    dest = client.load('newproj')
    doc = Path(dest['project_path']) / data['doc_path']
    assert MARKER not in doc.read_text(encoding='utf-8')


def test_revised_brief_is_a_second_version(client, _fake_transcript):
    """A later, revised brief adds a SECOND exploration-ready marker to the
    same conversation. Requesting version=2 must resolve the revision, not
    the original -- and each is its own transfer (never overwrites)."""
    _fake_transcript['messages'] = [
        {'role': 'user', 'text': 'brainstorm this'},
        {'role': 'assistant', 'text': _brief(1)},
        {'role': 'user', 'text': 'actually, reconsider the scope'},
        {'role': 'assistant', 'text': _brief(2)},
    ]
    _seed_source(client)
    r1 = client.post('/api/project/_ideas/brainstorm/transfer', json=_create_body(version=1), headers=UI_HEADERS)
    assert r1.status_code == 201
    r2 = client.post('/api/project/_ideas/brainstorm/transfer',
                      json=_create_body(version=2, destination={'mode': 'create', 'id': 'newproj2', 'name': 'x'}),
                      headers=UI_HEADERS)
    assert r2.status_code == 201
    dest2 = client.load('newproj2')
    doc2 = Path(dest2['project_path']) / r2.get_json()['doc_path']
    assert 'v2' in doc2.read_text(encoding='utf-8')


# ── Ideas workspace provisioning does not pollute load_projects() ──────────

def test_ideas_workspace_seed_round_trips_and_is_excluded_as_a_real_project(tmp_path, monkeypatch):
    """DATA_DIR pollution guard (CLAUDE.md load-bearing rule) is about the
    FILE, not a content filter: `load_projects()` only excludes sidecars by
    filename suffix (`EXCLUDED_SIDECAR_SUFFIXES`) -- a seeded `_ideas.json` is a
    real, well-formed project record and load_projects() must parse it back
    as exactly one project, not crash and not silently drop or duplicate it.
    The "don't count as a real user project" exclusion is a separate,
    content-based check -- `_has_real_user_project()` -- verified directly
    below, same as `_is_incognito_project`/`_is_steward_workspace` already are."""
    import server  # noqa: F401
    from mc.blueprints import guide_routes as gr
    from mc.blueprints import project_routes as pr

    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(gr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(pr, 'DATA_DIR', data_dir)
    monkeypatch.setitem(gr.state.CONFIG, 'auto_workspace_base', str(tmp_path / 'auto_ws'))

    def _load(pid):
        f = data_dir / f'{pid}.json'
        return json.loads(f.read_text(encoding='utf-8')) if f.exists() else None

    def _save(pid, doc):
        (data_dir / f'{pid}.json').write_text(json.dumps(doc), encoding='utf-8')

    monkeypatch.setattr(gr, 'load_project', _load)
    monkeypatch.setattr(gr, 'save_project', _save)

    assert gr._has_real_user_project() is False  # nothing seeded yet
    assert gr._seed_ideas_workspace() is True
    assert gr._seed_ideas_workspace() is False  # second call is a no-op

    projects = pr.load_projects()
    assert [p.get('id') for p in projects] == ['_ideas']
    assert projects[0]['_is_ideas_workspace'] is True

    assert gr._has_real_user_project() is False, \
        'the Ideas workspace alone must not read as user-project evidence'


def test_seed_refuses_when_reserved_id_holds_something_else(tmp_path, monkeypatch):
    """MC-990 D1 (review finding): a plain 'ideas' reserved id collided with
    any install where the user already had a real project named "Ideas" --
    the seed saw the file, returned False, and Claydo's Brainstorm sessions
    then landed in the user's REAL project. The id is now '_ideas' (no
    slugifier in the repo can ever produce a leading underscore from a typed
    name), but this is the remaining defense in depth: if a record still
    turns up at the reserved id without the workspace marker, the seed must
    refuse loudly, not silently adopt it."""
    import server  # noqa: F401
    from mc.blueprints import guide_routes as gr

    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(gr, 'DATA_DIR', data_dir)
    monkeypatch.setitem(gr.state.CONFIG, 'auto_workspace_base', str(tmp_path / 'auto_ws'))

    foreign = {'id': '_ideas', 'name': 'Something Else', 'backlog': []}
    (data_dir / '_ideas.json').write_text(json.dumps(foreign), encoding='utf-8')

    assert gr._seed_ideas_workspace() is False
    on_disk = json.loads((data_dir / '_ideas.json').read_text(encoding='utf-8'))
    assert on_disk == foreign, 'the foreign record must be left untouched, not silently claimed'
