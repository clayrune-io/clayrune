"""Sign in with a saved login, slice P2b of docs/DESK_SERVICE_PROFILES_SPEC.md
(`mc/desk_connect/signin_fill.py`, `signin_login_store.py`, `desk_connect_signin_routes.py`).

Pinned:

  * no username or password appears in any response, log line or draft fingerprint; the probe that
    reads the page carries neither; the typed values reach only the one fill evaluation;
  * the fill is refused (nothing typed, the vault not asked for the password) when the pane's top
    page is not exactly the route's declared sign-in host, when it moves between the probe and the
    typing, and for an unattended caller; a page that moves on AFTER the sign-in types nothing more;
  * a CAPTCHA or second-factor page hands the pane to the person: nothing typed, password not fetched;
  * the password is typed once per run, and only on a page that shows a password field;
  * a locked vault reports locked (423) and nothing falls back to asking for the value;
  * a new login is written only by the passcode-gated Save, as one login entry (username and password
    together), and is removed again if the binding write that follows it fails;
  * a route that declares no sign-in page is never filled; a project-scoped login is not usable from
    elsewhere; the declared sign-in hosts must be the service's own.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

PASSCODE = 'dash-passcode-1'
USER = 'ron.levy@example.test'
PW = 'S3cretPW-ZZTOP-98765'


class ScriptPane:
    """Stands in for the browser pane: answers each page evaluation from a script and keeps every
    expression it was given, so a test can see exactly what reached the page."""

    def __init__(self, probes, fills=None, running=True):
        self.probes = list(probes)
        self.fills = list(fills or [])
        self.running = running
        self.exprs: list = []

    def find(self, profile):
        return {'status': 'running', 'profile': profile} if self.running else None

    def evaluate(self, session, expression):
        self.exprs.append(expression)
        cfg = json.loads(expression.strip()[expression.strip().rindex('})(') + 3:-1])
        queue = self.fills if cfg['fill'] else self.probes
        return True, (queue.pop(0) if queue else {'state': 'other'})

    def typed(self):
        return [e for e in self.exprs if PW in e or USER in e]


def _p(state, **kw):
    return {'state': state, **kw}


FORM = _p('login_form', has_password=True, has_username=True)
USERNAME_PAGE = _p('username_step', has_password=False, has_username=True)
FILLED = _p('filled', filled=['username', 'password'], submitted=True)
FILLED_USER = _p('filled', filled=['username'], submitted=True)
MISMATCH = _p('origin_mismatch', host='evil.example')


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    from mc import desk as _desk
    from mc import secrets_store
    from mc.blueprints import desk_connect_purpose_routes as purpose_routes
    from mc.blueprints import desk_connect_signin_routes as routes
    from mc.blueprints import local_auth
    from mc.desk_connect import purpose_bindings, purpose_verification
    from mc.desk_connect import signin_fill
    from mc.state import agent_sessions
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    local_auth._LOCAL_AUTH_FAILS.clear()
    secrets_store._dispensed.clear()
    secrets_store._unlocked_key = None
    secrets_store._lock_notified = False
    secrets_store._key_mismatch = False
    monkeypatch.setattr(_desk, 'STORE_PATH', tmp_path / 'desk.json')
    agent_sessions.clear()
    purpose_bindings._forget_all_for_tests()
    purpose_verification._forget_all_for_tests()
    local_auth._local_auth_set_passcode(PASSCODE)
    logs: list = []
    for mod in (signin_fill, routes):
        monkeypatch.setattr(mod, '_log', lambda msg, *a, **k: logs.append(str(msg)))
    monkeypatch.setattr('mc.desk_connect.signin_login_store._log', lambda msg, *a, **k: logs.append(str(msg)))
    monkeypatch.setattr('mc.desk_connect.purpose_bindings._log', lambda msg, *a, **k: logs.append(str(msg)))
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(routes.bp)
    app.register_blueprint(purpose_routes.bp)
    yield app.test_client(), logs, tmp_path
    agent_sessions.clear()
    purpose_bindings._forget_all_for_tests()
    purpose_verification._forget_all_for_tests()


def _login(name='linkedin.login', **kw):
    from mc import secrets_store
    secrets_store.set_secret(name, PW, username=USER, entry_type=secrets_store.ENTRY_LOGIN, **kw)
    return name


def _fill(pane, login='linkedin.login', service='linkedin', route='linkedin-browser', **kw):
    from mc.desk_connect import signin_fill
    return signin_fill.fill(service, route, login, 'li-ron', pane=pane, sleep=lambda s: None, **kw)


def _no_leak(*blobs):
    for b in blobs:
        text = b if isinstance(b, str) else json.dumps(b)
        assert PW not in text and USER not in text, text[:200]


def _refused(pane, code, **kw):
    from mc.desk_connect import signin_fill
    with pytest.raises(signin_fill.FillError) as e:
        _fill(pane, **kw)
    assert e.value.code == code, (e.value.code, str(e.value))
    return e.value


# -- no value anywhere ---------------------------------------------------------------------

def test_a_fill_returns_a_state_word_and_no_value_reaches_a_response_or_log(env):
    client, logs, _ = env
    _login()
    from mc.desk_connect import signin_fill
    pane = ScriptPane([FORM], [FILLED])
    out = _fill(pane)
    assert out['state'] == 'submitted'
    _no_leak(out, logs)
    assert len(pane.exprs) == 3 and len(pane.typed()) == 1          # probe, the one fill, a look at the result: only the fill carries the login
    probe = json.loads(pane.exprs[0].strip()[pane.exprs[0].strip().rindex('})(') + 3:-1])
    assert probe['fill'] is False and 'user' not in probe and 'password' not in probe
    # and through the route, including the failure path
    from mc.blueprints import desk_connect_signin_routes as routes
    monkey_pane = ScriptPane([FORM], [FILLED])
    signin_fill_pane = monkey_pane
    orig = signin_fill.Pane
    signin_fill.Pane = lambda: signin_fill_pane
    try:
        r = client.post('/api/desk/connect/signin/fill', json={'service': 'linkedin', 'route_id': 'linkedin-browser',
                                                                'login': 'linkedin.login', 'profile': 'li-ron'})
    finally:
        signin_fill.Pane = orig
    assert r.status_code == 200 and r.get_json()['state'] == 'submitted'
    _no_leak(r.get_data(as_text=True), logs)
    assert routes is not None


def test_a_result_that_holds_a_typed_value_is_withheld(env):
    from mc.desk_connect import signin_fill
    out = signin_fill._clean({'ok': True, 'state': 'submitted', 'message': f'typed {PW}'}, (USER, PW))
    assert out['ok'] is False and out['code'] == 'internal' and PW not in json.dumps(out)
    assert signin_fill._clean({'ok': True, 'state': 'submitted'}, (USER, PW))['state'] == 'submitted'


def test_the_error_of_a_failed_pane_call_carries_no_value(env):
    client, logs, _ = env
    _login()

    class Broken(ScriptPane):
        def evaluate(self, session, expression):
            self.exprs.append(expression)
            return False, f'eval_exception:{PW}'
    pane = Broken([])
    err = _refused(pane, 'pane_error')
    _no_leak(str(err), logs)


# -- the origin ------------------------------------------------------------------------------

def test_a_page_that_is_not_the_declared_sign_in_host_is_refused_and_the_vault_is_not_asked(env, monkeypatch):
    from mc import secrets_store
    _login()
    asked = []
    real = secrets_store.get_secret_value
    monkeypatch.setattr(secrets_store, 'get_secret_value', lambda *a, **k: asked.append(a) or real(*a, **k))
    pane = ScriptPane([MISMATCH])
    err = _refused(pane, 'origin_mismatch')
    assert err.status == 409 and asked == [] and pane.typed() == []


def test_a_redirect_between_the_probe_and_the_typing_types_nothing(env):
    _login()
    pane = ScriptPane([FORM], [MISMATCH])                 # the page matched at the probe, then moved before the typing ran
    _refused(pane, 'origin_mismatch')
    assert len(pane.exprs) == 2
    # the typing expression itself re-checks the origin inside the same evaluation (nothing can move between check and type)
    from mc.desk_connect import signin_fill_js
    script = signin_fill_js.build(['https://www.linkedin.com'], fill=True, user='u', password='p')
    assert script.index("C.origins.indexOf(location.origin)") < script.index('put(pw, C.password)')
    assert "window.top !== window" in script


def test_a_page_that_moves_on_after_the_sign_in_is_not_typed_into_again(env):
    _login()
    pane = ScriptPane([USERNAME_PAGE, MISMATCH], [FILLED_USER])        # username typed, the site redirects to its own SSO host
    out = _fill(pane)
    assert out['state'] == 'submitted' and out.get('moved_on') is True
    assert len(pane.typed()) == 1                                       # the password was never fetched or sent


def test_the_origin_must_be_exact_https_never_a_suffix_or_http(env):
    from mc.desk_connect import signin_fill
    signin = {'hosts': ['www.linkedin.com']}
    assert signin_fill.origins_of(signin) == ['https://www.linkedin.com']


# -- human start -----------------------------------------------------------------------------

def test_an_unattended_caller_is_refused_before_the_pane_or_the_vault(env, monkeypatch):
    from mc import secrets_store
    from mc.blueprints import desk_connect_signin_routes as routes
    from mc.desk_connect import signin_fill
    client, _, _ = env
    _login()
    touched = []
    monkeypatch.setattr(signin_fill.Pane, 'find', lambda self, p: touched.append('pane'))
    monkeypatch.setattr(secrets_store, 'get_secret_value', lambda *a, **k: touched.append('vault'))
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: True)
    for path, body in (('/api/desk/connect/signin/fill', {'service': 'linkedin', 'route_id': 'linkedin-browser', 'login': 'linkedin.login'}),
                       ('/api/desk/connect/signin/store-login', {'service': 'linkedin', 'route_id': 'linkedin-browser', 'passcode': PASSCODE,
                                                                  'new_login': {'name': 'x.login', 'username': USER, 'value': PW}})):
        r = client.post(path, json=body)
        assert r.status_code == 403 and r.get_json()['code'] == 'human_required'
    assert touched == [] and 'x.login' not in {s['name'] for s in secrets_store.list_secrets()}
    _refused(ScriptPane([FORM]), 'human_required', unattended=True)


# -- 2FA and CAPTCHA -------------------------------------------------------------------------

@pytest.mark.parametrize('state', ['captcha', 'two_factor'])
def test_a_captcha_or_second_factor_hands_the_pane_to_the_person(env, monkeypatch, state):
    from mc import secrets_store
    _login()
    asked = []
    real = secrets_store.get_secret_value
    monkeypatch.setattr(secrets_store, 'get_secret_value', lambda *a, **k: asked.append(a) or real(*a, **k))
    pane = ScriptPane([_p(state)])
    out = _fill(pane)
    assert out['state'] == 'handoff' and out['reason'] == state
    assert asked == [] and pane.typed() == []


def test_a_challenge_that_appears_after_the_username_stops_the_run(env):
    _login()
    pane = ScriptPane([USERNAME_PAGE, _p('captcha')], [FILLED_USER])
    out = _fill(pane)
    assert out['state'] == 'handoff' and out['reason'] == 'captcha'
    assert len(pane.typed()) == 1


# -- once, and only where a password field shows ---------------------------------------------

def test_the_password_is_typed_once_and_not_retried_when_the_page_stays_on_sign_in(env):
    _login()
    pane = ScriptPane([FORM, FORM], [FILLED])                          # wrong password: the form is still there afterwards
    out = _fill(pane)
    assert out['state'] == 'submitted' and out['still_on_sign_in'] is True
    assert len(pane.typed()) == 1


def test_a_two_page_sign_in_types_the_username_then_the_password(env):
    _login()
    pane = ScriptPane([USERNAME_PAGE, FORM, _p('other')], [FILLED_USER, FILLED])
    out = _fill(pane)
    assert out['state'] == 'submitted'
    typed = pane.typed()
    assert len(typed) == 2 and PW not in typed[0] and PW in typed[1]  # the password is not even fetched for the username page


def test_a_page_with_no_form_types_nothing(env):
    _login()
    pane = ScriptPane([_p('other')])
    out = _fill(pane)
    assert out['state'] == 'no_form' and pane.typed() == []


# -- the vault -------------------------------------------------------------------------------

def test_a_locked_vault_reports_locked_and_nothing_is_typed(env, monkeypatch):
    from mc import secrets_store
    client, _, _ = env
    _login()
    secrets_store.set_passphrase('correct horse battery staple')
    secrets_store.lock_now()
    assert secrets_store.is_locked()
    pane = ScriptPane([FORM], [FILLED])
    err = _refused(pane, 'vault_locked')
    assert err.status == 423 and pane.exprs == []
    from mc.desk_connect import signin_fill
    monkeypatch.setattr(signin_fill.Pane, 'find', lambda self, p: pane.find(p))
    r = client.post('/api/desk/connect/signin/fill', json={'service': 'linkedin', 'route_id': 'linkedin-browser',
                                                            'login': 'linkedin.login', 'profile': 'li-ron'})
    assert r.status_code == 423 and r.get_json()['code'] == 'vault_locked'
    _no_leak(r.get_data(as_text=True))


def test_a_project_scoped_login_is_not_usable_from_another_project(env):
    _login(scope='some-project')
    pane = ScriptPane([FORM], [FILLED])
    err = _refused(pane, 'login_denied')
    assert pane.typed() == []
    assert 'some-project' in str(err)


@pytest.mark.parametrize('make,code', [
    (lambda: None, 'login_not_found'),
    (lambda: __import__('mc.secrets_store', fromlist=['x']).set_secret('linkedin.login', PW, entry_type='api_key'), 'not_a_login'),
    (lambda: __import__('mc.secrets_store', fromlist=['x']).set_secret('linkedin.login', PW, entry_type='login'), 'no_username'),
])
def test_only_a_stored_login_with_a_username_is_used(env, make, code):
    make()
    pane = ScriptPane([FORM])
    _refused(pane, code)
    assert pane.exprs == []


def test_a_clayrune_internal_sign_in_is_never_typed_anywhere(env):
    pane = ScriptPane([FORM])
    _refused(pane, 'bad_login', login='oauth.x.test')
    assert pane.exprs == []


def test_a_route_that_declares_no_sign_in_page_is_never_filled(env):
    _login()
    pane = ScriptPane([FORM])
    _refused(pane, 'no_signin_declared', route='linkedin-manual')
    _refused(pane, 'unknown_route', route='nope')
    _refused(pane, 'unknown_service', service='nope')
    assert pane.exprs == []


def test_no_pane_open_is_reported_not_guessed(env):
    _login()
    pane = ScriptPane([FORM], running=False)
    _refused(pane, 'no_pane')
    from mc.desk_connect import signin_fill
    with pytest.raises(signin_fill.FillError) as e:
        signin_fill.fill('linkedin', 'linkedin-browser', 'linkedin.login', '', pane=pane)
    assert e.value.code == 'no_pane'


# -- the declared pages ----------------------------------------------------------------------

def test_the_declared_sign_in_pages_are_exact_hosts_under_the_services_own(env):
    from mc.desk_connect import registry
    seen = {}
    for sid in ('linkedin', 'x', 'higgsfield'):
        prof = registry.profile(sid)
        for r in prof['routes']:
            if r.get('signin'):
                seen[(sid, r['id'])] = r['signin']
                for h in r['signin']['hosts']:
                    assert '*' not in h and '/' not in h and ':' not in h
                    assert any(h == own or h.endswith('.' + own) for own in prof['hosts'])
                if r['signin']['url']:
                    assert r['signin']['url'].startswith('https://' + r['signin']['hosts'][0])
    assert set(seen) == {('linkedin', 'linkedin-browser'), ('x', 'x-browser'), ('x', 'x-oauth'), ('higgsfield', 'higgsfield-oauth')}


def _raw_profile(service):
    return json.loads((REPO / 'mc' / 'desk_connect' / 'profiles' / f'{service}.json').read_text(encoding='utf-8'))


def test_a_profile_may_not_declare_a_sign_in_host_that_is_not_its_own():
    import copy
    from mc.desk_connect import profile_schema
    raw = _raw_profile('linkedin')
    profile_schema.check_profile(copy.deepcopy(raw))                       # the shipped file is valid
    bad = copy.deepcopy(raw)
    next(r for r in bad['routes'] if r['id'] == 'linkedin-browser')['signin'] = {'url': None, 'hosts': ['evil.example']}
    with pytest.raises(profile_schema.ProfileError, match='sign-in host'):
        profile_schema.check_profile(bad)
    bad = copy.deepcopy(raw)
    next(r for r in bad['routes'] if r['id'] == 'linkedin-browser')['signin'] = {'url': 'https://evil.example/login', 'hosts': ['www.linkedin.com']}
    with pytest.raises(profile_schema.ProfileError, match='url'):
        profile_schema.check_profile(bad)
    bad = copy.deepcopy(raw)
    next(r for r in bad['routes'] if r['id'] == 'linkedin-mcp-candidate')['signin'] = {'url': None, 'hosts': ['www.linkedin.com']}
    with pytest.raises(profile_schema.ProfileError, match='only a route that signs in'):
        profile_schema.check_profile(bad)


# -- storing a new login: nothing before the passcode-gated Save -----------------------------

def _vault_names():
    from mc import secrets_store
    return {s['name'] for s in secrets_store.list_secrets()}


def _new_login(**kw):
    d = {'name': 'linkedin.login', 'username': USER, 'value': PW, 'description': 'launch posts', 'allow_unattended': True}
    d.update(kw)
    return d


def _bind(client, account_id, new_login, passcode=PASSCODE, rid='req-signin-0001', **extra):
    from mc.desk_connect import registry
    b = {'purpose': 'read_own', 'route_id': 'linkedin-browser', 'capabilities': ['own_posts'], 'browser_profile': 'li-ron'}
    if new_login is not None:
        b['new_login'] = new_login
    b.update(extra)
    draft = {'service': 'linkedin', 'revision': registry.profile('linkedin')['revision'], 'account': {'id': account_id},
             'account_kind': 'member', 'bindings': [b]}
    return client.post('/api/desk/connect/purpose/commit', json={'request_id': rid, 'draft': draft, 'passcode': passcode})


def _acc():
    from mc import desk_accounts
    return desk_accounts.create_account('linkedin', 'Ron Levy')


def test_nothing_is_stored_by_a_wrong_passcode_or_a_bad_draft(env):
    client, logs, _ = env
    acc = _acc()
    for kw in ({'passcode': 'wrong'},):
        r = _bind(client, acc['id'], _new_login(), **kw)
        assert r.status_code in (401, 403), r.get_json()
    for bad in (_new_login(username=''), _new_login(value=''), _new_login(name='Bad Name'), _new_login(name='oauth.x.test'),
                _new_login(extra='x')):
        r = _bind(client, acc['id'], bad)
        assert r.status_code == 400, (bad, r.get_json())
    assert _vault_names() == set()
    _no_leak(logs)


def test_the_save_stores_one_login_entry_and_binds_it_by_name(env):
    client, logs, _ = env
    from mc import secrets_store
    acc = _acc()
    r = _bind(client, acc['id'], _new_login())
    assert r.status_code == 201, r.get_json()
    _no_leak(r.get_data(as_text=True), logs)
    rec = next(s for s in secrets_store.list_secrets() if s['name'] == 'linkedin.login')
    assert rec['entry_type'] == 'login' and rec['username'] == USER and rec['scope'] == 'global'
    assert secrets_store.get_secret_value('linkedin.login', consumer='test') == PW
    from mc import desk as _desk
    with _desk._store_lock:
        stored = _desk._read_store()
    refs = stored['accounts'][acc['id']]['connections']['read_own']['own_posts']['refs']
    assert refs == {'browser_profile': 'li-ron', 'login': 'linkedin.login'}
    _no_leak(json.dumps(stored))


def test_a_new_login_never_replaces_a_stored_entry_and_cannot_be_both_picked_and_typed(env):
    client, _, _ = env
    _login()
    acc = _acc()
    r = _bind(client, acc['id'], _new_login())
    assert r.status_code == 409 and r.get_json()['code'] == 'secret_exists'
    r = _bind(client, acc['id'], _new_login(name='li.other'), credentials={'login': 'linkedin.login'})
    assert r.status_code == 400
    from mc import secrets_store
    assert secrets_store.get_secret_value('linkedin.login', consumer='test') == PW


def test_a_failed_binding_write_removes_the_login_it_stored(env, monkeypatch):
    client, logs, _ = env
    from mc import desk as _desk
    acc = _acc()
    monkeypatch.setattr(_desk, '_write_store', lambda store: (_ for _ in ()).throw(OSError('disk full')))
    r = _bind(client, acc['id'], _new_login())
    assert r.status_code >= 400
    assert _vault_names() == set()
    _no_leak(r.get_data(as_text=True), logs)


def test_the_draft_fingerprint_does_not_depend_on_the_typed_password(env):
    from mc.desk_connect import purpose_bindings
    acc = _acc()
    from mc.desk_connect import registry
    def clean(pw):
        d = {'service': 'linkedin', 'revision': registry.profile('linkedin')['revision'], 'account': {'id': acc['id']},
             'account_kind': 'member', 'bindings': [{'purpose': 'read_own', 'route_id': 'linkedin-browser', 'capabilities': ['own_posts'],
                                                      'new_login': _new_login(value=pw)}]}
        return purpose_bindings.clean_draft(d)
    a, b = clean('first-password-1'), clean('second-password-2')
    assert purpose_bindings._draft_fingerprint(a) == purpose_bindings._draft_fingerprint(b)
    assert 'first-password-1' not in json.dumps(purpose_bindings._draft_fingerprint(a))


def test_the_signin_store_route_checks_shape_then_the_passcode_then_writes(env):
    client, logs, _ = env
    body = {'service': 'higgsfield', 'route_id': 'higgsfield-oauth', 'new_login': _new_login(name='higgsfield.login')}
    r = client.post('/api/desk/connect/signin/store-login', json={**body, 'new_login': _new_login(name='higgsfield.login', username='')})
    assert r.status_code == 400 and _vault_names() == set()
    r = client.post('/api/desk/connect/signin/store-login', json={**body, 'passcode': 'wrong'})
    assert r.status_code in (401, 403) and _vault_names() == set()
    r = client.post('/api/desk/connect/signin/store-login', json={**body, 'service': 'higgsfield', 'route_id': 'higgsfield-api-key', 'passcode': PASSCODE})
    assert r.status_code == 400 and _vault_names() == set()           # a route with no sign-in page takes no login
    r = client.post('/api/desk/connect/signin/store-login', json={**body, 'passcode': PASSCODE})
    assert r.status_code == 201 and r.get_json()['login'] == {'name': 'higgsfield.login', 'entry_type': 'login', 'allow_unattended': True}
    _no_leak(r.get_data(as_text=True), logs)
    assert _vault_names() == {'higgsfield.login'}
    r = client.post('/api/desk/connect/signin/store-login', json={**body, 'passcode': PASSCODE})
    assert r.status_code == 409 and r.get_json()['code'] == 'secret_exists'


def test_a_login_can_be_filled_from_an_account_binding_and_only_the_bound_one(env):
    client, logs, _ = env
    from mc.desk_connect import signin_fill
    acc = _acc()
    assert _bind(client, acc['id'], _new_login()).status_code == 201
    refs = signin_fill.bound_refs(acc['id'], 'linkedin', 'linkedin-browser')
    assert refs['login'] == 'linkedin.login' and refs['browser_profile'] == 'li-ron'
    with pytest.raises(signin_fill.FillError) as e:
        signin_fill.bound_refs(acc['id'], 'linkedin', 'linkedin-manual')
    assert e.value.code == 'login_not_bound'
    orig = signin_fill.Pane
    pane = ScriptPane([FORM], [FILLED])
    signin_fill.Pane = lambda: pane
    try:
        r = client.post('/api/desk/connect/signin/fill', json={'service': 'linkedin', 'route_id': 'linkedin-browser', 'account_id': acc['id']})
        assert r.status_code == 200 and r.get_json()['state'] == 'submitted'
        r = client.post('/api/desk/connect/signin/fill', json={'service': 'linkedin', 'route_id': 'linkedin-browser',
                                                                'account_id': acc['id'], 'login': 'someone.else'})
        assert r.status_code == 409 and r.get_json()['code'] == 'login_not_bound'
    finally:
        signin_fill.Pane = orig
    _no_leak(logs)


def test_the_options_route_lists_names_only(env):
    client, _, _ = env
    _login()
    from mc import secrets_store
    secrets_store.set_secret('plausible.api-key', PW, entry_type='api_key')
    r = client.post('/api/desk/connect/signin/options', json={'service': 'linkedin'})
    out = r.get_json()
    assert r.status_code == 200 and out['logins'] == [{'name': 'linkedin.login', 'matches': True}] and out['vault_locked'] is False
    assert [x['route_id'] for x in out['routes']] == ['linkedin-browser']
    _no_leak(r.get_data(as_text=True))
    assert client.post('/api/desk/connect/signin/options', json={'service': 'nope'}).status_code == 404
