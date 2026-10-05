"""Host-only check for passkey enrollment (docs/PASSKEYS_SPEC.md, "Enrollment").

First enrollment must come from the owner's own browser on this machine:
a direct loopback peer, the exact configured ``localhost`` Host and Origin, and
no tunnel or proxy path. `local_auth._local_auth_exempt()` is deliberately NOT
reused: it exempts tunnel traffic, and tunnel traffic reaches this server as a
loopback peer carrying Cf-* headers (see `remote_routes._is_cf_tunneled_request`).

Pure functions over a peer address and a header mapping, so the whole decision
table is unit-testable without a Flask app. Every header here is forgeable by a
same-box process; what the check refuses is the LAN, the tunnel, and a browser
page on another origin. The retyped dashboard passcode is the human proof.
"""
from __future__ import annotations

from typing import Mapping, Optional

RP_ID = 'localhost'

# Request headers a reverse proxy or tunnel adds. cloudflared forwards tunnel
# traffic over loopback, so none of these appear on a direct browser request.
_PROXY_HEADERS = (
    'forwarded', 'via', 'x-real-ip', 'true-client-ip', 'x-client-ip',
    'x-original-host', 'x-original-url', 'x-envoy-external-address',
)

REFUSAL_MESSAGES = {
    'not_loopback': 'Passkeys can only be enrolled from the host computer itself, '
                    'not from another device on the network.',
    'proxied': 'Passkeys can only be enrolled from the host computer, not through '
               'the remote-access tunnel or a proxy.',
    'bad_host': 'Open the dashboard at http://localhost:{port} on the host computer '
                'to enroll a passkey; other addresses are a different site to the '
                'browser.',
    'bad_origin': 'Open the dashboard at http://localhost:{port} on the host computer '
                  'to enroll a passkey; this request did not come from that page.',
    'cross_site': 'This request did not come from the dashboard page itself.',
}


def expected_origin(port: int) -> str:
    return f'http://localhost:{int(port)}'


def _is_loopback(addr: str) -> bool:
    ra = (addr or '').strip().lower()
    return ra in ('127.0.0.1', '::1') or ra.startswith('::ffff:127.')


def _lower_keys(headers: Mapping[str, str]) -> dict:
    return {str(k).lower(): v for k, v in headers.items()}


def refusal_code(remote_addr: Optional[str], headers: Mapping[str, str], port: int,
                 require_origin: bool = True) -> Optional[str]:
    """None when the request is the owner's own localhost dashboard page;
    otherwise a short code naming the first check it failed. Cheapest and most
    decisive checks first. `require_origin=False` is for a same-origin GET,
    which browsers send without an Origin header; a present Origin must still
    match."""
    if not _is_loopback(remote_addr or ''):
        return 'not_loopback'
    h = _lower_keys(headers)
    for name in h:
        if name.startswith('cf-') or name.startswith('x-forwarded-') or name in _PROXY_HEADERS:
            return 'proxied'
    if (h.get('host') or '').strip().lower() != f'localhost:{int(port)}':
        return 'bad_host'
    origin = (h.get('origin') or '').strip()
    if (require_origin or origin) and origin != expected_origin(port):
        return 'bad_origin'
    site = (h.get('sec-fetch-site') or '').strip().lower()
    if site and site != 'same-origin':
        return 'cross_site'
    return None


def refusal_message(code: str, port: int) -> str:
    return REFUSAL_MESSAGES.get(code, 'Passkeys can only be enrolled from the host computer.').format(port=int(port))
