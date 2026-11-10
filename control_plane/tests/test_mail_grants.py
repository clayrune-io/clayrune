"""Synthetic authority snapshots and ephemeral test keys only. No service clients."""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
import subprocess
import traceback

import pytest
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives import serialization

from control_plane.app.mail_enrollment_transcript import b64u, canonical_r
from control_plane.app.mail_grant_signing import MailGrantKey, PURPOSE
from control_plane.app.mail_grants import MailGrantIssuer, Snapshot, GrantRefused

NOW = 1_794_249_600_123
TUNNEL = "12345678-1234-1234-1234-123456789abc"


class Reader:
    def __init__(self, public):
        self.rows = {
            "device": {"device_id": "device1", "user_id": "account1", "device_pub_b64": base64.b64encode(public).decode(),
                       "revoked_at": None, "provisioning_state": "active", "cf_tunnel_uuid": TUNNEL,
                       "cf_dns_record_id": "dns1", "hostname_claim": "device.example.com",
                       "cf_tunnel_token": "test-only-do-not-serialize"},
            "account": {"user_id": "account1", "suspended": False},
            "tunnel": {"id": TUNNEL, "deleted_at": None, "config_src": "cloudflare"},
            "ingress": {"tunnel_id": TUNNEL, "config": {"ingress": [
                {"hostname": "device.example.com", "service": "http://localhost:5199"}, {"service": "http_status:404"}]}},
            "dns": {"id": "dns1", "type": "CNAME", "name": "device.example.com", "proxied": True,
                    "content": TUNNEL + ".cfargotunnel.com"},
        }
        self.calls = []
        self.fail = None
        self.no_version = None

    async def read(self, kind, identifier):
        self.calls.append((kind, identifier))
        if self.fail == kind:
            raise OSError("provider-test-only-secret")
        return Snapshot(copy.deepcopy(self.rows[kind]), "" if self.no_version == kind else kind + "-v1")

    async def device(self, identifier): return await self.read("device", identifier)
    async def account(self, identifier): return await self.read("account", identifier)
    async def tunnel(self, identifier): return await self.read("tunnel", identifier)
    async def ingress(self, identifier): return await self.read("ingress", identifier)
    async def dns(self, identifier): return await self.read("dns", identifier)


@pytest.fixture
def fixture():
    device = ed25519.Ed25519PrivateKey.generate()
    mail = ed25519.Ed25519PrivateKey.generate()
    public = lambda key: key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    reader = Reader(public(device))
    key = MailGrantKey("mail-grant-test", ec.generate_private_key(ec.SECP256R1()), PURPOSE)
    time = [NOW]
    issuer = MailGrantIssuer(reader=reader, key=key, issuer="https://authority.example.com",
                            service_origin="https://mail.example.com", enabled=True, clock=lambda: time[0])
    r = ["clayrune-mail-enrollment", "1", "https://mail.example.com", "flow1", "challenge1", "a" * 64,
         str(NOW + 900_000), "install1", "0", "device1", b64u(public(device)), "mail1", b64u(public(mail)),
         "b" * 64, TUNNEL, "https://device.example.com", "/api/agent-mail/ingress", "1", "service1", "c" * 64]
    async def issue(transcript=None, **kwargs):
        transcript = r if transcript is None else transcript
        raw = canonical_r(transcript)
        return await issuer.issue(transcript, device_signature=kwargs.get("device_signature", b64u(device.sign(raw))),
                                  mail_signature=kwargs.get("mail_signature", b64u(mail.sign(raw))))
    return reader, key, issuer, r, time, issue


def run(coro):
    import asyncio
    return asyncio.run(coro)


def payload(token):
    return json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))


def test_mail_grant_cross_language_worker_accepts_real_cp_signature(fixture):
    reader, key, issuer, r, time, issue = fixture
    token = run(issue())
    claims = payload(token)
    assert claims["aud"] == claims["token_use"] == PURPOSE
    assert claims["checked_at_ms"] == NOW and claims["exp"] - claims["iat"] == 60
    assert len(claims["record_version"]) == 64
    assert "test-only-do-not-serialize" not in json.dumps(claims)
    assert set(kind for kind, _ in reader.calls) == {"device", "account", "tunnel", "ingress", "dns"}
    root = Path(__file__).resolve().parents[2]
    script = """
import { verifyGrant } from './workers/agent-mail/enrollment-grant.js';
import { canonicalR } from './workers/agent-mail/enrollment-transcript.js';
let data=''; for await (const chunk of process.stdin) data+=chunk;
const v=JSON.parse(data);
await verifyGrant(v.token,v.r,v.trust,()=>v.now);
process.stdout.write(Buffer.from(canonicalR(v.r)).toString('hex'));
"""
    trust = {"issuer": issuer.issuer, "serviceOrigin": issuer.service_origin,
             "jwksUrl": issuer.issuer + "/v1/mail-enrollment/jwks", "jwks": {"keys": [key.public_jwk()]}}
    result = subprocess.run(["node", "--input-type=module", "-e", script],
                            input=json.dumps({"token": token, "r": r, "trust": trust, "now": time[0]}),
                            text=True, capture_output=True, cwd=root, timeout=15)
    assert result.returncode == 0, result.stderr
    assert bytes.fromhex(result.stdout) == canonical_r(r)


def test_disabled_has_zero_reads(fixture):
    reader, _, issuer, _, _, issue = fixture
    issuer.enabled = False
    with pytest.raises(GrantRefused, match="mail_grant_disabled"): run(issue())
    assert reader.calls == []


@pytest.mark.parametrize("kind,field,value", [
    ("device", "revoked_at", "revoked"), ("device", "provisioning_state", "pending"),
    ("device", "device_id", "other"), ("device", "user_id", None), ("device", "device_pub_b64", "bad"),
    ("device", "cf_tunnel_uuid", "other"), ("device", "cf_dns_record_id", None),
    ("device", "hostname_claim", "other.example.com"), ("account", "suspended", True),
    ("account", "suspended", None), ("account", "user_id", "other"),
    ("tunnel", "deleted_at", "yesterday"), ("tunnel", "config_src", "local"), ("tunnel", "id", "other"),
    ("ingress", "tunnel_id", "other"), ("ingress", "config", {"ingress": []}),
    ("dns", "id", "other"), ("dns", "type", "A"), ("dns", "proxied", False),
    ("dns", "name", "other.example.com"), ("dns", "content", "attacker.example.com"),
])
def test_bad_authoritative_snapshot_refused(fixture, kind, field, value):
    reader, _, _, _, _, issue = fixture
    reader.rows[kind][field] = value
    with pytest.raises(GrantRefused): run(issue())


@pytest.mark.parametrize("kind", ["device", "account", "tunnel", "ingress", "dns"])
def test_authority_failure_and_missing_version_fail_closed(fixture, kind):
    reader, _, _, _, _, issue = fixture
    reader.fail = kind
    with pytest.raises(GrantRefused, match="authority_unavailable") as error: run(issue())
    assert "secret" not in str(error.value)
    assert "provider-test-only-secret" not in "".join(traceback.format_exception(error.value))
    reader.fail = None
    reader.no_version = kind
    with pytest.raises(GrantRefused, match="authority_version_required"): run(issue())


def test_dual_proofs_required_before_reads(fixture):
    reader, _, _, r, _, issue = fixture
    with pytest.raises(GrantRefused, match="key_proof_required"): run(issue(mail_signature="bad"))
    with pytest.raises(GrantRefused, match="key_proof_required"): run(issue(device_signature="bad"))
    assert reader.calls == []
    r[5] = "b" * 64
    assert payload(run(issue()))["r_sha256"]


def test_freshness_from_read_start_and_no_slow_signing(fixture):
    reader, _, _, _, time, issue = fixture
    original = reader.read
    async def slow(*args):
        result = await original(*args)
        time[0] += 16_000
        return result
    reader.read = slow
    with pytest.raises(GrantRefused, match="authority_check_expired"): run(issue())


def test_service_and_challenge_time_are_bound(fixture):
    _, _, _, r, time, issue = fixture
    r[2] = "https://other.example.com"
    with pytest.raises(GrantRefused, match="challenge_unavailable"): run(issue())
    r[2] = "https://mail.example.com"
    time[0] += 900_000
    with pytest.raises(GrantRefused, match="challenge_unavailable"): run(issue())


def test_conflicting_or_redirected_ingress_refused(fixture):
    reader, _, _, _, _, issue = fixture
    reader.rows["ingress"]["config"]["ingress"][0]["path"] = "/dashboard"
    with pytest.raises(GrantRefused, match="cloudflare_mapping_mismatch"): run(issue())


@pytest.mark.parametrize("kind,field", [("device", "revoked_at"), ("tunnel", "deleted_at")])
def test_missing_revocation_schema_is_not_proof_of_nonrevocation(fixture, kind, field):
    reader, _, _, _, _, issue = fixture
    del reader.rows[kind][field]
    with pytest.raises(GrantRefused): run(issue())


def test_distinct_key_purpose_and_curve_required():
    for kid, purpose, curve in [("dashboard", PURPOSE, ec.SECP256R1()),
                                ("mail-grant-test", "dashboard", ec.SECP256R1()),
                                ("mail-grant-test", PURPOSE, ec.SECP384R1())]:
        with pytest.raises(ValueError, match="mail_grant_key_required"):
            MailGrantKey(kid, ec.generate_private_key(curve), purpose)


def test_signer_never_mints_dashboard_tokens(fixture):
    _, key, _, _, _, _ = fixture
    with pytest.raises(ValueError, match="wrong_signing_purpose"):
        key.sign({"aud": "dashboard", "token_use": "dashboard"})
