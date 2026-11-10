"""Production AuthorityReader (7d-2r): fakes only. No Firestore client, no Cloudflare, no network."""
from __future__ import annotations

import asyncio
import copy
import datetime as dt
import logging

import pytest

from control_plane.app import mail_authority_reader as mar
from control_plane.app import routes_mail_delivery as rmd
from control_plane.app.mail_authority_check import GrantRefused, check_authority
from control_plane.app.mail_authority_cloudflare import CloudflareAuthorityReads
from control_plane.app.mail_authority_firestore import (ACCOUNT_FIELDS, DEVICE_FIELDS, AuthorityReadError,
                                                        FirestoreAuthorityReads)
from control_plane.tests.mail_delivery_support import World
from control_plane.tests.test_mail_grants import NOW, TUNNEL

ACC, ZONE = "a" * 32, "b" * 32
HOST = "device.example.com"
SVC = "http://localhost:5199"
SECRET = "test-only-tunnel-token-do-not-serialize"
UPDATED = dt.datetime(2026, 11, 10, 12, 0, 0, 123456, tzinfo=dt.timezone.utc)


def run(coro):
    return asyncio.run(coro)


# ---- fakes ------------------------------------------------------------------------------


class _Snap:
    def __init__(self, row, update_time=UPDATED):
        self.exists, self._row, self.update_time = row is not None, row, update_time

    def to_dict(self):
        return copy.deepcopy(self._row)


class _Ref:
    def __init__(self, fs, col, doc):
        self.fs, self.col, self.doc = fs, col, doc

    def get(self, field_paths=None, timeout=None, **kw):
        self.fs.calls.append(("get", self.col, self.doc, tuple(field_paths or ()), timeout))
        if self.fs.fail:
            raise self.fs.fail
        row = self.fs.rows.get((self.col, self.doc))
        # A real server-side field mask would drop the rest; the fake deliberately does NOT,
        # so the test proves the adapter's own allowlist, not just the request.
        return _Snap(row, self.fs.update_time)

    def __getattr__(self, name):  # any write/query verb is a failure of the read-only contract
        raise AssertionError("read-only adapter touched DocumentReference." + name)


class _Coll:
    def __init__(self, fs, col):
        self.fs, self.col = fs, col

    def document(self, doc):
        return _Ref(self.fs, self.col, doc)

    def __getattr__(self, name):
        raise AssertionError("read-only adapter touched CollectionReference." + name)


class FakeFirestore:
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else {
            ("devices", "device1"): {
                "device_id": "device1", "user_id": "account1", "device_pub_b64": "AAAA", "revoked_at": None,
                "provisioning_state": "active", "cf_tunnel_uuid": TUNNEL, "cf_dns_record_id": "dns1",
                "hostname_claim": HOST, "cf_tunnel_token": SECRET, "enrollment_token_hash": SECRET,
                "device_name": "my laptop"},
            ("users", "account1"): {"user_id": "account1", "suspended": False, "email": "x@example.invalid"},
        }
        self.calls, self.fail, self.update_time = [], None, UPDATED

    def collection(self, name):
        return _Coll(self, name)

    def __getattr__(self, name):
        raise AssertionError("read-only adapter touched Client." + name)


class CfStub:
    """Recording GET-only transport. `routes` maps path -> result dict or an exception to raise."""

    def __init__(self):
        self.paths = []
        self.routes = {
            f"/accounts/{ACC}/cfd_tunnel/{TUNNEL}": {
                "id": TUNNEL, "created_at": "2026-01-01T00:00:00Z", "deleted_at": None, "status": "healthy",
                "remote_config": True, "connections": [{"colo_name": "x"}], "name": "n"},
            f"/accounts/{ACC}/cfd_tunnel/{TUNNEL}/configurations": {
                "tunnel_id": TUNNEL, "version": 7, "source": "cloudflare", "created_at": "t",
                "config": {"ingress": [{"hostname": HOST, "service": SVC, "originRequest": {}},
                                       {"service": "http_status:404", "originRequest": {}}]}},
            f"/zones/{ZONE}/dns_records/dns1": {
                "id": "dns1", "type": "CNAME", "name": HOST, "proxied": True, "ttl": 1,
                "content": TUNNEL + ".cfargotunnel.com", "modified_on": "2026-02-02T00:00:00.000000Z"},
        }

    async def __call__(self, path):
        self.paths.append(path)
        r = self.routes[path]
        if isinstance(r, Exception):
            raise r
        return copy.deepcopy(r)


class CfErr(Exception):
    def __init__(self, status):
        super().__init__("provider-test-only-secret")
        self.status = status


@pytest.fixture
def parts():
    fs, cf = FakeFirestore(), CfStub()
    reader = mar.ProductionAuthorityReader(
        FirestoreAuthorityReads(lambda: fs), CloudflareAuthorityReads(cf, account_id=ACC, zone_id=ZONE))
    return fs, cf, reader


def check(reader, **over):
    import base64
    kw = dict(device_id="device1", device_public_key=base64.b64decode("AAAA"), tunnel_uuid=TUNNEL,
              route_origin="https://" + HOST, expected_service=SVC)
    kw.update(over)
    return run(check_authority(reader, **kw))


# ---- Firestore half -------------------------------------------------------------------------


def test_device_row_is_projected_to_the_allowlist_and_versioned_by_update_time(parts):
    fs, _, reader = parts
    snap = run(reader.device("device1"))
    assert set(snap.data) == set(DEVICE_FIELDS)
    assert SECRET not in repr(snap) and "my laptop" not in repr(snap)
    assert snap.version == "2026-11-10T12:00:00.123456Z" or snap.version.startswith("2026-11-10T12:00:00.123456")
    kind, col, doc, fields, timeout = fs.calls[0]
    assert (kind, col, doc, fields) == ("get", "devices", "device1", DEVICE_FIELDS) and timeout


def test_account_row_projection_and_missing_rows_refuse_closed(parts):
    fs, _, reader = parts
    assert run(reader.account("account1")).data == {"user_id": "account1", "suspended": False}
    with pytest.raises(GrantRefused, match="device_unavailable"):
        run(reader.device("nope"))
    with pytest.raises(GrantRefused, match="authority_refused"):
        run(reader.account("nope"))


@pytest.mark.parametrize("bad", ["", "a/b", "../users/x", "x" * 129, "a b", None, 7])
def test_ids_that_could_change_the_document_path_never_reach_firestore(parts, bad):
    fs, _, reader = parts
    with pytest.raises(AuthorityReadError):
        run(reader.device(bad))
    with pytest.raises(AuthorityReadError):
        run(reader.account(bad))
    assert fs.calls == []


def test_firestore_failure_and_missing_update_time_refuse_as_authority_unavailable(parts, caplog):
    fs, _, reader = parts
    fs.fail = RuntimeError("provider-test-only-secret " + SECRET)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(GrantRefused, match="authority_unavailable"):
            check(reader)
    assert "provider-test-only-secret" not in caplog.text and SECRET not in caplog.text
    fs.fail, fs.update_time = None, None
    with pytest.raises(GrantRefused, match="authority_unavailable"):
        check(reader)


def test_firestore_timeout_refuses_closed():
    class Slow(FakeFirestore):
        def collection(self, name):
            import time
            time.sleep(0.5)
            return super().collection(name)

    r = FirestoreAuthorityReads(lambda: Slow(), timeout=-1.9)  # wait_for budget = timeout + 2 = 0.1 s
    with pytest.raises(AuthorityReadError, match="firestore_timeout"):
        run(r.device("device1"))


def test_never_suspended_user_is_refused_by_default_and_admitted_only_when_opted_in(parts):
    fs, cf, reader = parts
    del fs.rows[("users", "account1")]["suspended"]  # what /v1/enroll actually writes
    with pytest.raises(GrantRefused, match="authority_refused"):
        check(reader)
    opted = mar.ProductionAuthorityReader(FirestoreAuthorityReads(lambda: fs, suspended_absent_is_false=True),
                                          CloudflareAuthorityReads(cf, account_id=ACC, zone_id=ZONE))
    assert check(opted).user_id == "account1"
    fs.rows[("users", "account1")]["suspended"] = "false"  # present junk still refuses, opt-in or not
    with pytest.raises(GrantRefused, match="authority_refused"):
        check(opted)
    fs.rows[("users", "account1")]["suspended"] = True
    with pytest.raises(GrantRefused, match="authority_refused"):
        check(opted)


# ---- Cloudflare half ------------------------------------------------------------------------


def test_cloudflare_reads_are_get_only_pinned_paths_and_projected(parts):
    _, cf, reader = parts
    assert check(reader).record_version
    assert sorted(cf.paths) == sorted([
        f"/accounts/{ACC}/cfd_tunnel/{TUNNEL}", f"/accounts/{ACC}/cfd_tunnel/{TUNNEL}/configurations",
        f"/zones/{ZONE}/dns_records/dns1"])
    t = run(reader.tunnel(TUNNEL))
    assert t.data == {"id": TUNNEL, "created_at": "2026-01-01T00:00:00Z", "deleted_at": None,
                      "config_src": "cloudflare"}  # status/connections/name are not carried
    i = run(reader.ingress(TUNNEL))
    assert i.version == "ingress-7" and i.data["config"]["ingress"][0] == {"hostname": HOST, "service": SVC}
    d = run(reader.dns("dns1"))
    assert "ttl" not in d.data and d.version == "dns-2026-02-02T00:00:00.000000Z"


def test_tunnel_version_ignores_connection_health_but_tracks_deletion(parts):
    _, cf, reader = parts
    key = f"/accounts/{ACC}/cfd_tunnel/{TUNNEL}"
    v1 = run(reader.tunnel(TUNNEL)).version
    cf.routes[key]["status"] = "down"
    assert run(reader.tunnel(TUNNEL)).version == v1
    cf.routes[key]["deleted_at"] = "2026-03-03T00:00:00Z"
    assert run(reader.tunnel(TUNNEL)).version != v1


def test_config_src_is_read_directly_or_from_remote_config_and_never_invented(parts):
    _, cf, reader = parts
    key = f"/accounts/{ACC}/cfd_tunnel/{TUNNEL}"
    cf.routes[key]["config_src"] = "local"
    assert run(reader.tunnel(TUNNEL)).data["config_src"] == "local"
    del cf.routes[key]["config_src"]
    cf.routes[key]["remote_config"] = False
    assert run(reader.tunnel(TUNNEL)).data["config_src"] == "local"
    del cf.routes[key]["remote_config"]
    assert "config_src" not in run(reader.tunnel(TUNNEL)).data
    with pytest.raises(GrantRefused, match="cloudflare_mapping_mismatch"):
        check(reader)


def test_non_empty_origin_request_or_extra_rule_still_mismatches(parts):
    _, cf, reader = parts
    k = f"/accounts/{ACC}/cfd_tunnel/{TUNNEL}/configurations"
    cf.routes[k]["config"]["ingress"][0]["originRequest"] = {"noTLSVerify": True}
    with pytest.raises(GrantRefused, match="cloudflare_mapping_mismatch"):
        check(reader)
    cf.routes[k]["config"]["ingress"][0]["originRequest"] = {}
    cf.routes[k]["config"]["ingress"].insert(0, {"hostname": "evil.example.com", "service": "http://x"})
    with pytest.raises(GrantRefused, match="cloudflare_mapping_mismatch"):
        check(reader)


@pytest.mark.parametrize("which", ["tunnel", "ingress", "dns"])
def test_cloudflare_404_refuses_missing_object_and_5xx_or_network_refuses_unavailable(parts, which):
    _, cf, reader = parts
    path = {"tunnel": f"/accounts/{ACC}/cfd_tunnel/{TUNNEL}",
            "ingress": f"/accounts/{ACC}/cfd_tunnel/{TUNNEL}/configurations",
            "dns": f"/zones/{ZONE}/dns_records/dns1"}[which]
    good = cf.routes[path]
    cf.routes[path] = CfErr(404)
    with pytest.raises(GrantRefused, match="cloudflare_mapping_mismatch"):
        check(reader)
    for status in (500, 403, 0):
        cf.routes[path] = CfErr(status)
        with pytest.raises(GrantRefused, match="authority_unavailable"):
            check(reader)
    cf.routes[path] = good
    assert check(reader).record_version


@pytest.mark.parametrize("which,mutate", [
    ("tunnel", lambda r: None), ("ingress", lambda r: r.pop("version")),
    ("ingress", lambda r: r.update(version=True)), ("ingress", lambda r: r["config"].update(ingress="x")),
    ("dns", lambda r: r.pop("modified_on")), ("dns", lambda r: r.update(modified_on=""))])
def test_malformed_or_unversioned_cloudflare_results_refuse_unavailable(parts, which, mutate):
    _, cf, reader = parts
    path = {"tunnel": f"/accounts/{ACC}/cfd_tunnel/{TUNNEL}",
            "ingress": f"/accounts/{ACC}/cfd_tunnel/{TUNNEL}/configurations",
            "dns": f"/zones/{ZONE}/dns_records/dns1"}[which]
    if which == "tunnel":
        cf.routes[path] = ["not", "a", "dict"]
    else:
        mutate(cf.routes[path])
    with pytest.raises(GrantRefused, match="authority_unavailable"):
        check(reader)


def test_ids_that_could_rewrite_a_cloudflare_path_never_reach_the_transport(parts):
    _, cf, reader = parts
    for bad in ("../x", TUNNEL + "/../x", "", "ABC", None):
        with pytest.raises(AuthorityReadError):
            run(reader.tunnel(bad))
        with pytest.raises(AuthorityReadError):
            run(reader.ingress(bad))
    for bad in ("../x", "a/b", "", "x" * 65, None):
        with pytest.raises(AuthorityReadError):
            run(reader.dns(bad))
    assert cf.paths == []
    with pytest.raises(ValueError):
        CloudflareAuthorityReads(cf, account_id="../acc", zone_id=ZONE)


# ---- shared by both issuers -----------------------------------------------------------------


def test_one_reader_serves_check_authority_and_the_7a_grant_issuer(mem_firestore):
    """The 7a World, with its synthetic Reader replaced by the production reader over fakes."""
    w = World(mem_firestore)
    fs, cf = FakeFirestore(), CfStub()
    import base64
    from control_plane.app.mail_enrollment_transcript import unb64u
    fs.rows[("devices", "device1")]["device_pub_b64"] = base64.b64encode(unb64u(w.r[10], 32)).decode()
    w.reader = mar.ProductionAuthorityReader(
        FirestoreAuthorityReads(lambda: fs), CloudflareAuthorityReads(cf, account_id=ACC, zone_id=ZONE))
    token = w.grant()  # MailGrantIssuer.issue through the production reader
    assert token.count(".") == 2
    assert len(cf.paths) == 3 and all(c[0] == "get" for c in fs.calls)
    w.authority.reader = w.reader  # and the delivery issuer, same instance
    w.t[0] += 1000
    w.authority.bind(w.call(path="/v1/mail-delivery/bind"), {"grant": token, "r": w.r})
    out = run(w.authority.lease(w.call(path="/v1/mail-delivery/lease"), {"install_id": w.call().install_id,
                                                                         "generation": "1"}))
    assert out["record_version"] and out["generation"] == "1"
    fs.fail = OSError("x")
    with pytest.raises(Exception) as e:
        run(w.authority.lease(w.call(path="/v1/mail-delivery/lease"),
                              {"install_id": w.call().install_id, "generation": "1"}))
    assert getattr(e.value, "code", "") == "authority_unavailable"


def test_record_version_changes_when_any_one_backing_version_changes(parts):
    fs, cf, reader = parts
    base = check(reader).record_version
    fs.update_time = UPDATED + dt.timedelta(microseconds=1)
    assert check(reader).record_version != base
    fs.update_time = UPDATED
    assert check(reader).record_version == base
    cf.routes[f"/zones/{ZONE}/dns_records/dns1"]["modified_on"] = "2026-02-03T00:00:00.000000Z"
    assert check(reader).record_version != base


# ---- config and wiring ----------------------------------------------------------------------


def _env(**over):
    e = {mar.ENV_ENABLED: "1", mar.ENV_CF_TOKEN: "test-token-placeholder", mar.ENV_ACCOUNT: ACC,
         mar.ENV_ZONE: ZONE}
    e.update(over)
    return {k: v for k, v in e.items() if v is not None}


def test_reader_is_off_by_default_and_when_any_config_is_missing(caplog):
    assert mar.authority_reader_from_env({}) is None
    assert mar.authority_reader_from_env(_env(**{mar.ENV_ENABLED: "0"})) is None
    for name in (mar.ENV_CF_TOKEN, mar.ENV_ACCOUNT, mar.ENV_ZONE):
        with caplog.at_level(logging.ERROR):
            assert mar.authority_reader_from_env(_env(**{name: None})) is None
        assert name in caplog.text
    assert mar.authority_reader_from_env(_env(**{mar.ENV_ACCOUNT: "../x"})) is None
    # the write-capable token is never a fallback
    e = _env(**{mar.ENV_CF_TOKEN: None})
    e["CLOUDFLARE_API_TOKEN"] = "other"
    assert mar.authority_reader_from_env(e) is None


def test_configured_reader_builds_without_any_io_and_is_cached():
    r = mar.authority_reader_from_env(_env())
    assert isinstance(r, mar.ProductionAuthorityReader)
    assert mar.authority_reader_from_env(_env()) is r


def test_get_deps_wires_the_reader_only_when_enabled(monkeypatch):
    base = {"CLAYRUNE_MAIL_DELIVERY_ENABLED": "1"}
    for k, v in base.items():
        monkeypatch.setenv(k, v)
    from control_plane.tests import mail_delivery_support as s
    from control_plane.app.mail_delivery_signing import DeliveryKeyring
    # Keyring/service-key parse failures leave authority None, so supply real ones.
    monkeypatch.setattr(rmd, "parse_delivery_keys", lambda raw: DeliveryKeyring(((s.delivery_key(), True),)))
    monkeypatch.setattr(rmd, "parse_service_keys", lambda raw: __import__(
        "control_plane.app.mail_service_auth", fromlist=["x"]).ServiceKeyring({}))
    for k in (mar.ENV_ENABLED, mar.ENV_CF_TOKEN, mar.ENV_ACCOUNT, mar.ENV_ZONE):
        monkeypatch.delenv(k, raising=False)
    assert rmd.get_deps().authority.reader is None  # default off: still 503 authority_unavailable
    for k, v in _env().items():
        monkeypatch.setenv(k, v)
    assert isinstance(rmd.get_deps().authority.reader, mar.ProductionAuthorityReader)
    monkeypatch.delenv(mar.ENV_CF_TOKEN)
    assert rmd.get_deps().authority.reader is None  # half-configured: closed
