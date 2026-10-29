"""Retype a LinkedIn destination on the human account-edit path.

The chosen Company Page address owns the kind. Keep the existing routes, refs
and exact permission scopes; changing kind invalidates prior verification.
This helper never writes a store or bypasses an identity/coverage check.
"""
from __future__ import annotations

from mc.desk_connect import browser_setup_record as _setup
from mc.desk_connect import permission_policy as _policy
from mc.desk_connect import purpose_bindings as _bindings


def apply(rec: dict, organization_id: str) -> None:
    """Called under the account write lock after validation, before setting the ID."""
    kind = 'organization' if organization_id else 'member'
    # Only an already-valid explicit policy can retain its exact consent.
    # Never turn a malformed/tampered policy into valid consent by re-signing it.
    explicit = _policy.read_policy(rec)['state'] == 'explicit'
    rec['account_kind'] = kind
    setup = _setup.of(rec)
    if setup is not None:
        setup['account_kind'] = kind
    conns = rec.get('connections')
    for purpose, caps in conns.items() if isinstance(conns, dict) else []:
        for cap, binding in caps.items() if isinstance(caps, dict) else []:
            if not isinstance(binding, dict):
                continue
            binding['account_kind'] = kind
            if all(k in binding for k in ('route_id', 'refs', 'scopes', 'fingerprint')):
                binding['fingerprint'] = _bindings.fingerprint(
                    'linkedin', purpose, binding['route_id'], kind, [cap], binding['refs'], binding['scopes'])
    policy = rec.get(_policy.POLICY_KEY)
    if isinstance(policy, dict):
        policy['account_kind'] = kind
        if explicit:
            clean = {k: policy[k] for k in ('account_id', 'service', 'account_kind', 'read', 'post', 'scopes')}
            policy['fingerprint'] = _policy.fingerprint(clean)
        else:
            # Even a fingerprint precomputed for the destination being chosen
            # must not make an invalid source policy valid after retyping it.
            policy.pop('fingerprint', None)
