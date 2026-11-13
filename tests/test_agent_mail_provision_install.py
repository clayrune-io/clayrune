"""U2 provisioning tool: synthetic keys, temp stores, stubbed service pin, no network or Cloudflare.

The tool is never pointed at ~/.clayrune: every factory/root here is under tmp_path and the
reviewed-service callable (U1's frozen build(factory) signature) is a stub.
"""
import base64
import hashlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mc import agent_mail_factory as fac
from mc.agent_mail_enrollment_keys import EnrollmentRefused, SealReference
from mc.agent_mail_pull_schema import validate as validate_pull
from mc.agent_mail_service_manifest import ServiceKeyCandidate
from mc.agent_mail_signup import Signup

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('agent_mail_provision_install', REPO / 'tools/agent-mail-provision-install.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

NOW = 1_794_249_600_000
ORIGIN = 'https://mail.example.com'
OWNER = 'Private.Owner+tag@EXAMPLE.com'
CANONICAL_OWNER = 'Private.Owner+tag@example.com'
MASTER = b'M' * 32
DIGEST = 'd' * 64
DOMAIN = 'clayrune.example'


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


ROOT_PUBLIC = b64(Ed25519PrivateKey.from_private_bytes(b'R' * 32).public_key().public_bytes_raw())
SERVICE_PUBLIC = Ed25519PrivateKey.from_private_bytes(b'V' * 32).public_key().public_bytes_raw()
PIN = ServiceKeyCandidate(ORIGIN, 7, 'service-key', b64(SERVICE_PUBLIC),
                          hashlib.sha256(SERVICE_PUBLIC).hexdigest(), (), NOW + 10_000_000)


def make_factory(tmp_path, *, pin=PIN, digest=DIGEST, key=lambda: MASTER):
    return fac.MailFactory(tmp_path / 'mail', clock=lambda: NOW / 1000, key=key,
                           config={fac.ORIGIN_KEY: ORIGIN, fac.ROOT_KEY: ROOT_PUBLIC,
                                   'agent_mail_enrollment_enabled': True, 'agent_mail_outbound_enabled': True},
                           reviewed_service=lambda: (pin, digest))


def run_provision(factory, tmp_path, **kwargs):
    kwargs.setdefault('authorize', lambda: True)
    return tool.provision(factory, owner=OWNER, mail_domain=DOMAIN, sql_path=tmp_path / 'out' / 'd1.sql', **kwargs)


@pytest.fixture
def done(tmp_path):
    factory = make_factory(tmp_path)
    return factory, run_provision(factory, tmp_path)


def table(factory, name):
    with factory.require_pull_store()._read() as db:
        return [dict(r) for r in db.execute(f'SELECT * FROM {name}')]


def test_result_is_accepted_by_pull_client_outbound_and_validate_pull(done):
    factory, result = done
    store = factory.require_pull_store()
    validate_pull(store)
    # A second factory, as a restarted server would build it, sees the same accepted account.
    fresh = make_factory(Path(factory.paths.root).parent)
    with fresh.require_pull_store()._read() as db:
        account = fresh.pull_client()._account(db)
    assert (account['install_id'], account['state'], account['local_enabled']) == (result['install_id'], 'active', 1)
    assert account['service_key_id'] == PIN.key_id and account['manifest_sequence'] == 7
    assert account['manifest_digest'] == DIGEST
    with store._read() as db:
        row = factory.outbound()._authority(db)  # real Outbound, real pin comparison, real seal status
    assert (row['address'], row['owner_address'], row['epoch']) == (result['address'], CANONICAL_OWNER, 1)
    ref = SealReference(row['flow'], row['install'], 1, row['key_id'])
    assert factory.seal().status(ref) == 'ready'


def test_signup_reader_sees_an_enabled_active_install(done):
    factory, result = done
    view = factory.signup(authorize=lambda: True).status()
    assert (view['state'], view['enabled'], view['install_id']) == ('active', True, result['install_id'])
    assert view['address'] == result['address']


def test_rows_match_signup_shapes(done):
    factory, result = done
    install = result['install_id']
    (account,) = table(factory, 'mail_pull_accounts')
    assert (account['generation'], account['owner_generation'], account['state'], account['local_enabled'],
            account['consent_epoch'], account['reason']) == (1, 1, 'active', 1, 1, '')
    assert account['owner_address'] == CANONICAL_OWNER and account['service_origin'] == ORIGIN
    assert re.fullmatch(r'm[0-9a-f]{29}@' + re.escape(DOMAIN), account['address'])
    (gen,) = table(factory, 'mail_pull_generations')
    assert (gen['state'], gen['service_public_key'], gen['fenced_ms']) == ('current', PIN.public_key, None)
    (flow,) = table(factory, 'mail_pull_flows')
    assert (flow['purpose'], flow['state'], flow['flow_id']) == ('signup', 'completed', account['seal_flow_id'])
    (reg,) = table(factory, 'registrations')
    assert (reg['registration_id'], reg['generation'], reg['enabled']) == (install, 1, 1)
    assert result['config'] == {fac.INSTALL_KEY: install, fac.ROOT_KEY: ROOT_PUBLIC, fac.ORIGIN_KEY: ORIGIN}


def test_sql_file_has_the_four_worker_rows_and_a_fence_hash(done, tmp_path):
    factory, result = done
    text = Path(result['sql_path']).read_text(encoding='ascii')
    assert Path(result['sql_path']) == tmp_path / 'out' / 'd1.sql'
    inserts = re.findall(r'^INSERT INTO (\w+)', text, re.M)
    assert inserts == ['pull_installs', 'pull_generations', 'pull_addresses', 'pull_owner_slots']
    (flow,) = table(factory, 'mail_pull_flows')
    fence_token = json.loads(flow['result_json'])['fence_token']
    assert hashlib.sha256(fence_token.encode()).hexdigest() in text and fence_token not in text
    owner_hash = hashlib.sha256(CANONICAL_OWNER.encode()).hexdigest()
    assert owner_hash in text and "'active'" in text
    local = result['address'].split('@')[0]
    assert local in text and result['address'] not in text


def test_sql_owner_hash_and_current_predicates_agree_with_the_real_worker(done, tmp_path):
    """Run the Worker's own code over this SQL: owner_hash derivation, CURRENT, recipient lookup."""
    if shutil.which('node') is None:
        pytest.skip('node not available')
    factory, result = done
    code = r'''
import {readFileSync,readdirSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import {Ledger} from './ledger.js';
import {ownerIdentity} from './owner-registry.js';
const p=JSON.parse(readFileSync(0,'utf8'));
const sql=new DatabaseSync(':memory:'); sql.exec('PRAGMA foreign_keys=ON');
sql.exec(readFileSync('schema.sql','utf8'));
for(const n of readdirSync('migrations').filter(n=>/^\d+.*\.sql$/.test(n)).sort()) sql.exec(readFileSync('migrations/'+n,'utf8'));
sql.exec(readFileSync(p.sql,'utf8'));
const db={prepare(t){return {args:[],bind(...a){this.args=a;return this},
  async first(){return sql.prepare(t).get(...this.args)??null},
  async run(){const r=sql.prepare(t).run(...this.args);return {success:true,meta:{changes:Number(r.changes)}}}}}};
const pull=new Ledger(db).pull({clock:()=>p.now});
const byInstall=await pull.registration(p.install), byLocal=await pull.recipient(p.local);
const owner=await ownerIdentity(p.owner);
console.log(JSON.stringify({install:byInstall&&byInstall.install_id,local:byLocal&&byLocal.install_id,
  owner_hash:owner.owner_hash,owner_address:owner.owner_address,stored:byInstall&&byInstall.owner_hash,
  address_local:byInstall&&byInstall.address_local}));
'''
    payload = {'sql': result['sql_path'], 'install': result['install_id'], 'owner': OWNER,
               'local': result['address'].split('@')[0], 'now': NOW}
    done_ = subprocess.run(['node', '--input-type=module', '-e', code], input=json.dumps(payload), text=True,
                           capture_output=True, cwd=REPO / 'workers/agent-mail', timeout=30)
    assert done_.returncode == 0, done_.stderr
    seen = json.loads(done_.stdout)
    assert seen['install'] == seen['local'] == result['install_id']
    assert seen['owner_address'] == CANONICAL_OWNER
    assert seen['owner_hash'] == seen['stored'] == hashlib.sha256(CANONICAL_OWNER.encode()).hexdigest()
    assert seen['address_local'] == result['address'].split('@')[0]


def test_apostrophe_in_owner_is_escaped_in_sql(tmp_path):
    factory = make_factory(tmp_path)
    result = tool.provision(factory, owner="o'brien@example.com", authorize=lambda: True, mail_domain=DOMAIN,
                            sql_path=tmp_path / 'q.sql')
    text = Path(result['sql_path']).read_text(encoding='ascii')
    assert "'o''brien@example.com'" in text


@pytest.mark.parametrize('setup', ['account', 'seal'])
def test_refuses_when_an_account_or_seal_already_exists(done, tmp_path, setup):
    factory, _ = done
    if setup == 'seal':
        with factory.require_pull_store()._tx() as db:
            for t in ('registrations', 'mail_pull_status', 'mail_pull_connections', 'mail_pull_flows',
                      'mail_pull_generations', 'mail_pull_accounts'):
                db.execute(f'DELETE FROM {t}')
    before = table(factory, 'mail_pull_accounts'), sorted(p.name for p in factory.paths.identity.glob('*'))
    asked = []
    with pytest.raises(EnrollmentRefused, match='account_exists' if setup == 'account' else 'seal_exists'):
        tool.provision(make_factory(tmp_path), owner=OWNER, mail_domain=DOMAIN, sql_path=tmp_path / 'second.sql',
                       authorize=lambda: asked.append(1) or True)
    assert not asked and not (tmp_path / 'second.sql').exists()
    assert (table(factory, 'mail_pull_accounts'), sorted(p.name for p in factory.paths.identity.glob('*'))) == before


@pytest.mark.parametrize('answer', [False, None, 'PROVISION'])
def test_human_gate_must_be_exactly_true_and_leaves_no_state(tmp_path, answer):
    factory = make_factory(tmp_path)
    with pytest.raises(EnrollmentRefused, match='human_required'):
        run_provision(factory, tmp_path, authorize=lambda: answer)
    assert not factory.paths.root.exists() and not (tmp_path / 'out').exists()


def test_human_is_asked_once_for_bootstrap_and_key_generation(tmp_path):
    asked = []
    run_provision(make_factory(tmp_path), tmp_path, authorize=lambda: asked.append(1) or True)
    assert asked == [1]


def test_locked_vault_refuses_before_any_state(tmp_path):
    factory = make_factory(tmp_path, key=lambda: None)
    with pytest.raises(EnrollmentRefused, match='vault_locked'):
        run_provision(factory, tmp_path)
    assert not factory.paths.root.exists()


@pytest.mark.parametrize('mutation', ['wrong_origin', 'expired'])
def test_bad_service_pin_stops_before_a_key_exists(tmp_path, mutation):
    pin = PIN
    if mutation == 'wrong_origin':
        pin = ServiceKeyCandidate('https://other.example.com', 7, 'service-key', PIN.public_key,
                                  PIN.fingerprint, (), NOW + 10_000_000)
    else:
        pin = ServiceKeyCandidate(ORIGIN, 7, 'service-key', PIN.public_key, PIN.fingerprint, (), NOW)
    factory = make_factory(tmp_path, pin=pin)
    with pytest.raises(EnrollmentRefused, match='service_key_changed'):
        run_provision(factory, tmp_path)
    assert not factory.paths.identity.exists() and not (tmp_path / 'out' / 'd1.sql').exists()


def test_failed_local_write_removes_its_own_unapplied_sql(tmp_path, monkeypatch):
    factory = make_factory(tmp_path)

    def broken(self, *a, **k):
        raise EnrollmentRefused('schema_corrupt')
    monkeypatch.setattr(tool.Signup, '_row', broken)
    with pytest.raises(EnrollmentRefused, match='schema_corrupt'):
        run_provision(factory, tmp_path)
    assert not (tmp_path / 'out' / 'd1.sql').exists()
    assert table(factory, 'mail_pull_accounts') == []


def test_existing_sql_output_is_never_overwritten(tmp_path):
    target = tmp_path / 'out' / 'd1.sql'
    target.parent.mkdir()
    target.write_text('keep me')
    with pytest.raises(EnrollmentRefused, match='output_exists'):
        run_provision(make_factory(tmp_path), tmp_path)
    assert target.read_text() == 'keep me'


def test_sql_output_inside_a_checkout_is_refused(tmp_path):
    with pytest.raises(EnrollmentRefused, match='output_in_checkout'):
        tool.provision(make_factory(tmp_path), owner=OWNER, authorize=lambda: True, mail_domain=DOMAIN,
                       sql_path=REPO / '_scratch' / 'nope.sql')
    assert not (REPO / '_scratch' / 'nope.sql').exists()


@pytest.mark.parametrize('kwargs', [{'owner': 'not-an-address'}, {'mail_domain': 'Bad Domain'},
                                    {'consent_version': 'bad version'}])
def test_invalid_arguments_create_nothing(tmp_path, kwargs):
    factory = make_factory(tmp_path)
    args = {'owner': OWNER, 'mail_domain': DOMAIN, 'sql_path': tmp_path / 's.sql', 'authorize': lambda: True, **kwargs}
    with pytest.raises(EnrollmentRefused, match='invalid_request'):
        tool.provision(factory, **args)
    assert not factory.paths.root.exists()


def test_build_factory_ties_u1_callable_to_the_factory(tmp_path):
    seen = []

    def stub_build(factory):  # U1's frozen signature: build(factory) -> Callable[[], (candidate, digest)]
        seen.append(factory)
        return lambda: (PIN, DIGEST)
    factory = tool.build_factory(tmp_path / 'mail', {fac.ORIGIN_KEY: ORIGIN, fac.ROOT_KEY: ROOT_PUBLIC},
                                 lambda: MASTER, stub_build)
    assert seen == [factory] and factory.paths.root == tmp_path / 'mail'
    factory.bootstrap(authorize=lambda: True)
    assert factory.reviewed_service() == (PIN, DIGEST)


def run_cli(tmp_path, answer='PROVISION', **kwargs):
    out, err = io.StringIO(), io.StringIO()
    argv = ['--owner', OWNER, '--service-origin', ORIGIN, '--root-public-key', ROOT_PUBLIC,
            '--mail-domain', DOMAIN, '--sql-out', str(tmp_path / 'cli.sql')]
    code = tool.run(argv, stdout=out, stderr=err, prompt=lambda _: answer, key=lambda: MASTER,
                    root=tmp_path / 'mail', clock=lambda: NOW / 1000,
                    build=lambda f: (lambda: (PIN, DIGEST)), **kwargs)
    return code, out.getvalue(), err.getvalue()


def test_cli_prints_config_keys_and_never_a_secret(tmp_path):
    code, out, err = run_cli(tmp_path)
    assert code == 0 and err == ''
    for name in (fac.INSTALL_KEY, fac.ROOT_KEY, fac.ORIGIN_KEY):
        assert name in out
    assert 'agent_mail_outbound_enabled' in out and str(tmp_path / 'cli.sql') in out
    flow = table(make_factory(tmp_path), 'mail_pull_flows')[0]
    fence_token = json.loads(flow['result_json'])['fence_token']
    assert fence_token not in out and 'MMMM' not in out


@pytest.mark.parametrize('answer', ['', 'no', 'provision'])
def test_cli_wrong_confirmation_exits_2_and_creates_nothing(tmp_path, answer):
    code, out, err = run_cli(tmp_path, answer)
    assert code == 2 and err == 'error: human_required\n'
    assert not (tmp_path / 'mail').exists() and not (tmp_path / 'cli.sql').exists()


def test_cli_second_run_is_refused(tmp_path):
    assert run_cli(tmp_path)[0] == 0
    out, err = io.StringIO(), io.StringIO()
    code = tool.run(['--owner', OWNER, '--service-origin', ORIGIN, '--root-public-key', ROOT_PUBLIC,
                     '--mail-domain', DOMAIN, '--sql-out', str(tmp_path / 'again.sql')], stdout=out, stderr=err,
                    prompt=lambda _: 'PROVISION', key=lambda: MASTER, root=tmp_path / 'mail',
                    clock=lambda: NOW / 1000, build=lambda f: (lambda: (PIN, DIGEST)))
    assert code == 2 and err.getvalue() == 'error: account_exists\n'
