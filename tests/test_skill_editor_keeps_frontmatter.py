"""MC-1179 SK4: saving an existing skill from the editor must not drop its other frontmatter.

The editor's PUT carries only description and body. write_skill builds the
frontmatter block from name, description and extra_meta, so a promoted skill's
provenance keys and an imported skill's allowed-tools/license were erased on
every Save. The PUT route now asks write_skill to keep what the file has.
Real Flask routes over a temp skills store; nothing under ~/.claude is touched.
"""

from flask import Flask
import pytest

from mc import skills, skill_import_guard, unattended
from mc.blueprints import local_auth, skills_routes

PASSCODE = 'skills-test-1234'
ORIGIN = {'Origin': 'http://localhost:5199'}
KEPT = {
    'provenance': 'distiller',
    'promoted_from': 'exploration-abc123',
    'source_session': 'deadbeef1234',
    'extraction_fingerprint_exact': 'ff00ff00ff00',
    'allowed-tools': 'Read, Grep',
    'license': 'MIT',
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    for attr in ('GLOBAL_SKILLS_DIR', 'ARCHIVE_SKILLS_DIR', 'STAGING_SKILLS_DIR',
                 'GLOBAL_COMMANDS_DIR', 'GLOBAL_AGENTS_DIR'):
        monkeypatch.setattr(skills, attr, tmp_path / attr)
    monkeypatch.setattr(skill_import_guard, 'QUARANTINE_DIR', tmp_path / 'quarantine')
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    monkeypatch.setattr(local_auth, '_LOCAL_AUTH_FAILS', {})
    monkeypatch.setattr(unattended, 'agent_sessions', {})
    local_auth._local_auth_set_passcode(PASSCODE)
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(skills_routes.bp)
    return app.test_client()


def _write_with_meta(name='promoted', description='Original', body='Verify the result.'):
    skills.write_skill(name=name, description=description, body=body, scope='global',
                       extra_meta=dict(KEPT), overwrite=False)


def _put(client, name, **fields):
    return client.put(f'/api/skills/global/{name}', json={**fields, 'passcode': PASSCODE}, headers=ORIGIN)


def test_editor_save_keeps_every_other_frontmatter_key(client):
    _write_with_meta()
    r = _put(client, 'promoted', description='Original, typo fixed', body='Verify the result twice.')
    assert r.status_code == 200, r.json
    rec = skills.read_skill('global', 'promoted')
    assert rec['description'] == 'Original, typo fixed'
    assert rec['body'] == 'Verify the result twice.'
    assert {k: rec['frontmatter'].get(k) for k in KEPT} == KEPT


def test_name_and_description_in_the_stored_file_are_not_overridden_by_stale_keys(client):
    _write_with_meta()
    _put(client, 'promoted', description='New description', body='Body.')
    fm = skills.read_skill('global', 'promoted')['frontmatter']
    assert fm['name'] == 'promoted'
    assert fm['description'] == 'New description'


def test_a_key_the_file_never_had_is_not_added(client):
    _write_with_meta()
    r = client.put('/api/skills/global/promoted', headers=ORIGIN, json={
        'description': 'D', 'body': 'B', 'passcode': PASSCODE, 'frontmatter': {'allowed-tools': 'Bash'},
        'extra_meta': {'allowed-tools': 'Bash'}})
    assert r.status_code == 200, r.json
    assert skills.read_skill('global', 'promoted')['frontmatter']['allowed-tools'] == 'Read, Grep'


def test_write_skill_default_still_rewrites_from_name_and_description(client):
    # Other overwrite callers (import --force, promote) keep the old contract.
    _write_with_meta()
    skills.write_skill(name='promoted', description='D', body='B', scope='global', overwrite=True)
    assert set(skills.read_skill('global', 'promoted')['frontmatter']) == {'name', 'description'}


def test_extra_meta_wins_over_kept_keys(client):
    _write_with_meta()
    skills.write_skill(name='promoted', description='D', body='B', scope='global', overwrite=True,
                       keep_frontmatter=True, extra_meta={'license': 'Apache-2.0'})
    fm = skills.read_skill('global', 'promoted')['frontmatter']
    assert fm['license'] == 'Apache-2.0'
    assert fm['provenance'] == 'distiller'
