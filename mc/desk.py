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

from datetime import datetime
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

# -- wired by server.py -------------------------------------------------------
# Path constants are server.py-owned; this module holds wired placeholders, the
# same shape automation_suggestions.STORE_PATH uses.
STORE_PATH: Path | None = None
SIGNALS_PATH: Path | None = None

_store_lock = threading.Lock()
_signals_lock = threading.Lock()

# v2 (MC-977 IA revision 2 amend, R1-P): adds the presence store and the
# campaign goal/term/how/map/approval shapes docs/THE_DESK_V1_IA_REVISION_2.md
# §5 freezes (fixture-identical, see static/js/desk-v1-fixtures.js). `_migrate`
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
        # §10.6 (R1-L, MC-977 IA revision 2): findings live HERE, beside
        # voices/campaigns/ledger, under the same _store_lock — not in the
        # Distiller. `findings` keyed by id (same convention as `campaigns`);
        # `rejections` is a list because a rejection has no id of its own,
        # only the (dimension, arms, direction) tuple it durably suppresses.
        'playbook': {'findings': {}, 'rejections': []},
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
    return camp


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


def _migrate_store(data: dict) -> dict:
    presences: dict = data.get('presences') or {}
    for pid, rec in list(presences.items()):
        presences[pid] = _migrate_presence_record(rec)
    campaigns: dict = data.get('campaigns') or {}
    for cid, camp in list(campaigns.items()):
        campaigns[cid] = _migrate_campaign_record(camp)
    data['ledger'] = [_migrate_ledger_row(dict(row)) for row in (data.get('ledger') or [])]
    playbook = data.setdefault('playbook', {})
    playbook.setdefault('findings', {})
    playbook.setdefault('rejections', [])
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
# (static/js/desk-v1-fixtures.js PROJECTS[].presence): who plans/writes for
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


CAMPAIGN_STATES = ('proposed', 'running', 'paused', 'done', 'dropped')


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


def _earmarked_total_locked(store: dict, project_id: str, *, exclude_campaign_id: str | None = None) -> float:
    total = 0.0
    for c in store['campaigns'].values():
        if c.get('project_id') != project_id:
            continue
        if exclude_campaign_id and c.get('id') == exclude_campaign_id:
            continue
        if c.get('state') in ('archived', 'dropped', 'done', 'completed'):
            continue
        b = (c.get('how') or {}).get('budget') or {}
        if b.get('source') == 'project':
            total += float(b.get('amount') or 0)
    return total


def project_earmarked_total(project_id: str, *, exclude_campaign_id: str | None = None) -> float:
    """Sum of live campaigns' project-sourced earmarks for this project (§5.2)."""
    with _store_lock:
        store = _read_store()
    return _earmarked_total_locked(store, project_id, exclude_campaign_id=exclude_campaign_id)


def _check_earmark_locked(store: dict, project_id: str | None, budget: dict | None, *,
                          exclude_campaign_id: str | None = None) -> None:
    """§5.2: "Launch refuses an earmark the project cannot cover and says by
    how much." Only fires for a `source: 'project'` budget against a project
    that has its own budget set — a project with no presence/budget yet has
    nothing to enforce against (validatePlan's own project-optional stance).
    """
    if not project_id or not budget or budget.get('source') != 'project':
        return
    presence = store.get('presences', {}).get(project_id)
    if not presence:
        return
    proj_amount = (presence.get('budget') or {}).get('amount')
    if proj_amount is None:
        return
    other = _earmarked_total_locked(store, project_id, exclude_campaign_id=exclude_campaign_id)
    amount = float(budget.get('amount') or 0)
    short = (other + amount) - float(proj_amount)
    if short > 0:
        raise ValueError(f'earmark exceeds project budget: short by ${short:g}')


def create_campaign(title: str, thesis: str, *, voice=None, voices=None,
                    agenda: str = '', project_ids: Iterable[str] = (),
                    planned: Iterable[str] = (), visual: str | None = None,
                    project_id: str | None = None, plan: dict | None = None,
                    goal: dict | None = None, term: dict | None = None,
                    how: dict | None = None, map_: dict | None = None,
                    subject: dict | None = None) -> dict:
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
    voices = _normalise_voices(voices or voice)
    if not voices:
        raise ValueError('no voices exist yet; create one before a campaign')
    camp = {
        'id': _new_id('camp'),
        'title': (title or '').strip(),
        'thesis': (thesis or '').strip(),
        'agenda': agenda,
        'voices': voices,
        # Kept in sync for anything still reading the singular field.
        'voice': voices[0],
        'project_ids': list(project_ids),
        'planned': list(planned),   # intended posts, in order
        # What kind of visual this campaign's posts need. Defaults to a real
        # product screenshot (docs/THE_DESK_SPEC.md standing position,
        # 2026-09-10) rather than leaving the writer to skip the visual or
        # invent one.
        'visual': (visual or '').strip() or DEFAULT_VISUAL_REQUIREMENT,
        'state': 'proposed',
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
        bounds = _campaign_bounds(camp)
        camp['approval'] = {'bounds': bounds, 'bounds_hash': compute_bounds_hash(bounds)}
        store['campaigns'][camp['id']] = camp
        _write_store(store)
    return camp


def update_campaign(campaign_id: str, patch: dict) -> dict | None:
    allowed = {'title', 'thesis', 'agenda', 'voice', 'voices', 'project_ids',
               'planned', 'state', 'visual',
               'project_id', 'subject', 'plan', 'goal', 'term', 'how', 'map'}
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
        for k, v in (patch or {}).items():
            if k in allowed:
                camp[k] = v
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


def delete_campaign(campaign_id: str) -> bool:
    with _store_lock:
        store = _read_store()
        if campaign_id not in store['campaigns']:
            return False
        del store['campaigns'][campaign_id]
        _write_store(store)
        return True


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
                    confidence: str, maybe_why: str | None = None) -> str:
    """Add a `proposed` finding. ALWAYS `origin: 'unattended'` (§10.5.2): the
    retro that produces this is code-computed, not a human judgement, so its
    output starts on the unattended side of the loop no matter who or what
    triggered the retro run. It only becomes `origin: 'interactive'` when Ron
    confirms it (`confirm_finding` below) — autonomous output never becomes
    autonomous input, the same rule `exploration_read_floor` enforces."""
    fid = _new_id('finding')
    finding = {
        'id': fid, 'project_id': project_id, 'scope': 'project',
        'dimension': dimension, 'arms': arms, 'account': account, 'metric': metric,
        'effect': effect, 'evidence': evidence, 'n_total': n_total,
        'confidence': confidence, 'maybe_why': _guarded_text(maybe_why),
        'state': 'proposed', 'origin': 'unattended',
        'decided_at': None, 'decided_by': None, 'edited_text': None,
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
