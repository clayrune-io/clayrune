"""Numbered docs/remote-access/ citations in the tunnel and provider-seam files
must point at a tracked file or say the doc is operator-internal
(MC-1179 pass 12 second wave, TL50 bullets 3 and 4)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    'mc_tunnel/build.rs', 'mc_tunnel/Cargo.toml', 'mc_tunnel/src/main.rs',
    'mc_tunnel/src/attestation.rs', 'mc_tunnel/src/handshake.rs',
    'mc_tunnel/bin/README.md', 'mc_remote_iface/README.md',
]
CITE = re.compile(r'\b(0[0-9]-[a-z][a-z-]*\.md|error_codes\.md)')


def _published(name):
    return (ROOT / 'docs' / 'remote-access' / name).exists()


def test_every_numbered_citation_resolves_or_is_marked_internal():
    bad = []
    for rel in FILES:
        for n, line in enumerate((ROOT / rel).read_text(encoding='utf-8').splitlines(), 1):
            for m in CITE.finditer(line):
                if not _published(m.group(1)) and 'operator-internal' not in line:
                    bad.append(f'{rel}:{n}: {m.group(1)}')
    assert bad == []


def test_bundled_version_record_no_longer_reads_as_current():
    text = (ROOT / 'mc_tunnel/bin/README.md').read_text(encoding='utf-8')
    assert 'latest at the time of vendoring' not in text
    assert '2026.10.2' in text
