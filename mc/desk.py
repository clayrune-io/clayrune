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
  * VOICE PROFILES(here) — how `ron` and `clayrune` each sound, maintained by
                            diffing every edit Ron makes to a draft.
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

from pathlib import Path
from typing import Iterable
import difflib
import json
import re
import threading
import uuid

from mc.core import _atomic_write_text, _log, now_iso

# -- wired by server.py -------------------------------------------------------
# Path constants are server.py-owned; this module holds wired placeholders, the
# same shape automation_suggestions.STORE_PATH uses.
STORE_PATH: Path | None = None
SIGNALS_PATH: Path | None = None

_store_lock = threading.Lock()
_signals_lock = threading.Lock()

STORE_VERSION = 1

# The two voices are fixed by Ron's 2026-09-09 decision and are NOT variants of
# one another: `ron` is first person, a builder saying what he built and what it
# cost him, and owns X. `clayrune` is the product speaking about itself, and
# owns LinkedIn. Adding a third is a product decision, not a config change.
VOICES = ('ron', 'clayrune')

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
    }


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
    return data


def _write_store(store: dict) -> None:
    if STORE_PATH is None:
        return
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(STORE_PATH, json.dumps(store, indent=2, ensure_ascii=False))


def _new_id(prefix: str) -> str:
    return f'{prefix}-{uuid.uuid4().hex[:8]}'


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

def _empty_voice(name: str) -> dict:
    return {
        'name': name,
        'register': '',
        'banned': [],          # words and constructions this voice will not use
        'never_claims': [],    # claims this voice will not make
        'product_refs': [],    # how it refers to the product
        'rewrites': [],        # {before, after, at, draft_id} — the learning
        'updated_at': None,
    }


def get_voice(name: str) -> dict:
    if name not in VOICES:
        raise ValueError(f'unknown voice {name!r}; expected one of {VOICES}')
    with _store_lock:
        store = _read_store()
        return store['voices'].get(name) or _empty_voice(name)


def list_voices() -> list[dict]:
    return [get_voice(v) for v in VOICES]


def update_voice(name: str, patch: dict) -> dict:
    """Human-edited fields only. Rewrites are appended by record_edit()."""
    if name not in VOICES:
        raise ValueError(f'unknown voice {name!r}; expected one of {VOICES}')
    allowed = {'register', 'banned', 'never_claims', 'product_refs'}
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
    if name not in VOICES:
        raise ValueError(f'unknown voice {name!r}; expected one of {VOICES}')
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


# -- campaign board -----------------------------------------------------------
#
# What makes the Desk an incubator rather than a draft generator. A campaign
# carries a THESIS and an agenda note explaining why it is running now, so the
# Board surface can answer "what is happening this month, and why" rather than
# just listing pending items.

CAMPAIGN_STATES = ('proposed', 'running', 'paused', 'done', 'dropped')


def list_campaigns(state: str | None = None) -> list[dict]:
    with _store_lock:
        rows = list(_read_store()['campaigns'].values())
    if state:
        rows = [r for r in rows if r.get('state') == state]
    rows.sort(key=lambda r: r.get('created_at') or '', reverse=True)
    return rows


def create_campaign(title: str, thesis: str, *, voice: str = 'ron',
                    agenda: str = '', project_ids: Iterable[str] = (),
                    planned: Iterable[str] = ()) -> dict:
    if voice not in VOICES:
        raise ValueError(f'unknown voice {voice!r}; expected one of {VOICES}')
    camp = {
        'id': _new_id('camp'),
        'title': (title or '').strip(),
        'thesis': (thesis or '').strip(),
        'agenda': agenda,
        'voice': voice,
        'project_ids': list(project_ids),
        'planned': list(planned),   # intended posts, in order
        'state': 'proposed',
        'created_at': now_iso(),
        'updated_at': now_iso(),
    }
    with _store_lock:
        store = _read_store()
        store['campaigns'][camp['id']] = camp
        _write_store(store)
    return camp


def update_campaign(campaign_id: str, patch: dict) -> dict | None:
    allowed = {'title', 'thesis', 'agenda', 'voice', 'project_ids',
               'planned', 'state'}
    if 'state' in (patch or {}) and patch['state'] not in CAMPAIGN_STATES:
        raise ValueError(f"unknown state {patch['state']!r}")
    if 'voice' in (patch or {}) and patch['voice'] not in VOICES:
        raise ValueError(f"unknown voice {patch['voice']!r}")
    with _store_lock:
        store = _read_store()
        camp = store['campaigns'].get(campaign_id)
        if not camp:
            return None
        for k, v in (patch or {}).items():
            if k in allowed:
                camp[k] = v
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
                     published_at: str | None = None) -> dict:
    """Record that a human released something. NOT a publish path."""
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
        'outcome': None,        # filled in later, by hand or by a reader
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


def record_outcome(post_id: str, outcome: dict) -> dict | None:
    with _store_lock:
        store = _read_store()
        for row in store['ledger']:
            if row.get('id') == post_id:
                row['outcome'] = outcome
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
