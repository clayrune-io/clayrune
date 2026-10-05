"""The Desk v1 workspace account store (MC-1021 R1-W S5; docs/desk_v1/R1W_WIRING_PLAN.md §2.C, §4).

An ACCOUNT is a place the Desk can put a message: `X @ron`, the Clayrune
LinkedIn Company Page, a blog. Before this module accounts lived per project
inside `presences[pid].accounts` (and the v1 surfaces filed an account under
"the first campaign's project, else the first project"); the retire-presence
decision made them workspace-level. Now `store['accounts']` in `data/desk.json`
(same `_store_lock`, outside DATA_DIR, so nothing to suffix-exclude) holds them,
keyed by id, and `mc.desk._lift_presence_accounts` lifts the old presence rows in
on every read (idempotent; the presence copy stays, because engagement still
reads an account's read settings from it).

CREDENTIALS ARE NEVER HERE. An account says which vault entry its publishing
needs by NAME (`publish.secret`); this module only asks the vault whether that
entry exists (`secrets_store.list_secrets`, metadata, and `is_readable`, which
decrypts nothing it returns). No route returns a secret and no path here
creates one: only a human creates a vault entry (CLAUDE.md, vault rule 3).

`publish` is DERIVED on every read and never stored, so an account cannot claim
a connection that is not there:

  X          ready iff the vault holds a readable `x.oauth-token`
             (`desk_publish.X_OAUTH_TOKEN_SECRET`); otherwise the reason says so.
  LinkedIn   NOT ready, whatever the vault holds, until LinkedIn approves the
             `w_organization_social` scope (Community Management API review):
             posting as the Company Page needs it. The publisher exists
             (`desk_publish`, S7); `LINKEDIN_ORG_POSTING_APPROVED` is the one
             switch, flipped by whoever learns the review passed, not by config.
             Once on, ready also needs a readable `linkedin.oauth-token` and the
             account's `organization_id` (not a secret: it is in the Company
             Page admin URL).
  manual     ready: the human publishes it (a blog); nothing to connect.
"""

from __future__ import annotations

import re

from mc import desk as _desk
from mc import desk_account_refs as _refs
from mc import desk_oauth as _oauth
from mc import secrets_store
from mc.core import _log, now_iso
from mc.desk_publish import LINKEDIN_TOKEN_SECRET, X_OAUTH_TOKEN_SECRET

# Where the Desk can place anything in v1: X and the LinkedIn Company Page, plus
# a blog the human publishes. YouTube / Discord / Reddit / Drive / Dropbox are
# placeholder tiles in the UI and cannot be created here.
ACCOUNT_PLATFORMS = ('x', 'linkedin', 'blog')
READ_PLATFORMS = ('x', 'linkedin')          # the ones a read route exists for
CAPABILITIES = ('direct', 'manual')

LINKEDIN_ORG_POSTING_APPROVED = False
LINKEDIN_PENDING_REASON = 'LinkedIn app review pending (w_organization_social)'

_TOKEN_SECRET = {'x': X_OAUTH_TOKEN_SECRET, 'linkedin': LINKEDIN_TOKEN_SECRET}

MAX_TEXT = 80
_CLIENT_ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
_LABEL_PREFIX = {'x': '\U0001d54f · ', 'linkedin': 'in · '}


class AccountError(ValueError):
    """A refusal with the HTTP status the route should answer."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# -- derived publish state --------------------------------------------------------

def _vault_meta() -> dict:
    """Vault entry name -> metadata (never a value). `{}` plus a log line when
    the vault cannot be listed, which reads as "no token", never as "ready"."""
    try:
        return {s['name']: s for s in secrets_store.list_secrets()}
    except Exception as e:
        _log(f'[desk_accounts] vault listing failed: {e}', flush=True)
        return {}


def publish_state(acc: dict, vault: dict | None = None) -> dict:
    """`{ready, reason, secret, unattended_ok}` for one account record.
    `unattended_ok` is the vault entry's own `allow_unattended` (a scheduled post
    is an unattended caller), or None when no vault entry is involved."""
    plat = acc.get('platform')
    if acc.get('preview'):
        return {'ready': False, 'reason': 'preview only: nothing publishes here yet',
                'secret': None, 'unattended_ok': None}
    if acc.get('capability') == 'manual':
        return {'ready': True, 'reason': None, 'secret': None, 'unattended_ok': None}
    if plat == 'linkedin' and not LINKEDIN_ORG_POSTING_APPROVED:
        # No vault entry can fix this, so none is named: Connections would
        # otherwise offer "Open Secrets" for a connection nothing can make yet.
        return {'ready': False, 'reason': LINKEDIN_PENDING_REASON,
                'secret': None, 'unattended_ok': None}
    if plat == 'x':
        # A sign-in made from Connections wins over a hand-pasted `x.oauth-token`.
        # An account with a sign-in of its own (`desk_account_refs`) has no such
        # fallback: the singleton's token would post as a different account.
        own = _refs.oauth_arg(acc)
        entry = _oauth.vault_name('x', own)
        st = _oauth.status('x', own)
        if st['state'] == 'connected':
            vault = _vault_meta() if vault is None else vault
            meta = vault.get(entry) or {}
            return {'ready': True, 'reason': None, 'secret': entry,
                    'unattended_ok': bool(meta.get('allow_unattended', True))}
        if st['state'] == 'needs_signin':
            return {'ready': False, 'reason': st['reason'], 'secret': None, 'unattended_ok': None}
        if own is not None:
            return {'ready': False, 'reason': 'not signed in to X for this account yet',
                    'secret': None, 'unattended_ok': None}
    if plat in _TOKEN_SECRET:
        secret = _TOKEN_SECRET[plat]
        what = 'X' if plat == 'x' else 'LinkedIn'
        vault = _vault_meta() if vault is None else vault
        meta = vault.get(secret)
        if meta is None:
            return {'ready': False, 'reason': f'no {what} API token in the vault',
                    'secret': secret, 'unattended_ok': None}
        if plat == 'linkedin' and not acc.get('organization_id'):
            return {'ready': False, 'reason': 'no LinkedIn organization id on the account (Company Page admin URL)',
                    'secret': secret, 'unattended_ok': None}
        if not secrets_store.is_readable(secret):
            return {'ready': False,
                    'reason': f'the {what} API token in the vault cannot be read (vault locked, or its key changed)',
                    'secret': secret, 'unattended_ok': None}
        return {'ready': True, 'reason': None, 'secret': secret,
                'unattended_ok': bool(meta.get('allow_unattended', True))}
    return {'ready': False, 'reason': f'no publisher for {plat}', 'secret': None, 'unattended_ok': None}


def v1_account(acc: dict, vault: dict | None = None) -> dict:
    """A stored account as the v1 surfaces read a `channel`: the record's own
    fields, `publish`, and `connected` (= `publish.ready`, which Connections,
    Where's tray and Launch's offline check already key on). `read_via` and
    `browser_profile` appear only when set (absent = the browser pane)."""
    pub = publish_state(acc, vault)
    out = {
        'id': acc['id'], 'platform': acc.get('platform'), 'identity': acc.get('identity') or acc['id'],
        'label': acc.get('label') or acc.get('identity') or acc['id'],
        'capability': acc.get('capability') or 'manual', 'voice': acc.get('voice') or '',
        'created_at': acc.get('created_at'),
        'connected': pub['ready'], 'publish': pub,
    }
    for k in ('read_via', 'browser_profile', 'organization_id'):
        if acc.get(k):
            out[k] = acc[k]
    if acc.get('credentials'):          # names of the account's own sign-in, never a value
        out['credentials'] = dict(acc['credentials'])
    if acc.get('preview'):
        out['preview'] = True
    return out


def v1_accounts(store: dict) -> list[dict]:
    rows = list((store.get('accounts') or {}).values())
    vault = _vault_meta() if any(r.get('platform') in _TOKEN_SECRET and r.get('capability') != 'manual'
                                 and not r.get('preview') for r in rows) else {}
    return [v1_account(r, vault) for r in rows]


# -- reads ------------------------------------------------------------------------

def list_accounts() -> list[dict]:
    with _desk._store_lock:
        store = _desk._read_store()
    return v1_accounts(store)


def get_account(account_id: str) -> dict | None:
    with _desk._store_lock:
        rec = (_desk._read_store().get('accounts') or {}).get(account_id)
    return v1_account(rec) if rec else None


# -- writes -----------------------------------------------------------------------

def _clean_text(value, what: str, *, required: bool = False) -> str:
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise AccountError(f'{what} must be text')
    value = value.strip()
    if required and not value:
        raise AccountError(f'{what} is required')
    if len(value) > MAX_TEXT:
        raise AccountError(f'{what} is limited to {MAX_TEXT} characters')
    return value


def create_account(platform, identity, *, label=None, capability=None, voice=None,
                   account_id: str | None = None) -> dict:
    if platform not in ACCOUNT_PLATFORMS:
        raise AccountError(f'platform must be one of {", ".join(ACCOUNT_PLATFORMS)}; '
                           'other channels are not available yet')
    identity = _clean_text(identity, 'identity', required=True)
    if capability is None:
        capability = 'manual' if platform == 'blog' else 'direct'
    if capability not in CAPABILITIES:
        raise AccountError(f'capability must be one of {", ".join(CAPABILITIES)}')
    if platform == 'blog' and capability != 'manual':
        raise AccountError('a blog has no publishing API: its capability is manual')
    label = _clean_text(label, 'label') or f'{_LABEL_PREFIX.get(platform, "")}{identity}'
    voice = _clean_text(voice, 'voice')
    if account_id is not None and not (isinstance(account_id, str) and _CLIENT_ID.match(account_id)):
        raise AccountError('account id must be 1-80 letters, digits, - or _')
    with _desk._store_lock:
        store = _desk._read_store()
        accounts = store['accounts']
        if account_id and account_id in accounts:
            raise AccountError('an account with that id already exists', 409)
        for other in accounts.values():
            if other.get('platform') == platform and (other.get('identity') or '').lower() == identity.lower():
                raise AccountError(f'{identity} on {platform} is already an account', 409)
        rec = {'id': account_id or _desk._new_id('acct'), 'platform': platform, 'identity': identity,
               'label': label, 'capability': capability, 'voice': voice, 'created_at': now_iso()}
        accounts[rec['id']] = rec
        _refs.bind(store)
        _desk._write_store(store)
    return v1_account(rec)


def apply_read_to_store(store: dict, rec: dict, read_via: str | None, profile: str | None,
                        project_id: str | None = None) -> None:
    """Set a read setting on the workspace record `rec` and mirror it onto every
    presence copy (engagement reads the copy). `project_id` files the account under
    that project's presence when no copy exists yet. Mutates `store`; the caller
    holds `_store_lock` and writes. Validation is the caller's."""
    _desk.apply_read_settings(rec, read_via, profile)
    presences = store['presences']
    account_id = rec['id']
    filed = False
    for pres in presences.values():
        for i, a in enumerate(pres.get('accounts') or []):
            if a == account_id:      # bare-id form: promote to a record
                pres['accounts'][i] = a = {'channel_id': account_id}
            if isinstance(a, dict) and a.get('channel_id') == account_id:
                if not a.get('platform'):
                    a['platform'] = rec['platform']
                _desk.apply_read_settings(a, read_via, profile)
                filed = filed or pres.get('project_id') == project_id
    if project_id and not filed:
        pres = presences.get(project_id) or _desk._empty_presence(project_id)
        copy = {'channel_id': account_id, 'platform': rec['platform']}
        _desk.apply_read_settings(copy, rec.get('read_via'), rec.get('browser_profile'))
        pres.setdefault('accounts', []).append(copy)
        pres['project_id'] = project_id
        pres['updated_at'] = now_iso()
        presences[project_id] = pres


_PATCHABLE = ('label', 'voice', 'read_via', 'browser_profile', 'project_id', 'organization_id')
_ORG_ID = re.compile(r'^\d{1,20}$')


def update_account(account_id: str, patch: dict) -> dict:
    """Label, voice, and how the Desk reads the account. `project_id` (optional,
    with a read setting) files the account under that project's presence, which
    is what engagement polls: a workspace account no project has bound is read
    by nobody. A read setting is mirrored onto every presence copy, so the
    workspace record and the copy engagement reads never disagree."""
    if not isinstance(patch, dict):
        raise AccountError('body must be a JSON object')
    unknown = sorted(k for k in patch if k not in _PATCHABLE)
    if unknown:
        raise AccountError(f'cannot change: {", ".join(unknown)}')
    read_via = patch.get('read_via')
    profile = patch.get('browser_profile')
    if read_via is not None and read_via not in _desk.READ_VIA:
        raise AccountError(f'read_via must be one of {_desk.READ_VIA}')
    if profile is not None and not isinstance(profile, str):
        raise AccountError('browser_profile must be text')
    project_id = patch.get('project_id')
    if project_id is not None and (not isinstance(project_id, str) or not project_id):
        raise AccountError('project_id must be a project id')
    clean = {}
    for k in ('label', 'voice'):
        if k in patch:
            clean[k] = _clean_text(patch[k], k, required=(k == 'label'))
    org = None
    if 'organization_id' in patch:
        org = _clean_text(patch['organization_id'], 'organization_id')
        if org and not _ORG_ID.match(org):
            raise AccountError('organization_id is digits only (the number in the Company Page admin URL)')
        clean['organization_id'] = org
    with _desk._store_lock:
        store = _desk._read_store()
        rec = store['accounts'].get(account_id)
        if rec is None:
            raise AccountError('account not found', 404)
        if org is not None and rec.get('platform') != 'linkedin':
            raise AccountError('organization_id applies to LinkedIn accounts only')
        if (read_via is not None or profile is not None) and rec.get('platform') not in READ_PLATFORMS:
            raise AccountError('read settings apply to X and LinkedIn accounts only')
        rec.update(clean)
        if read_via is not None or profile is not None:
            apply_read_to_store(store, rec, read_via, profile, project_id)
        _desk._write_store(store)
    return v1_account(rec)


def _places(plan_accounts, account_id: str) -> bool:
    for a in plan_accounts or []:
        if (a if isinstance(a, str) else (a or {}).get('channel_id')) == account_id:
            return True
    return False


def delete_account(account_id: str) -> bool:
    """Remove the account and its presence copies (a copy left behind would be
    lifted straight back in). Refused with 409 while a campaign that is not
    archived places it, or any piece still has a live version on it: deleting an
    account under a version that is waiting to go out, or already went out, would
    leave history pointing at nothing."""
    with _desk._store_lock:
        store = _desk._read_store()
        if account_id not in store['accounts']:
            return False
        using = [c for c in store['campaigns'].values()
                 if c.get('state') != 'dropped' and _places((c.get('plan') or {}).get('accounts'), account_id)]
        if using:
            names = ', '.join((c.get('title') or c.get('id')) for c in using[:3])
            raise AccountError(f'{len(using)} campaign(s) still use this account ({names}): '
                               'remove it from them or archive them first', 409)
        live = [v for p in (store.get('pieces') or {}).values() for v in p.get('versions') or []
                if v.get('account_id') == account_id and v.get('state') not in ('archived', 'skipped')]
        if live:
            raise AccountError(f'{len(live)} version(s) of pieces are still on this account: '
                               'archive or move them first', 409)
        del store['accounts'][account_id]
        for pres in store['presences'].values():
            pres['accounts'] = [a for a in (pres.get('accounts') or [])
                                if (a if isinstance(a, str) else (a or {}).get('channel_id')) != account_id]
        _desk._write_store(store)
    return True
