"""Which network addresses an unknown-service lookup may reach (spec "Detection and
trust": "Enforce network restrictions before navigation, redirects and subrequests,
including DNS rebinding").

Two rules live here and nowhere else, so the pre-flight check and the proxy that
confines the temporary browser pane (`guard_proxy.py`) cannot disagree:

  * a HOST NAME must be one `url_check` accepts for a public service (no reserved
    names, no IP literal that is not global, not Clayrune's own origin);
  * an ADDRESS it resolves to must be globally routable. EVERY address the name
    resolves to is checked, not the first: a name that answers one public and one
    private address is refused, so a rebinding record cannot win a later pick.

`resolve_public` returns the addresses it vetted. The proxy connects to one of THOSE,
never to the name again, which is what closes the DNS-rebinding window: there is no
second resolution to swap.
"""
from __future__ import annotations

import ipaddress
import socket

from mc.desk_connect import url_check
from mc.desk_connect.url_check import UrlError

# Prefixes that embed an IPv4 address (or are transition mechanisms) which `is_global`
# alone does not reject: a hostile record could point NAT64 / 6to4 / Teredo space at
# 127.0.0.1 or 10.0.0.1 on a network that translates it.
_EMBEDDING_NETS = tuple(ipaddress.ip_network(n) for n in ('64:ff9b::/96', '64:ff9b:1::/48', '2002::/16', '2001::/32'))


class Blocked(Exception):
    """A refusal. `code` is machine-readable (`private_address`, `bad_host`,
    `no_address`, `dns_failed`); `str(e)` is a sentence for the person."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def is_public_ip(text: str) -> bool:
    """True only for a globally routable address. An IPv4-mapped IPv6 address is judged
    by the IPv4 inside it; the transition prefixes above are refused outright."""
    try:
        ip = ipaddress.ip_address(text.split('%', 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif any(ip in net for net in _EMBEDDING_NETS):
            return False
    return bool(ip.is_global) and not ip.is_multicast


def check_host(host: str, own_hosts=()) -> str:
    """Normalised host name, or Blocked. The same rules a pasted address gets."""
    try:
        return url_check.check_url(f'https://{host}', own_hosts=own_hosts)['host']
    except UrlError as e:
        raise Blocked('bad_host', str(e)) from e


def resolve_public(host: str, own_hosts=(), resolver=None) -> list[str]:
    """Vetted addresses for `host`. Raises Blocked: `bad_host` (the name itself is not
    allowed), `dns_failed` (it does not resolve), `private_address` (ANY address it
    resolves to is not global). `resolver` has `socket.getaddrinfo`'s shape."""
    name = check_host(host, own_hosts)
    try:
        ipaddress.ip_address(name)
        return [name]                        # an IP literal: check_host already vetted it
    except ValueError:
        pass
    try:
        infos = (resolver or socket.getaddrinfo)(name, 443, type=socket.SOCK_STREAM)
    except OSError as e:
        raise Blocked('dns_failed', 'That host name does not resolve.') from e
    addrs = []
    for info in infos:
        a = str(info[4][0])
        if a not in addrs:
            addrs.append(a)
    if not addrs:
        raise Blocked('no_address', 'That host name has no address.')
    bad = [a for a in addrs if not is_public_ip(a)]
    if bad:
        raise Blocked('private_address', 'That host name points at a private or local network address.')
    return addrs
