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


@pytest.fixture
def out(kit, setup):
    core = kit.active()
    recording = Recording()
    outbound = Outbound(core, transport=recording, enabled=True, key=lambda: kit.master)
    return SimpleNamespace(core=core, store=core.store, recording=recording, outbound=outbound,
                           kit=kit, setup=setup, session=setup.session)


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
            assert db.execute('SELECT * FROM mail_transport_threads WHERE thread_id=?', (value['thread_id'],)).fetchone()
            bound = json.loads(db.execute('SELECT snapshot FROM mail_continuations').fetchone()[0])
            assert bound['session_id'] == out.session['session_id']
            assert bound['native_id'] == 'native-original'
        assert value['reply_token'].encode() not in out.store.path.read_bytes()
        assert request()['body'].encode() not in out.store.path.read_bytes()
        assert set(value) == {'intent_id', 'thread_id', 'reply_token', 'subject', 'body', 'kind'}
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
    restarted = Outbound(out.kit.core(), transport=out.recording, enabled=True, key=lambda: out.kit.master)
    assert restarted.prepare('proj1', out.session['session_id'], request()) == out.outbound.status(first['intent_id'])
    out.recording.code = 'accepted'
    assert restarted.send(first['intent_id'])['state'] == 'accepted'
    a, b = out.recording.calls
    assert a[3] == b[3]
    assert a[2]['x-mail-nonce'] != b[2]['x-mail-nonce']
    assert a[2]['x-mail-signature'] != b[2]['x-mail-signature']
    public = Ed25519PrivateKey.from_private_bytes(SEED).public_key()
    for _, path, headers, body in (a, b):
        canonical = ['clayrune-agent-mail', '1', 'install-to-service', headers['x-mail-key-id'],
                     'install1', '1', 'POST', path, headers['x-mail-timestamp'], headers['x-mail-nonce'],
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
    reopened = Outbound(out.kit.core(), transport=out.recording, enabled=True, key=lambda: MASTER)
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
    restarted = Outbound(out.kit.core(), transport=out.recording, enabled=True, key=lambda: MASTER)
    assert restarted.status(first['intent_id'])['state'] == 'sending'
    out.kit.now += 60000
    assert restarted.send(first['intent_id'])['state'] == 'uncertain'


@pytest.mark.parametrize('change', ['disabled', 'lease', 'manifest', 'prerequisite', 'locked', 'identity'])
def test_current_authority_required_at_send(out, change):
    first = prepare(out)
    if change == 'disabled':
        out.core.disable()
        assert out.outbound.send(first['intent_id'])['state'] == 'revoked'
    else:
        if change == 'lease':
            out.kit.now = NOW + 300000
        elif change == 'manifest':
            with out.store._tx() as db:
                db.execute("UPDATE mail_enrollment_flow SET manifest_digest=?", ('f'*64,))
        elif change == 'prerequisite':
            from mc.agent_mail_enrollment import Prerequisites
            out.kit.ready = Prerequisites()
        elif change == 'locked':
            out.kit.master = None
        else:
            out.session['claude_session_id'] = 'another-native'
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
        db.execute('DELETE FROM mail_transport_threads')
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
        ev = envelope(thread_id=value['thread_id'])
        receipt = MailIngress(out.store).receive(*signed(ROOT, ev))
        captured.append(receipt['event_id'])
        event = out.store.get_event(receipt['event_id'], include_content=True)
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


def test_actual_worker_verifies_local_signature_and_derives_only_enrolled_owner(out):
    script = """import { D1 } from './workers/agent-mail/test/transport-support.js';
import { createWorker } from './workers/agent-mail/worker.js';
let raw=''; for await (const c of process.stdin) raw+=c;
const p=JSON.parse(raw); const db=new D1(); const sent=[];
db.sql.prepare('INSERT INTO registrations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)').run(
 'install1',1,1,1,p.owner,'opaque1',p.key_id,p.public_key,
 'https://proven.example.test','/api/agent-mail/ingress',1,1);
const worker=createWorker({clock:()=>p.now,send:async mail=>sent.push(mail),
 fetcher:async()=>{throw new Error('network forbidden')}});
const env={MAIL_ENABLED:'true',MAIL_INGRESS_VERIFIED:'true',MAIL_DB:db,
 MAIL_DOMAIN:'example.test',MAIL_FROM:'agent@example.test',MAIL_SERVICE_KEY_ID:'service1',
 MAIL_SERVICE_PRIVATE_KEY:'unused'};
const response=await worker.fetch(new Request(p.origin+p.path,
 {method:'POST',headers:p.headers,body:p.body}),env);
const result=await response.json();
console.log(JSON.stringify({result,sent,threads:db.sql.prepare('SELECT * FROM threads').all(),
 intents:db.sql.prepare('SELECT * FROM intents').all()}));
db.sql.close();
"""
    observed = []
    def worker(origin, path, headers, raw):
        child = subprocess.run(['node', '--input-type=module', '-e', script],
            input=json.dumps(dict(origin=origin, path=path, headers=headers, body=raw.decode(),
                                  owner=OWNER, key_id=headers['x-mail-key-id'], now=out.kit.now,
                                  public_key=b64(Ed25519PrivateKey.from_private_bytes(SEED).public_key().public_bytes_raw()))),
            text=True, capture_output=True, timeout=20, cwd=Path(__file__).resolve().parents[1])
        observed.append(child)
        return json.loads(child.stdout)['result'] if child.returncode == 0 else dict(code='error')
    out.recording.send = worker
    first = prepare(out)
    response = out.outbound.send(first['intent_id'])
    assert observed[0].returncode == 0, observed[0].stderr
    result = json.loads(observed[0].stdout)
    assert len(result['sent']) == len(result['threads']) == len(result['intents']) == 1
    assert result['sent'][0]['to'] == OWNER
    assert f'To: {OWNER}\r\n' in result['sent'][0]['raw']
    assert result['intents'][0]['state'] == 'accepted'
    assert response['state'] == 'accepted'


@pytest.mark.parametrize('stage', ['off', 'pending', 'delivery_pending'])
def test_nonactive_enrollment_cannot_prepare_or_send(kit, setup, stage):
    core = kit.core() if stage == 'off' else kit.pending()
    if stage == 'pending':
        with core.store._tx() as db:
            db.execute("UPDATE mail_enrollment_flow SET stage='pending'")
    obj = Outbound(core, transport=Recording(), enabled=True, key=lambda: MASTER)
    with pytest.raises(EnrollmentRefused, match='enrollment_required'):
        obj.prepare('proj1', setup.session['session_id'], request())
    assert obj.transport.calls == []


def test_slow_prerequisite_cannot_extend_ownership_lease(out):
    first = prepare(out)
    def slow():
        out.kit.now = NOW + 300000
        return out.kit.ready
    out.core.prerequisites = slow
    with pytest.raises(EnrollmentRefused, match='ownership_expired'):
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
