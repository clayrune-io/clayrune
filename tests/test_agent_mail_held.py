"""Versioned body-free holds: actual Worker replay -> local durable custody."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from mc import mail_launder
from mc.agent_mail_ingress import MailIngress
from mc.agent_mail_launder import MailLaunder
from mc.agent_mail_signing import IngressRefused
from mc.agent_mail_store import BLOCKED, MailStore
from tests.test_agent_mail_ingress import TOKEN, count, envelope, setup, signed, worker_clock


def held():
    value = envelope(text='')
    del value['text']
    return dict(value, version=2, reason='empty_body')


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    monkeypatch.setattr(mail_launder, 'claude_mail_transform',
                        lambda **kw: pytest.fail('held event reached a model'))


def worker_hold(mime, mutation=None, *, now):
    script = """import {fixture,sendValue,emailMessage} from './workers/agent-mail/test/transport-support.js';
import {sign,bytes} from './workers/agent-mail/signing.js';
import assert from 'node:assert/strict';
let input=''; for await (const c of process.stdin) input+=c;
const {mime,token,mutation,now}=JSON.parse(input); const f=await fixture(); f.clock.now=now;
// Retained v2 hold/replay contract; the public Worker now exclusively serves pull.
const sent=await f.http.fetch(await f.request('/v1/mail/send',sendValue({reply_token:token})),f.env);
assert.equal(sent.status,200);
const out=await sent.json();
const options=mime==='signed' ? {
 type:'multipart/signed; boundary=s; protocol="application/pgp-signature"',
 text:'--s\\r\\nContent-Type: text/plain\\r\\n\\r\\n\\r\\n--s\\r\\nContent-Type: application/pgp-signature\\r\\n\\r\\nNEVER-STORE-SIGNATURE\\r\\n--s--'
} : {text:''};
const accepted=await f.legacy.email(emailMessage({outId:out.message_id,token,...options}),f.env);
const queued=await f.ledger.first('SELECT * FROM events');
// Capture the real replay request, including its fresh signature, without a network call.
await f.legacy.scheduled({},f.env); const d=f.delivered[0];
let raw=new TextDecoder().decode(d.body); let headers=d.headers;
if (mutation) {
 const p=JSON.parse(raw);
 if (mutation==='tamper') p.reason='other_hold';
 if (mutation==='body') p.text='NEVER-DELIVER-BODY';
 if (mutation==='version') p.version=99;
 raw=JSON.stringify(p);
 if (mutation!=='tamper') headers=await sign({...f.serviceKeys,key_id:'sk1',install_id:'install1',
  generation:1,direction:'service-to-install',path:'/api/agent-mail/ingress',body:bytes(raw),now:f.clock.now});
}
console.log(JSON.stringify({public_key:f.serviceKeys.publicKey,accepted,queued,
 headers:Object.fromEntries(headers),raw}));
"""
    child = subprocess.run(['node', '--input-type=module', '-e', script],
                           input=json.dumps(dict(mime=mime, token=TOKEN, mutation=mutation, now=now)), text=True,
                           capture_output=True, check=False, timeout=20,
                           cwd=Path(__file__).resolve().parents[1])
    assert child.returncode == 0, f'Node child exited {child.returncode}:\n{child.stderr}'
    return json.loads(child.stdout)


def worker_ingress(setup, result):
    store = MailStore(setup[0].store.path.parent / 'worker-held.sqlite3', clock=setup[1])
    store.register('install1')
    store.bind_thread('install1', TOKEN, 'project1', 'session1')
    ingress = MailIngress(store)
    ingress.pin_service(replace(setup[3], key_id='sk1', public_key=result['public_key']))
    ingress.bind_transport('install1', 1, 'thread1', TOKEN)
    return ingress


@pytest.mark.parametrize('mime', ['plain', 'signed'])
def test_real_worker_empty_hold_replay_and_local_custody(setup, worker_clock, mime):
    result = worker_hold(mime, now=worker_clock)
    assert result['accepted']['status'] == 'queued'
    assert result['queued']['state'] == 'pending'
    assert 'NEVER-STORE-SIGNATURE' not in result['queued']['payload']
    payload = json.loads(result['raw'])
    assert payload['version'] == 2 and payload['reason'] == 'empty_body'
    assert 'text' not in payload
    assert payload['body_digest'] == hashlib.sha256(b'').hexdigest()
    ingress = worker_ingress(setup, result)
    store = ingress.store
    receipt = ingress.receive(result['headers'], result['raw'].encode())
    assert receipt['custody'] == 'durable'
    row = store.get_event(receipt['event_id'], include_content=True)
    assert (row['state'], row['reason'], row['body'], row['body_len'], row['laundered']) == (
        BLOCKED, 'empty_body', None, 0, None)
    assert (row['project_id'], row['session_id']) == ('project1', 'session1')
    reopened = MailStore(store.path, clock=setup[1])
    assert reopened.list_events()[0]['reason'] == 'empty_body'
    assert MailLaunder(reopened, enabled=True).process_next()['state'] == 'idle'
    assert reopened.claim_for_submission() is None
    with pytest.raises(ValueError, match='nothing to release'):
        reopened.release_hold(row['event_id'])


@pytest.mark.parametrize('mutation', ['tamper', 'body', 'version'])
def test_worker_signed_held_negative_variants_refused(setup, worker_clock, mutation):
    result = worker_hold('signed', mutation, now=worker_clock)
    ingress = worker_ingress(setup, result)
    with pytest.raises(IngressRefused):
        ingress.receive(result['headers'], result['raw'].encode())
    assert count(ingress, 'events') == count(ingress, 'mail_ingress_nonces') == 0


def test_held_custody_retry_dedupes_and_blocks_later_submission(setup):
    ingress, _, private, _ = setup
    value = held()
    receipt = ingress.receive(*signed(private, value))
    assert ingress.receive(*signed(private, value, nonce='b'*32)) == receipt
    assert count(ingress, 'events') == 1
    ingress.store.insert_event(event_id='later', registration_id='install1',
                               thread_token=TOKEN, message_id='<later@example.test>', body='Thanks')
    claim = ingress.store.claim_for_laundering()
    ingress.store.complete_laundering('later', claim['fence_token'], {'reply_text': 'Thanks'})
    assert ingress.store.claim_for_submission() is None
    ingress.store.reject_event(value['event_id'], 'owner_rejected')
    assert ingress.store.claim_for_submission()['event_id'] == 'later'


@pytest.mark.parametrize('field,value', [
    ('reason', 'different_hold'), ('version', 1), ('event_id', 'b'*64),
    ('thread_id', 'other'), ('install_id', 'other'), ('generation', 2),
    ('body_digest', 'b'*64), ('received_at', 1794249600001),
    ('expires_at', 1794249700000), ('incoming_message_id', 'other@example.test'),
    ('auth', {}),
])
def test_tampered_held_envelope_refused_before_nonce(setup, field, value):
    ingress, _, private, _ = setup
    payload = held()
    headers, _ = signed(private, payload)
    payload[field] = value
    with pytest.raises(IngressRefused):
        ingress.receive(headers, json.dumps(payload, separators=(',', ':')).encode())
    assert count(ingress, 'events') == count(ingress, 'mail_ingress_nonces') == 0


@pytest.mark.parametrize('extra', [{'text': ''}, {'text': 'BODY'}, {'body': 'BODY'},
                                  {'reply_text': 'BODY'}, {'reason': 'unknown'},
                                  {'version': 3}, {'version': '2'}, {'version': True},
                                  {'body_digest': 'b'*64}])
def test_even_valid_signature_refuses_body_unknown_version_or_reason(setup, extra):
    ingress, _, private, _ = setup
    payload = held()
    payload.update(extra)
    with pytest.raises(IngressRefused):
        ingress.receive(*signed(private, payload))
    assert count(ingress, 'events') == count(ingress, 'mail_ingress_nonces') == 0
