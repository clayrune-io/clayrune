"""GitHub setup selection: run-type scripts cannot reach Save, required build cannot be skipped."""
import json
from pathlib import Path

import pytest
from flask import Flask

from mc.desk_connect import github_connection as github, github_install_steps as steps
from mc.desk_connect import github_activation as activation, custom_connection_service as service
from mc.desk_connect.mcp_errors import ActivationError
from mc.blueprints import desk_connect_custom_routes as routes
from tests.test_desk_connect_github import staged, prepare, URL  # noqa: F401


def test_script_filter_and_lifecycle_order():
    declared = {'start':'node dist/index.js', 'compile:z':'z', 'build:z':'z', 'test':'test',
                'prepare':'prepare', 'postinstall':'post', 'compile':'compile', 'dev':'nodemon',
                'prepublishOnly':'publish', 'build':'tsc', 'install':'install', 'preinstall':'pre',
                'build:a':'a', 'compile:a':'a', 'buildish':'no'}
    got = steps.declared_scripts(declared)
    assert [s['script'] for s in got] == ['preinstall','install','postinstall','prepare',
                                        'build','build:a','build:z','compile','compile:a','compile:z']
    assert all(not s['argv'] for s in got)


@pytest.mark.parametrize('bad', [None, [], {'build':None}, {'build':'x'*10001}])
def test_malformed_install_scripts_refused(bad):
    with pytest.raises(ActivationError):
        steps.declared_scripts(bad)


def youtube_stage(staged, monkeypatch):
    rec = github._stages[staged[0]['stage_id']]
    metadata = {'bin':{'youtube':'dist/cli.js'}, 'scripts':{'start':'node ./dist/index.js',
                'build':'tsc','dev':'nodemon','prepublishOnly':'npm run build','test':'jest'}}
    (Path(rec['install_dir'])/'package.json').write_text(json.dumps(metadata))
    monkeypatch.setattr(github.installer, 'stage_clone', lambda *a,**k: {key:rec[key] for key in ('sha','install_dir')})
    monkeypatch.setattr(steps, 'dependencies', lambda path: [{'id':'dependencies:0','argv':['npm','ci','--ignore-scripts']}])
    return github.stage({'url':URL})


def test_stage_card_and_operation_only_offer_dependency_then_build(staged, monkeypatch):
    answer = youtube_stage(staged, monkeypatch)
    assert answer['command'] == 'node' and answer['args'] == ['dist/cli.js']
    assert answer['build_required'] and answer['required_steps'] == ['script:build']
    card = prepare(answer, args=answer['args'])
    assert [s['id'] for s in card['scripts']] == ['dependencies:0','script:build']
    assert not any(s['approved'] for s in card['scripts'])
    assert card['scripts'][1]['required']
    with pytest.raises(ActivationError, match='required build'):
        service.check_submission(card['request_id'], card['fingerprint'])
    approved = prepare(answer, approve_scripts=['script:build','dependencies:0'])
    op = service.check_submission(approved['request_id'], approved['fingerprint'])['op']
    assert [s['id'] for s in op['install_steps']] == ['dependencies:0','script:build']
    assert op['required_steps'] == ['script:build']
    assert card['fingerprint'] != approved['fingerprint']
    for name in ['start','dev','test','prepublishOnly']:
        with pytest.raises(ActivationError, match='install step changed'):
            prepare(answer, approve_scripts=[f'script:{name}'])


def test_missing_required_build_refused_before_passcode(staged, monkeypatch):
    card = prepare(youtube_stage(staged, monkeypatch))
    app = Flask(__name__); app.register_blueprint(routes.bp)
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda:False)
    monkeypatch.setattr(routes, '_require_human_passcode', lambda *a:pytest.fail('must refuse before asking passcode'))
    monkeypatch.setattr(service, 'commit', lambda *a:pytest.fail('must not save'))
    response = app.test_client().post('/api/desk/connect/custom/commit',
                                    json={'request_id':card['request_id'],'fingerprint':card['fingerprint']})
    assert response.status_code == 400
    assert response.json == {'error':'Select the required build step in Review before saving.',
                             'code':'required_install_step'}


@pytest.mark.parametrize('step', [{'id':'script:build','script':'build','body':'tsc','argv':[]},
                                 {'id':'dependencies:0','argv':['npm','ci','--ignore-scripts']}])
def test_failed_step_names_plain_reason_without_package_pin(staged, monkeypatch, step):
    card = prepare(staged[0])
    op = service.check_submission(card['request_id'],card['fingerprint'])['op']
    op['install_steps'] = [step]
    def fail(*a, **k):
        raise ActivationError(f'{URL}@{op["version"]} raw error NOT saved', 'script_failed', 422)
    monkeypatch.setattr(activation.scripts, 'run', fail)
    monkeypatch.setattr(activation.scripts, 'spawn', lambda *a: (1,b''))
    monkeypatch.setattr(activation.mcp, '_write_project_servers', lambda *a:pytest.fail('failure must not register'))
    out = activation.provision(op, '/project')
    label = '"build" step' if step['id'] == 'script:build' else 'dependency setup'
    assert out['state'] == 'setup_failed'
    assert out['message'] == f'The {label} failed; the connection was not saved.'
    assert URL not in out['message'] and op['version'] not in out['message']
