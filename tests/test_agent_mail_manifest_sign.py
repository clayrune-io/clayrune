"""Offline manifest signer (tools/agent-mail-manifest-sign.py): its output must satisfy the dashboard's
EXISTING verifier and client end to end. Throwaway keys are generated in tmp dirs; nothing touches a
real key, the network or any config.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mc import agent_mail_manifest_fetch as fetch_mod
from mc.agent_mail_manifest_fetch import ManifestFetchError, fetch_verified_manifest
from mc.agent_mail_service_manifest import (
    ManifestRefused, ManifestState, canonical_manifest_bytes, verify_service_manifest)

_TOOL = Path(__file__).resolve().parent.parent / 'tools' / 'agent-mail-manifest-sign.py'
_spec = importlib.util.spec_from_file_location('agent_mail_manifest_sign', _TOOL)
signer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(signer)

NOW = 1_794_249_600_000
ORIGIN = 'https://mail.example.com'


def enc(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def raw_pub(key: Ed25519PrivateKey) -> str:
    return enc(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def seed_of(key: Ed25519PrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())


@pytest.fixture
def root():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def keydir(tmp_path_factory):
    # A tmp dir outside this repo; asserts below confirm it has no .git ancestor.
    d = tmp_path_factory.mktemp('rootkeys')
    assert not signer._key_path_in_checkout(d / 'k')
    return d


def write_key(keydir: Path, key: Ed25519PrivateKey, form: str = 'pem') -> Path:
    path = keydir / f'root-{form}.key'
    if form == 'pem':
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    elif form == 'seed':
        path.write_text(enc(seed_of(key)) + '\n')
    elif form == 'der':
        path.write_text(enc(key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption())))
    return path


def args_for(keyfile, **over):
    service = over.pop('service', None) or Ed25519PrivateKey.generate()
    values = {'origin': ORIGIN, 'sequence': '7', 'issued': str(NOW - 1000), 'activate': str(NOW - 1000),
              'expires': str(NOW + 100_000), 'retire': str(NOW + 200_000), 'key-id': 'service1',
              'service-public-key': raw_pub(service)}
    values.update({k.replace('_', '-'): v for k, v in over.items()})
    argv = ['--root-key-file', str(keyfile)]
    for k, v in values.items():
        if v is not None:
            argv.append(f'--{k}={v}')  # `=` form: a base64url key may begin with '-'
    return argv


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    code = signer.run(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


class Reply:
    def __init__(self, status, body): self.status, self._body = status, body
    def read(self, n): return self._body[:n]
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Opener:
    def __init__(self, body): self.body, self.seen = body, []
    def open(self, request, timeout=None):
        self.seen.append((request.full_url, request.get_method()))
        return Reply(200, self.body)


# ---- the signed document satisfies the existing verifier and client ---------------------------------
@pytest.mark.parametrize('form', ['pem', 'seed', 'der'])
def test_output_passes_verifier_and_client_end_to_end(root, keydir, form):
    code, out, err = run(args_for(write_key(keydir, root, form)))
    assert code == 0, err
    doc = json.loads(out)
    assert set(doc) == {'manifest', 'signature'} and len(doc['manifest']) == 11
    assert len(base64.urlsafe_b64decode(doc['signature'] + '==')) == 64 and len(out.encode()) <= 32 * 1024

    class Floor:
        def load(self, origin): return None
        def compare_and_set(self, origin, expected, value): return True
    cand = verify_service_manifest(doc['manifest'], doc['signature'], root_public_key=raw_pub(root),
                                   service_origin=ORIGIN, store=Floor(), now_ms=NOW, enabled=True)
    assert cand.sequence == 7 and cand.key_id == 'service1'

    opener = Opener(out.encode('ascii'))
    got = fetch_verified_manifest(origin=ORIGIN, root_public_key=raw_pub(root),
                                  transport=fetch_mod.HttpsGetTransport(opener=opener), now_ms=lambda: NOW)
    assert opener.seen == [(ORIGIN + '/v1/mail/manifest', 'GET')]
    assert got['manifest'] == doc['manifest'] and got['signature'] == doc['signature']
    assert got['sequence'] == 7 and got['expires_at_ms'] == NOW + 100_000


def test_stdout_is_compact_json_line_and_stderr_names_the_root_public_key(root, keydir):
    code, out, err = run(args_for(write_key(keydir, root)))
    assert code == 0 and out.endswith('\n') and out.count('\n') == 1 and ' ' not in out.strip()
    assert raw_pub(root) in err


def test_signature_is_over_the_shared_canonical_bytes(root, keydir):
    code, out, _ = run(args_for(write_key(keydir, root)))
    doc = json.loads(out)
    assert canonical_manifest_bytes(doc['manifest']) == json.dumps(
        doc['manifest'], separators=(',', ':'), ensure_ascii=True).encode('ascii')
    root.public_key().verify(base64.urlsafe_b64decode(doc['signature'] + '=='), canonical_manifest_bytes(doc['manifest']))


# ---- tamper is rejected by the verifier and the client -----------------------------------------------
def _fetch(root, doc):
    return fetch_verified_manifest(origin=ORIGIN, root_public_key=raw_pub(root),
                                   transport=fetch_mod.HttpsGetTransport(opener=Opener(json.dumps(doc).encode())),
                                   now_ms=lambda: NOW)


def test_tampered_manifest_or_signature_or_wrong_root_is_rejected(root, keydir):
    _, out, _ = run(args_for(write_key(keydir, root)))
    doc = json.loads(out)
    assert _fetch(root, doc)['sequence'] == 7
    for index, value in [(3, '8'), (6, 'evil'), (7, raw_pub(Ed25519PrivateKey.generate())), (10, ['extra'])]:
        bad = json.loads(out)
        bad['manifest'][index] = value
        with pytest.raises(ManifestFetchError) as e:
            _fetch(root, bad)
        assert e.value.code == 'bad_signature', index
    flipped = json.loads(out)
    sig = bytearray(base64.urlsafe_b64decode(flipped['signature'] + '=='))
    sig[0] ^= 1
    flipped['signature'] = enc(bytes(sig))
    with pytest.raises(ManifestFetchError) as e:
        _fetch(root, flipped)
    assert e.value.code == 'bad_signature'
    with pytest.raises(ManifestFetchError) as e:
        _fetch(Ed25519PrivateKey.generate(), doc)  # a different pinned root
    assert e.value.code == 'bad_signature'


# ---- the key: path only, never printed, never inside a checkout -----------------------------------------
def test_key_material_never_appears_in_any_output(root, keydir, tmp_path):
    seed = enc(seed_of(root))
    ok = run(args_for(write_key(keydir, root, 'seed')))
    assert ok[0] == 0 and seed not in ok[1] + ok[2]
    pem = write_key(keydir, root, 'pem').read_text()
    assert pem.splitlines()[1] not in ok[1] + ok[2]
    junk = keydir / 'junk.key'
    junk.write_text('SECRETSECRETSECRET' * 3)
    bad = run(args_for(junk))
    assert bad[0] == 2 and 'SECRETSECRET' not in bad[1] + bad[2] and bad[1] == ''
    odd = keydir / 'odd.key'
    odd.write_text('-----BEGIN PRIVATE KEY-----\nSECRETSECRETSECRET\n-----END PRIVATE KEY-----\n')
    bad = run(args_for(odd))
    assert bad[0] == 2 and 'SECRETSECRET' not in bad[1] + bad[2]


def test_there_is_no_way_to_pass_a_key_value_on_the_command_line(root):
    seed = enc(seed_of(root))
    parser_help = signer._parser().format_help()
    assert '--root-key-file' in parser_help and 'PATH' in parser_help
    assert signer._parser().parse_args(['--root-key-file', 'x']).root_key_file == 'x'
    with pytest.raises(SystemExit):  # abbreviations are off, so a value cannot ride in on a prefix
        signer._parser().parse_args(['--root-key', seed])


def test_key_file_inside_this_repo_is_refused_before_it_is_opened():
    inside = signer._REPO_ROOT / '_scratch' / 'never-created.key'
    assert not inside.exists()
    code, out, err = run(args_for(inside))
    assert code == 2 and out == '' and 'inside a git checkout' in err


def test_key_file_in_any_git_checkout_is_refused(root, tmp_path):
    other = tmp_path / 'some-other-repo'
    (other / '.git').mkdir(parents=True)
    key = write_key(other, root)
    code, out, err = run(args_for(key))
    assert code == 2 and out == '' and 'inside a git checkout' in err
    worktree = tmp_path / 'wt'  # a worktree has a `.git` FILE, not a directory
    worktree.mkdir()
    (worktree / '.git').write_text('gitdir: elsewhere\n')
    code, _, err = run(args_for(write_key(worktree, root)))
    assert code == 2 and 'inside a git checkout' in err


def test_non_ed25519_and_oversize_and_missing_key_files_fail_closed(keydir):
    from cryptography.hazmat.primitives.asymmetric import rsa
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = keydir / 'rsa.key'
    pem.write_bytes(rsa_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    big = keydir / 'big.key'
    big.write_text('A' * 5000)
    for target in (pem, big, keydir / 'absent.key', keydir):
        code, out, _ = run(args_for(target))
        assert code == 2 and out == ''


# ---- inputs ------------------------------------------------------------------------------------------------
def test_iso_times_equal_epoch_milliseconds(root, keydir):
    key = write_key(keydir, root)
    service = Ed25519PrivateKey.generate()
    iso = run(args_for(key, service=service, issued='2026-11-11T00:00:00Z', activate='2026-11-11T00:00:00Z',
                       expires='2026-12-11T00:00:00Z', retire='2027-01-11T00:00:00.000Z'))
    ms = run(args_for(key, service=service, issued='1794355200000', activate='1794355200000',
                      expires='1796947200000', retire='1799625600000'))
    assert iso[0] == 0 and ms[0] == 0
    assert json.loads(iso[1])['manifest'] == json.loads(ms[1])['manifest']


def test_input_file_with_cli_override_and_sorted_retired_ids(root, keydir, tmp_path):
    service = Ed25519PrivateKey.generate()
    spec = {'origin': ORIGIN, 'sequence': 3, 'issued': NOW - 1000, 'activate': NOW - 1000, 'expires': NOW + 5000,
            'retire': NOW + 9000, 'key_id': 'k2', 'service_public_key': raw_pub(service),
            'retired_key_ids': ['zz', 'aa', 'aa']}
    f = tmp_path / 'in.json'
    f.write_text(json.dumps(spec))
    code, out, err = run(['--root-key-file', str(write_key(keydir, root)), '--input', str(f), '--sequence', '4',
                          '--retired-key-id', 'mm'])
    assert code == 0, err
    manifest = json.loads(out)['manifest']
    assert manifest[3] == '4' and manifest[6] == 'k2' and manifest[10] == ['mm']  # CLI list replaces file list
    code, out, _ = run(['--root-key-file', str(write_key(keydir, root)), '--input', str(f)])
    assert code == 0 and json.loads(out)['manifest'][10] == ['aa', 'zz'] and json.loads(out)['manifest'][3] == '3'
    f.write_text(json.dumps({**spec, 'root_key': 'nope'}))
    assert run(['--root-key-file', str(write_key(keydir, root)), '--input', str(f)])[0] == 2
    f.write_text('{not json')
    assert run(['--root-key-file', str(write_key(keydir, root)), '--input', str(f)])[0] == 2


@pytest.mark.parametrize('over', [
    {'origin': 'http://mail.example.com'}, {'origin': 'https://localhost'}, {'sequence': '0'}, {'sequence': 'x'},
    {'service_public_key': 'AAAA'}, {'service_public_key': '!' * 43}, {'key_id': 'bad key!'},
    {'issued': 'yesterday'}, {'issued': '12'}, {'activate': str(NOW + 150_000)},  # active after expiry
    {'expires': str(NOW - 5000)}, {'retire': str(NOW + 50_000)},                  # retire before expiry
])
def test_values_the_verifier_would_refuse_are_never_signed(root, keydir, over):
    code, out, err = run(args_for(write_key(keydir, root), **over))
    assert code == 2 and out == '' and err.startswith('error:')


def test_key_id_listed_as_retired_is_refused(root, keydir):
    code, out, err = run(args_for(write_key(keydir, root)) + ['--retired-key-id', 'service1'])
    assert code == 2 and out == '' and 'manifest_retired_key' in err


def test_missing_values_are_named_not_defaulted(root, keydir):
    code, out, err = run(['--root-key-file', str(write_key(keydir, root)), '--origin', ORIGIN])
    assert code == 2 and out == ''
    for name in ('sequence', 'issued', 'activate', 'expires', 'retire', 'key_id', 'service_public_key'):
        assert name in err


# ---- --out ---------------------------------------------------------------------------------------------------
def test_out_file_written_then_refused_without_force(root, keydir, tmp_path):
    key = write_key(keydir, root)
    argv = args_for(key) + ['--out', str(tmp_path / 'manifest.json')]
    code, out, err = run(argv)
    assert code == 0 and out == '' and (tmp_path / 'manifest.json').read_text().startswith('{"manifest":[')
    code, _, err = run(argv)
    assert code == 2 and 'exists' in err
    assert run(argv + ['--force'])[0] == 0


def test_refusal_writes_nothing(root, keydir, tmp_path):
    target = tmp_path / 'manifest.json'
    code, _, _ = run(args_for(write_key(keydir, root), sequence='0') + ['--out', str(target)])
    assert code == 2 and not target.exists()


def test_signer_has_no_network_and_reads_no_environment_for_the_key():
    src = _TOOL.read_text(encoding='utf-8')
    for banned in ('urllib', 'requests', 'socket', 'os.environ', 'getenv', 'subprocess'):
        assert banned not in src, banned


def test_service_public_key_starting_with_dash_works_in_equals_form(root, keydir):
    service = Ed25519PrivateKey.generate()
    while not raw_pub(service).startswith('-'):
        service = Ed25519PrivateKey.generate()
    code, out, err = run(args_for(write_key(keydir, root), service=service))
    assert code == 0, err
    assert json.loads(out)['manifest'][7] == raw_pub(service)
