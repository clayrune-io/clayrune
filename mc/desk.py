"""The Desk — cross-project marketing state.

Spec: `docs/THE_DESK_SPEC.md`. Field scan behind it:
`docs/research/SOCIAL_WORKSPACE_FIELD_SCAN.md`.

WHY THIS MODULE EXISTS, and the one thing not to lose: the 2026-09-09 field
scan looked for a competitor that keeps a durable *strategy* and found none —
every product surveyed stores a queue or a calendar and nothing else, and
PostEverywhere's own comparison of eleven agents concluded that no tool
maintains strategic memory across decisions. A queue is table stakes. The four
stores below are the part that is not, so if a future refactor collapses them
back into "a list of pending drafts", the Desk has become the thing the scan
says already exists a dozen times over.

The five stores, and who owns them:

  * SIGNAL FEED   (here, append-only jsonl) — what happened across all projects.
  * VOICE PROFILES(here) — how each voice sounds, maintained by diffing every
                            edit the human makes to a draft. USER DATA, not a
                            constant: see STARTER_VOICES below.
  * CAMPAIGNS     (here) — live arcs, each with a thesis and a reason to run now.
  * STORY LEDGER  (here) — what was published, so the Desk stops repeating itself.
  * DRAFT QUEUE   (NOT here) — stays `social_queue` on the project record, with
                            its five existing CRUD routes in project_routes.py.

WHERE THE STATE LIVES, and why it is not where you would first put it:
`data/desk.json` and `data/desk_signals.jsonl` are SIBLINGS of DATA_DIR
(`data/projects/`), never members of it. `load_projects()` parses every `*.json`
under DATA_DIR as a project record, so a stray file there becomes a malformed
"project" and 500s `_get_active_restart_blockers`, taking down both restart
endpoints (the LOAD-BEARING DATA_DIR rule in CLAUDE.md). `data/schedules.json`
and `data/automation_suggestions.json` are the precedent this follows. Because
neither file enters DATA_DIR, neither needs an `EXCLUDED_SIDECAR_SUFFIXES` entry.

WHY THE SIGNAL FEED IS A JSONL AND NOT A LIST IN THE JSON: it is a log, and this
project has already paid for putting a log somewhere with a silent cap. The
backlog note API truncated at 2000 bytes and kept the last 50, both silently,
and ate roughly three weeks of steward findings before anyone measured it
(CLAUDE.md, 2026-08-15). An append-only file has no cap and drops nothing. If
you ever add pruning here, it must `_log` when it bites.

THE APPROVAL GATE IS PERMANENT — it is a platform term, not our caution.
Pinterest requires the end user to choose each item published, YouTube requires
prior express consent, and Postiz's own agent documentation asks for a human in
the loop. Nothing in this module publishes. `record_published()` records that a
human already released something; it is not a publish path. Keep it that way,
for the same reason `automation_suggestions` has no code path to the scheduler.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
import difflib
import hashlib
import json
import re
import threading
import uuid

from mc.core import _atomic_write_text, _log, now_iso
from mc import distiller as _distiller
from mc.desk_pane_pages import PANE_ONLY_PLATFORMS

# -- wired by server.py -------------------------------------------------------
# Path constants are server.py-owned; this module holds wired placeholders, the
# same shape automation_suggestions.STORE_PATH uses.
STORE_PATH: Path | None = None
SIGNALS_PATH: Path | None = None

_store_lock = threading.Lock()
_signals_lock = threading.Lock()

# v2 (MC-977 IA revision 2 amend, R1-P): adds the presence store and the
# campaign goal/term/how/map/approval shapes docs/THE_DESK_V1_IA_REVISION_2.md
# §5 freezes (fixture-identical, see tools/smoke/fixtures/desk-v1-fixtures.js). `_migrate`
# below carries any v1 record forward on read.
STORE_VERSION = 2

# VOICES ARE USER DATA, NOT SOURCE. This was `VOICES = ('ron', 'clayrune')` — one
# operator's identity, hardcoded in a file that ships to strangers, which is
# exactly what CLAUDE.md's "nothing operator-specific goes in the repo" rule
# forbids. A fresh install would have handed someone a voice named after another
# person. Caught 2026-09-10 by Ron, who asked the right question: what do other
# users do, and can a voice be per-project?
#
# So the list now lives in the store and the seed below is neutral. The two
# starter voices are ROLES rather than names, which is what was actually load-
# bearing about the original pair: they hold different standing to make claims.
# `personal` may say "I got this wrong for three weeks"; `product` may not say it
# in the first person. That distinction is why a story gets written twice from
# one signal rather than cross-posted, and it survives being renamed.
#
# An existing store keeps whatever voices it already has — see `_seed_voices`.
STARTER_VOICES = (
    {'name': 'personal', 'platform': 'x',
     'register': 'First person. A builder saying what they built and what it cost them.'},
    {'name': 'product', 'platform': 'linkedin',
     'register': 'The product speaking about itself. Never first person.'},
)

# `scope` mirrors the secrets vault: 'global', or a project id. Cheap to carry in
# the model now and expensive to retrofit later; the UI only offers global today.
VOICE_SCOPE_GLOBAL = 'global'

# How many published posts the repetition check looks back over. Not a cap on
# the ledger — the ledger keeps everything — just the window that "have we said
# this already" considers. Small on purpose: re-announcing a feature a year
# later is legitimate, re-announcing it in six weeks is the failure mode.
REPEAT_WINDOW = 40

# A signal is worth a human's attention above this. Deliberately a dumb
# threshold on a dumb score: the spec leaves open whether a clever story score
# is worth building before there is any published history to learn from, and
# notes that a dumb score plus Ron's veto may beat a smart one.
STORY_SCORE_FLOOR = 0.35


# -- store --------------------------------------------------------------------

def _empty_store() -> dict:
    return {
        'version': STORE_VERSION,
        'voices': {},
        'campaigns': {},
        'ledger': [],
        'proposals': {},
        'voices_seeded': False,
        'platforms': {},
        'platforms_seeded': False,
        'presences': {},
        # R1-W S5: workspace accounts, keyed by id (`mc/desk_accounts.py`).
        # Lifted from `presences[*].accounts` by `_lift_presence_accounts`.
        'accounts': {},
        # §10.6 (R1-L, MC-977 IA revision 2): findings live HERE, beside
        # voices/campaigns/ledger, under the same _store_lock — not in the
        # Distiller. `findings` keyed by id (same convention as `campaigns`);
        # `rejections` is a list because a rejection has no id of its own,
        # only the (dimension, arms, direction) tuple it durably suppresses.
        'playbook': {'findings': {}, 'rejections': []},
        # §8 R1-E: the inbound side. `items` keyed by id (engagement feed rows),
        # `reads` the append-only cost ledger of every platform read (§10.7:
        # "each read costed against the budget"), `coverage` the last
        # read outcome per project+platform so a gap is a recorded fact, not
        # an absence. See mc/desk_engagement.py.
        'engagement': {'items': {}, 'reads': [], 'coverage': {}},
        # R1-W S4: the v1 piece store (pieces keyed by id, each with its
        # versions[] and assets[]). See mc/desk_pieces.py.
        'pieces': {},
    }


def _migrate_presence_record(rec: dict) -> dict:
    """§5.2: `presence.production` -> `presence.budget`, renamed only.

    Guarded by absence of the new key, so running this on an already-migrated
    (or freshly-created v2) record is a no-op — required for idempotency.
    """
    if 'production' in rec and 'budget' not in rec:
        rec['budget'] = rec.pop('production')
    return rec


def _migrate_campaign_record(camp: dict) -> dict:
    """§5.1: `setup{step,done}` -> `map{stop,done}`; `goal.outcome` ->
    `goal.metric`, `goal.tracked` folded into `goal.source` (`manual` when the
    old goal was tracked, absent when it was not — §9 Q1's "manual is a real
    source", the closest a boolean has to one). Each half only fires when the
    OLD key is present and the NEW one is not, so re-running is a no-op.
    """
    if 'setup' in camp and 'map' not in camp:
        setup = camp.pop('setup') or {}
        camp['map'] = {'stop': setup.get('step'), 'done': list(setup.get('done') or [])}
    goal = camp.get('goal')
    if isinstance(goal, dict) and 'outcome' in goal and 'metric' not in goal:
        goal['metric'] = goal.pop('outcome')
        tracked = goal.pop('tracked', None)
        if 'source' not in goal:
            goal['source'] = 'manual' if tracked else None
    _retire_project_earmark(camp)
    return camp


def _retire_project_earmark(camp: dict) -> None:
    """MC-977 2026-10-01 (Presence screen retired): the project budget pool is
    gone, so a `source: 'project'` earmark has nothing left to be drawn from and
    nobody to edit it. Re-file it as the campaign's own amount: `amount` is
    untouched and `was_source` keeps what it used to be, so nothing is lost and
    re-running is a no-op. The approval snapshot is migrated the same way (its
    stored `bounds_hash` is left alone: the amount, which is what the owner
    approved, did not change).
    """
    budgets = [(camp.get('how') or {}).get('budget'),
               ((camp.get('approval') or {}).get('bounds') or {}).get('budget')]
    for b in budgets:
        if isinstance(b, dict) and b.get('source') == 'project':
            b['source'] = 'own'
            b.setdefault('was_source', 'project')


def _migrate_ledger_row(row: dict) -> dict:
    """§8 R1-L: the free-form `outcome` dict -> per-post `outcomes[]`; ledger
    rows gain `piece_id/format/account/term/cost`.

    Migration keeps whatever the old dict held as ONE `source:'manual'`
    entry rather than discarding it — the same "degrade to under-learn,
    never unlearn" posture `_read_store` already takes on a corrupt file.
    Guarded by absence of `outcomes` (like the other migrations here), so
    re-running is a no-op.
    """
    if 'outcomes' not in row:
        old = row.pop('outcome', None)
        if isinstance(old, dict) and old:
            entry = dict(old)
            entry.setdefault('source', 'manual')
            entry.setdefault('at', row.get('published_at'))
            row['outcomes'] = [entry]
        else:
            row['outcomes'] = []
    row.pop('outcome', None)
    row.setdefault('piece_id', None)
    row.setdefault('format', None)
    row.setdefault('account', None)
    row.setdefault('term', None)
    row.setdefault('cost', 0)
    return row


def _lift_presence_accounts(data: dict) -> None:
    """Copy every `presences[*].accounts[]` record into the workspace store
    `data['accounts']` (R1-W S5, plan §2.C). IDEMPOTENT and run on every read:
    an id already in the workspace store is never touched, so a second run (or a
    later write that persisted the first) changes nothing, and an edit made to
    the workspace record is never overwritten by a stale presence copy. The
    presence copy stays (engagement still reads its read settings from it). A
    bare-id entry carries no platform and is skipped: an account with no
    platform is not one the Desk can place anything on. `capability` defaults to
    `manual` (the human publishes): nothing lifted here can claim a direct
    publish route that was never configured. `created_at` is the presence
    record's own stamp, never `now`, so two reads of one unpersisted store agree."""
    accounts = data.setdefault('accounts', {})
    presences = data.get('presences') or {}
    for pid in sorted(presences):
        pres = presences[pid] or {}
        for acc in (pres.get('accounts') or []):
            cid = acc.get('channel_id') if isinstance(acc, dict) else None
            plat = acc.get('platform') if isinstance(acc, dict) else None
            if not cid or not isinstance(cid, str) or not plat or cid in accounts:
                continue
            ident = acc.get('identity') or cid
            rec = {
                'id': cid, 'platform': plat, 'identity': ident,
                'label': acc.get('label') or ident,
                'capability': (acc.get('capability') if acc.get('capability') in ('direct', 'manual')
                               else 'none' if plat in PANE_ONLY_PLATFORMS else 'manual'),
                'voice': acc.get('voice') or '',
                'created_at': acc.get('created_at') or pres.get('updated_at'),
            }
            if acc.get('read_via') in READ_VIA:
                rec['read_via'] = acc['read_via']
            if acc.get('browser_profile'):
                rec['browser_profile'] = acc['browser_profile']
            if acc.get('preview'):
                rec['preview'] = True
            accounts[cid] = rec


def _migrate_store(data: dict) -> dict:
    presences: dict = data.get('presences') or {}
    for pid, rec in list(presences.items()):
        presences[pid] = _migrate_presence_record(rec)
    _lift_presence_accounts(data)
    from mc import desk_account_refs  # lazy: it imports desk_oauth
    desk_account_refs.bind(data)
    campaigns: dict = data.get('campaigns') or {}
    for cid, camp in list(campaigns.items()):
        campaigns[cid] = _migrate_campaign_record(camp)
    data['ledger'] = [_migrate_ledger_row(dict(row)) for row in (data.get('ledger') or [])]
    playbook = data.setdefault('playbook', {})
    playbook.setdefault('findings', {})
    playbook.setdefault('rejections', [])
    engagement = data.setdefault('engagement', {})
    engagement.setdefault('items', {})
    engagement.setdefault('reads', [])
    engagement.setdefault('coverage', {})
    data['version'] = STORE_VERSION
    return data


def _read_store() -> dict:
    if STORE_PATH is None or not STORE_PATH.exists():
        return _empty_store()
    try:
        data = json.loads(STORE_PATH.read_text(encoding='utf-8'))
    except Exception as e:
        # Treat an unreadable store as empty for READS only. Every write below
        # merges into a freshly-read store rather than overwriting wholesale,
        # so a parse failure cannot silently erase voice history we could not
        # read — it degrades to "learned nothing yet", never to "unlearned".
        _log(f'[desk] store unreadable, treating as empty: {e}')
        return _empty_store()
    if not isinstance(data, dict):
        _log('[desk] store is not an object, treating as empty')
        return _empty_store()
    data.setdefault('version', STORE_VERSION)
    data.setdefault('voices', {})
    data.setdefault('campaigns', {})
    data.setdefault('ledger', [])
    data.setdefault('proposals', {})
    data.setdefault('voices_seeded', False)
    data.setdefault('platforms', {})
    data.setdefault('platforms_seeded', False)
    data.setdefault('presences', {})
    data.setdefault('playbook', {'findings': {}, 'rejections': []})
    data.setdefault('engagement', {'items': {}, 'reads': [], 'coverage': {}})
    data.setdefault('pieces', {})
    data.setdefault('accounts', {})
    return _migrate_store(data)


def _write_store(store: dict) -> None:
    if STORE_PATH is None:
        return
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(STORE_PATH, json.dumps(store, indent=2, ensure_ascii=False))


def _new_id(prefix: str) -> str:
    return f'{prefix}-{uuid.uuid4().hex[:8]}'


# -- presence (IA revision 2 §5.3; docs/THE_DESK_V1_IA_REVISION_2.md) --------
#
# The project-level record IA1 stubbed and R2-1 froze the fixture shape for
# (tools/smoke/fixtures/desk-v1-fixtures.js PROJECTS[].presence): who plans/writes for
# this project (`desk_agent`), the accounts bound to it, and the promotion
# `budget` (§5.2 rename of `production` — see `_migrate_presence_record`).

_DEFAULT_PRESENCE_BUDGET = {'amount': 0, 'period': 'month', 'per_job': 0, 'kinds': []}


def _empty_presence(project_id: str) -> dict:
    return {
        'project_id': project_id,
        'accounts': [],
        'audience': '',
        'ceilings': {},
        'replies': 'drafts',
        'desk_agent': None,
        'budget': dict(_DEFAULT_PRESENCE_BUDGET),
        'measurement': [],
        'updated_at': None,
    }


def get_presence(project_id: str) -> dict | None:
    with _store_lock:
        store = _read_store()
    return store['presences'].get(project_id)


def upsert_presence(project_id: str, patch: dict) -> dict:
    """Merge `patch` onto the project's presence record, creating it if new.

    Accepts an old-shaped `production` key defensively (a caller PUTting a
    payload it read before this ticket's rename) — folded into `budget`
    before the merge so it never lands in the store under its old name.
    """
    patch = dict(patch or {})
    if 'production' in patch and 'budget' not in patch:
        patch['budget'] = patch.pop('production')
    with _store_lock:
        store = _read_store()
        rec = store['presences'].get(project_id) or _empty_presence(project_id)
        for k, v in patch.items():
            if k == 'project_id':
                continue
            rec[k] = v
        rec['project_id'] = project_id
        rec['updated_at'] = now_iso()
        store['presences'][project_id] = rec
        _write_store(store)
        return rec


READ_VIA = ('pane', 'api')
# The only site with a paid API read route (LinkedIn has none: Ron/Dave 2026-10-06).
API_READ_PLATFORMS = ('x',)
_ACCOUNT_PLATFORMS = ('x', 'linkedin') + PANE_ONLY_PLATFORMS


def account_read_via(acc) -> str:
    """How the Desk reads this account's own mentions/replies and post stats.
    Absent or unrecognised = `pane` (the free route; Ron 2026-09-30). Never
    raises: a hand-edited store must not break a read."""
    v = acc.get('read_via') if isinstance(acc, dict) else None
    return v if v in READ_VIA else 'pane'


def _stored_platform(project_id: str, channel_id: str) -> str | None:
    with _store_lock:
        rec = _read_store()['presences'].get(project_id) or {}
    for a in rec.get('accounts') or []:
        if isinstance(a, dict) and a.get('channel_id') == channel_id:
            return a.get('platform')
    return None


def set_account_read_settings(project_id: str, channel_id: str, *, platform: str | None = None,
                              read_via: str | None = None,
                              browser_profile: str | None = None) -> dict:
    """Set `read_via` and/or the named browser profile on one presence account.

    Nothing in the backend populated `presence.accounts` before this (the UI's
    accounts are fixtures), so an account with no record yet is created here
    holding ONLY what reading needs: channel id + platform. That grants no
    publishing authority: publishing is bounded by the campaign `plan`, not by
    this list. Raises ValueError for a value outside the allowed set.
    """
    if read_via is not None and read_via not in READ_VIA:
        raise ValueError(f'read_via must be one of {READ_VIA}')
    if platform is not None and platform not in _ACCOUNT_PLATFORMS:
        raise ValueError(f'platform must be one of {_ACCOUNT_PLATFORMS}')
    if read_via == 'api' and (platform or _stored_platform(project_id, channel_id)) not in API_READ_PLATFORMS:
        raise ValueError('only X accounts can be read through an API; this one is read through the browser pane')
    if not channel_id or not isinstance(channel_id, str):
        raise ValueError('channel_id is required')
    with _store_lock:
        store = _read_store()
        rec = store['presences'].get(project_id) or _empty_presence(project_id)
        accounts = rec.setdefault('accounts', [])
        acc = None
        for i, a in enumerate(accounts):
            if a == channel_id:          # bare-id form: promote to a record
                accounts[i] = a = {'channel_id': channel_id}
            if isinstance(a, dict) and a.get('channel_id') == channel_id:
                acc = a
                break
        if acc is None:
            if not platform:
                raise ValueError('platform is required for an account with no record yet')
            acc = {'channel_id': channel_id, 'platform': platform}
            accounts.append(acc)
        if platform and not acc.get('platform'):
            acc['platform'] = platform
        apply_read_settings(acc, read_via, browser_profile)
        rec['project_id'] = project_id
        rec['updated_at'] = now_iso()
        store['presences'][project_id] = rec
        # R1-W S5: the workspace account store is the source of truth the v1
        # surfaces read, so this (presence-scoped) route writes through to it.
        # `_read_store` already lifted a copy that existed before this call; an
        # account created by this very call has none yet.
        ws = store['accounts'].get(channel_id)
        if ws is None:
            plat = acc.get('platform') or platform
            ws = store['accounts'][channel_id] = {
                'id': channel_id, 'platform': plat,
                'identity': channel_id, 'label': channel_id,
                'capability': 'none' if plat in PANE_ONLY_PLATFORMS else 'manual',
                'voice': '', 'created_at': rec['updated_at']}
        apply_read_settings(ws, read_via, browser_profile)
        _write_store(store)
        return dict(acc)


def apply_read_settings(acc: dict, read_via: str | None, browser_profile: str | None) -> None:
    """Set `read_via` and/or the named browser profile on one account dict (a
    presence copy or a workspace record: the same two keys on both, which is
    what lets engagement keep reading the presence copy). `''` clears the
    profile. Validation is the caller's."""
    if read_via is not None:
        acc['read_via'] = read_via
    if browser_profile is not None:
        name = browser_profile.strip().lower()
        if name:
            acc['browser_profile'] = name
        else:
            acc.pop('browser_profile', None)


# -- bounds hash + widening (IA revision 2 §5.1/§5.2; the R2-1 kit's own
# `computeBoundsHash`/`boundsWiden`/`nextBoundsHash`, static/js/desk-v1-kit.js)
#
# Ported line-for-line so the backend's widening call matches the UI's exactly
# — Dave's review of the UI version (2026-09-29) found the original only
# checked budget, missing a cadence raise, an account add or a term extension;
# every dimension here is additive-OR, same as the kit's.

def _stable_stringify(v) -> str:
    if v is None or isinstance(v, (str, int, float, bool)):
        return json.dumps(v)
    if isinstance(v, list):
        return '[' + ','.join(_stable_stringify(x) for x in v) + ']'
    if isinstance(v, dict):
        keys = sorted(v.keys())
        return '{' + ','.join(json.dumps(k) + ':' + _stable_stringify(v[k]) for k in keys) + '}'
    return json.dumps(v)


def compute_bounds_hash(bounds: dict | None) -> str:
    s = _stable_stringify(bounds or {})
    h = 5381
    for ch in s:
        h = ((h * 33) ^ ord(ch)) & 0xFFFFFFFF
    return format(h, 'x').rjust(8, '0')


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def _accounts_widen(prev: dict, nxt: dict) -> bool:
    def id_of(a):
        return a if isinstance(a, str) else (a or {}).get('channel_id')
    prev_ids = {id_of(a) for a in (prev.get('accounts') or [])}
    next_ids = [id_of(a) for a in (nxt.get('accounts') or [])]
    return any(i not in prev_ids for i in next_ids)


def _cadence_widen(prev: dict, nxt: dict) -> bool:
    p = prev.get('cadence') or {}
    n = nxt.get('cadence') or {}
    prev_per_week = p.get('per_week')
    next_per_week = n.get('per_week')
    if prev_per_week is None:
        return False
    # A ceiling removed is looser than any finite one.
    return next_per_week is None or next_per_week > prev_per_week


def _end_widen(prev: dict, nxt: dict) -> bool:
    p = prev.get('end') or {}
    n = nxt.get('end') or {}
    if p.get('date'):
        if not n.get('date'):
            return True
        if _parse_dt(n['date']) > _parse_dt(p['date']):
            return True
    prev_cap = p.get('post_cap')
    next_cap = n.get('post_cap')
    if prev_cap is not None:
        if next_cap is None or next_cap > prev_cap:
            return True
    return False


def _term_widen(prev: dict, nxt: dict) -> bool:
    p = prev.get('term') or {}
    n = nxt.get('term') or {}
    if not p.get('ends'):
        return False
    return not n.get('ends') or _parse_dt(n['ends']) > _parse_dt(p['ends'])


def _budget_widen(prev: dict, nxt: dict) -> bool:
    pb = prev.get('budget') or {}
    nb = nxt.get('budget') or {}
    if (nb.get('amount') or 0) > (pb.get('amount') or 0):
        return True
    if pb.get('source') == 'own' and nb.get('source') == 'project':
        return True
    return False


def bounds_widen(prev_bounds: dict | None, next_bounds: dict | None) -> bool:
    prev = prev_bounds or {}
    nxt = next_bounds or {}
    return (_accounts_widen(prev, nxt) or _cadence_widen(prev, nxt)
            or _end_widen(prev, nxt) or _term_widen(prev, nxt) or _budget_widen(prev, nxt))


def next_bounds_hash(prev_hash: str | None, prev_bounds: dict | None, next_bounds: dict | None) -> str:
    if bounds_widen(prev_bounds, next_bounds):
        return compute_bounds_hash(next_bounds)
    return prev_hash or compute_bounds_hash(prev_bounds)


# -- signal feed --------------------------------------------------------------
#
# The raw material that separates the Desk from a prompt box, and the one thing
# Clayrune has that a standalone social tool cannot get: it sees the backlog,
# the journals, the commits and the agent runs of every project, continuously,
# rather than a repo at release time. The field scan found the whole
# own-work-derivation category to be release-triggered and single-shot — the
# nearest competitors take a pasted GitHub URL (LangChain) or read one release
# and print to stdout (humanwhocodes/social-changelog). None holds a view of a
# project across time. Most entries here will never become posts, and that is
# the point: the feed is the evidence, the campaign board is the argument.

def append_signal(project_id: str, kind: str, summary: str,
                  *, ref: str | None = None, detail: str | None = None,
                  story_score: float | None = None,
                  occurred_at: str | None = None) -> dict:
    """Record something that happened. Append-only; never rewrites history."""
    entry = {
        'id': _new_id('sig'),
        'project_id': project_id,
        'kind': kind,                      # commit | backlog | journal | run | release | note
        'summary': (summary or '').strip(),
        'ref': ref,                        # commit sha, item key, file path
        'detail': detail,
        'story_score': score_signal(kind, summary, detail) if story_score is None else story_score,
        'occurred_at': occurred_at or now_iso(),
        'recorded_at': now_iso(),
        'consumed_by': None,               # draft id, once it becomes one
    }
    if SIGNALS_PATH is None:
        return entry
    with _signals_lock:
        SIGNALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with SIGNALS_PATH.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + '\n')
    return entry


def list_signals(project_id: str | None = None, *, limit: int = 200,
                 min_score: float | None = None,
                 unconsumed_only: bool = False,
                 sort: str = 'recent') -> list[dict]:
    """Reads the whole file; it is small and append-only.

    `sort='recent'` (default) is what the feed wants — chronology is the story.
    `sort='score'` is what the BOARD wants: the question there is "what is worth
    saying", not "what happened last", and the two orderings disagree often
    enough to matter. Score ties break by recency so the order stays stable.
    """
    rows = _read_signals()
    if project_id:
        rows = [r for r in rows if r.get('project_id') == project_id]
    if min_score is not None:
        rows = [r for r in rows if (r.get('story_score') or 0) >= min_score]
    if unconsumed_only:
        rows = [r for r in rows if not r.get('consumed_by')]
    if sort == 'score':
        rows.sort(key=lambda r: ((r.get('story_score') or 0),
                                 r.get('occurred_at') or ''), reverse=True)
    else:
        rows.sort(key=lambda r: r.get('occurred_at') or '', reverse=True)
    return rows[:limit]


def _read_signals() -> list[dict]:
    if SIGNALS_PATH is None or not SIGNALS_PATH.exists():
        return []
    out: list[dict] = []
    consumed: dict[str, str] = {}
    try:
        text = SIGNALS_PATH.read_text(encoding='utf-8')
    except Exception as e:
        _log(f'[desk] signal feed unreadable: {e}')
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            # One bad line must not cost the whole feed. Skip it loudly rather
            # than returning [] and letting the Desk believe nothing happened.
            _log('[desk] skipping unparseable signal line')
            continue
        if not isinstance(row, dict):
            continue
        # A consumption marker is a later line referring back to an earlier id,
        # which is how an append-only log records a state change without
        # rewriting the entry it is about.
        if row.get('_consume'):
            consumed[row['_consume']] = row.get('consumed_by') or 'unknown'
            continue
        out.append(row)
    for row in out:
        if row.get('id') in consumed:
            row['consumed_by'] = consumed[row['id']]
    return out


def mark_signal_consumed(signal_id: str, draft_id: str) -> None:
    """Append a consumption marker. Does not rewrite the original entry."""
    if SIGNALS_PATH is None:
        return
    with _signals_lock:
        SIGNALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with SIGNALS_PATH.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps({
                '_consume': signal_id,
                'consumed_by': draft_id,
                'at': now_iso(),
            }, ensure_ascii=False) + '\n')


# A deliberately unclever score. Weighted toward things a reader could feel and
# away from maintenance, which matches the survivors' advice in the field scan:
# announce features users can feel, batch small fixes into a weekly roundup,
# leave pure maintenance in the changelog.
_HIGH_SIGNAL = re.compile(
    r'\b(ship|shipped|launch|release[sd]?|now live|first|new|redesign|rewrote|'
    r'replaced|fixed a bug that|turns out|learned|cost me|broke|outage|'
    r'measured|benchmark)\b', re.I)
_LOW_SIGNAL = re.compile(
    r'\b(bump|chore|lint|typo|whitespace|rename|refactor|merge branch|'
    r'wip|formatting|dependenc)\w*\b', re.I)


def score_signal(kind: str, summary: str | None, detail: str | None = None) -> float:
    """0..1. A veto-able suggestion, not a verdict — see STORY_SCORE_FLOOR."""
    text = f'{summary or ""} {detail or ""}'
    score = {'release': 0.6, 'journal': 0.45, 'backlog': 0.3,
             'commit': 0.25, 'run': 0.15, 'note': 0.3}.get(kind, 0.25)
    if _HIGH_SIGNAL.search(text):
        score += 0.3
    if _LOW_SIGNAL.search(text):
        score -= 0.25
    # A one-liner rarely carries a story; a paragraph usually does.
    if len((summary or '').split()) >= 12:
        score += 0.1
    return max(0.0, min(1.0, round(score, 3)))


# -- voice profiles -----------------------------------------------------------
#
# What stops the output smelling like marketing. Seeded from Ron's own writing,
# then maintained by diffing every edit he makes to a draft — which the spec
# calls the highest-quality training signal in the system, and which today is
# discarded on save. Typefully is the closest thing in the field and its voice
# is IMPLICIT: inferred from post history, not something you can open and
# correct. This one is an editable object, which is the whole differentiator.

_VOICE_NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')


def _empty_voice(name: str, *, platform: str = 'x', destination: str = '',
                 scope: str = VOICE_SCOPE_GLOBAL) -> dict:
    return {
        'name': name,
        'platform': platform,  # which platform this voice posts to
        # Which ACCOUNT on that platform — distinct from platform, because two
        # voices can share a platform (two LinkedIn voices) and still need to
        # publish to different places. Empty means "the platform's default
        # account", so an existing install with no destination set is
        # unchanged. Never a rule-generator: what a destination MAY say is
        # carried by the voice's own `register`, not inferred here.
        'destination': destination,
        'scope': scope,        # 'global' or a project id
        'register': '',
        'banned': [],          # words and constructions this voice will not use
        'never_claims': [],    # claims this voice will not make
        'product_refs': [],    # how it refers to the product
        'rewrites': [],        # {before, after, at, draft_id} — the learning
        'updated_at': None,
    }


def _seed_voices(store: dict) -> bool:
    """Give a fresh install two starter voices. Returns True if it wrote any.

    SEEDING IS GATED ON A FLAG, NOT ON EMPTINESS. Inferring "fresh" from "no
    voices" cannot tell a new install apart from a user who deleted the starters
    on purpose — that version resurrected them on the next read, which is a store
    overruling a human's deletion. Caught by
    `test_an_existing_store_is_never_reseeded`.
    """
    if store.get('voices_seeded') or store.get('voices'):
        store['voices_seeded'] = True
        return False
    store['voices_seeded'] = True
    for spec in STARTER_VOICES:
        store.setdefault('voices', {})[spec['name']] = _empty_voice(
            spec['name'], platform=spec['platform'])
        store['voices'][spec['name']]['register'] = spec['register']
    return True


def voice_names(project_id: str | None = None) -> list[str]:
    """Voices usable here: the global ones, plus any scoped to this project."""
    with _store_lock:
        store = _read_store()
        # _seed_voices sets `voices_seeded` even when it writes no voices,
        # so persist whenever the flag was not already on disk.
        had_flag = store.get('voices_seeded')
        _seed_voices(store)
        if not had_flag:
            _write_store(store)
        out = []
        for name, v in store['voices'].items():
            scope = v.get('scope') or VOICE_SCOPE_GLOBAL
            if scope == VOICE_SCOPE_GLOBAL or scope == project_id:
                out.append(name)
    return sorted(out)


def is_voice(name: str, project_id: str | None = None) -> bool:
    return name in voice_names(project_id)


def default_voice(project_id: str | None = None) -> str | None:
    """The first available voice. There is NO hardcoded fallback: a literal
    default name was how one operator's identity leaked into five call sites."""
    names = voice_names(project_id)
    return names[0] if names else None


def get_voice(name: str) -> dict:
    if not is_voice(name):
        raise ValueError(f'unknown voice {name!r}; expected one of {voice_names()}')
    with _store_lock:
        store = _read_store()
        return store['voices'].get(name) or _empty_voice(name)


def list_voices(project_id: str | None = None) -> list[dict]:
    return [get_voice(v) for v in voice_names(project_id)]


def create_voice(name: str, *, platform: str = 'x', destination: str = '',
                 register: str = '', scope: str = VOICE_SCOPE_GLOBAL) -> dict:
    """Add a voice. Names are slug-shaped because they appear in briefs and URLs."""
    name = (name or '').strip().lower()
    if not _VOICE_NAME.match(name):
        raise ValueError('a voice name is 1-32 chars, lowercase letters, digits, - or _')
    with _store_lock:
        store = _read_store()
        _seed_voices(store)
        if name in store['voices']:
            raise ValueError(f'a voice named {name!r} already exists')
        v = _empty_voice(name, platform=platform, destination=destination,
                         scope=scope or VOICE_SCOPE_GLOBAL)
        v['register'] = register
        v['updated_at'] = now_iso()
        store['voices'][name] = v
        _write_store(store)
        return v


def delete_voice(name: str) -> bool:
    """Forget a voice AND everything it learned. Destructive on purpose — the
    rewrites are the only copy of what the human taught it."""
    with _store_lock:
        store = _read_store()
        if name not in (store.get('voices') or {}):
            return False
        del store['voices'][name]
        _write_store(store)
        return True


def update_voice(name: str, patch: dict) -> dict:
    """Human-edited fields only. Rewrites are appended by record_edit()."""
    if not is_voice(name):
        raise ValueError(f'unknown voice {name!r}; expected one of {voice_names()}')
    allowed = {'register', 'banned', 'never_claims', 'product_refs',
               'platform', 'destination', 'scope'}
    with _store_lock:
        store = _read_store()
        voice = store['voices'].get(name) or _empty_voice(name)
        for k, v in (patch or {}).items():
            if k in allowed:
                voice[k] = v
        voice['updated_at'] = now_iso()
        store['voices'][name] = voice
        _write_store(store)
        return voice


def record_edit(name: str, before: str, after: str,
                *, draft_id: str | None = None) -> dict | None:
    """Learn from a human edit to a draft.

    Called on SAVE of an edited draft. Returns the rewrite it stored, or None if
    the edit was cosmetic. This is the loop the field scan could not find closed
    in any product it surveyed — vendors assert "learning from performance data"
    and none of them could be verified. An edit is a better signal than a like
    anyway: it is the human saying, in their own words, what the right output was.
    """
    if not is_voice(name):
        raise ValueError(f'unknown voice {name!r}; expected one of {voice_names()}')
    before = (before or '').strip()
    after = (after or '').strip()
    if not before or not after or before == after:
        return None
    # Ignore whitespace-only and trivially small edits — they teach nothing and
    # would drown the real rewrites.
    ratio = difflib.SequenceMatcher(None, before, after).ratio()
    if ratio > 0.98:
        return None
    rewrite = {
        'before': before,
        'after': after,
        'draft_id': draft_id,
        'similarity': round(ratio, 3),
        'at': now_iso(),
    }
    with _store_lock:
        store = _read_store()
        voice = store['voices'].get(name) or _empty_voice(name)
        voice.setdefault('rewrites', []).append(rewrite)
        voice['updated_at'] = now_iso()
        store['voices'][name] = voice
        _write_store(store)
    return rewrite


def voice_brief(name: str, *, recent: int = 12) -> str:
    """Render a voice as prompt text for the agent that drafts.

    Deliberately the last N rewrites rather than a summary of them: a summary is
    where specificity goes to die, and specificity is the whole point of a voice.
    """
    v = get_voice(name)
    parts = [f'VOICE: {name}']
    if v.get('register'):
        parts.append(f"Register: {v['register']}")
    if v.get('banned'):
        parts.append('Never use: ' + ', '.join(v['banned']))
    if v.get('never_claims'):
        parts.append('Never claim: ' + '; '.join(v['never_claims']))
    if v.get('product_refs'):
        parts.append('Refer to the product as: ' + ', '.join(v['product_refs']))
    rewrites = (v.get('rewrites') or [])[-recent:]
    if rewrites:
        parts.append('\nEdits this human actually made — match these, they are '
                     'the voice more than any adjective above:')
        for r in rewrites:
            parts.append(f"  - was: {r['before']}\n    became: {r['after']}")
    return '\n'.join(parts)


# -- platform rules ------------------------------------------------------------
#
# PLATFORM RULES ARE USER DATA, NOT SOURCE. `desk_brief.PLATFORM_NOTES` was a
# hardcoded dict with exactly two keys, 'x' and 'linkedin' — every other
# platform got `.get(platform, '')`, an empty string, where the char limit and
# cost should have been. Measured 2026-09-10: clayrune_website's queue held an
# 837-char facebook draft and a 2236-char discord draft, both briefed with
# nothing, because nobody could reach the rules to set them. Ron asked the
# right question: "where can I insert rules on the post types?" The answer was
# nowhere — they were in the source. This is the same class of bug the
# STARTER_VOICES fix above closed for voice names, and it uses the same
# seeded-flag trick so a deleted seed does not resurrect itself.
#
# A platform with no rules is a REAL state, not a defect — an unseeded platform
# (facebook, discord, anything Ron adds) starts with none, and `desk_brief`
# must say so explicitly rather than handing the writer silence. See
# `get_platform_rules` (returns None) and `desk_brief._platform_rules_text`.
_PLATFORM_NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')

# Verified at docs.x.com/x-api/getting-started/pricing and learn.microsoft.com
# on 2026-09-09 — the same two platforms `PLATFORM_NOTES` used to hardcode.
# Nothing else is seeded: a limit or a cost invented for facebook/discord would
# be exactly the kind of unverified claim this module exists to avoid.
STARTER_PLATFORM_RULES = (
    {'name': 'x', 'char_limit': 280, 'text': (
        'A plain post costs $0.015 to publish; a post CONTAINING A LINK costs '
        '$0.200 — 13x. Include a URL only when the link is the point, not as a '
        'reflex. Threads are fine; each part bills separately.')},
    {'name': 'linkedin', 'char_limit': None, 'text': (
        'Long-form is fine and rewarded. Published free via Share on LinkedIn, '
        'capped at 150/day. LinkedIn suppressed reach on content its classifier '
        'reads as AI slop by ~40% (its CPO Hari Srinivasan, 2026-08-21), and '
        'external-link posts are demoted — put the link in a comment or omit it. '
        'Specific, first-hand and concrete survives; generic summary does not.')},
)


def _empty_platform_rules(name: str) -> dict:
    return {'name': name, 'text': '', 'char_limit': None, 'updated_at': None}


def _seed_platform_rules(store: dict) -> bool:
    """Seed x/linkedin once. Gated on a flag, not on emptiness — same reasoning
    as `_seed_voices`: inferring "fresh" from "no platforms" cannot tell a new
    install apart from someone who deleted a seed on purpose."""
    if store.get('platforms_seeded') or store.get('platforms'):
        store['platforms_seeded'] = True
        return False
    store['platforms_seeded'] = True
    for spec in STARTER_PLATFORM_RULES:
        store.setdefault('platforms', {})[spec['name']] = {
            'name': spec['name'], 'text': spec['text'],
            'char_limit': spec.get('char_limit'), 'updated_at': None,
        }
    return True


def _store_with_platforms_seeded() -> dict:
    store = _read_store()
    had_flag = store.get('platforms_seeded')
    _seed_platform_rules(store)
    if not had_flag:
        _write_store(store)
    return store


def platform_rule_names() -> list[str]:
    with _store_lock:
        return sorted(_store_with_platforms_seeded().get('platforms', {}).keys())


def list_platform_rules() -> list[dict]:
    with _store_lock:
        rows = list(_store_with_platforms_seeded().get('platforms', {}).values())
    rows.sort(key=lambda r: r.get('name') or '')
    return rows


def get_platform_rules(name: str) -> dict | None:
    """None means genuinely no rules are set — a real state, not a missing one.
    `desk_brief` must handle it explicitly rather than treating it as ''."""
    with _store_lock:
        return _store_with_platforms_seeded().get('platforms', {}).get(name)


def empty_platform_rules(name: str) -> dict:
    """The shape a not-yet-configured platform has. Public so the routes layer
    can hand the UI a form to fill in without reaching into a private."""
    return _empty_platform_rules(name)


def update_platform_rules(name: str, patch: dict) -> dict:
    """Upsert: setting rules for a platform for the first time creates its
    record. Unlike a voice, a platform name is not a closed set to validate
    against — Ron can set rules for any platform he actually publishes to."""
    name = (name or '').strip().lower()
    if not _PLATFORM_NAME.match(name):
        raise ValueError('a platform name is 1-32 chars, lowercase letters, digits, - or _')
    allowed = {'text', 'char_limit'}
    with _store_lock:
        store = _store_with_platforms_seeded()
        rules = store.setdefault('platforms', {}).get(name) or _empty_platform_rules(name)
        for k, v in (patch or {}).items():
            if k in allowed:
                rules[k] = v
        rules['updated_at'] = now_iso()
        store['platforms'][name] = rules
        _write_store(store)
        return rules


def delete_platform_rules(name: str) -> bool:
    with _store_lock:
        store = _store_with_platforms_seeded()
        if name not in (store.get('platforms') or {}):
            return False
        del store['platforms'][name]
        _write_store(store)
        return True


# -- campaign board -----------------------------------------------------------
#
# What makes the Desk an incubator rather than a draft generator. A campaign
# carries a THESIS and an agenda note explaining why it is running now, so the
# Board surface can answer "what is happening this month, and why" rather than
# just listing pending items.

# -- triage: what POSY thinks is worth saying ---------------------------------
#
# THE REGEX WAS IN THE WRONG SEAT. `score_signal` is a keyword heuristic, and
# until now it was the only thing deciding what deserved a post — the human then
# read every row and picked. Ron's expectation, and the spec's §2 Incubator
# ("scores signal into candidate stories and DISCARDS MOST OF IT"), is that the
# writer proposes and the human approves the SELECTION. With 120 signals in the
# feed the difference is the whole product: reading 120 rows does not scale, and
# the score cannot explain itself.
#
# So a proposal is Posy's argument for one post: which signal, which voice, and
# WHY — in her words, checkable against the signal it names. Two gates follow,
# and they ask different questions:
#
#   accept a proposal  -> "is this worth saying?"      -> triggers the draft
#   release a draft    -> "is this the right way to say it?"  (the Queue, existing)
#
# DISMISSAL IS LATCHED, deliberately copying `automation_suggestions`: a "no"
# that does not survive means the same suggestion returns next cycle and the
# human learns to ignore the surface. The latch is keyed on the SIGNAL id, not
# the proposal id — a fresh proposal for the same signal is the same ask wearing
# a new id, which is exactly how `preference-1ba8d678` came back from the dead.
PROPOSAL_STATES = ('proposed', 'accepted', 'dismissed')


def add_proposal(signal_id: str, voice: str, why: str, *,
                 campaign_id: str | None = None) -> dict | None:
    """Record one of Posy's suggestions. Returns None if it was already ruled on."""
    if not is_voice(voice):
        raise ValueError(f'unknown voice {voice!r}; expected one of {voice_names()}')
    with _store_lock:
        store = _read_store()
        props = store.setdefault('proposals', {})
        # Never re-offer a signal the human has already ruled on, and never
        # double-propose one that is already pending a decision.
        for p in props.values():
            if p.get('signal_id') == signal_id and p.get('state') != 'dismissed':
                return None
            if p.get('signal_id') == signal_id and p.get('state') == 'dismissed':
                return None
        prop = {
            'id': _new_id('prop'),
            'signal_id': signal_id,
            'voice': voice,
            'why': (why or '').strip(),
            'campaign_id': campaign_id,
            'state': 'proposed',
            'created_at': now_iso(),
            'decided_at': None,
            'draft_dispatch': None,
        }
        props[prop['id']] = prop
        _write_store(store)
        return prop


def list_proposals(state: str | None = 'proposed') -> list[dict]:
    with _store_lock:
        rows = list((_read_store().get('proposals') or {}).values())
    if state:
        rows = [r for r in rows if r.get('state') == state]
    rows.sort(key=lambda r: r.get('created_at') or '', reverse=True)
    return rows


def get_proposal(proposal_id: str) -> dict | None:
    with _store_lock:
        return (_read_store().get('proposals') or {}).get(proposal_id)


def decide_proposal(proposal_id: str, state: str, *,
                    draft_dispatch: str | None = None) -> dict | None:
    if state not in PROPOSAL_STATES:
        raise ValueError(f'unknown proposal state {state!r}')
    with _store_lock:
        store = _read_store()
        prop = (store.get('proposals') or {}).get(proposal_id)
        if not prop:
            return None
        prop['state'] = state
        prop['decided_at'] = now_iso()
        if draft_dispatch:
            prop['draft_dispatch'] = draft_dispatch
        _write_store(store)
        return prop


def signal_is_ruled_on(signal_id: str) -> bool:
    """True once the human has accepted or dismissed a proposal for this signal.

    Consulted at PROPOSAL time so a dismissed signal never re-enters the list —
    the same shape as `automation_suggestions.is_decided`, and for the same
    reason: a "no" recorded only against a row id is not a "no" at all.
    """
    with _store_lock:
        for p in (_read_store().get('proposals') or {}).values():
            if p.get('signal_id') == signal_id and p.get('state') in ('accepted', 'dismissed'):
                return True
    return False


# `draft` is the v1 Desk's first state: a campaign nobody has proposed yet, still
# being filled in through the six map stops (R1-W S1). The legacy Desk never
# creates one.
CAMPAIGN_STATES = ('draft', 'proposed', 'running', 'paused', 'done', 'dropped')


MAX_TERM_DAYS = 90


def _parse_term_date(value) -> datetime:
    """A `term.starts`/`term.ends` value as a datetime. Accepts 'YYYY-MM-DD'
    and full ISO timestamps (a trailing 'Z' included)."""
    text = str(value).strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _start_gate_problems(camp: dict) -> list[str]:
    """Server-side mirror of the client's Start gate (`DeskV1Kit.validatePlan`'s
    `_goalMissing` + `_termMissing`, IA revision 2 §9 Q1/Q2, both binding).

    Until R2-11 these were enforced only in the browser, so a PATCH straight to
    `state: running` skipped them. Gated on the new field shapes exactly as the
    client is, so a legacy campaign with no `goal.metric` / `term` (the old
    Board's "Start it") keeps activating as before:
      - a goal that has been set needs a target AND a measurement source;
      - a term runs at most 90 days (a long horizon renews, it does not stay
        open-ended under one approval).
    """
    problems = []
    goal = camp.get('goal')
    if isinstance(goal, dict) and 'metric' in goal:
        if goal.get('target') is None:
            problems.append('goal has no target')
        if not goal.get('source'):
            problems.append('goal has no measurement source')
    term = camp.get('term')
    if isinstance(term, dict) and term.get('starts') and term.get('ends'):
        try:
            days = (_parse_term_date(term['ends']) - _parse_term_date(term['starts'])).total_seconds() / 86400
        except (ValueError, TypeError):
            problems.append('term dates are not valid ISO dates')
        else:
            if days > MAX_TERM_DAYS:
                problems.append(f'term is {round(days)} days (max {MAX_TERM_DAYS})')
    return problems


def list_campaigns(state: str | None = None) -> list[dict]:
    with _store_lock:
        rows = list(_read_store()['campaigns'].values())
    if state:
        rows = [r for r in rows if r.get('state') == state]
    rows.sort(key=lambda r: r.get('created_at') or '', reverse=True)
    return rows


def _normalise_voices(voices) -> list[str]:
    """Accept a list, a single name, or nothing, and validate every entry."""
    if not voices:
        d = default_voice()
        return [d] if d else []
    if isinstance(voices, str):
        voices = [voices]
    out = []
    for v in voices:
        if not is_voice(v):
            raise ValueError(f'unknown voice {v!r}; expected one of {voice_names()}')
        if v not in out:
            out.append(v)
    return out


def campaign_platforms(camp: dict) -> list[str]:
    """Which platforms a campaign reaches — derived, never stored separately.

    A campaign does NOT carry a platform of its own. Platform is a property of
    the voice, so storing both would let them disagree; this reads them back.
    """
    seen = []
    for name in camp.get('voices') or ([camp['voice']] if camp.get('voice') else []):
        try:
            p = (get_voice(name) or {}).get('platform') or ''
        except ValueError:
            continue
        if p and p not in seen:
            seen.append(p)
    return seen


DEFAULT_VISUAL_REQUIREMENT = (
    'a real screenshot of the product feature this campaign is about — not '
    'generated art. LinkedIn suppresses AI-read content and this project has '
    'already ruled that generated imagery hallucinates illegible UI, so the '
    'default for anything depicting the product is a captured screenshot.'
)


def _campaign_bounds(camp: dict) -> dict:
    """The approval envelope (§5.1: "bounds now include how.budget and term").

    Bare fields, not the whole campaign — this dict is exactly what
    `bounds_widen`/`compute_bounds_hash` above compare, mirroring the kit's
    own bounds object (accounts/cadence/end from `plan`, term/budget layered
    on from the campaign's own `term`/`how.budget`).
    """
    plan = camp.get('plan') or {}
    how = camp.get('how') or {}
    return {
        'accounts': plan.get('accounts') or [],
        'cadence': plan.get('cadence') or {},
        'end': plan.get('end') or {},
        'term': camp.get('term') or {},
        'budget': how.get('budget') or {},
    }


def _check_earmark_locked(store: dict, project_id: str | None, budget: dict | None, *,
                          exclude_campaign_id: str | None = None) -> None:
    """Retired (MC-977 2026-10-01, Presence screen retired). Section 5.2 used to
    refuse a `source: 'project'` earmark the project's budget pool could not
    cover; the pool is no longer editable anywhere, and a limit the user cannot
    see and edit on the campaign must not refuse anything. Campaign budgets are
    self-contained now, so this never raises. Kept (not deleted) so the two call
    sites and any stored `presence.budget` need no migration; a `source:
    'project'` budget from an old client is re-filed as the campaign's own amount
    by `_retire_project_earmark` the next time the store is read.
    """
    return None


class CampaignExists(ValueError):
    """A client-chosen campaign id is already taken (the route answers 409)."""


def create_campaign(title: str, thesis: str, *, voice=None, voices=None,
                    agenda: str = '', project_ids: Iterable[str] = (),
                    planned: Iterable[str] = (), visual: str | None = None,
                    project_id: str | None = None, plan: dict | None = None,
                    goal: dict | None = None, term: dict | None = None,
                    how: dict | None = None, map_: dict | None = None,
                    subject: dict | None = None, state: str = 'proposed',
                    campaign_id: str | None = None,
                    voiceless_ok: bool = False) -> dict:
    # A CAMPAIGN CARRIES A SET OF VOICES, NOT ONE. Ron asked whether a campaign
    # should also pick a platform; the sharper version of his question is that a
    # single-voice campaign can only ever reach ONE room, and a thesis usually
    # deserves both. So the campaign is the ARGUMENT and the voices are how it is
    # carried — the same claim written twice, once per voice, never cross-posted
    # (which the platforms punish anyway).
    #
    # Platform stays derived from the voice. See `campaign_platforms`.
    #
    # `voice=` is still accepted so older callers and stored records keep working.
    # A v1 draft/proposal has no voice yet: voice is chosen per account in Where
    # and Start is what requires one (R1-W S1). Such a caller gets NO voice, not
    # the default one `_normalise_voices` would otherwise fill in; a caller that
    # names voices still has them validated. The legacy route still refuses.
    if voiceless_ok and not (voices or voice):
        voices = []
    else:
        voices = _normalise_voices(voices or voice)
    if not voices and not voiceless_ok:
        raise ValueError('no voices exist yet; create one before a campaign')
    if state not in CAMPAIGN_STATES:
        raise ValueError(f'unknown state {state!r}')
    camp = {
        'id': campaign_id or _new_id('camp'),
        'title': (title or '').strip(),
        'thesis': (thesis or '').strip(),
        'agenda': agenda,
        'voices': voices,
        # Kept in sync for anything still reading the singular field.
        'voice': voices[0] if voices else None,
        'project_ids': list(project_ids),
        'planned': list(planned),   # intended posts, in order
        # What kind of visual this campaign's posts need. Defaults to a real
        # product screenshot (docs/THE_DESK_SPEC.md standing position,
        # 2026-09-10) rather than leaving the writer to skip the visual or
        # invent one.
        'visual': (visual or '').strip() or DEFAULT_VISUAL_REQUIREMENT,
        'state': state,
        'created_at': now_iso(),
        'updated_at': now_iso(),
        # IA revision 2 §5.1 (R1-P amend): additive alongside the legacy
        # fields above — `project_id` is the parent project, `plan` carries
        # the accounts/cadence/end bounds `_campaign_bounds` reads.
        'project_id': project_id,
        'subject': subject or None,
        'plan': plan or {},
        'goal': goal or {},
        'term': term or {},
        'how': how or {},
        'map': map_ or {},
    }
    with _store_lock:
        store = _read_store()
        # §5.2: Launch refuses an earmark the project cannot cover — checked
        # here too, since create_campaign is the only entry point a caller
        # can hand a `how.budget` to on day one (no separate Launch route
        # exists yet; this ticket is the backend shapes, not R2-11).
        _check_earmark_locked(store, project_id, (camp['how'] or {}).get('budget'))
        if camp['id'] in store['campaigns']:
            raise CampaignExists(camp['id'])
        bounds = _campaign_bounds(camp)
        camp['approval'] = {'bounds': bounds, 'bounds_hash': compute_bounds_hash(bounds)}
        store['campaigns'][camp['id']] = camp
        _write_store(store)
    return camp


WHEN_SLOT_ORIGINS = ('user', 'agent')
WHEN_SLOT_STATES = ('suggested', 'accepted')
MAX_WHEN_SLOTS = 500


def _clean_campaign_when(value, store: dict, campaign_id: str) -> dict:
    """The When stop's own record, `campaign['when'] = {slots:[...]}` (R1-W S6):
    time windows the user reserved (`origin:'user'`) or an agent proposed
    (`origin:'agent'`, `state:'suggested'|'accepted'`), each optionally FILLED
    with a version Where already placed. Time only: a slot never creates a
    version and never names a different account than that version's, so a PATCH
    cannot use When to place a message somewhere Where did not. Anything else in
    the body is refused, not dropped. Raises ValueError (the route answers 400)."""
    if not isinstance(value, dict):
        raise ValueError('when must be an object')
    extra = sorted(k for k in value if k != 'slots')
    if extra:
        raise ValueError(f'when cannot carry: {", ".join(extra)}')
    slots = value.get('slots', [])
    if not isinstance(slots, list) or len(slots) > MAX_WHEN_SLOTS:
        raise ValueError(f'when.slots must be a list of at most {MAX_WHEN_SLOTS} slots')
    placed: dict[str, str | None] = {}
    for piece in (store.get('pieces') or {}).values():
        if piece.get('campaign_id') == campaign_id:
            for ver in piece.get('versions') or []:
                placed[ver.get('id')] = ver.get('account_id')
    seen: set[str] = set()
    out = []
    for s in slots:
        if not isinstance(s, dict):
            raise ValueError('each slot must be an object')
        unknown = sorted(k for k in s if k not in ('id', 'at', 'origin', 'state', 'because', 'filled'))
        if unknown:
            raise ValueError(f'a slot cannot carry: {", ".join(unknown)}')
        sid = s.get('id')
        if not isinstance(sid, str) or not sid or len(sid) > 80 or sid in seen:
            raise ValueError('each slot needs its own id (text, at most 80 characters)')
        seen.add(sid)
        at = s.get('at')
        try:
            _parse_dt(at)
        except (ValueError, AttributeError, TypeError):
            raise ValueError(f'slot {sid!r} needs an ISO 8601 time in `at`')
        if s.get('origin') not in WHEN_SLOT_ORIGINS:
            raise ValueError(f'slot {sid!r} origin must be one of {", ".join(WHEN_SLOT_ORIGINS)}')
        if s.get('state') is not None and s['state'] not in WHEN_SLOT_STATES:
            raise ValueError(f'slot {sid!r} state must be one of {", ".join(WHEN_SLOT_STATES)}')
        because = s.get('because')
        if because is not None and (not isinstance(because, list) or len(because) > 20
                                    or not all(isinstance(b, str) for b in because)):
            raise ValueError(f'slot {sid!r} because must be a list of finding ids')
        clean = {k: s[k] for k in ('id', 'at', 'origin', 'state', 'because') if s.get(k) is not None}
        filled = s.get('filled')
        if filled is not None:
            if not isinstance(filled, dict):
                raise ValueError(f'slot {sid!r} filled must be an object')
            bad = sorted(k for k in filled if k not in ('title', 'platform', 'channelId', 'versionId'))
            if bad:
                raise ValueError(f'slot {sid!r} filled cannot carry: {", ".join(bad)}')
            if not all(v is None or isinstance(v, str) for v in filled.values()):
                raise ValueError(f'slot {sid!r} filled values must be text')
            vid = filled.get('versionId')
            if vid is not None:
                if vid not in placed:
                    raise ValueError(f'slot {sid!r} is filled with a version this campaign does not have')
                if filled.get('channelId') not in (None, placed[vid]):
                    raise ValueError(f'slot {sid!r} names an account other than the one Where placed that '
                                     'version on; When sets times, it does not move a message')
            clean['filled'] = dict(filled)
        out.append(clean)
    return {'slots': out}


def update_campaign(campaign_id: str, patch: dict, *, forbid_start: bool = False) -> dict | None:
    # `approved`, `approvals`, `terms`, `started_at`, `policy_record` are
    # deliberately NOT here: they are written only by the human-action routes
    # (`start_campaign`, `approve_campaign`, `renew_campaign`), never by a PATCH.
    # `forbid_start` (the v1 route sets it) refuses a PATCH that would put a
    # campaign into `running` without a human approval snapshot on file; the
    # legacy Desk's "Start it" still activates through a plain PATCH.
    allowed = {'title', 'thesis', 'agenda', 'voice', 'voices', 'project_ids',
               'planned', 'state', 'visual',
               'project_id', 'subject', 'plan', 'goal', 'term', 'how', 'map',
               'when', 'answers', 'log'}
    patch = dict(patch or {})
    if 'state' in patch and patch['state'] not in CAMPAIGN_STATES:
        raise ValueError(f"unknown state {patch['state']!r}")
    # Either field may arrive; both end up consistent so nothing downstream has
    # to know which one the caller used.
    if 'voices' in patch or 'voice' in patch:
        # An EXPLICIT empty list is a request to have no voices, which is not a
        # campaign — refuse it. Falling through to `_normalise_voices(None)` here
        # silently substituted the default instead, so clearing the voices
        # quietly reassigned the campaign to whichever voice happened to be
        # first. Caught by `test_voices_can_be_changed_after_the_fact`.
        raw = patch['voices'] if 'voices' in patch else patch.get('voice')
        if raw is not None and not raw:
            raise ValueError('a campaign needs at least one voice')
        norm = _normalise_voices(raw)
        if not norm:
            raise ValueError('a campaign needs at least one voice')
        patch['voices'] = norm
        patch['voice'] = norm[0]
    with _store_lock:
        store = _read_store()
        camp = store['campaigns'].get(campaign_id)
        if not camp:
            return None
        prev_bounds = (camp.get('approval') or {}).get('bounds') or _campaign_bounds(camp)
        prev_hash = (camp.get('approval') or {}).get('bounds_hash') or compute_bounds_hash(prev_bounds)
        prev_state = camp.get('state')
        if (forbid_start and patch.get('state') == 'running' and prev_state != 'running'
                and not camp.get('approved')):
            raise ValueError('a campaign is started with POST /api/desk/campaigns/<id>/start, and '
                             'one with no approval on file is approved with .../approve (both are '
                             'human actions), not by changing its state')
        if 'when' in patch:
            patch['when'] = _clean_campaign_when(patch['when'], store, campaign_id)
        if isinstance(patch.get('how'), dict) and 'suggested' in patch['how']:
            # `how.suggested` is the agent's, written only by set_suggestions: a
            # client holding an older copy must not put it back or clear it.
            patch['how'] = {k: v for k, v in patch['how'].items() if k != 'suggested'}
        for k, v in (patch or {}).items():
            if k in allowed:
                camp[k] = v
        # R2-11: activating (Start, or Resume from paused) is gated on the
        # post-patch campaign, so one PATCH can fix the goal and start it.
        if camp.get('state') == 'running' and prev_state != 'running':
            problems = _start_gate_problems(camp)
            if problems:
                raise ValueError('cannot start: ' + '; '.join(problems))
        # §5.2: re-checked on every update that touches how.budget, not just
        # at creation — a PATCH is how a later Launch/edit raises the
        # earmark, and the project pool it's checked against may itself have
        # changed since create_campaign's own check.
        _check_earmark_locked(store, camp.get('project_id'), (camp.get('how') or {}).get('budget'),
                              exclude_campaign_id=campaign_id)
        next_bounds = _campaign_bounds(camp)
        camp['approval'] = {
            'bounds': next_bounds,
            'bounds_hash': next_bounds_hash(prev_hash, prev_bounds, next_bounds),
        }
        camp['updated_at'] = now_iso()
        _write_store(store)
        return camp


# -- agent suggestions (R1-W S7; plan M9/M10) -----------------------------------
#
# The Brief's "Suggest What / When / Where": the campaign's desk agent proposes,
# a human accepts. Stored under `camp['suggestions']`, NOT inside `camp['how']`,
# because a client PATCH of `how` replaces the whole object and would wipe a
# suggestion the agent wrote a moment earlier. `v1_campaign` projects it back as
# `how.suggested` (the shape the surfaces already read) and `suggestBlocker`.
# A suggestion is never a commitment: nothing here touches `plan`, `how.budget`
# or `approved`, and any text that names a bound is refused (`_DESK_BOUND_RE`).

SUGGESTION_KEYS = ('what', 'when', 'where', 'because', 'blocker')
MAX_SUGGESTED = 12


def _sugg_text(value, label: str, limit: int = 300, *, required: bool = True) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError(f'{label} is required')
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f'{label} must be text of at most {limit} characters')
    text = value.strip()
    if _DESK_BOUND_RE.search(text):
        raise ValueError(f'{label} names a campaign limit ({text[:60]!r}): a suggestion proposes content, '
                         'it never sets or raises a bound')
    return text


def _sugg_because(value, label: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 20 or not all(isinstance(b, str) and b for b in value):
        raise ValueError(f'{label} because must be a list of finding ids')
    return list(value)


def set_suggestions(campaign_id: str, body: dict) -> dict | None:
    """M10. Merge the keys present in `body` into the campaign's suggestions
    (an absent key is left as it was; an empty list clears it). Returns the v1
    campaign, or None when it does not exist. Raises ValueError (400)."""
    if not isinstance(body, dict):
        raise ValueError('body must be a JSON object')
    unknown = sorted(k for k in body if k not in SUGGESTION_KEYS)
    if unknown:
        raise ValueError(f'cannot set: {", ".join(unknown)}')
    for k in ('what', 'when', 'where'):
        if k in body and (not isinstance(body[k], list) or len(body[k]) > MAX_SUGGESTED):
            raise ValueError(f'{k} must be a list of at most {MAX_SUGGESTED}')
    with _store_lock:
        store = _read_store()
        camp = store['campaigns'].get(campaign_id)
        if not camp:
            return None
        accounts = store.get('accounts') or {}
        sugg = dict(camp.get('suggestions') or {})

        def channel(raw, label):
            if raw is None:
                return None
            if raw not in accounts:
                raise ValueError(f'{label} names an account that is not in the workspace: {raw!r}')
            return raw

        if 'what' in body:
            what = []
            for i, it in enumerate(body['what']):
                if not isinstance(it, dict):
                    raise ValueError('each what entry must be an object')
                what.append({'title': _sugg_text(it.get('title'), f'what[{i}].title', 200),
                             'channelId': channel(it.get('channel_id', it.get('channelId')), f'what[{i}]'),
                             'because': _sugg_because(it.get('because'), f'what[{i}]')})
            sugg['what'] = what
        slots = None
        if 'when' in body:
            slots = []
            for i, it in enumerate(body['when']):
                if not isinstance(it, dict):
                    raise ValueError('each when entry must be an object')
                try:
                    _parse_dt(it.get('at'))
                except (ValueError, AttributeError, TypeError):
                    raise ValueError(f'when[{i}] needs an ISO 8601 time in `at`')
                slots.append({'id': _new_id('slot'), 'at': it['at'], 'origin': 'agent', 'state': 'suggested',
                              'because': _sugg_because(it.get('because'), f'when[{i}]'),
                              'label': _sugg_text(it.get('label'), f'when[{i}].label', 120, required=False)})
            sugg['when'] = ({'label': slots[0]['label'] or f"{len(slots)} suggested time{'s' if len(slots) != 1 else ''}",
                             'slotId': slots[0]['id'], 'because': slots[0]['because']} if slots else None)
        if 'where' in body:
            where = []
            for i, it in enumerate(body['where']):
                if not isinstance(it, dict):
                    raise ValueError('each where entry must be an object')
                cid = channel(it.get('channel_id', it.get('channelId')), f'where[{i}]')
                if cid is None:
                    raise ValueError(f'where[{i}] needs a channel_id')
                where.append({'channelId': cid, 'label': accounts[cid].get('label') or accounts[cid].get('identity'),
                              'because': _sugg_because(it.get('because'), f'where[{i}]')})
            sugg['where'] = where[0] if where else None
        if 'because' in body:
            sugg['because'] = _sugg_because(body['because'], 'suggestion') or []
        if 'blocker' in body:
            b = body['blocker']
            if b is None:
                sugg['blocker'] = None
            else:
                if not isinstance(b, dict):
                    raise ValueError('blocker must be an object {id, question, answers[]}')
                answers = b.get('answers')
                if not isinstance(answers, list) or not 2 <= len(answers) <= 6:
                    raise ValueError('a blocker needs 2 to 6 answers to choose from')
                bid = b.get('id')
                if not isinstance(bid, str) or not re.match(r'^[A-Za-z0-9_-]{1,80}$', bid):
                    raise ValueError('a blocker needs an id of 1-80 letters, digits, - or _')
                clean_answers = []
                for j, a in enumerate(answers):
                    if not isinstance(a, dict) or not isinstance(a.get('id'), str):
                        raise ValueError(f'blocker answer {j} needs an id and a label')
                    clean_answers.append({'id': a['id'], 'label': _sugg_text(a.get('label'), f'blocker answer {j}', 120)})
                sugg['blocker'] = {'id': bid, 'question': _sugg_text(b.get('question'), 'blocker question', 400),
                                   'answers': clean_answers}
        if slots is not None:
            # Re-running Suggest replaces the still-suggested agent slots;
            # the user's own and the accepted ones stay.
            when = camp.get('when') if isinstance(camp.get('when'), dict) else {}
            kept = [s for s in (when.get('slots') or []) if not (s.get('origin') == 'agent' and s.get('state') == 'suggested')]
            camp['when'] = {'slots': kept + [{k: v for k, v in s.items() if k != 'label' and v is not None} for s in slots]}
        sugg['updated_at'] = now_iso()
        camp['suggestions'] = sugg
        camp['updated_at'] = now_iso()
        _write_store(store)
        return v1_campaign(camp)


# -- human approval snapshot (R1-W S2) -----------------------------------------
#
# `camp['approval']` (written by create/update above) tracks the CURRENT bounds
# and a hash that only moves on a widening: it cannot say what a human approved,
# because an ordinary PATCH rewrites it. `camp['approved']` is the record that
# can: {bounds, bounds_hash, at, by, term}, written ONLY by `start_campaign`,
# `approve_campaign` and `renew_campaign`, each reached through a human-only
# route, and absent from `update_campaign`'s allowlist. The publisher
# (`mc.desk_publish.publish`, via `publish_blockers`) refuses unless the current
# bounds still sit inside it. Standing position (desk release policy,
# 2026-09-23): widening ANY bound voids the approval, and the Desk can never
# approve or widen its own campaign.

class ApprovalRefused(ValueError):
    """A human-action route could not do what it was asked. `problems` lists
    why (the routes answer 409 with them); the message joins them."""

    def __init__(self, message: str, problems=None):
        super().__init__(message)
        self.problems = list(problems or [])


def _approval_snapshot(camp: dict, *, by: str, at: str | None = None) -> dict:
    bounds = json.loads(json.dumps(_campaign_bounds(camp)))
    return {
        'bounds': bounds,
        'bounds_hash': compute_bounds_hash(bounds),
        'at': at or now_iso(),
        'by': by,
        'term': (camp.get('term') or {}).get('index') or 1,
    }


def approval_problems(camp: dict) -> list[str]:
    """Why this campaign's current bounds are NOT covered by a human approval
    (empty list = covered). Fails closed: a record that is missing, that no
    longer matches its own hash, or whose bounds cannot be compared with the
    current ones, all count as not approved."""
    ap = camp.get('approved')
    if not isinstance(ap, dict) or not isinstance(ap.get('bounds'), dict):
        return ['no human approval on file']
    if compute_bounds_hash(ap['bounds']) != ap.get('bounds_hash'):
        return ['the approval record does not match its own hash']
    try:
        widened = bounds_widen(ap['bounds'], _campaign_bounds(camp))
    except (ValueError, TypeError):
        return ['bounds could not be compared with the approved ones']
    if widened:
        return ['bounds were widened after the approval; a human must approve again']
    return []


def awaiting_approval(camp: dict) -> bool:
    """A started campaign whose bounds the human has not approved (never, or
    since widened). Draft/proposed/over campaigns are not "awaiting": nothing
    about them can publish."""
    return camp.get('state') in ('running', 'paused') and bool(approval_problems(camp))


def publish_blockers(campaign_id: str) -> list[str]:
    """What stops the publisher posting for this campaign right now. Empty =
    clear. Called by `mc.desk_publish.publish` for any item that names its
    campaign."""
    with _store_lock:
        camp = _read_store()['campaigns'].get(campaign_id)
    if not camp:
        return [f'campaign {campaign_id!r} not found']
    if camp.get('state') != 'running':
        return [f"campaign is {camp.get('state')!r}, not running"]
    return approval_problems(camp)


def _v1_start_problems(camp: dict) -> list[str]:
    """`_start_gate_problems` plus the plan bounds the v1 client's
    `validatePlan` requires (a project, an account, a cadence ceiling, an end
    date or post cap), so a Start that skipped the browser's gate is refused
    here too."""
    problems = []
    plan = camp.get('plan') or {}
    if not camp.get('project_id'):
        problems.append('no project')
    if not plan.get('accounts'):
        problems.append('no accounts')
    if (plan.get('cadence') or {}).get('per_week') is None:
        problems.append('no cadence ceiling')
    end = plan.get('end') or {}
    if not end.get('date') and end.get('post_cap') is None:
        problems.append('no end date or post cap')
    return problems + _start_gate_problems(camp)


def start_campaign(campaign_id: str, *, by: str = 'human', policy_record: dict | None = None,
                   today: str | None = None) -> dict | None:
    """Start a draft/proposed campaign: the one place it becomes `running`
    with a human approval on file. Opens term 1 (today to the plan's end date,
    unless a term is already set), runs the Start gate on the result, stamps
    `approved` and appends it to `approvals`. Raises `ApprovalRefused` (and
    changes nothing) when the campaign is not startable. None if not found.
    The caller has already established that a human asked for this."""
    with _store_lock:
        store = _read_store()
        camp = store['campaigns'].get(campaign_id)
        if not camp:
            return None
        if camp.get('state') not in ('draft', 'proposed'):
            raise ApprovalRefused(f"campaign is {camp.get('state')!r}; only a draft or proposed "
                                  f"campaign can be started")
        now = now_iso()
        trial = json.loads(json.dumps(camp))
        term = trial.get('term') if isinstance(trial.get('term'), dict) else {}
        if not term.get('starts'):
            end = (trial.get('plan') or {}).get('end') or {}
            trial['term'] = {'index': 1, 'starts': today or datetime.now().date().isoformat(),
                             'ends': end.get('date') or None,
                             'post_cap': end.get('post_cap')}
        problems = _v1_start_problems(trial)
        if problems:
            raise ApprovalRefused('cannot start: ' + '; '.join(problems), problems)
        trial['state'] = 'running'
        trial['started_at'] = now
        snap = _approval_snapshot(trial, by=by, at=now)
        trial['approved'] = snap
        trial['approvals'] = list(trial.get('approvals') or []) + [snap]
        if isinstance(policy_record, dict):
            trial['policy_record'] = policy_record
        trial['updated_at'] = now
        store['campaigns'][campaign_id] = trial
        _write_store(store)
        return trial


def approve_campaign(campaign_id: str, *, by: str = 'human') -> dict | None:
    """The human re-approves the bounds a started campaign now carries (after a
    widening), replacing the snapshot and appending to `approvals`. Refuses a
    campaign that has not started (Start is its own route) and bounds that
    would fail the Start gate. None if not found."""
    with _store_lock:
        store = _read_store()
        camp = store['campaigns'].get(campaign_id)
        if not camp:
            return None
        if camp.get('state') not in ('running', 'paused'):
            raise ApprovalRefused(f"campaign is {camp.get('state')!r}; only a started campaign "
                                  f"is approved again (a new one is started)")
        problems = _v1_start_problems(camp)
        if problems:
            raise ApprovalRefused('cannot approve: ' + '; '.join(problems), problems)
        snap = _approval_snapshot(camp, by=by)
        camp['approved'] = snap
        camp['approvals'] = list(camp.get('approvals') or []) + [snap]
        camp['updated_at'] = snap['at']
        _write_store(store)
        return camp


def renew_campaign(campaign_id: str, *, by: str = 'human', today: str | None = None) -> dict | None:
    """Open the next term of a started campaign whose current term has ended:
    it starts where the last one ended and runs to the goal deadline or
    MAX_TERM_DAYS, whichever is sooner, and gets a fresh approval record. Only
    the TERM may be new relative to the approved bounds: if anything else was
    widened since, renewing would approve it silently, so that is refused and
    the human approves it first. None if not found."""
    with _store_lock:
        store = _read_store()
        camp = store['campaigns'].get(campaign_id)
        if not camp:
            return None
        if camp.get('state') not in ('running', 'paused'):
            raise ApprovalRefused(f"campaign is {camp.get('state')!r}; only a started campaign renews")
        term = camp.get('term') if isinstance(camp.get('term'), dict) else {}
        if not term.get('ends'):
            raise ApprovalRefused('this campaign has no term to renew')
        today_d = (_parse_term_date(today).date() if today else datetime.now().date())
        starts = _parse_term_date(term['ends'])
        if starts.date() > today_d:
            raise ApprovalRefused(f"term {term.get('index') or 1} has not ended yet "
                                  f"(ends {str(term['ends'])[:10]})")
        ap = camp.get('approved')
        if not isinstance(ap, dict) or not isinstance(ap.get('bounds'), dict):
            raise ApprovalRefused('no human approval on file to renew; approve the campaign first')
        try:
            other_widened = bounds_widen(ap['bounds'], {**_campaign_bounds(camp),
                                                        'term': ap['bounds'].get('term') or {}})
        except (ValueError, TypeError):
            other_widened = True
        if other_widened:
            raise ApprovalRefused('bounds other than the term were widened since the last approval; '
                                  'approve them first, then renew')
        end_cap = starts + timedelta(days=MAX_TERM_DAYS)
        deadline = (camp.get('goal') or {}).get('deadline')
        ends = min(end_cap, _parse_term_date(deadline)) if deadline else end_cap
        if not ends > starts:
            raise ApprovalRefused('the goal deadline has passed, so there is no next term to renew')
        nxt = {'index': (term.get('index') or 1) + 1, 'starts': starts.date().isoformat(),
               'ends': ends.date().isoformat(), 'post_cap': term.get('post_cap')}
        camp['terms'] = list(camp.get('terms') or [term]) + [nxt]
        camp['term'] = nxt
        snap = _approval_snapshot(camp, by=by)
        camp['approved'] = snap
        camp['approvals'] = list(camp.get('approvals') or []) + [snap]
        camp['updated_at'] = snap['at']
        _write_store(store)
        return camp


def delete_campaign(campaign_id: str) -> bool:
    with _store_lock:
        store = _read_store()
        if campaign_id not in store['campaigns']:
            return False
        del store['campaigns'][campaign_id]
        # A deleted campaign takes its pieces with it (R1-W S4): nothing else can
        # reach them, and an orphan would still show up in M1's `pieces`.
        pieces = store.get('pieces') or {}
        for pid in [k for k, p in pieces.items() if p.get('campaign_id') == campaign_id]:
            pieces.pop(pid, None)
            store.get('storyboards', {}).pop(f'piece:{pid}', None)   # mc/desk_storyboard.py
        _write_store(store)
        return True


# -- Goal: what a v1 client may write, and what the server derives (R1-W S3) ---
#
# docs/desk_v1/R1W_WIRING_PLAN.md M11. RULE (MET-01): a number with no data
# behind it is None, never 0. `goal.current` is therefore never client-writable:
# it is derived from the dated manual entries (`goal_current`), so a PATCH, a
# draft body carrying `current: 0`, or a stale stored value cannot make the
# Desk say "0 of 30" about something nobody measured.

GOAL_HORIZONS = ('short', 'long')
# `manual` is the one measurement source that exists (IA revision 2 §9 Q1); the
# automatic feed read is not built, so a PATCH naming any other source is
# refused rather than stored as a promise nothing keeps.
GOAL_SOURCES = ('manual',)
GOAL_WRITABLE = ('metric', 'target', 'baseline', 'unit', 'horizon', 'deadline', 'source', 'entries')
MAX_GOAL_ENTRIES = 2000


def _as_number(value):
    """`value` as a finite float, or None. bool is not a number here, and a
    numeric string is accepted (an outcome typed into a form arrives as one);
    NaN/inf and everything else is None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if value == value and value not in (float('inf'), float('-inf')) else None


def v1_goal_in(goal) -> dict:
    """The part of a v1 `goal` body the server will store. Allowlist: only the
    keys in GOAL_WRITABLE, and only the ones present. Like every other key a v1
    PATCH names, the result REPLACES the stored goal (the client sends its whole
    goal). `current` and anything else the client's goal object happens to carry
    are dropped, not stored. A value of the
    wrong type raises ValueError (the route answers 400, the client rolls the
    edit back)."""
    if goal is None:
        return {}
    if not isinstance(goal, dict):
        raise ValueError('goal must be an object')
    out: dict = {}
    for key in ('metric', 'unit'):
        if key in goal:
            v = goal[key]
            if v is not None and not isinstance(v, str):
                raise ValueError(f'goal.{key} must be text or null')
            out[key] = (v.strip() or None) if isinstance(v, str) else None
    for key in ('target', 'baseline'):
        if key in goal:
            v = goal[key]
            if v is not None and (isinstance(v, str) or _as_number(v) is None):
                raise ValueError(f'goal.{key} must be a number or null')
            out[key] = v
    if 'horizon' in goal:
        if goal['horizon'] is not None and goal['horizon'] not in GOAL_HORIZONS:
            raise ValueError(f'goal.horizon must be one of {GOAL_HORIZONS} or null')
        out['horizon'] = goal['horizon']
    if 'deadline' in goal:
        d = goal['deadline']
        if d is not None:
            try:
                _parse_term_date(d)
            except (ValueError, TypeError):
                raise ValueError('goal.deadline must be an ISO date or null')
        out['deadline'] = d
    if 'source' in goal:
        src = goal['source'] or None
        if src is not None and src not in GOAL_SOURCES:
            raise ValueError(f'goal.source must be one of {GOAL_SOURCES} or null')
        out['source'] = src
    if 'entries' in goal:
        entries = [] if goal['entries'] is None else goal['entries']
        if not isinstance(entries, list) or len(entries) > MAX_GOAL_ENTRIES:
            raise ValueError(f'goal.entries must be a list of at most {MAX_GOAL_ENTRIES}')
        clean = []
        for e in entries:
            if not isinstance(e, dict):
                raise ValueError('each goal entry must be {at, value}')
            value = e.get('value')
            if isinstance(value, (bool, str)) or _as_number(value) is None:
                raise ValueError('each goal entry needs a numeric value')
            try:
                _parse_term_date(e.get('at'))
            except (ValueError, TypeError):
                raise ValueError('each goal entry needs an ISO date in `at`')
            clean.append({'at': e['at'], 'value': value})
        out['entries'] = clean
    return out


def _latest_entry(entries, start: datetime | None = None, end: datetime | None = None):
    """The newest entry by `at`, optionally within [start, end). Ties keep the
    first listed, the same rule the browser's own reduce uses, so Home and the
    Goal stop cannot disagree about which entry is current. Entries that cannot
    be read are skipped, not counted as 0."""
    best, best_at = None, None
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict) or _as_number(e.get('value')) is None:
            continue
        try:
            at = _parse_term_date(e.get('at'))
        except (ValueError, TypeError):
            continue
        if (start is not None and at < start) or (end is not None and at >= end):
            continue
        if best_at is None or at > best_at:
            best, best_at = e, at
    return best


def goal_current(goal):
    """Where the goal stands now: the newest manual entry's value, or None when
    the goal has no measurement source or no entry yet. Never 0 for "no data"."""
    if not isinstance(goal, dict) or goal.get('source') != 'manual':
        return None
    last = _latest_entry(goal.get('entries'))
    return last['value'] if last else None


def _term_window(term) -> tuple[datetime, datetime] | None:
    """[starts, ends) of a term. A date-only `ends` runs through that day."""
    if not isinstance(term, dict) or not term.get('starts') or not term.get('ends'):
        return None
    try:
        start, end = _parse_term_date(term['starts']), _parse_term_date(term['ends'])
    except (ValueError, TypeError):
        return None
    if len(str(term['ends']).strip()) <= 10:
        end += timedelta(days=1)
    return (start, end) if end > start else None


def campaign_results(campaign_id: str, *, now: datetime | None = None) -> dict | None:
    """M11: how a campaign is doing, read live (None for an unknown campaign).

    `goal.current` is derived (`goal_current`); `terms[]` carries each term's own
    reading (the newest entry dated inside that term's window, None when there is
    none); `versions[]` is one row per post in the ledger naming this campaign,
    with the value of the goal's metric among its recorded outcomes; `costs` is
    what the ledger recorded. Every figure with nothing behind it is None and
    says why (`metric_label`: 'delayed' = a measured outcome may still arrive,
    'n/a' = the goal names no metric to read); no 0 stands in for it. `forecast`
    is None until current, target and an elapsed share of the term all exist."""
    now = now or datetime.now(timezone.utc)
    with _store_lock:
        store = _read_store()
        camp = store['campaigns'].get(campaign_id)
        if not camp:
            return None
        camp = json.loads(json.dumps(camp))
        rows = [json.loads(json.dumps(r)) for r in store['ledger'] if r.get('campaign_id') == campaign_id]
    goal = camp.get('goal') if isinstance(camp.get('goal'), dict) else {}
    manual = goal.get('source') == 'manual'
    current = goal_current(goal)
    target = _as_number(goal.get('target'))
    last = _latest_entry(goal.get('entries')) if manual else None

    forecast = None
    window = _term_window(camp.get('term'))
    if current is not None and target is not None and target > 0 and window:
        elapsed = (now - window[0]) / (window[1] - window[0])
        if elapsed > 0:
            projected = current / min(elapsed, 1)
            forecast = {'projected': round(projected, 2), 'target': target,
                        'label': 'on target' if projected >= target else 'below target'}

    terms_src = camp['terms'] if isinstance(camp.get('terms'), list) and camp['terms'] \
        else ([camp['term']] if isinstance(camp.get('term'), dict) else [])
    terms = []
    for t in terms_src:
        if not isinstance(t, dict):
            continue
        w = _term_window(t)
        reading = _latest_entry(goal.get('entries'), *w) if (w and manual) else None
        t_target = _as_number(t.get('target'))
        terms.append({'index': t.get('index'),
                      'current': reading['value'] if reading else None,
                      'target': t_target if t_target is not None else target})

    metric = (goal.get('metric') or '').strip().lower()
    versions = []
    for r in sorted(rows, key=lambda r: r.get('published_at') or ''):
        pick = None
        for o in r.get('outcomes') or []:
            if (isinstance(o, dict) and metric and str(o.get('metric') or '').strip().lower() == metric
                    and _as_number(o.get('value')) is not None
                    and (pick is None or str(o.get('at') or '') > str(pick.get('at') or ''))):
                pick = o
        if pick is not None:
            versions.append({'version_id': r.get('id'), 'piece_id': r.get('piece_id'),
                             'outcome': 'measured', 'metric': _as_number(pick['value']),
                             'metric_label': goal.get('metric')})
        else:
            versions.append({'version_id': r.get('id'), 'piece_id': r.get('piece_id'),
                             'outcome': 'unknown', 'metric': None,
                             'metric_label': 'delayed' if metric else 'n/a'})

    spend = round(sum(_as_number(r.get('cost')) or 0 for r in rows), 4) if rows else None
    return {
        'campaign_id': campaign_id,
        'goal': {'current': current, 'target': target, 'deadline': goal.get('deadline'),
                 'source': goal.get('source') or None,
                 'freshness': last['at'] if last else None},
        'forecast': forecast,
        'terms': terms,
        'versions': versions,
        'costs': {'ledger': spend},
    }


# -- v1 workspace read model (R1-W S0, docs/desk_v1/R1W_WIRING_PLAN.md M1) ----
#
# Desk v1's store (static/js/desk-v1-store.js) reads ONE bootstrap payload. The
# backend keeps its own campaign vocabulary (CAMPAIGN_STATES above) so the
# legacy Desk keeps working until it retires; the v1 words are translated HERE,
# on the way out, never stored (Dave's S0 decision, 2026-09-30).

_V1_STATE_OUT = {'running': 'active', 'done': 'completed', 'dropped': 'archived'}


def v1_campaign(camp: dict) -> dict:
    """A stored campaign in the shape the v1 surfaces read: `state` in v1 words
    (running->active, done->completed, dropped->archived; proposed/paused
    unchanged), `projectId` beside `project_id`, `plan.title` carrying the
    title. A deep copy: nothing the caller does to it reaches the store."""
    out = json.loads(json.dumps(camp))
    out.pop('chat', None)   # the agent conversation has its own GET (mc/desk_campaign_chat.py); it can be long
    state = camp.get('state') or ''
    out['state'] = _V1_STATE_OUT.get(state, state)
    out['projectId'] = camp.get('project_id')
    plan = out.get('plan') if isinstance(out.get('plan'), dict) else {}
    plan['title'] = plan.get('title') or camp.get('title') or ''
    out['plan'] = plan
    # The client's `camp.approval` is "what a human approved" (it compares it to
    # the bounds the Brief now holds). The stored `approval` is the legacy
    # current-bounds tracker an ordinary PATCH rewrites, so a v1 reader gets the
    # human snapshot under that name, or none, and the server's own verdict.
    out['approval'] = out.get('approved')
    out['awaiting_approval'] = awaiting_approval(camp)
    out['startedAt'] = camp.get('started_at')
    out['policyRecord'] = camp.get('policy_record')
    # `goal.current` is derived, never the stored number (see `goal_current`).
    if isinstance(out.get('goal'), dict):
        out['goal']['current'] = goal_current(camp.get('goal'))
    sugg = out.pop('suggestions', None)
    if isinstance(sugg, dict):
        how = out.get('how') if isinstance(out.get('how'), dict) else {}
        how['suggested'] = {k: sugg.get(k) for k in ('what', 'when', 'where', 'because')
                            if sugg.get(k) not in (None, [])}
        out['how'] = how
        out['suggestBlocker'] = sugg.get('blocker')
    return out


_V1_STATE_IN = {v1: stored for stored, v1 in _V1_STATE_OUT.items()}

# What a v1 client may name in a create/patch body. Everything else on the v1
# draft object is client-local (`id` is read by the route, `_`-prefixed flags,
# `rules`; `projectId` is renamed) and is dropped, never stored.
_V1_BODY_KEYS = ('subject', 'plan', 'goal', 'term', 'how', 'map', 'state', 'thesis',
                 'agenda', 'voice', 'voices', 'when')


def v1_campaign_in(body: dict) -> dict:
    """The inverse of `v1_campaign`: a v1-shaped create/patch body in the stored
    vocabulary. `state` words are translated (active->running, completed->done,
    archived->dropped), `projectId` becomes `project_id`, and `plan.title` is
    mirrored to `title` so the legacy Desk and the v1 surfaces never disagree
    about a campaign's name. Only keys present in `body` are present in the
    result, so a PATCH stays a patch."""
    body = body if isinstance(body, dict) else {}
    out: dict = {}
    for k in _V1_BODY_KEYS:
        if k in body:
            out[k] = body[k]
    if 'goal' in out:
        out['goal'] = v1_goal_in(out['goal'])
    if 'state' in out:
        out['state'] = _V1_STATE_IN.get(out['state'], out['state'])
    if 'projectId' in body:
        out['project_id'] = body['projectId']
    elif 'project_id' in body:
        out['project_id'] = body['project_id']
    plan = out.get('plan')
    if isinstance(plan, dict):
        if 'title' in plan:
            out['title'] = (plan.get('title') or '').strip()
        if 'thesis' not in out and plan.get('brief') is not None:
            out['thesis'] = (plan.get('brief') or '').strip()
    return out


PROJECT_STATES = ('active', 'paused')
# A campaign in one of these is over (or already paused): pausing leaves it be.
_NOT_PAUSABLE = ('done', 'dropped', 'paused')


def set_project_state(project_id: str, state: str) -> dict:
    """Pause or resume a project AND its campaigns in one write, so a half-
    paused project is impossible (R1-W S1; the v1 project page used to do this
    in the browser only).

    pause:  every campaign of the project that is not over and not already
            paused records `pre_pause_state` and becomes `paused`.
    resume: every campaign carrying `pre_pause_state` goes back to it. One going
            back to `running` is re-run through `_start_gate_problems`, the same
            gate Start uses, and one that fails stays paused (reported in
            `held`) rather than reactivating with a bound it can no longer meet.

    Returns `{presence, campaigns, changed, held}`; `campaigns` are the v1
    shapes of every campaign this call touched."""
    if state not in PROJECT_STATES:
        raise ValueError(f'state must be one of {PROJECT_STATES}')
    changed: list[dict] = []
    held: list[dict] = []
    with _store_lock:
        store = _read_store()
        rec = store['presences'].get(project_id) or _empty_presence(project_id)
        rec['state'] = state
        rec['project_id'] = project_id
        rec['updated_at'] = now_iso()
        store['presences'][project_id] = rec
        for camp in store['campaigns'].values():
            if camp.get('project_id') != project_id:
                continue
            cur = camp.get('state')
            if state == 'paused':
                if cur in _NOT_PAUSABLE:
                    continue
                camp['pre_pause_state'] = cur
                camp['state'] = 'paused'
            else:
                target = camp.get('pre_pause_state')
                if cur != 'paused' or not target:
                    continue
                if target == 'running':
                    problems = _start_gate_problems(camp)
                    if problems:
                        held.append({'id': camp['id'], 'reasons': problems})
                        continue
                camp['state'] = target
                camp.pop('pre_pause_state', None)
            camp['updated_at'] = now_iso()
            changed.append(camp)
        _write_store(store)
        return {'presence': rec, 'campaigns': [v1_campaign(c) for c in changed],
                'changed': len(changed), 'held': held}


def v1_accounts(store: dict) -> list[dict]:
    """The workspace's accounts in the v1 shape (`mc.desk_accounts.v1_account`):
    one row per `store['accounts']` record, each with its derived `publish`
    state. Reads the vault's METADATA (is the token there), so call it outside
    `_store_lock`."""
    from mc import desk_accounts  # lazy: desk_accounts imports this module
    return desk_accounts.v1_accounts(store)


def _v1_pieces(store: dict) -> list[dict]:
    from mc import desk_pieces  # lazy: desk_pieces imports this module
    rows = sorted((store.get('pieces') or {}).values(), key=lambda p: (p.get('created_at') or '', p['id']))
    return [desk_pieces.v1_piece(p) for p in rows]


def v1_workspace(projects: Iterable[dict]) -> dict:
    """M1: `{projects, campaigns, accounts, pieces}`. `pieces` are in the v1
    family shape (`mc.desk_pieces.v1_piece`), oldest first."""
    with _store_lock:
        store = _read_store()
    presences = store['presences']
    rows = []
    for p in projects:
        pid = p.get('id')
        if not pid:
            continue
        pres = presences.get(pid) or {}
        rows.append({
            'id': pid,
            'name': p.get('name') or pid,
            'state': pres.get('state') or 'active',
            'roster': [r.get('character') for r in (p.get('roster') or [])
                       if isinstance(r, dict) and r.get('character') and not r.get('removed_at')],
            'presence': {
                'replies': pres.get('replies') or 'drafts',
                'desk_agent': pres.get('desk_agent'),
                'state': pres.get('state') or 'active',
            },
        })
    camps = sorted(store['campaigns'].values(), key=lambda r: r.get('created_at') or '', reverse=True)
    return {
        'projects': rows,
        'campaigns': [v1_campaign(c) for c in camps],
        'accounts': v1_accounts(store),
        'pieces': _v1_pieces(store),
    }


# -- story ledger -------------------------------------------------------------
#
# Its job is NOT analytics. Its job is to stop the Desk repeating itself and to
# let it reference its own earlier posts. Without it, an autonomous writer
# re-announces the same feature every month — which is the single most likely
# way this feature embarrasses Ron in front of the exact audience the field scan
# says punishes it hardest (B2B, where founder credibility IS the product).

def record_published(*, platform: str, voice: str, body: str,
                     signal_id: str | None = None,
                     campaign_id: str | None = None,
                     project_id: str | None = None,
                     url: str | None = None,
                     published_at: str | None = None,
                     piece_id: str | None = None,
                     format: str | None = None,
                     account: str | None = None,
                     term: str | None = None,
                     cost: float = 0) -> dict:
    """Record that a human released something. NOT a publish path.

    `piece_id/format/account/term/cost` (§8 R1-L, §10.7): without these a
    filled-in outcome cannot be attributed to anything — the retro's
    per-dimension table groups by exactly these fields.
    """
    entry = {
        'id': _new_id('post'),
        'platform': platform,
        'voice': voice,
        'body': body,
        'signal_id': signal_id,
        'campaign_id': campaign_id,
        'project_id': project_id,
        'url': url,
        'published_at': published_at or now_iso(),
        'piece_id': piece_id,
        'format': format,
        'account': account,
        'term': term,
        'cost': cost,
        'outcomes': [],   # per-post outcomes[{metric, value, at, source}], §10.7
    }
    with _store_lock:
        store = _read_store()
        store['ledger'].append(entry)
        _write_store(store)
    return entry


def list_ledger(*, limit: int = 100, platform: str | None = None,
                project_id: str | None = None) -> list[dict]:
    with _store_lock:
        rows = list(_read_store()['ledger'])
    if platform:
        rows = [r for r in rows if r.get('platform') == platform]
    if project_id:
        rows = [r for r in rows if r.get('project_id') == project_id]
    rows.sort(key=lambda r: r.get('published_at') or '', reverse=True)
    return rows[:limit]


def record_outcome(post_id: str, metric: str, value, *,
                   source: str = 'manual', at: str | None = None) -> dict | None:
    """Append one per-post outcome entry (§8 R1-L: `outcomes[]` replaces the
    free-form `outcome` dict). Never overwrites an existing entry for the same
    metric — R1-E's `source:'feed'` entries and a human's typed entry for the
    same post + metric are BOTH kept (§10.7), so the retro can show its
    source (`typed 3 Oct` / `from x`) rather than silently picking one."""
    entry = {'metric': metric, 'value': value, 'at': at or now_iso(), 'source': source}
    with _store_lock:
        store = _read_store()
        for row in store['ledger']:
            if row.get('id') == post_id:
                row.setdefault('outcomes', []).append(entry)
                _write_store(store)
                return row
    return None


# -- engagement feed + read costing (§8 R1-E, §10.7) --------------------------
#
# Storage only. What to read, from whom, and what a read costs is
# mc/desk_engagement.py's business; this block is the same shape as the
# findings store below: every mutation under `_store_lock`, nothing here
# touches the network.

# Lane states, same vocabulary static/js/desk-v1-engagement.js `LANES` matches
# on: needs_you = Incoming, needs_reply = Suggested (reply drafted, awaiting
# the human), sent = Sent. The others never count as needing anyone.
ENGAGEMENT_STATES = ('needs_you', 'needs_reply', 'sent', 'reviewed', 'no_reply', 'stale', 'ignored')
ENGAGEMENT_SOURCES = ('our_posts', 'mentions', 'discussions')


def upsert_engagement_item(item: dict) -> tuple[dict, bool]:
    """Insert one feed row, deduped on (platform, external_id). Returns
    `(row, created)`. A re-read of a row we already hold changes nothing the
    human may have touched (`state`, `read_at`): it is a no-op returning the
    stored row, so polling twice can never resurrect a read item as unread."""
    platform, ext = item.get('platform'), item.get('external_id')
    if not platform or not ext:
        raise ValueError('engagement item needs platform and external_id')
    with _store_lock:
        store = _read_store()
        items = store['engagement']['items']
        for row in items.values():
            if row.get('platform') == platform and row.get('external_id') == ext:
                return dict(row), False
        row = {
            'id': _new_id('eng'),
            'project_id': item.get('project_id'),
            'campaign_id': item.get('campaign_id'),
            'platform': platform,
            'account': item.get('account'),
            'source': item.get('source') or 'mentions',
            'post_id': item.get('post_id'),
            'external_id': ext,
            'author': item.get('author'),
            'excerpt': item.get('excerpt') or '',
            'url': item.get('url'),
            'created_at': item.get('created_at') or now_iso(),
            'fetched_at': now_iso(),
            'state': item.get('state') or 'needs_you',
            'read_at': None,
        }
        items[row['id']] = row
        _write_store(store)
        return dict(row), True


def list_engagement_items(*, project_id: str | None = None, campaign_id: str | None = None,
                          platform: str | None = None, source: str | None = None,
                          state: str | None = None, limit: int = 200) -> list[dict]:
    with _store_lock:
        rows = [dict(r) for r in _read_store()['engagement']['items'].values()]
    for key, want in (('project_id', project_id), ('campaign_id', campaign_id),
                      ('platform', platform), ('source', source), ('state', state)):
        if want:
            rows = [r for r in rows if r.get(key) == want]
    rows.sort(key=lambda r: r.get('created_at') or '', reverse=True)
    return rows[:limit]


def mark_engagement_read(item_id: str, *, at: str | None = None) -> dict | None:
    """Stamp `read_at` once. Idempotent: a second call keeps the first stamp."""
    with _store_lock:
        store = _read_store()
        row = store['engagement']['items'].get(item_id)
        if row is None:
            return None
        if not row.get('read_at'):
            row['read_at'] = at or now_iso()
            _write_store(store)
        return dict(row)


# What a person may set on a feed row by hand (R1-W S8, plan M23b). `sent` is
# written only by `record_engagement_reply` (a reply that really went out) and
# `stale` only by the reader, so neither is settable here.
_ENGAGEMENT_SETTABLE_STATES = ('needs_you', 'needs_reply', 'reviewed', 'no_reply', 'ignored')
_ENGAGEMENT_DRAFT_MAX = 2000


def get_engagement_item(item_id: str) -> dict | None:
    with _store_lock:
        row = _read_store()['engagement']['items'].get(item_id)
        return json.loads(json.dumps(row)) if row else None


def update_engagement_item(item_id: str, patch: dict, *, by: str = 'human') -> dict | None:
    """Apply a hand edit to one feed row: `state` (see `_ENGAGEMENT_SETTABLE_STATES`),
    `assigned_to` (a name, or None to unassign), `taken_over` (bool) and `draft`
    (`{text}`; empty text clears it). None when the row does not exist; ValueError
    for a value the row may not take, with nothing written.

    A draft is the proposed reply, never a sent one: saving one moves a
    `needs_you` row to `needs_reply` (the Suggested lane) and clearing it moves
    it back. A row someone has taken over takes no new draft (taking over
    cancels the queued reply), and a row already `sent` takes no state change.
    """
    unknown = set(patch) - {'state', 'assigned_to', 'taken_over', 'draft'}
    if unknown:
        raise ValueError(f'cannot change {sorted(unknown)}: only state, assigned_to, taken_over and draft')
    with _store_lock:
        store = _read_store()
        row = store['engagement']['items'].get(item_id)
        if row is None:
            return None
        new = dict(row)
        if 'state' in patch:
            if patch['state'] not in _ENGAGEMENT_SETTABLE_STATES:
                raise ValueError(f"state must be one of {list(_ENGAGEMENT_SETTABLE_STATES)}")
            if row.get('state') == 'sent':
                raise ValueError('this reply was already sent')
            new['state'] = patch['state']
        if 'assigned_to' in patch:
            who = patch['assigned_to']
            if who is not None and not (isinstance(who, str) and 0 < len(who.strip()) <= 40):
                raise ValueError('assigned_to must be a name of 1-40 characters, or null')
            new['assigned_to'] = who.strip() if who else None
        if 'taken_over' in patch:
            if not isinstance(patch['taken_over'], bool):
                raise ValueError('taken_over must be true or false')
            new['taken_over'] = patch['taken_over']
        if 'draft' in patch:
            draft = patch['draft']
            text = ((draft or {}).get('text') if isinstance(draft, dict) else None)
            if draft is not None and not isinstance(draft, dict):
                raise ValueError('draft must be {text} or null')
            if text is not None and not isinstance(text, str):
                raise ValueError('draft text must be text')
            text = (text or '').strip()
            if len(text) > _ENGAGEMENT_DRAFT_MAX:
                raise ValueError(f'a draft is at most {_ENGAGEMENT_DRAFT_MAX} characters')
            if text and new.get('taken_over'):
                raise ValueError('this thread is taken over: resume it before a draft is saved')
            if row.get('state') == 'sent':
                raise ValueError('this reply was already sent')
            new['draft'] = {'text': text, 'by': by, 'at': now_iso()} if text else None
            if 'state' not in patch:
                if text and new.get('state') == 'needs_you':
                    new['state'] = 'needs_reply'
                elif not text and new.get('state') == 'needs_reply':
                    new['state'] = 'needs_you'
        if new.get('taken_over') and 'draft' not in patch and new.get('draft'):
            # Taking over cancels the queued reply: nothing the agent drafted
            # may be sent as if it were still wanted.
            new['draft'] = None
            if new.get('state') == 'needs_reply' and 'state' not in patch:
                new['state'] = 'needs_you'
        store['engagement']['items'][item_id] = new
        _write_store(store)
        return json.loads(json.dumps(new))


def record_engagement_reply(item_id: str, receipt: dict, text: str) -> dict | None:
    """A reply really went out: state `sent`, with what was sent and its receipt."""
    with _store_lock:
        store = _read_store()
        row = store['engagement']['items'].get(item_id)
        if row is None:
            return None
        row['state'] = 'sent'
        row['draft'] = None
        row['reply'] = {'text': text, 'post_id': receipt.get('post_id'),
                        'permalink': receipt.get('permalink'), 'posted_at': receipt.get('posted_at')}
        _write_store(store)
        return json.loads(json.dumps(row))


def record_read(*, platform: str, project_id: str | None, kind: str, resources: int,
                cost: float, ok: bool, error: str | None = None,
                campaign_id: str | None = None) -> dict:
    """Append one row to the read-cost ledger. Failed reads are recorded too
    (`ok: False`, cost as charged, usually 0) so spend is never undercounted
    by omission. `campaign_id` is set only on a read charged to a campaign's
    budget (`mc/desk_spend_guard.py`); the key is absent otherwise."""
    entry = {'id': _new_id('read'), 'at': now_iso(), 'platform': platform,
             'project_id': project_id, 'kind': kind, 'resources': int(resources),
             'cost': float(cost), 'ok': bool(ok), 'error': error}
    if campaign_id:
        entry['campaign_id'] = campaign_id
    with _store_lock:
        store = _read_store()
        store['engagement']['reads'].append(entry)
        _write_store(store)
    return entry


def list_reads(*, project_id: str | None = None, since: str | None = None) -> list[dict]:
    with _store_lock:
        rows = list(_read_store()['engagement']['reads'])
    if project_id:
        rows = [r for r in rows if r.get('project_id') == project_id]
    if since:
        rows = [r for r in rows if (r.get('at') or '') >= since]
    return rows


def set_read_coverage(project_id: str, platform: str, *, ok: bool, cursor: str | None = None,
                      error: str | None = None, via: str | None = None,
                      error_kind: str | None = None) -> dict:
    """Remember the outcome of the latest read attempt. `last_ok_at` only moves
    on success, so "connected but never successfully read" stays distinguishable
    from "read, found nothing". `cursor` only moves on a successful read.

    `via` is the read route (`api` | `pane`) the attempt used: a success on one
    route must not read as "ok" once the account is switched to the other
    (`desk_engagement.platform_coverage` compares it). `error_kind` tags a
    failure the UI words specially (`not_signed_in`)."""
    with _store_lock:
        store = _read_store()
        rec = store['engagement']['coverage'].setdefault(project_id, {}).setdefault(
            platform, {'last_ok_at': None, 'last_attempt_at': None,
                       'last_error': None, 'cursor': None})
        rec['last_attempt_at'] = now_iso()
        if via:
            rec['via'] = via
        if ok:
            rec['last_ok_at'] = rec['last_attempt_at']
            rec['last_error'] = None
            rec['error_kind'] = None
            if cursor:
                # since_id for the next mentions read: without it every poll
                # re-reads (and re-pays for) the same newest N mentions.
                rec['cursor'] = cursor
        else:
            rec['last_error'] = error
            rec['error_kind'] = error_kind
        _write_store(store)
        return dict(rec)


def get_read_coverage(project_id: str) -> dict:
    with _store_lock:
        return {k: dict(v) for k, v in
                (_read_store()['engagement']['coverage'].get(project_id) or {}).items()}


def record_feed_outcome(post_id: str, metric: str, value, *, at: str | None = None) -> dict | None:
    """Write one `source:'feed'` per-post outcome (§10.7). Returns the ledger
    row, or None for an unknown post.

    Two guarantees the retro depends on:
    - A typed (`source != 'feed'`) entry for the same post + metric is NEVER
      touched: both are kept and the retro shows each one's source.
    - Goals are measured daily (§10.8 Q1), so a feed entry is one per
      post + metric + calendar day: a second read the same day REPLACES that
      day's earlier feed entry instead of stacking duplicates. Feed entries
      only ever replace feed entries.
    `value` must be a real number from the platform; callers skip a metric the
    platform did not return rather than passing 0 (§10.7: never a fake zero).
    """
    at = at or now_iso()
    day = at[:10]
    entry = {'metric': metric, 'value': value, 'at': at, 'source': 'feed'}
    with _store_lock:
        store = _read_store()
        for row in store['ledger']:
            if row.get('id') != post_id:
                continue
            outs = row.setdefault('outcomes', [])
            for i, o in enumerate(outs):
                if (o.get('source') == 'feed' and o.get('metric') == metric
                        and (o.get('at') or '')[:10] == day):
                    outs[i] = entry
                    break
            else:
                outs.append(entry)
            _write_store(store)
            return row
    return None


_WORD = re.compile(r"[a-z0-9']+")


def _shingles(text: str) -> set[str]:
    words = _WORD.findall((text or '').lower())
    return {' '.join(words[i:i + 3]) for i in range(max(0, len(words) - 2))}


def similar_published(body: str, *, threshold: float = 0.35,
                      window: int = REPEAT_WINDOW) -> list[dict]:
    """Have we already said this? Returns matching ledger entries, worst first.

    Trigram Jaccard rather than an embedding: this needs to be explainable to
    the human who is about to be told "you already posted this", and a number
    they can argue with beats a similarity score they cannot.
    """
    target = _shingles(body)
    if not target:
        return []
    hits = []
    for row in list_ledger(limit=window):
        other = _shingles(row.get('body') or '')
        if not other:
            continue
        overlap = len(target & other) / len(target | other)
        if overlap >= threshold:
            hits.append({**row, 'overlap': round(overlap, 3)})
    hits.sort(key=lambda r: r['overlap'], reverse=True)
    return hits


def already_said(body: str, **kw) -> bool:
    return bool(similar_published(body, **kw))


# -- playbook (§10 outcome learning loop, MC-977 R1-L) ------------------------
#
# Findings live HERE, in `store['playbook']`, beside voices/campaigns/ledger —
# §10.6's recommendation, not the Distiller. A finding is numbers tied to
# campaign ids consumed by one brief; the Distiller's skill artifacts are
# prose loaded into every agent's prompt on every project, and would leak
# Desk statistics into unrelated agents. Reused, not rebuilt, from the
# Distiller: `authority_violation` (below) and the durable-rejection pattern
# of `_suppress_artifact` / `_is_suppressed`.

# Desk bounds pattern (§10.5.1, second layer beyond `authority_violation`): a
# finding may describe what happened ("got 2.1x the clicks"), never prescribe
# widening a bound ("raise the cadence"). `authority_violation` alone does not
# cover this — its phrases are about the AGENT's own permissions, not a
# CAMPAIGN's bounds, so the Desk needs its own narrow pattern for the second
# vocabulary.
_DESK_BOUND_WORDS = r'cadence|budget|spend|cap|ceiling|accounts?|approval'
_DESK_BOUND_RE = re.compile(
    r'\b(?:raise|increase|more)\b(?:\s+\w+){0,4}\s+\b(?:' + _DESK_BOUND_WORDS + r')\b',
    re.IGNORECASE)


def _guarded_text(text: str | None) -> str | None:
    """Drop (never edit) model-written finding text that fails either
    authority check. Failing closed here, before the text ever reaches the
    store, is the same posture `_generate_and_write_artifact` takes for
    skills — a gate a human has to click past is not a gate."""
    if not text:
        return text
    violation = _distiller.authority_violation(text)
    if violation:
        _log(f'[desk] playbook text dropped (authority_violation: {violation!r}): {text!r}')
        return None
    if _DESK_BOUND_RE.search(text):
        _log(f'[desk] playbook text dropped (Desk bounds pattern): {text!r}')
        return None
    return text


def _arms_key(arms) -> tuple:
    if isinstance(arms, dict):
        return tuple(sorted(arms.items()))
    return tuple(arms or ())


def evidence_key(evidence: Iterable[dict]) -> str:
    """§10.5.3: hash of the sorted (campaign_id, term) set backing a finding.
    Same key => same evidence => a rejected finding is not re-proposed; NEW
    evidence (different campaigns/terms) changes the key and may return."""
    pairs = sorted({(e.get('campaign_id'), e.get('term')) for e in (evidence or [])})
    digest = hashlib.sha256(json.dumps(pairs, sort_keys=True).encode('utf-8')).hexdigest()
    return digest[:16]


def is_finding_suppressed(project_id: str | None, dimension: str, arms,
                          direction: str | None, evidence_key_: str) -> bool:
    """§10.5.3 "No" is durable: a rejection matches on (project, dimension,
    arms, direction). A `permanent` rejection (Don't suggest again) suppresses
    regardless of evidence; a plain Reject only suppresses THIS evidence — new
    evidence (a different `evidence_key_`) may re-propose it."""
    arms_k = _arms_key(arms)
    with _store_lock:
        store = _read_store()
    for r in store['playbook']['rejections']:
        if r.get('project_id') != project_id or r.get('dimension') != dimension:
            continue
        if r.get('direction') != direction or _arms_key(r.get('arms')) != arms_k:
            continue
        if r.get('permanent') or r.get('evidence_key') == evidence_key_:
            return True
    return False


def propose_finding(*, project_id: str | None, dimension: str, arms,
                    account: str | None = None, metric: str | None = None,
                    effect: dict, evidence: list[dict], n_total: int,
                    confidence: str, maybe_why: str | None = None,
                    contradicts: str | None = None) -> str:
    """Add a `proposed` finding. ALWAYS `origin: 'unattended'` (§10.5.2): the
    retro that produces this is code-computed, not a human judgement, so its
    output starts on the unattended side of the loop no matter who or what
    triggered the retro run. It only becomes `origin: 'interactive'` when Ron
    confirms it (`confirm_finding` below) — autonomous output never becomes
    autonomous input, the same rule `exploration_read_floor` enforces.

    `contradicts` (R2-16, §10.2): the id of a CONFIRMED finding this one
    points the opposite direction from — `desk_retro.run_retro` passes it
    when it has already called `mark_stale` on that finding for the same
    reason. Purely descriptive (evidence trail for the UI's `Contradicts F3`
    line); it carries no authority of its own."""
    fid = _new_id('finding')
    finding = {
        'id': fid, 'project_id': project_id, 'scope': 'project',
        'dimension': dimension, 'arms': arms, 'account': account, 'metric': metric,
        'effect': effect, 'evidence': evidence, 'n_total': n_total,
        'confidence': confidence, 'maybe_why': _guarded_text(maybe_why),
        'state': 'proposed', 'origin': 'unattended',
        'decided_at': None, 'decided_by': None, 'edited_text': None,
        'stale_at': None, 'stale_reason': None, 'contradicts': contradicts,
    }
    with _store_lock:
        store = _read_store()
        store['playbook']['findings'][fid] = finding
        _write_store(store)
    return fid


def get_finding(finding_id: str) -> dict | None:
    with _store_lock:
        return _read_store()['playbook']['findings'].get(finding_id)


def list_findings(project_id: str | None = None, state: str | None = None) -> list[dict]:
    with _store_lock:
        rows = list(_read_store()['playbook']['findings'].values())
    if project_id:
        rows = [r for r in rows if r.get('project_id') == project_id]
    if state:
        rows = [r for r in rows if r.get('state') == state]
    return rows


def confirm_finding(finding_id: str, *, edited_text: str | None = None,
                    decided_by: str | None = None) -> dict | None:
    """Only Ron moves a finding between states (§10.2) — this route (and
    reject/dont_suggest_again/undo_reject below) is what desk_routes.py must
    refuse to an unattended caller."""
    with _store_lock:
        store = _read_store()
        f = store['playbook']['findings'].get(finding_id)
        if f is None:
            return None
        f['state'] = 'confirmed'
        f['origin'] = 'interactive'
        f['decided_at'] = now_iso()
        f['decided_by'] = decided_by
        if edited_text is not None:
            f['edited_text'] = _guarded_text(edited_text)
        _write_store(store)
        return dict(f)


def _record_rejection(store: dict, f: dict, *, permanent: bool) -> None:
    store['playbook']['rejections'].append({
        'finding_id': f['id'], 'project_id': f.get('project_id'),
        'dimension': f.get('dimension'), 'arms': f.get('arms'),
        'direction': (f.get('effect') or {}).get('direction'),
        'evidence_key': evidence_key(f.get('evidence') or []),
        'decided_at': f['decided_at'], 'permanent': permanent,
    })


def reject_finding(finding_id: str, *, decided_by: str | None = None) -> dict | None:
    """Plain Reject: durable against THIS evidence only (§10.5.3) — the same
    finding may return with >=10 new posts from campaigns/terms outside the
    rejected evidence set."""
    with _store_lock:
        store = _read_store()
        f = store['playbook']['findings'].get(finding_id)
        if f is None:
            return None
        f['state'] = 'rejected'
        f['decided_at'] = now_iso()
        f['decided_by'] = decided_by
        _record_rejection(store, f, permanent=False)
        _write_store(store)
        return dict(f)


def dont_suggest_again(finding_id: str, *, decided_by: str | None = None) -> dict | None:
    """§10.5.3: suppresses this dimension + arms + direction for the project
    PERMANENTLY, regardless of future evidence — lifted only by `undo_reject`."""
    with _store_lock:
        store = _read_store()
        f = store['playbook']['findings'].get(finding_id)
        if f is None:
            return None
        f['state'] = 'rejected'
        f['decided_at'] = now_iso()
        f['decided_by'] = decided_by
        _record_rejection(store, f, permanent=True)
        _write_store(store)
        return dict(f)


def undo_reject(finding_id: str) -> dict | None:
    """The project page's `Undo reject` (§10.4) — the only thing that lifts a
    `Don't suggest again` suppression. Moves the finding back to `proposed`
    and drops ALL of its rejection records, so the same evidence may be
    re-proposed on the next retro run rather than waiting for new evidence."""
    with _store_lock:
        store = _read_store()
        f = store['playbook']['findings'].get(finding_id)
        if f is None or f.get('state') != 'rejected':
            return None
        f['state'] = 'proposed'
        f['decided_at'] = None
        f['decided_by'] = None
        store['playbook']['rejections'] = [
            r for r in store['playbook']['rejections'] if r.get('finding_id') != finding_id]
        _write_store(store)
        return dict(f)


# ── Stale (R2-16, §10.2) ──────────────────────────────────────────────────────
#
# "confirmed -> stale when a newer retro points the other way ... or after 180
# days -> Ron re-confirms or retires." Two ways IN (a contradicting retro via
# `mark_stale`, called by `desk_retro.run_retro`; age, via the sweep below),
# both system-triggered — neither stamps `decided_by`, because going stale is
# not a decision, it is the trigger for one. Only `reconfirm_finding` and
# `retire_finding` are decisions, and (like confirm/reject/undo above) only
# Ron may make them — `desk_routes.py` refuses an unattended caller on both.

STALE_AGE_DAYS = 180


def mark_stale(finding_id: str, *, reason: str) -> dict | None:
    """Only a `confirmed` finding can go stale — mirrors `undo_reject` only
    operating on `rejected`. Returns None (no-op) for any other state, so a
    caller need not check state first."""
    with _store_lock:
        store = _read_store()
        f = store['playbook']['findings'].get(finding_id)
        if f is None or f.get('state') != 'confirmed':
            return None
        f['state'] = 'stale'
        f['stale_at'] = now_iso()
        f['stale_reason'] = reason
        _write_store(store)
        return dict(f)


def sweep_stale_findings(*, now: str | None = None, age_days: int = STALE_AGE_DAYS) -> list[str]:
    """The 180-day half of §10.2's stale trigger (the other half — a newer
    retro pointing the other way — is `mark_stale`, called from
    `desk_retro.run_retro`). Not wired to any request; a scheduled/steward
    cycle calls this directly. Returns the ids just marked stale."""
    now_dt = datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
    marked: list[str] = []
    with _store_lock:
        store = _read_store()
        for f in store['playbook']['findings'].values():
            if f.get('state') != 'confirmed' or not f.get('decided_at'):
                continue
            decided_dt = datetime.fromisoformat(f['decided_at'])
            if (now_dt - decided_dt).days >= age_days:
                f['state'] = 'stale'
                f['stale_at'] = now_iso()
                f['stale_reason'] = f'{age_days} days since last confirmation'
                marked.append(f['id'])
        if marked:
            _write_store(store)
    return marked


def reconfirm_finding(finding_id: str, *, edited_text: str | None = None,
                      decided_by: str | None = None) -> dict | None:
    """Ron's response to a stale finding that still holds (§10.2): stale ->
    confirmed, resetting the 180-day clock. Only operates on a `stale`
    finding — the same "only its own prior state" guard `undo_reject`
    applies to `rejected`."""
    with _store_lock:
        store = _read_store()
        f = store['playbook']['findings'].get(finding_id)
        if f is None or f.get('state') != 'stale':
            return None
        f['state'] = 'confirmed'
        f['origin'] = 'interactive'
        f['decided_at'] = now_iso()
        f['decided_by'] = decided_by
        f['stale_at'] = None
        f['stale_reason'] = None
        if edited_text is not None:
            f['edited_text'] = _guarded_text(edited_text)
        _write_store(store)
        return dict(f)


def retire_finding(finding_id: str, *, decided_by: str | None = None) -> dict | None:
    """Ron's other response to a stale finding: drop it for good. Distinct
    from `reject_finding` — a retired finding was once confirmed and earned
    real evidence, so retiring it records no rejection and blocks nothing;
    it is a closed chapter, not a "no". Only operates on a `stale` finding."""
    with _store_lock:
        store = _read_store()
        f = store['playbook']['findings'].get(finding_id)
        if f is None or f.get('state') != 'stale':
            return None
        f['state'] = 'retired'
        f['decided_at'] = now_iso()
        f['decided_by'] = decided_by
        _write_store(store)
        return dict(f)


def _render_finding_sentence(f: dict) -> str:
    """§10.2's rendered-from-structure sentence. Approximate wording — the
    spec gives one worked example, not a literal template; what matters, and
    what a caller renders from structure, is that the state machine and its
    guardrails (never in this module: bounds, always human-gated confirm) are
    authoritative, not this prose."""
    arms = f.get('arms') or {}
    effect = f.get('effect') or {}
    direction = effect.get('direction') or ''
    ratio = effect.get('ratio')
    winner_key, _, loser_key = direction.partition('>')
    winner = arms.get(winner_key) if isinstance(arms, dict) else None
    loser = arms.get(loser_key) if isinstance(arms, dict) else None
    metric = f.get('metric') or 'the metric'
    where = f" on {f['account']}" if f.get('account') else ''
    if winner and loser and ratio:
        return (f"{winner} got {ratio:.1f}× the {metric} per post of {loser}{where} "
                f"({f.get('n_total')} posts, {f.get('confidence')}).")
    return f"{f.get('dimension')}: {direction} ({metric}, n={f.get('n_total')}, {f.get('confidence')})."


def playbook_brief(project_id: str) -> str:
    """The PLAYBOOK section for the drafting brief (§10.3, beside
    `voice_brief`) — CONFIRMED findings ONLY. Proposed, rejected and stale
    findings never reach here: a finding earns an agent's attention by a human
    confirming it, not merely by existing."""
    findings = list_findings(project_id=project_id, state='confirmed')
    if not findings:
        return 'PLAYBOOK: no confirmed findings yet for this project.'
    lines = ['PLAYBOOK (confirmed findings — cite the id in `because`, never invent one):']
    for f in findings:
        text = f.get('edited_text') or _render_finding_sentence(f)
        lines.append(f"  - {f['id']} ({f.get('confidence')}): {text}")
    return '\n'.join(lines)
