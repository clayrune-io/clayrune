"""MC-1179 pass 08 / T8-3: one app-wide table of every mutating route and the gate on it.

38 test files stub the human gate open in their shared fixture, so a route that
never calls the gate passes every one of them. Three HIGH findings (ML1, P26,
SF22) were routes nobody had listed. `tests/route_gate_matrix_data.py` lists every
(method, rule) of `server.app.url_map` that is not GET/HEAD/OPTIONS with the gate
that guards it, and this file keeps that table equal to the route table.

What is checked:
  1. the table equals the route table (a route with no row fails, a row with no route fails);
  2. every row has a known gate and non-empty evidence; UNDECIDED rows say what they change;
  3. UNDECIDED rows are counted against UNDECIDED_CEILING;
  4. every 'passcode' and 'human_only' row is probed live (see below).

Nothing here changes a gate. 'open' and 'unattended_refused' rows are NEVER
requested: a request to an open route is a real call.

The live probe (4) sends one request per row with an empty JSON body and no
passcode, against the real `server.app` with a passcode configured in a temp auth
file. A 'passcode' row gets a browser-style Origin, so the only thing left to
refuse it is the passcode check; a 'human_only' row gets no Origin, which every
human-only gate here reads as an agent caller. Both must answer 403.

Because a misclassified row would ACT instead of refusing, the probe arms these
before any request is sent, each of which records the call and raises:
subprocess.Popen, subprocess.run, os.kill, os.execv, os._exit, shutil.rmtree,
urllib.request.urlopen. The suite's autouse guards stand behind that and were
read, not assumed: `_no_live_state_writes` (tests/live_state_guard.py) refuses any
write under the repo's data/, config.json or ~/.clayrune, and any socket connect
to the live server port 5199, and fails the test on a violation; `_no_real_cli_spawn`
refuses a spawn of the claude/codex/gemini CLIs. They do NOT cover a write
outside those roots, a spawn of any other program (hence the Popen patch), or
what a view does in memory, so the probe also asserts the response is the refusal
and not something else. A probe that comes back with any other status fails.
"""
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from route_gate_matrix_data import ROWS  # noqa: E402

GATES = {'passcode', 'human_only', 'unattended_refused', 'open'}
UNDECIDED = 'UNDECIDED'

# Rows whose evidence is the literal UNDECIDED, counted today. It may only go
# DOWN: resolve a row by finding or deciding its reason and lowering this in the
# same edit. A new route that lands as UNDECIDED fails the count until someone
# raises it here, in plain sight of the reviewer.
UNDECIDED_CEILING = 161

# Rows that cannot be probed live, each with the reason. (method, rule) -> reason.
LIVE_EXCLUDED = {
    ('POST', '/api/agent-mail/holds/<event_id>/<action>'):
        'answers 404 not_found before the gate: the route is off unless the agent-mail feature flag is on, and the flag is live state',
    ('POST', '/api/backup/export-project/<project_id>'):
        'answers 400 "no registered project": the project lookup runs before the gate; a registered project would reach the export',
    ('POST', '/api/backup/import'):
        'answers 400/404 on the path (required, then opened) before the gate; a real path would reach the import',
    ('POST', '/api/browser/agent-read/requests/<rid>/decision'):
        'answers 400/404 on the decision value and an unknown request id before the gate; a live request id would reach the grant',
    ('PUT', '/api/browser/profiles/<name>/agent-read'):
        'answers 400 on the body (enabled, then at least one allowed domain) before the gate; a valid body would reach a browser-profile write',
    ('PATCH', '/api/desk/connect/browser-profile/<identifier>'):
        'answers 400 on the draft (request_id, name, url, browser_profile) before the gate; a valid draft would reach the connection store',
    ('POST', '/api/desk/connect/browser-profile/commit'):
        'answers 400 on the draft (request_id, name, url, browser_profile) before the gate; a valid draft would reach the connection store',
    ('POST', '/api/desk/connect/browser-setup/commit'):
        'answers 400 on the draft (request_id, then a draft object) before the gate; a valid draft would reach the connection store',
    ('POST', '/api/desk/connect/commit'):
        'answers 400 on the draft (request_id, then a draft object) before the gate; a valid draft would reach the connection store',
    ('POST', '/api/desk/connect/custom/commit'):
        'answers 400 on request_id and fingerprint before the gate; a valid pair would reach the custom-connection store',
    ('DELETE', '/api/desk/connect/custom/connections'):
        'answers 400 on the name and audience before the gate; a valid body would reach a connection delete',
    ('POST', '/api/desk/connect/custom/remote/adopt'):
        'answers 400 on name, reach and project before the gate; a valid body would adopt a remote server',
    ('POST', '/api/desk/connect/permissions/commit'):
        'answers 400 on the draft (request_id, then a draft object) before the gate; a valid draft would reach the connection store',
    ('POST', '/api/desk/connect/purpose/commit'):
        'answers 400 on the draft (request_id, then a draft object) before the gate; a valid draft would reach the connection store',
    ('POST', '/api/desk/connect/saved-site/<service_id>/account'):
        "answers 400 'choose an account' before the gate; a valid account would reach the saved-site store",
    ('POST', '/api/desk/connect/signin/fill'):
        'answers 404 unknown_service before the gate; a real service profile is live state and the route drives a browser sign-in',
    ('POST', '/api/desk/connect/signin/store-login'):
        'answers 404 unknown_service before the gate; a real service profile is live state and the route stores a login',
    ('DELETE', '/api/desk/engines/<engine_id>/connection'):
        "answers 404 'engine not found' before the gate; a real engine id is live state",
    ('PUT', '/api/desk/engines/<engine_id>/connection'):
        "answers 404 'engine not found' before the gate; a real engine id is live state",
    ('PUT', '/api/desk/impact/definition/<campaign_id>'):
        "answers 404 'not available' before the gate (feature off or no such campaign); needs campaign state",
    ('POST', '/api/desk/impact/refresh/<campaign_id>'):
        "answers 404 'not available' before the gate (feature off or no such campaign); needs campaign state",
    ('PUT', '/api/mcp/<scope>/<name>'):
        "answers 400 on scope before the gate; a valid scope writes the operator's MCP config outside the paths the live-state guard covers",
    ('POST', '/api/mcp/url/install'):
        "answers 400 'Name is required' before the gate; a valid body would install an MCP server into the operator's config, outside the paths the live-state guard covers",
    ('DELETE', '/api/passkeys/<credential_id>'):
        'answers 404 unknown_credential before its passkey-assertion proof; a real credential needs the passkey registry, which needs a vault passphrase',
    ('POST', '/api/passkeys/recover'):
        'answers 400 bad_confirm, and 503 once confirmed, because the passkey registry needs a vault passphrase; the confirm check runs before the gate',
    ('POST', '/api/passkeys/register/options'):
        'answers 503 passkey_vault_not_configured before the gate: the registry needs a vault passphrase, which is live state',
    ('POST', '/api/passkeys/reset'):
        'answers 400 bad_confirm, and 409 reset_not_needed once confirmed, before the gate; a registry that needs a reset is live state',
    ('POST', '/api/project/<project_id>/agent/<session_id>/attend'):
        "the view returns 410 'disabled' for every caller before it reaches the passcode check, so the passcode refusal cannot be observed",
    ('POST', '/api/project/<project_id>/dev-harness/install'):
        "answers 404 'Project not found' before the gate; a registered project would reach a harness install",
    ('POST', '/api/secrets/import-authenticator'):
        "answers 400 'not an otpauth-migration:// URI' before the gate; a valid migration URI would write the operator's vault",
}

# Rows probed with something other than an empty body. (method, rule) -> kwargs for
# the request: 'json' (the body), 'base_url' (a browser-style host) and 'error' (the
# refusal code the view must answer). The body only gets the request past input
# validation that runs before the gate; it carries no passcode and no proof.
PROBE_OVERRIDES = {
    ('POST', '/api/local-auth/set'): {'json': {'passcode': 'a-new-probe-passcode'}, 'error': 'bad_current_passcode'},
}

PROBE_PASSCODE = 'route-gate-matrix-1234'
BROWSER_ORIGIN = 'http://localhost:5199'


def _app_routes():
    import server
    return {(m, r.rule) for r in server.app.url_map.iter_rules()
            for m in (r.methods or set()) - {'GET', 'HEAD', 'OPTIONS'}}


def _table_routes():
    return [(m, r) for m, r, _g, _e, _c in ROWS]


def test_table_equals_the_route_table():
    table = _table_routes()
    assert len(table) == len(set(table)), (
        f'duplicate rows: {sorted({k for k in table if table.count(k) > 1})}')
    app = _app_routes()
    missing = sorted(app - set(table))
    stale = sorted(set(table) - app)
    assert not missing, (
        f'{len(missing)} mutating route(s) have no row in tests/route_gate_matrix_data.py '
        f'(read the view, then add one): {missing}')
    assert not stale, f'{len(stale)} row(s) name a route the app does not have: {stale}'


def test_every_row_has_a_known_gate_and_evidence():
    for m, rule, gate, evidence, changes in ROWS:
        where = f'{m} {rule}'
        assert gate in GATES, f'{where}: unknown gate {gate!r}'
        assert isinstance(evidence, str) and evidence.strip(), f'{where}: empty evidence'
        assert evidence == evidence.strip(), f'{where}: evidence has stray whitespace'
        if gate != 'open':
            assert evidence != UNDECIDED, f'{where}: only an open row may be UNDECIDED'
        if evidence == UNDECIDED:
            assert changes.strip(), f'{where}: an UNDECIDED row must say what the route changes'
        else:
            assert not changes.strip(), f'{where}: only an UNDECIDED row carries a changes note'


def test_undecided_rows_stay_under_the_ceiling():
    undecided = sorted((m, r) for m, r, _g, e, _c in ROWS if e == UNDECIDED)
    assert len(undecided) <= UNDECIDED_CEILING, (
        f'{len(undecided)} UNDECIDED rows, ceiling is {UNDECIDED_CEILING}. A new route '
        f'must be classified from its view, not parked as UNDECIDED. The ceiling only goes down.')


# ── live probe: passcode and human_only rows only ────────────────────────────

PROBED = [(m, r, g) for m, r, g, _e, _c in ROWS
          if g in ('passcode', 'human_only') and (m, r) not in LIVE_EXCLUDED]


def _url(rule):
    return re.sub(r'<(?:int:)?[^>]+>', lambda mo: '7' if mo.group(0).startswith('<int:') else 'probe', rule)


class MisclassifiedRowActed(AssertionError):
    """A probed row did something other than refuse."""


@pytest.fixture
def armed(tmp_path, monkeypatch):
    import os
    import shutil
    import subprocess
    import urllib.request

    import server
    from mc.blueprints import local_auth

    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    monkeypatch.setattr(local_auth, '_LOCAL_AUTH_FAILS', {})
    local_auth._local_auth_set_passcode(PROBE_PASSCODE)

    acted = []

    def tripwire(name):
        def fn(*args, **kwargs):
            acted.append(name)
            raise MisclassifiedRowActed(f'a probed route reached {name}')
        return fn

    for owner, attr, label in ((subprocess, 'Popen', 'subprocess.Popen'),
                               (subprocess, 'run', 'subprocess.run'),
                               (os, 'kill', 'os.kill'),
                               (os, 'execv', 'os.execv'),
                               (os, '_exit', 'os._exit'),
                               (shutil, 'rmtree', 'shutil.rmtree'),
                               (urllib.request, 'urlopen', 'urllib.request.urlopen')):
        monkeypatch.setattr(owner, attr, tripwire(label))
    server.app.config['TESTING'] = True
    return server.app.test_client(), acted


def test_live_probe_set_is_what_the_table_says():
    probed = {(m, r) for m, r, _g in PROBED}
    wanted = {(m, r) for m, r, g, _e, _c in ROWS if g in ('passcode', 'human_only')}
    assert probed | set(LIVE_EXCLUDED) == wanted
    assert not probed & set(LIVE_EXCLUDED)
    for key, reason in LIVE_EXCLUDED.items():
        assert key in wanted, f'{key} is excluded but is not a passcode/human_only row'
        assert reason.strip(), f'{key}: exclusion without a reason'


@pytest.mark.parametrize('method,rule,gate', PROBED, ids=[f'{m} {r}' for m, r, _g in PROBED])
def test_gated_row_refuses_without_proof(armed, method, rule, gate):
    client, acted = armed
    headers = {'Origin': BROWSER_ORIGIN} if gate == 'passcode' else {}
    ov = PROBE_OVERRIDES.get((method, rule), {})
    kwargs = {'base_url': ov['base_url']} if 'base_url' in ov else {}
    resp = client.open(_url(rule), method=method, json=ov.get('json', {}), headers=headers, **kwargs)
    assert acted == [], f'{method} {rule} ({gate}) reached {acted} instead of refusing'
    assert resp.status_code == 403, (
        f'{method} {rule} is marked {gate} but answered {resp.status_code}: '
        f'{resp.get_data(as_text=True)[:200]}')
    if gate == 'passcode':
        assert (resp.get_json(silent=True) or {}).get('error') == ov.get('error', 'bad_passcode'), (
            f'{method} {rule} is marked passcode but refused for another reason than {ov.get("error", "bad_passcode")!r}: '
            f'{resp.get_data(as_text=True)[:200]}')
