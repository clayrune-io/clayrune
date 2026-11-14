"""MC-1166 thin-slice U7: the Python install and the real Worker, run together with no network.

Under test, unmodified: workers/agent-mail/worker.js and PullSocketMailbox under Miniflare with a
real D1 (schema.sql plus every migration plus the SQL U2's provisioning tool wrote), and the Python
PullClient / Outbound / PullReceiver / MailLaunder / MailWake over the same shared store.

What this file supplies (test plumbing only, no production file is touched):
* a Node harness started with ``node --input-type=module -e`` (the pattern the other
  test_agent_mail_*.py files use) that wraps worker.js: it rewrites the request URL to the reviewed
  service origin, adds the ``cf-connecting-ip`` header Cloudflare's edge would add, records what the
  Worker hands to ``MAIL_SEND``, and exposes ``/__x/inject`` (calls the real ``email()``) and
  ``/__x/sql`` (reads D1). ``postal-mime`` is bundled by wrangler in production, so the harness
  rewrites that one bare import for workerd.
* an HttpsTransport opener and a websocket factory that point the reviewed ``https://`` origin at
  Miniflare's 127.0.0.1 listener. Everything else is the code that ships.
* the model boundary of laundering (``agent_runtime.run_text_transform``) is stubbed, as in
  test_agent_mail_launder.py, so the real digest validation still runs; the follow-up is the real
  ``agent_followup`` writing to a fake session pipe.

Needs workers/agent-mail/node_modules (Miniflare); skips if it is absent.
"""
import base64
import hashlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from test_agent_mail_wake import setup, env  # noqa: F401  (session + route patching fixtures)
from mc import agent_mail_factory as fac
from mc import agent_runtime
from mc.agent_mail_extract import WARNING
from mc import agent_mail_pull_client as pull_client
from mc.agent_mail_launder import MailLaunder
from mc.agent_mail_service_client import HttpsTransport
from mc.agent_mail_service_manifest import ServiceKeyCandidate
from mc.agent_mail_store import LOCAL_RECEIVED, SUBMITTED
from mc.agent_mail_wake import MailWake

REPO = Path(__file__).resolve().parent.parent
WORKER = REPO / 'workers/agent-mail'
spec = importlib.util.spec_from_file_location('agent_mail_provision_install', REPO / 'tools/agent-mail-provision-install.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

ORIGIN = 'https://mail.example.com'
DOMAIN = 'clayrune.example'
OWNER = 'owner@example.com'
MASTER = b'M' * 32
DIGEST = 'd' * 64
KEY_ID = 'service-key'
SERVICE_SEED = b'V' * 32
REPLY = 'Friday works for me.'
PKCS8_PREFIX = bytes.fromhex('302e020100300506032b657004220420')

pytestmark = pytest.mark.skipif(shutil.which('node') is None or not (WORKER / 'node_modules/miniflare').is_dir(),
                                reason='node or workers/agent-mail/node_modules (Miniflare) not available')


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


HARNESS = r'''
import { readFileSync, readdirSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { Miniflare, Log, LogLevel } from 'miniflare';
const root = process.cwd().replaceAll('\\', '/') + '/';
const rl = createInterface({ input: process.stdin });
const cfg = JSON.parse(await new Promise((resolve) => rl.once('line', resolve)));
const now = Date.now();
const wrapper = [
  "import worker from './worker.js';",
  "import { PullSocketMailbox } from './pull-socket-mailbox.js';",
  "export { PullSocketMailbox };",
  "const ORIGIN = " + JSON.stringify(cfg.origin) + ";",
  "const sent = [];",
  "const send = { async send(m) { sent.push(m); return { messageId: 'provider-' + sent.length }; } };",
  "const withSend = (env) => ({ ...env, MAIL_SEND: send });",
  "export default {",
  "  async fetch(request, env) {",
  "    const u = new URL(request.url);",
  "    if (u.pathname === '/__x/sent') return Response.json(sent);",
  "    if (u.pathname === '/__x/sql') {",
  "      const q = await request.json();",
  "      const r = await env.MAIL_DB.prepare(q.sql).bind(...(q.args || [])).all();",
  "      return Response.json(r.results);",
  "    }",
  "    if (u.pathname === '/__x/inject') {",
  "      const m = await request.json();",
  "      const fields = ['Authentication-Results: ' + m.ar, 'From: ' + m.from, 'To: ' + m.to,",
  "        'In-Reply-To: <' + m.inReplyTo + '>', 'Message-ID: <' + m.messageId + '>',",
  "        'Content-Type: text/plain; charset=utf-8'];",
  "      const raw = new TextEncoder().encode([...fields, '', m.text].join('\\r\\n'));",
  "      const headers = new Headers();",
  "      for (const f of fields) { const p = f.indexOf(':'); headers.append(f.slice(0, p), f.slice(p + 1).trim()); }",
  "      const rejects = [];",
  "      const message = { to: m.to, from: m.envelopeFrom, rawSize: raw.length, raw: new Blob([raw]).stream(),",
  "        headers, setReject: (s) => rejects.push(s) };",
  "      await worker.email(message, withSend(env));",
  "      return Response.json({ rejects });",
  "    }",
  "    const headers = new Headers(request.headers);",
  "    headers.set('cf-connecting-ip', '192.0.2.10');",
  "    const routed = new Request(ORIGIN + u.pathname + u.search, new Request(request, { headers }));",
  "    return worker.fetch(routed, withSend(env));",
  "  },",
  "};",
].join('\n');
const modules = [{ type: 'ESModule', path: root + 'slice-e2e-entry.js', contents: wrapper }];
const seen = new Set();
function add(name) {
  if (seen.has(name) || name.startsWith('postal-mime/')) return; seen.add(name);
  let contents = readFileSync(root + name, 'utf8');
  if (name === 'strip.js') contents = contents.replace("from 'postal-mime'", "from './postal-mime/postal-mime.js'");
  modules.push({ type: 'ESModule', path: root + name, contents });
  for (const m of contents.matchAll(/from ['"]\.\/([^'"]+)['"]/g)) add(m[1]);
}
['worker.js', 'pull-socket-mailbox.js'].forEach(add);
const pm = 'node_modules/postal-mime/dist/esm/';
for (const f of readdirSync(pm).filter((n) => n.endsWith('.js'))) {
  modules.push({ type: 'ESModule', path: root + 'postal-mime/' + f, contents: readFileSync(pm + f, 'utf8') });
}
const manifest = JSON.stringify({ manifest: ['clayrune-mail-service-manifest', '1', cfg.origin, String(cfg.sequence),
  String(now - 10000), String(now + 172800000), cfg.keyId, cfg.publicKey, String(now - 10000), String(now + 259200000), []],
  signature: 'A'.repeat(86) });
const mf = new Miniflare({ name: 'slice-e2e', modules, compatibilityDate: '2026-07-01',
  host: '127.0.0.1', port: 0,
  durableObjects: { MAIL_PULL_MAILBOX: 'PullSocketMailbox' }, d1Databases: ['MAIL_DB'],
  bindings: { MAIL_ENABLED: 'true', MAIL_INGRESS_VERIFIED: 'true', MAIL_SERVICE_ORIGIN: cfg.origin,
    MAIL_DOMAIN: cfg.domain, MAIL_FROM: 'mail@' + cfg.domain, MAIL_SERVICE_KEY_ID: cfg.keyId,
    MAIL_SERVICE_PRIVATE_KEY: cfg.privateKey, MAIL_SERVICE_PIN_EXPIRES_MS: String(now + 86400000),
    MAIL_SERVICE_MANIFEST: manifest },
  log: new Log(LogLevel.ERROR) });
const db = await mf.getD1Database('MAIL_DB');
const flat = (sql) => sql.replace(/--[^\n]*/g, '').replace(/[\r\n]/g, ' ');
const dir = 'migrations/';
for (const sql of [readFileSync('schema.sql', 'utf8'),
  ...readdirSync(dir).filter((n) => /^\d+.*\.sql$/.test(n)).sort().map((n) => readFileSync(dir + n, 'utf8')),
  readFileSync(cfg.provisionSql, 'utf8')]) await db.exec(flat(sql));
const url = await mf.ready;
console.log(JSON.stringify({ port: Number(url.port) }));
rl.on('close', async () => { await mf.dispose(); process.exit(0); });
'''


class LocalOpener:
    """urllib opener seam of HttpsTransport: the reviewed https origin is served by Miniflare on loopback."""

    def __init__(self):
        self.port = 0
        self._real = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def open(self, request, timeout=None):
        original = request.full_url
        assert original.startswith(ORIGIN + '/'), original
        local = urllib.request.Request('http://127.0.0.1:%d%s' % (self.port, original[len(ORIGIN):]),
                                       data=request.data, method=request.get_method(), headers=dict(request.header_items()))
        reply = self._real.open(local, timeout=timeout)
        reply.geturl = lambda: original  # HttpsTransport insists the reply is for the origin it asked.
        return reply


class Harness:
    def __init__(self, tmp_path, provision, key_public, key_private):
        self.opener = LocalOpener()
        # Run from a file: under ``node -e`` Miniflare's first binding call never returns on this stack, so the
        # harness is written out and Miniflare is imported by absolute URL (the file lives outside workers/).
        script = tmp_path / 'slice-e2e-harness.mjs'
        script.write_text(HARNESS.replace("from 'miniflare'", "from '%s'" % (WORKER / 'node_modules/miniflare/dist/src/index.js').as_uri()),
                          encoding='utf-8')
        self.proc = subprocess.Popen(['node', str(script)], cwd=WORKER, text=True,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.proc.stdin.write(json.dumps(dict(origin=ORIGIN, domain=DOMAIN, keyId=KEY_ID, sequence=7,
                                              publicKey=key_public, privateKey=key_private,
                                              provisionSql=str(provision['sql_path']))) + '\n')
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError('harness did not start: ' + self.proc.stderr.read())
        self.port = self.opener.port = json.loads(line)['port']

    def call(self, op, payload=None):
        request = urllib.request.Request('http://127.0.0.1:%d/__x/%s' % (self.port, op), method='POST',
                                         data=json.dumps(payload or {}).encode(),
                                         headers={'content-type': 'application/json'})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=20) as reply:
            return json.load(reply)

    def sql(self, sql, *args):
        return self.call('sql', dict(sql=sql, args=list(args)))

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=20)
        except Exception:
            self.proc.kill()  # our own child, by handle
            self.proc.wait(timeout=10)
        finally:
            self.proc.stdout.close()
            self.proc.stderr.close()


class LocalSocket(pull_client._Socket):
    """The real client socket, reaching Miniflare instead of the DNS name behind the pinned origin."""
    port = 0

    def connect(self, url, **kwargs):
        prefix = 'wss://mail.example.com'
        assert url.startswith(prefix + '/'), url
        return super().connect('ws://127.0.0.1:%d%s' % (self.port, url[len(prefix):]), **kwargs)


def wait_until(predicate, what, timeout=25):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = predicate()
        if last:
            return last
        time.sleep(.05)
    pytest.fail('timed out waiting for ' + what)


@pytest.fixture
def world(tmp_path, setup):  # noqa: F811
    service_public = Ed25519PrivateKey.from_private_bytes(SERVICE_SEED).public_key().public_bytes_raw()
    pin = ServiceKeyCandidate(ORIGIN, 7, KEY_ID, b64(service_public), hashlib.sha256(service_public).hexdigest(),
                              (), int(time.time() * 1000) + 3_600_000)
    root_public = b64(Ed25519PrivateKey.from_private_bytes(b'R' * 32).public_key().public_bytes_raw())
    opener = LocalOpener()
    config = {fac.ORIGIN_KEY: ORIGIN, fac.ROOT_KEY: root_public,
              'agent_mail_enrollment_enabled': True, 'agent_mail_outbound_enabled': True}
    factory = fac.MailFactory(tmp_path / 'mail', clock=time.time, key=lambda: MASTER, config=config,
                              pull_transport=HttpsTransport(origin=ORIGIN, opener=opener),
                              reviewed_service=lambda: (pin, DIGEST))
    # U2: the operator's hand-provisioning of the one install.
    provision = tool.provision(factory, owner=OWNER, mail_domain=DOMAIN, sql_path=tmp_path / 'worker-d1.sql',
                               authorize=lambda: True)
    config.update(provision['config'])
    harness = Harness(tmp_path, provision, b64(service_public), b64(PKCS8_PREFIX + SERVICE_SEED))
    opener.port = harness.port
    LocalSocket.port = harness.port
    client = factory.pull_client()
    client.socket_factory = LocalSocket
    other = dict(setup.session, session_id='other-session', claude_session_id='other-native',
                 proc=SimpleNamespace(pid=-992, poll=lambda: None, stdin=io.StringIO()))
    setup.ar.agent_sessions['other-session'] = other
    try:
        yield SimpleNamespace(factory=factory, harness=harness, client=client, provision=provision, setup=setup,
                              other=other, store=factory.require_pull_store())
    finally:
        client.stop()
        harness.close()


def connect(w):
    w.client.start()
    wait_until(lambda: w.store.path.exists() and _connected(w), 'socket hello')


def _connected(w):
    with w.store._read() as db:
        row = db.execute('SELECT epoch FROM mail_pull_connections WHERE install_id=?',
                         (w.provision['install_id'],)).fetchone()
    return bool(row and row['epoch'] > 0)


def send_note(w, request_id='note1'):
    out = w.factory.outbound()
    prepared = out.prepare('proj1', w.setup.session['session_id'], dict(
        request_id=request_id, kind='note', subject='Slice proof', body='Does Friday work?'))
    assert prepared['state'] == 'ready'
    return out, out.send(prepared['intent_id'])


def reply_token(w, intent_id):
    out = w.factory.outbound()
    with w.store._read() as db:
        row = dict(db.execute('SELECT * FROM mail_outbound_intents WHERE intent_id=?', (intent_id,)).fetchone())
    row['payload'] = row['payload'] or None
    return row


def test_provisioned_install_gets_a_ticket_and_the_socket_says_hello(world):
    connect(world)
    install = world.provision['install_id']
    with world.store._read() as db:
        conn = dict(db.execute('SELECT * FROM mail_pull_connections WHERE install_id=?', (install,)).fetchone())
    assert (conn['generation'], conn['owner_generation'], conn['connection_id'] is not None) == (1, 1, True)
    tickets = world.harness.sql('SELECT install_id,state FROM pull_tickets')
    assert tickets == [dict(install_id=install, state='consumed')]  # signed ticket issued, then spent on the socket


def test_outbound_note_reaches_send_and_mail_send_sees_owner_only(world):
    connect(world)
    out, sent = send_note(world)
    assert sent['state'] == 'accepted' and re.fullmatch(r'[0-9a-f]{64}@' + re.escape(DOMAIN), sent['message_id'])
    (message,) = world.harness.call('sent')
    local = world.provision['address'].split('@')[0]
    # Exactly one recipient, the owner; the reply address carries the thread token; nobody else is addressed.
    assert message['to'] == OWNER
    assert re.fullmatch(re.escape(local) + r'\+[a-f0-9]{32}@' + re.escape(DOMAIN), message['replyTo'])
    assert not {'cc', 'bcc'} & set(message)
    assert set(message) == {'from', 'to', 'replyTo', 'subject', 'text', 'headers'}
    assert message['from'] == 'mail@' + DOMAIN
    assert 'Does Friday work?' in message['text']
    intents = world.harness.sql('SELECT state,message_id FROM pull_intents')
    assert intents == [dict(state='accepted', message_id=sent['message_id'])]
    (thread,) = world.harness.sql('SELECT token_hash FROM pull_threads')
    token = message['replyTo'].split('+')[1].split('@')[0]
    assert thread['token_hash'] == hashlib.sha256(token.encode()).hexdigest()


THREAD_EXPIRY_BUG = ('REAL WIRING BUG 1, reported not fixed: the Worker stamps the thread expiry at /v1/pull/send time '
                     '(ledger-pull.js:178, now+threadTTL) while Outbound.prepare stamps the local one at prepare time '
                     '(agent_mail_outbound.py:236) and PullReceiver._thread (agent_mail_pull_receiver.py:237) demands '
                     'equality, so every reply is refused unknown_thread: no custody, no ack.')
UNSOLICITED_STATUS_BUG = ('REAL WIRING BUG 2, reported not fixed: when email() wakes a connected socket, '
                          'PullMailbox.wake() (pull-mailbox.js:236) drains with a Worker-made request id and sends '
                          'status after the event; PullClient._handle (agent_mail_pull_client.py:524) requires the '
                          'request id to be one it sent, so it halts protocol_failure before ack_committed is read '
                          'and the Worker copy is never retired.')


def _all(store, sql):
    with store._read() as db:
        return db.execute(sql).fetchall()


def align_thread_expiry(w):
    """Test-side stand-in for the missing agreement (bug 1): copy the local thread expiry onto the Worker's row."""
    (local,) = _all(w.store, 'SELECT thread_expires_ms,dedupe_until_ms FROM mail_pull_threads')
    w.harness.sql('UPDATE pull_threads SET expires_ms=?,dedupe_until_ms=?', local['thread_expires_ms'], local['dedupe_until_ms'])


def owner_reply(w, align):
    """Connect, send the note, optionally align the expiry, then Ron's reply arrives through email()."""
    connect(w)
    out, sent = send_note(w)
    (message,) = w.harness.call('sent')
    if align:
        align_thread_expiry(w)
    result = w.harness.call('inject', dict(
        to=message['replyTo'], envelopeFrom=OWNER, inReplyTo=sent['message_id'], messageId='reply-1@example.com',
        text=REPLY, ar=f'mx.cloudflare.net; spf=pass smtp.mailfrom={OWNER}; dkim=pass header.d=example.com; '
                       'dmarc=pass header.from=example.com', **{'from': OWNER}))
    assert result == {'rejects': []}
    return sent


def custodied_event(w):
    """The installed copy: a mail_pull_receipts row, i.e. the event passed PullReceiver and is durable locally."""
    return wait_until(lambda: (_all(w.store, 'SELECT * FROM mail_pull_receipts') or [None])[0], 'local custody')


@pytest.mark.xfail(strict=True, reason=THREAD_EXPIRY_BUG)
def test_reply_over_the_real_send_path_reaches_local_custody(world):
    owner_reply(world, align=False)
    custodied_event(world)


@pytest.mark.xfail(strict=True, reason=UNSOLICITED_STATUS_BUG)
def test_custody_ack_is_committed_and_the_worker_copy_retired(world):
    install = world.provision['install_id']
    owner_reply(world, align=True)
    got = custodied_event(world)
    assert (got['install_id'], got['disposition']) == (install, 'local_received')

    def retired():
        (event,) = world.harness.sql('SELECT event_id,state,text,body_bytes,terminal_ms FROM pull_events')
        return event if event['terminal_ms'] is not None else None
    event = wait_until(retired, 'D1 event retired')
    assert event['event_id'] == got['event_id'] and (event['state'], event['text'], event['body_bytes']) == ('local_received', None, 0)
    (cloud,) = world.harness.sql('SELECT event_id FROM pull_receipts')
    assert cloud['event_id'] == got['event_id']
    assert _all(world.store, 'SELECT COUNT(*) FROM mail_pull_ack_outbox')[0][0] == 0


def test_worker_delivered_reply_is_laundered_and_wakes_the_exact_session(world, monkeypatch):
    setup = world.setup
    owner_reply(world, align=True)  # align = workaround for bug 1; bug 2 hits only after custody is durable
    got = custodied_event(world)
    local = world.store.get_event(got['event_id'], include_content=True)
    assert local['state'] == LOCAL_RECEIVED and local['session_id'] == setup.session['session_id']
    # The Worker pushed the body to the install; the install holds it, so D1 kept no text (not the ack path).
    digest = json.dumps(dict(reply_text=REPLY, segments=[dict(kind='reply', text=REPLY)], ambiguous_or_mixed=False,
                             extraction_failed=False, truncated=False, instruction_like=False))
    calls = []
    monkeypatch.setattr(agent_runtime, 'claude_oneshot_available', lambda: True)
    monkeypatch.setattr(agent_runtime, 'run_text_transform', lambda provider, **kw: calls.append(kw) or digest)
    assert MailLaunder(world.store, enabled=True).process_next()['state'] == 'ready'
    assert len(calls) == 1 and calls[0]['stdin_text'] == REPLY
    laundered = world.store.get_event(got['event_id'], include_content=True)
    assert laundered['laundered']['warning'] == WARNING

    # Wake: the real follow-up lands in the session the note was sent for, and only there.
    assert MailWake(world.store, enabled=True).process_next(setup.app)['state'] == SUBMITTED
    delivered = json.loads(setup.session['proc'].stdin.getvalue())
    assert REPLY in delivered['message']['content'] and 'Owner mail reply' in delivered['message']['content']
    assert world.other['proc'].stdin.getvalue() == ''
    after = world.store.get_event(got['event_id'])
    assert after['state'] == SUBMITTED and after['session_id'] == 'mail-session'
    assert after['turn_id'] == setup.session['_delivery_attempt_id']
