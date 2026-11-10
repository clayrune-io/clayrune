"""Cross-language gates (7d-2b): Python-signed capabilities verified by the JS verifier, and a JS-signed
service call verified by the Python service-auth verifier. Skipped only when `node` is not installed."""
from __future__ import annotations

import json
import shutil

import pytest

from control_plane.app.mail_service_auth import ServiceKeyring, verify_service_call
from control_plane.tests.delivery_vectors import build_vectors
from control_plane.tests.mail_delivery_support import run_node

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

VERIFY = """
import { verifyDeliveryCapability } from './workers/agent-mail/delivery-capability.js';
const d = JSON.parse(await new Promise((res) => { let s = ''; process.stdin.on('data', (c) => s += c); process.stdin.on('end', () => res(s)); }));
const trust = { issuer: d.issuer, jwksUrl: d.jwks_url, jwks: d.jwks };
const out = [];
for (const v of d.vectors) {
  try { await verifyDeliveryCapability(v.token, trust, v.request, () => v.now_ms); out.push('ok'); }
  catch (e) { out.push(e.message); }
}
console.log(JSON.stringify(out));
"""

SIGN = """
import { createCpClient } from './workers/agent-mail/cp-client.js';
import { base64url } from './workers/agent-mail/signing.js';
const k = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
const privateKey = base64url(await crypto.subtle.exportKey('pkcs8', k.privateKey));
const publicKey = base64url(await crypto.subtle.exportKey('raw', k.publicKey));
let seen;
const fetcher = async (url, init) => { seen = { url, headers: Object.fromEntries(init.headers.entries()), body: Buffer.from(init.body).toString('base64') };
  return new Response('{}', { status: 200 }); };
const client = createCpClient({ issuer: 'https://authority.example.com', key_id: 'service1', privateKey, fetcher, clock: () => Number(process.argv[1] ?? Date.now()) });
await client.issue({ install_id: 'install1', generation: 4, mode: 'mail', req_digest: 'cd'.repeat(32), mail_nonce: 'ab'.repeat(16), mail_key_id: 'mail1' });
console.log(JSON.stringify({ publicKey, ...seen }));
"""


def test_python_signed_vectors_verify_in_the_js_verifier_with_the_expected_outcome(mem_firestore):
    data = build_vectors(mem_firestore)
    res = run_node(VERIFY, data)
    assert res.returncode == 0, res.stderr
    actual = json.loads(res.stdout)
    wrong = [(v["name"], v["expect"], a) for v, a in zip(data["vectors"], actual) if v["expect"] != a]
    assert not wrong
    assert len(actual) >= 45 and actual.count("ok") >= 3


def test_js_signed_service_call_verifies_in_python_and_binds_scope(mem_firestore):
    import base64
    import time
    now = int(time.time() * 1000)
    res = run_node(SIGN.replace("process.argv[1] ?? ", ""), {})
    assert res.returncode == 0, res.stderr
    seen = json.loads(res.stdout)
    pub = base64.urlsafe_b64decode(seen["publicKey"] + "==")
    body = base64.b64decode(seen["body"])
    ring = ServiceKeyring({"service1": pub})
    claimed = []
    call = verify_service_call(seen["headers"], body, method="POST", path="/v1/mail-delivery/issue", keyring=ring,
                               claim_nonce=lambda k, n, e: claimed.append(n) or True, clock_ms=lambda: now)
    assert (call.key_id, call.install_id, call.generation, call.path) == ("service1", "install1", "4", "/v1/mail-delivery/issue")
    assert json.loads(body)["generation"] == "4" and claimed == [call.nonce]
    assert seen["url"] == "https://authority.example.com/v1/mail-delivery/issue"
    with pytest.raises(Exception):
        verify_service_call(seen["headers"], body, method="POST", path="/v1/mail-delivery/lease", keyring=ring,
                            claim_nonce=lambda *_: True, clock_ms=lambda: now)
