"""Who may set the FIRST dashboard passcode (MC-1179 P19, Ron 2026-11-17: "Only
user should be able to set passcode").

Until a passcode exists, `POST /api/local-auth/set` had no proof to ask for, and
every process on the host is exempt from the LAN gate, so an agent's `curl` set
the first passcode; that passcode then stands for "a human is asking" on every
gated route (vault writes, the vault-lock passphrase, the attend-once grant).

The first set is therefore host-only and human-only:

  1. the peer must be the host itself: a LAN device, and a tunnel client (the
     tunnel reaches this server as a loopback peer carrying Cf-* headers), get
     `setup_requires_host`. `mc.passkeys.host_check` already tells those two
     apart; only its `not_loopback` and `proxied` verdicts are used here, so a
     dashboard opened at 127.0.0.1 instead of localhost still works;
  2. the caller must pass the human-only gate (`mc.human_only_gate`): a request
     with no browser Origin, or one OS-attributed to a managed agent session, or
     one made while an unattended session could be the caller, gets its 403
     `human_only`.

Changing an existing passcode is not this gate's business: the current passcode
is the proof there, from every caller.
"""
from __future__ import annotations

from flask import jsonify, request

from mc.human_only_gate import refuse_non_human
from mc.passkeys import host_check

_NOT_THE_HOST = ('not_loopback', 'proxied')


def refuse_first_set():
    """None when this request may set the first passcode, else a (response,
    status) to return as-is."""
    try:
        port = int(request.environ.get('SERVER_PORT') or 0)
    except (TypeError, ValueError):
        port = 0
    code = host_check.refusal_code(request.remote_addr, dict(request.headers.items()),
                                   port, require_origin=False)
    if code in _NOT_THE_HOST:
        return jsonify({'error': 'setup_requires_host'}), 403
    return refuse_non_human('set the first dashboard passcode')
