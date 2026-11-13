"""Unit 7c synthetic enrollment -> outbound -> immediate signed reply -> wake."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import threading
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from test_agent_mail_enrollment import kit, MASTER, NOW, OWNER, SEED, ROOT  # noqa: F401
from test_agent_mail_ingress import envelope, signed
from test_agent_mail_service_client_pull import World
from test_agent_mail_pull_receiver import INSTALL, MANIFEST, NOW as PULL_NOW, event
from mc.agent_mail_service_client import SEND, ServiceRefused
from mc.agent_mail_pull_receiver import PullReceiver
from test_agent_mail_wake import setup, env, wait_for  # noqa: F401
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_enrollment_keys import b64
from mc.agent_mail_extract import WARNING
from mc.agent_mail_ingress import MailIngress
from mc.agent_mail_outbound import Outbound
from mc.agent_mail_signing import unbase64url
from mc.agent_mail_store import MailStore, SUBMITTED
from mc.agent_mail_wake import MailWake


def request(**changes):
    return dict(dict(request_id='note1', kind='note', subject='Same subject',
                     body='Friday works for me.'), **changes)


class Recording:
    def __init__(self):
        self.calls = []
        self.hook = lambda *args: None
        self.code = 'accepted'

    def send(self, origin, path, headers, body):
        self.calls.append((origin, path, headers, body))
        self.hook(headers, body)
        value = json.loads(body)
        mid = hashlib.sha256(f"{headers['x-mail-install-id']}:{headers['x-mail-generation']}:{value['intent_id']}".encode()).hexdigest()
        return dict(code=self.code, message_id=mid + '@example.test')


class PullWorld(World):
    """Real v2 signatures/verification around an injected fake provider result."""
    def __init__(self, path):
        super().__init__(path)
        self.recording = Recording()
        with self.store._tx() as db:
            for table in ('mail_pull_threads', 'thread_bindings', 'mail_continuations'):
                db.execute('DELETE FROM ' + table)

    def disable(self):
        with self.store._tx() as db:
            db.execute("UPDATE mail_pull_accounts SET local_enabled=0,state='disabled',consent_epoch=consent_epoch+1")
            db.execute('UPDATE registrations SET enabled=0,generation=generation+1')

    def http(self, method, path, h, raw):
        assert method == 'POST' and path == SEND
        self.proof(path, h, raw)
        value = json.loads(raw)
        result = self.recording.send(self.pin.origin, path, h, raw)
        code = result['code']
        status = 200 if code == 'accepted' else 429 if code == 'send_limit' else 409
        receipt = {} if code == 'send_limit' else dict(
            intent_id=value['intent_id'], thread_id=value['thread_id'], state=code,
            message_id=result.get('message_id', ''), provider_id='provider-id' if code == 'accepted' else '')
        receipt.update({'code': code} if code == 'send_limit' else result)
        response = self.response(path, h, value, dict(v=1, request_id=value['op_id'], **receipt), status)
        return self.change(*response)

    def outbound(self, **changes):
        return Outbound(self.store, client=self.make, reviewed_service=lambda: self.review(),
                        enabled=True, key=lambda: self.master, **changes)

    def receive(self, value, text='Friday works.'):
        with self.store._read() as db:
            thread = db.execute('SELECT thread_expires_ms FROM mail_pull_threads WHERE thread_id=?',
                                (value['thread_id'],)).fetchone()
        e = event(thread_id=value['thread_id'], text=text, received_ms=self.now,
                  expires_ms=min(self.now + 86400000, thread[0]), thread_expires_ms=thread[0])
        ack = self.receiver.receive(self.wire(e))
        return e, ack['payload']['event_id']


@pytest.fixture
def out(kit, setup, tmp_path):
    w = PullWorld(tmp_path / 'pull')
    core = SimpleNamespace(store=w.store, seal=w.seal, disable=w.disable)
    return SimpleNamespace(core=core, store=w.store, recording=w.recording, outbound=w.outbound(),
                           kit=w, setup=setup, session=setup.session)


def prepare(out, **changes):
    return out.outbound.prepare('proj1', out.session['session_id'], request(**changes))


def test_default_disabled_cannot_create_intents(kit):
    core = kit.active()
    obj = Outbound(core, transport=Recording())
    with pytest.raises(EnrollmentRefused, match='outbound_disabled'):
        obj.prepare('p', 's', request())
    with core.store._read() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'mail_outbound_%'").fetchone()


def test_all_bindings_and_immutable_intent_precede_send(out):
    result = prepare(out)
    def inspect(headers, raw):
        value = json.loads(raw)
        with out.store._read() as db:
            row = db.execute('SELECT * FROM mail_outbound_intents').fetchone()
            assert row['state'] == 'sending' and row['attempt']
            assert db.execute('SELECT * FROM mail_pull_threads WHERE thread_id=?', (value['thread_id'],)).fetchone()
            bound = json.loads(db.execute('SELECT snapshot FROM mail_continuations').fetchone()[0])
            assert bound['session_id'] == out.session['session_id']
            assert bound['native_id'] == 'native-original'
        assert value['reply_token'].encode() not in out.store.path.read_bytes()
        assert request()['body'].encode() not in out.store.path.read_bytes()
        assert set(value) == {'op_id', 'intent_id', 'thread_id', 'reply_token', 'subject', 'body', 'kind'}
        assert OWNER not in raw.decode()  # Worker derives sole verified owner
    out.recording.hook = inspect
    assert out.outbound.send(result['intent_id'])['state'] == 'accepted'
    assert len(out.recording.calls) == 1
    assert out.outbound.send(result['intent_id'])['state'] == 'accepted'
    assert prepare(out) == out.outbound.status(result['intent_id'])
    with out.store._read() as db:
        assert db.execute('SELECT payload FROM mail_outbound_intents').fetchone()[0] is None


@pytest.mark.parametrize('field', ['to', 'recipient', 'cc', 'bcc', 'from', 'reply_to', 'headers',
                                  'message_id', 'raw', 'install_id', 'callback', 'generation'])
def test_recipient_and_header_overrides_refused_before_custody(out, field):
    with pytest.raises(EnrollmentRefused, match='invalid_send'):
        out.outbound.prepare('proj1', out.session['session_id'], request(**{field: 'other@example.test'}))
    with out.store._read() as db:
        assert db.execute('SELECT COUNT(*) FROM mail_outbound_intents').fetchone()[0] == 0
    assert out.recording.calls == []


@pytest.mark.parametrize('changes', [dict(subject='a\r\nTo: other@example.test'), dict(subject='a\x7f'),
                                    dict(subject='🔥'*51), dict(body='x'*16385), dict(body='x\0'),
                                    dict(body=' '), dict(kind='raw'), dict(request_id='../bad')])
def test_invalid_content_has_no_intent(out, changes):
    with pytest.raises(EnrollmentRefused, match='invalid_send'):
        prepare(out, **changes)


def test_conflicting_retry_cannot_change_content_or_conversation(out):
    first = prepare(out)
    with pytest.raises(EnrollmentRefused, match='intent_conflict'):
        prepare(out, body='changed')
    with out.store._tx() as db:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute("UPDATE mail_outbound_intents SET session_id='other'")
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            db.execute("UPDATE mail_outbound_intents SET payload=x'01'")
    assert out.outbound.status(first['intent_id'])['state'] == 'ready'


def test_proven_no_write_retry_reuses_exact_bytes_fresh_signature_after_restart(out):
    first = prepare(out)
    out.recording.code = 'send_limit'
    assert out.outbound.send(first['intent_id'])['state'] == 'retryable'
    out.kit.now += 1
    restarted = out.kit.outbound()
    assert restarted.prepare('proj1', out.session['session_id'], request()) == out.outbound.status(first['intent_id'])
    out.recording.code = 'accepted'
    assert restarted.send(first['intent_id'])['state'] == 'accepted'
    a, b = out.recording.calls
    assert a[3] == b[3]
    assert a[2]['x-mail-nonce'] != b[2]['x-mail-nonce']
    assert a[2]['x-mail-signature'] != b[2]['x-mail-signature']
    from mc.agent_mail_signing import unbase64url
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    public = Ed25519PublicKey.from_public_bytes(unbase64url(out.kit.public, 32))
    for _, path, headers, body in (a, b):
        canonical = ['clayrune-agent-mail-pull-http', '1', 'install-to-pull', out.kit.pin.origin, 'POST', path,
                     headers['x-mail-key-id'], INSTALL, '1', '1', headers['x-mail-timestamp'], headers['x-mail-nonce'],
                     hashlib.sha256(body).hexdigest()]
        public.verify(unbase64url(headers['x-mail-signature'], 64),
                      json.dumps(canonical, separators=(',', ':')).encode())


@pytest.mark.parametrize('result', ['exception', 'uncertain', 'sending', 'garbage', 'accepted_bad_mid'])
def test_possible_provider_write_never_blindly_resends(out, result):
    first = prepare(out)
    def uncertain(*args):
        out.recording.calls.append(args)
        if result == 'exception':
            raise OSError('owner@example.test secret text')
        return dict(code='accepted' if result == 'accepted_bad_mid' else result, message_id='bad\r\nCC: evil')
    out.recording.send = uncertain
    assert out.outbound.send(first['intent_id'])['state'] == 'uncertain'
    assert out.outbound.send(first['intent_id'])['state'] == 'uncertain'
    reopened = out.kit.outbound()
    assert reopened.send(first['intent_id'])['state'] == 'uncertain'
    assert len(out.recording.calls) == 1
    assert 'secret' not in json.dumps(reopened.status(first['intent_id']))


def test_crash_after_admission_becomes_uncertain_without_replay(out):
    first = prepare(out)
    def crash(*args):
        raise SystemExit('simulated process death')
    out.recording.send = crash
    with pytest.raises(SystemExit):
        out.outbound.send(first['intent_id'])
    restarted = out.kit.outbound()
    assert restarted.status(first['intent_id'])['state'] == 'sending'
    out.kit.now += 60000
    assert restarted.send(first['intent_id'])['state'] == 'uncertain'


@pytest.mark.parametrize('change', ['disabled', 'generation', 'manifest', 'consent', 'locked', 'identity'])
def test_current_authority_required_at_send(out, change):
    first = prepare(out)
    if change == 'disabled':
        out.core.disable()
        assert out.outbound.send(first['intent_id'])['state'] == 'revoked'
    else:
        if change == 'generation':
            with out.store._tx() as db:
                db.execute('UPDATE mail_pull_accounts SET generation=2')
        elif change == 'manifest':
            with out.store._tx() as db:
                db.execute("UPDATE mail_pull_generations SET manifest_digest=?", ('f'*64,))
        elif change == 'consent':
            with out.store._tx() as db:
                db.execute('UPDATE mail_pull_accounts SET local_enabled=0')
        elif change == 'locked':
            out.kit.master = None
        else:
            out.session['claude_session_id'] = 'another-native'
        if change in ('generation', 'consent'):
            assert out.outbound.send(first['intent_id'])['state'] == 'revoked'
        else:
            with pytest.raises((EnrollmentRefused, OSError)):
                out.outbound.send(first['intent_id'])
    assert out.recording.calls == []


def test_racing_senders_admit_once_and_disable_cannot_start_another(out):
    first = prepare(out)
    started, release = threading.Event(), threading.Event()
    out.recording.hook = lambda *a: (started.set(), release.wait(3))
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(out.outbound.send, first['intent_id'])
        assert started.wait(2)
        b = pool.submit(out.outbound.send, first['intent_id'])
        assert b.result(timeout=2)['state'] == 'sending'
        out.core.disable()
        assert out.outbound.send(first['intent_id'])['state'] == 'revoked'
        release.set()
        assert a.result(timeout=2)['state'] == 'revoked'
    assert len(out.recording.calls) == 1  # previously admitted write is not recallable


def test_snapshot_failure_or_missing_mapping_cannot_send(out, monkeypatch):
    original = MailWake.bind
    monkeypatch.setattr(MailWake, 'bind', lambda *a, **kw: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError):
        prepare(out)
    with out.store._read() as db:
        row = dict(db.execute('SELECT * FROM mail_outbound_intents').fetchone())
    assert row['state'] == 'draft'
    assert out.outbound.send(row['intent_id'])['state'] == 'draft'
    monkeypatch.setattr(MailWake, 'bind', original)
    assert prepare(out)['intent_id'] == row['intent_id']
    with out.store._tx() as db:
        db.execute('DELETE FROM mail_pull_threads')
    with pytest.raises(EnrollmentRefused, match='outbound_binding_required'):
        out.outbound.send(row['intent_id'])
    assert not out.recording.calls


def test_encrypted_payload_corruption_or_lost_vault_never_changes_intent(out):
    first = prepare(out)
    out.kit.master = b'wrong-master-key'.ljust(32, b'x')
    with pytest.raises(EnrollmentRefused, match='outbound_payload_unavailable'):
        out.outbound.send(first['intent_id'])
    assert not out.recording.calls


def test_immediate_reply_before_send_receipt_reaches_only_original_conversation(out):
    first = prepare(out)
    # Same persona/subject in another conversation must not influence routing.
    other = dict(out.session, session_id='other-session', claude_session_id='other-native')
    out.setup.ar.agent_sessions['other-session'] = other
    captured = []
    def reply(headers, raw):
        value = json.loads(raw)
        ev, event_id = out.kit.receive(value)
        captured.append(event_id)
        event = out.store.get_event(event_id, include_content=True)
        assert event['session_id'] == out.session['session_id']
        claim = out.store.claim_for_laundering()
        out.store.complete_laundering(event['event_id'], claim['fence_token'], dict(
            version=1, source='owner_mail_laundered', warning=WARNING, reply_text=ev['text'],
            ambiguous_or_mixed=False, quoted_content_present=False,
            forwarded_content_present=False, event_id=event['event_id']))
        assert MailWake(out.store, enabled=True).process_next(out.setup.app)['state'] == SUBMITTED
        assert other['proc'].stdin.getvalue() == ''  # no second dispatch
    # Give the other synthetic session its own pipe.
    import io
    other['proc'] = SimpleNamespace(pid=-992, poll=lambda: None, stdin=io.StringIO())
    out.recording.hook = reply
    assert out.outbound.send(first['intent_id'])['state'] == 'accepted'
    wait_for(lambda: 'Friday works.' in out.session['proc'].stdin.getvalue())
    assert len(captured) == 1 and other['proc'].stdin.getvalue() == ''


def test_service_verifies_local_signature_and_request_has_no_owner_override(out):
    first = prepare(out)
    assert out.outbound.send(first['intent_id'])['state'] == 'accepted'
    origin, path, h, raw = out.recording.calls[0]
    out.kit.proof(path, h, raw)
    value = json.loads(raw)
    assert path == '/v1/pull/send' and origin == out.kit.pin.origin
    assert not {'to', 'owner', 'recipient', 'from', 'headers'}.intersection(value)
    assert h['x-mail-install-id'] == INSTALL
    assert h['x-mail-owner-generation'] == '1'


def test_actual_pull_worker_verifies_signature_and_derives_only_verified_owner(out):
    script = """import { D1 } from './workers/agent-mail/test/transport-support.js';
import { PullSendHTTP } from './workers/agent-mail/pull-send-http.js';
import { readFileSync, readdirSync } from 'node:fs';
let raw=''; for await (const c of process.stdin) raw+=c;
const p=JSON.parse(raw), db=new D1(), sent=[];
for(const n of readdirSync('workers/agent-mail/migrations').filter(n=>/^\\d+.*\\.sql$/.test(n)).sort())
 db.sql.exec(readFileSync('workers/agent-mail/migrations/'+n,'utf8'));
db.sql.function('strftime',(fmt,at)=>fmt==='%s'?String(Math.floor(p.now/1000)):
 String(Math.floor(p.now/1000)%60).padStart(2,'0')+'.'+String(p.now%1000).padStart(3,'0'));
db.prepare=function(sql){return {args:[],bind(...a){this.args=a;return this;},
 async first(){return db.sql.prepare(sql).get(...this.args)??null;},
 async run(){return this.runSync();},runSync(){const q=db.sql.prepare(sql);
 return q.columns().length?{success:true,results:q.all(...this.args),meta:{changes:0}}:
 {success:true,meta:{changes:Number(q.run(...this.args).changes)}};}};};
const install=p.headers['x-mail-install-id'], local='m'+'a'.repeat(29);
db.sql.prepare(`INSERT INTO pull_installs(install_id,generation,owner_generation,state,owner_hash,
owner_address,address_local,key_id,public_key,consent_version,epoch,created_ms,updated_ms)
VALUES(?,1,1,'active',?,?,?,?,?,'v1',1,?,?)`).run(install,'a'.repeat(64),p.owner,local,'key1',p.public,p.now,p.now);
db.sql.prepare("INSERT INTO pull_generations VALUES(?,1,1,?,'key1',?,?,?,'current',?,NULL)")
 .run(install,'a'.repeat(64),p.public,'f'.repeat(64),'c'.repeat(32),p.now);
db.sql.prepare("INSERT INTO pull_addresses VALUES(?,?,'assigned',NULL,?)").run(local,install,p.now);
const http=new PullSendHTTP({db,serviceOrigin:p.origin,serviceKeyId:'service1',
 servicePrivateKey:p.private,clock:()=>p.now,config:{from:'agent@example.test',domain:'example.test'},
 binding:{async send(mail){sent.push(mail);return {messageId:'opaque-provider-id'};}}});
const response=await http.handle(new Request(p.origin+p.path,{method:'POST',headers:p.headers,body:p.body}),
 {trustedSource:'synthetic-test'});
console.log(JSON.stringify({status:response.status,headers:Object.fromEntries(response.headers),
 body:await response.text(),sent,threads:db.sql.prepare('SELECT * FROM pull_threads').all(),
 intents:db.sql.prepare('SELECT * FROM pull_intents').all()}));db.sql.close();
"""
    from cryptography.hazmat.primitives import serialization
    observed = []
    def worker(method, path, headers, raw):
        private = out.kit.private.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption())
        child = subprocess.run(['node', '--input-type=module', '-e', script],
            input=json.dumps(dict(origin=out.kit.pin.origin, path=path, headers=headers, body=raw.decode(),
                                  owner=OWNER, public=out.kit.public, private=b64(private), now=out.kit.now)),
            text=True, capture_output=True, timeout=20, cwd=Path(__file__).resolve().parents[1])
        observed.append(child)
        assert child.returncode == 0, child.stderr
        result = json.loads(child.stdout)
        return result['status'], result['headers'], result['body'].encode()
    out.kit.http = worker
    first = prepare(out)
    response = out.outbound.send(first['intent_id'])
    assert observed[0].returncode == 0, observed[0].stderr
    result = json.loads(observed[0].stdout)
    assert len(result['sent']) == len(result['threads']) == len(result['intents']) == 1
    assert result['sent'][0]['to'] == OWNER
    assert result['sent'][0]['replyTo'].startswith('m' + 'a' * 29 + '+')
    assert 'raw' not in result['sent'][0] and 'Message-ID' not in result['sent'][0]['headers']
    assert result['intents'][0]['state'] == response['state'] == 'accepted'


@pytest.mark.parametrize('stage', ['off', 'pending', 'delivery_pending'])
def test_nonactive_enrollment_cannot_prepare_or_send(kit, setup, stage):
    core = kit.core() if stage == 'off' else kit.pending()
    if stage == 'pending':
        with core.store._tx() as db:
            db.execute("UPDATE mail_enrollment_flow SET stage='pending'")
    obj = Outbound(core, transport=Recording(), enabled=True, key=lambda: MASTER)
    with pytest.raises(EnrollmentRefused, match='legacy_retired'):
        obj.prepare('proj1', setup.session['session_id'], request())
    assert obj.transport.calls == []


def test_slow_trust_review_cannot_extend_manifest_expiry(out):
    first = prepare(out)
    def slow():
        out.kit.now = out.kit.pin.expires_at_ms
        return out.kit.pin, MANIFEST
    out.kit.review = slow
    with pytest.raises(EnrollmentRefused, match='service_key_changed'):
        out.outbound.send(first['intent_id'])
    assert not out.recording.calls


def test_local_reconciliation_purges_expired_content_and_later_tombstone(out):
    first = prepare(out)
    out.kit.now += int(out.store.config.thread_ttl_seconds * 1000)
    out.outbound.reconcile()
    assert out.outbound.status(first['intent_id'])['state'] == 'expired'
    with out.store._read() as db:
        assert db.execute('SELECT payload FROM mail_outbound_intents').fetchone()[0] is None
    out.kit.now += int(out.store.config.terminal_retention_seconds * 1000)
    out.outbound.reconcile()
    with pytest.raises(EnrollmentRefused, match='unknown_intent'):
        out.outbound.status(first['intent_id'])


def test_revocation_retains_positive_receipt_of_already_admitted_send(out):
    first = prepare(out)
    out.recording.hook = lambda *args: out.core.disable()
    result = out.outbound.send(first['intent_id'])
    assert result['state'] == 'revoked' and result['transport_state'] == 'accepted'
    assert result['message_id']


def test_accepted_provider_write_with_failed_local_receipt_is_not_replayed(out, monkeypatch):
    first = prepare(out)
    original = out.store._tx
    @contextmanager
    def broken_commit():
        raise OSError('receipt storage unavailable')
        yield  # pragma: no cover
    out.recording.hook = lambda *args: monkeypatch.setattr(out.store, '_tx', broken_commit)
    with pytest.raises(OSError, match='receipt storage'):
        out.outbound.send(first['intent_id'])
    monkeypatch.setattr(out.store, '_tx', original)
    assert out.outbound.send(first['intent_id'])['state'] == 'sending'
    out.kit.now += 60000
    assert out.outbound.send(first['intent_id'])['state'] == 'uncertain'
    assert len(out.recording.calls) == 1
