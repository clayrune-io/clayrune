"""The Desk publish tick (MC-1021 R1-W S7; docs/desk_v1/R1W_WIRING_PLAN.md §4).

The one thing that sends an approved version out. A human approves a version
(`desk_pieces.approve_version`, reached only through the passcode route); this
module then takes it the rest of the way, in one fixed order, and writes down
what happened to it. It never decides to post something nobody approved: a
version is eligible only while it is `approved` or `scheduled` AND still carries
the human stamp for the exact text and account it has now.

TWO ENTRY POINTS, ONE PATH
  `approve_and_send`  the human's "Approve now" click. The approval and the send
                      happen under one lock, so the background pass cannot see
                      the half-way state, and the send counts as ATTENDED: a
                      vault token set attended-only (`allow_unattended` off) is
                      usable, because a person is clicking.
  `run_once`          the background pass, every `TICK_SECONDS` once `start()` is
                      called by `server.py`. It is an UNATTENDED caller: a
                      scheduled post whose token is attended-only is HELD, never
                      sent (plan §4, "Unattended consequence").

THE ORDERED CHECKS (`_refusal`; the first that fails holds the version)
  0. the human stamp still matches the text and account (edited behind it? held)
  1. the campaign is active
  2. the campaign's bounds are inside what a human approved
  3. cadence against the story ledger: `per_week` (rolling 7 days), `min_gap_h`,
     `post_cap` (campaign and term), `term.ends`, `plan.end.date`
  4. the account can publish now (`desk_accounts.publish_state`)
  5. a scheduled send is allowed to use the vault token unattended
Any refusal moves the version to `held` with the reason on `failure`: nothing
silently skips. A held version is released only by a human approving it again.
There is no dry-run mode (Ron, 2026-10-01): a version past these checks posts.

EXACTLY ONCE. The publisher keys its receipt on `version.id`, and the
`approved|scheduled -> sending` write is a compare-and-set, so two passes (or a
pass and a click) racing for one version cannot both send. A `sending` version
found at the start of a pass is one a crashed process left behind: if the
publisher has its receipt the version is completed from it, otherwise it becomes
`unknown_outcome` ("may have posted") and is NEVER sent again by the tick.

AFTER A SEND: receipt -> `submitted`, one row in the story ledger
(`desk.record_published`, with the piece/format/account/term/cost the retro
groups by), then `verify_post`; `verified_published` only when the platform
confirms the post. A platform that cannot be asked (LinkedIn) stays `submitted`.

MANUAL ACCOUNTS (a blog, or any account marked manual) are never called: the
version gets a publishing task (copy text, plus an X share-intent link where
the platform has one; standing position 2026-09-23) and waits for the human to
say they posted it (`report_posted`).

SAFETY: `start()` refuses to run unless the Desk store and the receipts file are
wired, and nothing in `mc/` calls it except `server.py`'s boot. Tests call
`run_once` / `approve_and_send` directly against a temp store with the
transport patched; importing this module starts nothing.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from mc import desk as _desk
from mc import desk_accounts as _accounts
from mc import desk_pieces as _pieces
from mc import desk_publish as _publish
from mc import desk_spend_guard as _spend
from mc.core import _log, now_iso

TICK_SECONDS = 60

# What a post costs to send, recorded on its ledger row (`cost`) so the retro and
# the budget see it. X: $0.015 a post, $0.20 when the post carries a link
# (docs/SOCIAL_WORKSPACE_FIELD_SCAN.md, Feb 2026 pricing). LinkedIn's API is free.
X_POST_COST = _spend.X_POST_COST
X_LINK_POST_COST = _spend.X_LINK_POST_COST

# How many ticks may try to verify one submitted post before giving up (each X
# attempt is a paid read), and how long after posting a verification is still tried.
VERIFY_MAX_ATTEMPTS = 5
VERIFY_WINDOW = timedelta(hours=6)

X_INTENT_BASE = 'https://x.com/intent/post'

# Serialises "check, send, record" for every version in this process. Held across
# the network call on purpose: the cadence checks read the ledger the previous
# send just wrote, so two sends overlapping could both pass a ceiling of one.
_send_lock = threading.RLock()

_stop = threading.Event()
_thread: threading.Thread | None = None


# -- small helpers -------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse(s) -> datetime | None:
    try:
        return _aware(_desk._parse_dt(str(s)))
    except (ValueError, TypeError):
        return None


def _post_cost(platform: str, body: str) -> float:
    return _spend.post_cost(platform, body)


def _failure(reason: str, now: datetime | None = None) -> dict:
    return {'reason': reason, 'at': (now or _now()).isoformat()}


def _snapshot(piece_id: str, version_id: str):
    """(store, piece, version, campaign, account) read once, or None for any
    that is missing. A copy of the store: nothing here mutates it."""
    with _desk._store_lock:
        store = _desk._read_store()
    piece = (store.get('pieces') or {}).get(piece_id)
    if not piece:
        return None
    ver = next((v for v in piece.get('versions') or [] if v.get('id') == version_id), None)
    if not ver:
        return None
    camp = (store.get('campaigns') or {}).get(piece.get('campaign_id'))
    acc = (store.get('accounts') or {}).get(ver.get('account_id'))
    return store, piece, ver, camp, acc


# -- the ordered checks ----------------------------------------------------------

def _cadence_refusal(camp: dict, ledger: list, now: datetime) -> str | None:
    plan = camp.get('plan') or {}
    cadence = plan.get('cadence') or {}
    end = plan.get('end') or {}
    term = camp.get('term') if isinstance(camp.get('term'), dict) else {}
    rows = [r for r in ledger if r.get('campaign_id') == camp.get('id')]

    def when(r):
        return _parse(r.get('published_at'))

    if term.get('ends'):
        ends = _parse(term['ends'])
        if ends is not None and now >= ends + timedelta(days=1):
            return f"term {term.get('index') or 1} ended {str(term['ends'])[:10]}: renew the campaign to keep posting"
    if end.get('date'):
        ends = _parse(end['date'])
        if ends is not None and now >= ends + timedelta(days=1):
            return f"the campaign's end date ({str(end['date'])[:10]}) has passed"
    cap = end.get('post_cap')
    if cap is not None and len(rows) >= cap:
        return f'the campaign has reached its post cap ({len(rows)} of {cap})'
    tcap = term.get('post_cap')
    if tcap is not None:
        idx = str(term.get('index') or 1)
        in_term = [r for r in rows if str(r.get('term')) == idx]
        if len(in_term) >= tcap:
            return f'this term has reached its post cap ({len(in_term)} of {tcap})'
    per_week = cadence.get('per_week')
    if per_week is not None:
        recent = [r for r in rows if (w := when(r)) is not None and w > now - timedelta(days=7)]
        if len(recent) >= per_week:
            return f'the weekly ceiling is reached ({len(recent)} of {per_week} in the last 7 days)'
    gap = cadence.get('min_gap_h')
    if gap:
        times = [w for w in (when(r) for r in rows) if w is not None]
        if times and now - max(times) < timedelta(hours=gap):
            mins = int(((timedelta(hours=gap) - (now - max(times))).total_seconds() + 59) // 60)
            return f'the minimum gap of {gap} h since the last post has not passed ({mins} min left)'
    return None


def _refusal(store: dict, piece: dict, ver: dict, camp: dict | None, acc: dict | None,
             now: datetime, *, attended: bool) -> str | None:
    """The reason this version must NOT go out right now, or None. See the module
    docstring for the order; it is the order plan §4 gives."""
    stamp = ver.get('approved')
    if not isinstance(stamp, dict) or stamp.get('by') != 'human':
        return 'there is no human approval on this version'
    if stamp.get('account_id') != ver.get('account_id'):
        return 'the account was changed after this version was approved'
    if stamp.get('body_sha') != _pieces._body_sha(_pieces._effective_body(piece, ver)):
        return 'the text was changed after this version was approved'
    if camp is None:
        return 'the campaign no longer exists'
    if camp.get('state') != 'running':
        return f"the campaign is not active (it is {_desk._V1_STATE_OUT.get(camp.get('state'), camp.get('state'))})"
    problems = _desk.approval_problems(camp)
    if problems:
        return "the campaign's limits are not covered by your approval: " + '; '.join(problems)
    refused = _cadence_refusal(camp, store.get('ledger') or [], now)
    if refused:
        return refused
    if acc is None:
        return 'the account is not in the workspace any more'
    pub = _accounts.publish_state(acc)
    if not pub['ready']:
        return f"{acc.get('label') or acc['id']} cannot publish: {pub['reason']}"
    if not attended and acc.get('capability') != 'manual' and pub.get('unattended_ok') is False:
        return (f"the vault entry {pub['secret']} is set attended-only, so a scheduled post cannot use it: "
                f"approve this version again with Approve now, or allow unattended use of the entry")
    return None


# -- manual accounts ---------------------------------------------------------------

def manual_task(acc: dict, piece: dict, body: str, now: datetime | None = None) -> dict:
    """The publishing task for an account the Desk does not call: what to copy,
    and where to open to post it. A share-intent link only where the platform has
    one that carries text (X). LinkedIn's Company Page composer has no such URL,
    so it is copy-and-open by hand, said plainly rather than guessed."""
    label = acc.get('label') or acc.get('identity') or acc['id']
    share = f'{X_INTENT_BASE}?text={quote(body)}' if acc.get('platform') == 'x' else None
    return {'title': f"Post \"{piece.get('title') or 'this piece'}\" on {label}",
            'platform': acc.get('platform'), 'account_id': acc['id'],
            'copy_text': body, 'share_url': share,
            'created_at': (now or _now()).isoformat()}


# -- sending one version ---------------------------------------------------------------

def _ledger_has(url: str) -> bool:
    return any(r.get('url') == url for r in _desk.list_ledger(limit=100000))


def _record_ledger(piece: dict, ver: dict, camp: dict, acc: dict, body: str, receipt: dict) -> None:
    """One story-ledger row per post, keyed so a recovery pass cannot add a second
    for a post already recorded."""
    if receipt.get('permalink') and _ledger_has(receipt['permalink']):
        return
    term = camp.get('term') if isinstance(camp.get('term'), dict) else {}
    _desk.record_published(
        platform=acc.get('platform'), voice=acc.get('voice') or '', body=body,
        campaign_id=camp.get('id'), project_id=camp.get('project_id'),
        url=receipt.get('permalink'), published_at=receipt.get('posted_at'),
        piece_id=piece['id'], format=ver.get('format') or piece.get('kind'),
        account=acc['id'], term=str(term.get('index') or 1),
        cost=_post_cost(acc.get('platform') or '', body))


def _complete_from_receipt(piece, ver, camp, acc, body, receipt, *, attended: bool, now: datetime) -> str:
    """`sending` -> `submitted` with a receipt in hand, the ledger row, then one
    verification attempt. Returns the version's resulting state."""
    sub = _pieces.transition_version(piece['id'], ver['id'], to='submitted', expect='sending',
                                     receipt=dict(receipt), failure=None)
    if sub is None:
        return ver.get('state') or ''
    try:
        _record_ledger(piece, ver, camp, acc, body, receipt)
    except Exception as e:
        # The post is live and the version says so; a ledger failure must not
        # undo that. Loud, because the retro will miss this post.
        _log(f"[desk_tick] CRITICAL: post {receipt.get('post_id')} for version {ver['id']} is live but its "
             f"ledger row failed: {e}", level='error')
    return _verify(piece, ver, camp, acc, attended=attended, now=now)


def _verify(piece, ver, camp, acc, *, attended: bool, now: datetime) -> str:
    """One verification attempt for a `submitted` version. Never raises."""
    snap = _snapshot(piece['id'], ver['id'])
    cur = (snap[2] if snap else ver)
    receipt = dict(cur.get('receipt') or {})
    if cur.get('state') != 'submitted' or receipt.get('verify') in ('unsupported', 'unconfirmed'):
        return cur.get('state') or ''
    try:
        ok = _publish.verify_post(acc.get('platform'), receipt.get('post_id'), consumer='desk_tick',
                                  project_id=camp.get('project_id'), unattended=not attended,
                                  account_id=acc.get('id'), campaign_id=camp.get('id'))
    except _publish.ReadConsentDenied as e:
        # Refused before any token or GET: not an attempt, never `unconfirmed`,
        # so granting Read later verifies on the next pass.
        receipt['verify_error'] = str(e)[:300]
        _write_receipt(piece['id'], ver['id'], receipt)
        return 'submitted'
    except _publish.PublishError as e:
        receipt['verify_attempts'] = int(receipt.get('verify_attempts') or 0) + 1
        receipt['verify_error'] = str(e)[:300]
        if receipt['verify_attempts'] >= VERIFY_MAX_ATTEMPTS:
            receipt['verify'] = 'unconfirmed'
        _write_receipt(piece['id'], ver['id'], receipt)
        return 'submitted'
    if ok is None:
        receipt['verify'] = 'unsupported'
        _write_receipt(piece['id'], ver['id'], receipt)
        return 'submitted'
    if ok:
        receipt.pop('verify_error', None)
        receipt['verified_at'] = now.isoformat()
        out = _pieces.transition_version(piece['id'], ver['id'], to='verified_published',
                                         expect='submitted', receipt=receipt)
        return 'verified_published' if out else 'submitted'
    receipt['verify_attempts'] = int(receipt.get('verify_attempts') or 0) + 1
    receipt['verify_error'] = 'the platform does not show this post'
    if receipt['verify_attempts'] >= VERIFY_MAX_ATTEMPTS:
        receipt['verify'] = 'unconfirmed'
    _write_receipt(piece['id'], ver['id'], receipt)
    return 'submitted'


def _write_receipt(piece_id: str, version_id: str, receipt: dict) -> None:
    """Update the receipt on a `submitted` version without changing its state."""
    with _desk._store_lock:
        store = _desk._read_store()
        piece = (store.get('pieces') or {}).get(piece_id)
        ver = next((v for v in (piece or {}).get('versions') or [] if v.get('id') == version_id), None)
        if ver is None or ver.get('state') != 'submitted':
            return
        ver['receipt'] = receipt
        _desk._write_store(store)


def _process(piece_id: str, version_id: str, *, attended: bool, now: datetime) -> str | None:
    """Take one `approved`/`scheduled` version through the checks and, if it
    clears them, out. Caller holds `_send_lock`. Returns the state this call moved
    it to, or None when it did nothing (not eligible, or another caller got there
    first), so a pass never reports a send it did not make."""
    snap = _snapshot(piece_id, version_id)
    if snap is None:
        return None
    store, piece, ver, camp, acc = snap
    if ver.get('state') not in ('approved', 'scheduled'):
        return None

    reason = _refusal(store, piece, ver, camp, acc, now, attended=attended)
    if reason:
        held = _pieces.transition_version(piece_id, version_id, to='held', expect=('approved', 'scheduled'),
                                          failure=_failure(reason, now))
        if held:
            _log(f'[desk_tick] held version {version_id}: {reason}')
        return 'held' if held else None
    assert camp is not None and acc is not None
    body = _pieces._effective_body(piece, ver)

    if acc.get('capability') == 'manual':
        if ver.get('manual'):
            return None                  # the task exists; waiting for the person
        out = _pieces.transition_version(piece_id, version_id, to='approved', expect=('approved', 'scheduled'),
                                         manual=manual_task(acc, piece, body, now))
        return 'approved' if out else None

    if _pieces.transition_version(piece_id, version_id, to='sending', expect=('approved', 'scheduled'),
                                  failure=None) is None:
        return None
    item = {'id': version_id, 'platform': acc.get('platform'), 'body': body, 'campaign_id': camp['id'],
            'organization_id': acc.get('organization_id') or '', 'account_id': acc.get('id')}
    try:
        receipt = _publish.publish(item, consumer='desk_tick' if not attended else 'desk_approve',
                                   project_id=camp.get('project_id'), unattended=not attended)
    except _publish.PublishError as e:
        if getattr(e, 'maybe_posted', False):
            _pieces.transition_version(piece_id, version_id, to='unknown_outcome', expect='sending',
                                       failure=_failure(str(e), now))
            _log(f'[desk_tick] version {version_id} has an unknown outcome: {e}')
            return 'unknown_outcome'
        _pieces.transition_version(piece_id, version_id, to='failed', expect='sending',
                                   failure=_failure(str(e), now))
        _log(f'[desk_tick] version {version_id} failed: {e}')
        return 'failed'
    except Exception as e:
        # Anything unexpected after `sending` was written: the request may or may
        # not have left, so it is an unknown outcome, never a quiet retry.
        _pieces.transition_version(piece_id, version_id, to='unknown_outcome', expect='sending',
                                   failure=_failure(f'unexpected error while sending: {e}', now))
        _log(f'[desk_tick] version {version_id} errored while sending: {e}', level='error')
        return 'unknown_outcome'
    return _complete_from_receipt(piece, ver, camp, acc, body, receipt, attended=attended, now=now)


def _recover_sending(piece_id: str, version_id: str, *, now: datetime) -> str:
    """A version left in `sending` by a process that died mid-send. Caller holds
    `_send_lock`, so no live send can be in flight."""
    snap = _snapshot(piece_id, version_id)
    if snap is None or snap[2].get('state') != 'sending':
        return 'gone'
    _, piece, ver, camp, acc = snap
    receipt = _publish.get_receipt(version_id)
    if receipt and camp is not None and acc is not None:
        body = _pieces._effective_body(piece, ver)
        return _complete_from_receipt(piece, ver, camp, acc, body, receipt, attended=False, now=now)
    _pieces.transition_version(piece_id, version_id, to='unknown_outcome', expect='sending',
                               failure=_failure('the server stopped while this was sending and no receipt '
                                                'was saved: the post MAY have gone out. Check the account '
                                                'before posting it again.', now))
    return 'unknown_outcome'


# -- entry points ------------------------------------------------------------------

def approve_and_send(piece_id: str, version_id: str, *, scheduled_at: object = ..., by: str = 'human',
                     now: datetime | None = None) -> dict:
    """The human's Approve click: approve (M19), and when no future time was
    given, send it straight away as an ATTENDED caller. Returns the v1 piece.
    Raises PieceError exactly as `approve_version` does."""
    now = now or _now()
    with _send_lock:
        out = _pieces.approve_version(piece_id, version_id, scheduled_at=scheduled_at, by=by, now=now)
        snap = _snapshot(piece_id, version_id)
        if snap and snap[2].get('state') == 'approved':
            _process(piece_id, version_id, attended=True, now=now)
            return _pieces.get_piece(piece_id)
        return out


def report_posted(piece_id: str, version_id: str, *, url: str | None = None) -> dict:
    """The person says they posted a manual version (or checked an
    `unknown_outcome` one and found it live). A statement, not a post: the ledger
    row carries `url` only if they gave one. Raises PieceError."""
    if url is not None and not (isinstance(url, str) and re.match(r'^https?://\S+$', url.strip())):
        raise _pieces.PieceError('the link must be an http(s) address, or left blank')
    url = (url or '').strip() or None
    with _send_lock:
        snap = _snapshot(piece_id, version_id)
        if snap is None:
            raise _pieces.PieceError('version not found', 404)
        _, piece, ver, camp, acc = snap
        state = ver.get('state')
        if state not in ('approved', 'scheduled', 'unknown_outcome') or (
                state in ('approved', 'scheduled') and not ver.get('manual')):
            raise _pieces.PieceError(
                f'this version is {state}: only a publishing task you were handed, or an unknown outcome, '
                'can be marked as posted by you', 409)
        now = _now()
        receipt = {'post_id': None, 'permalink': url, 'posted_at': now.isoformat(), 'reported': True}
        out = _pieces.transition_version(piece_id, version_id, to='you_reported',
                                         expect=('approved', 'scheduled', 'unknown_outcome'),
                                         receipt=receipt, failure=None)
        if out is None:
            raise _pieces.PieceError('this version changed while you were marking it: reload and try again', 409)
        if camp is not None and acc is not None:
            try:
                body = _pieces._effective_body(piece, ver)
                term = camp.get('term') if isinstance(camp.get('term'), dict) else {}
                _desk.record_published(
                    platform=acc.get('platform'), voice=acc.get('voice') or '', body=body,
                    campaign_id=camp.get('id'), project_id=camp.get('project_id'), url=url,
                    published_at=receipt['posted_at'], piece_id=piece['id'],
                    format=ver.get('format') or piece.get('kind'), account=acc['id'],
                    term=str(term.get('index') or 1), cost=0)
            except Exception as e:
                _log(f'[desk_tick] ledger row for reported version {version_id} failed: {e}', level='error')
        return _pieces.get_piece(piece_id)


def _due_candidates(store: dict, now: datetime) -> tuple[list, list, list]:
    """(to send, left in `sending`, `submitted` awaiting verification) as
    (piece_id, version_id) pairs, from one read of the store."""
    send, stale, verify = [], [], []
    for piece in (store.get('pieces') or {}).values():
        for ver in piece.get('versions') or []:
            state = ver.get('state')
            key = (piece['id'], ver['id'])
            if state == 'scheduled':
                at = _parse(ver.get('scheduled_at'))
                if at is not None and at <= now:
                    send.append((at, (ver.get('approved') or {}).get('at') or '', key))
            elif state == 'approved' and not ver.get('manual'):
                send.append((now, (ver.get('approved') or {}).get('at') or '', key))
            elif state == 'sending':
                stale.append(key)
            elif state == 'submitted':
                receipt = ver.get('receipt') or {}
                posted = _parse(receipt.get('posted_at'))
                if (receipt.get('verify') not in ('unsupported', 'unconfirmed')
                        and posted is not None and now - posted <= VERIFY_WINDOW):
                    verify.append(key)
    send.sort(key=lambda t: (t[0], t[1]))
    return [k for _, _, k in send], stale, verify


def run_once(now: datetime | None = None) -> dict:
    """One background pass. Returns `{sent, held, failed, unknown, recovered,
    verified, manual, errors}` as lists of version ids, for the log and for tests.
    Never raises: a failure on one version is logged and the pass goes on."""
    now = _aware(now) if now else _now()
    result: dict = {'sent': [], 'held': [], 'failed': [], 'unknown': [], 'recovered': [],
                    'verified': [], 'manual': [], 'errors': []}
    try:
        with _desk._store_lock:
            store = _desk._read_store()
        send, stale, verify = _due_candidates(store, now)
    except Exception as e:
        _log(f'[desk_tick] could not read the Desk store: {e}', level='error')
        result['errors'].append(str(e))
        return result

    for pid, vid in stale:
        try:
            with _send_lock:
                out = _recover_sending(pid, vid, now=now)
            result['recovered'].append((vid, out))
        except Exception as e:
            _log(f'[desk_tick] recovery of {vid} failed: {e}', level='error')
            result['errors'].append(f'{vid}: {e}')
    for pid, vid in send:
        try:
            with _send_lock:
                out = _process(pid, vid, attended=False, now=now)
        except Exception as e:
            _log(f'[desk_tick] processing {vid} failed: {e}', level='error')
            result['errors'].append(f'{vid}: {e}')
            continue
        bucket = {'submitted': 'sent', 'verified_published': 'sent', 'held': 'held', 'failed': 'failed',
                  'unknown_outcome': 'unknown', 'approved': 'manual', 'scheduled': 'manual'}.get(out)
        if bucket:
            result[bucket].append(vid)
    for pid, vid in verify:
        try:
            with _send_lock:
                snap = _snapshot(pid, vid)
                if snap is None or snap[2].get('state') != 'submitted' or snap[3] is None or snap[4] is None:
                    continue
                out = _verify(snap[1], snap[2], snap[3], snap[4], attended=False, now=now)
            if out == 'verified_published':
                result['verified'].append(vid)
        except Exception as e:
            _log(f'[desk_tick] verifying {vid} failed: {e}', level='error')
            result['errors'].append(f'{vid}: {e}')
    return result


# -- the server thread -----------------------------------------------------------------

def _loop(interval_s: float, enabled) -> None:
    while not _stop.wait(interval_s):
        try:
            if enabled():
                out = run_once()
                if any(out[k] for k in ('sent', 'held', 'failed', 'unknown', 'recovered', 'manual', 'errors')):
                    _log(f"[desk_tick] pass: " + json.dumps({k: v for k, v in out.items() if v}, default=str))
        except Exception as e:
            _log(f'[desk_tick] pass crashed: {e}', level='error')


def start(*, interval_s: float = TICK_SECONDS, enabled=lambda: True) -> threading.Thread | None:
    """Start the background thread (server process only; `server.py` boot). Refuses
    to start, with a log line, unless the Desk store and the publisher's receipts
    file are both wired: a tick without durable idempotency could double post.
    `enabled` is read before every pass, so a config flip needs no restart."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return _thread
    if _desk.STORE_PATH is None or _publish.RECEIPTS_PATH is None:
        _log('[desk_tick] not started: the Desk store or the receipts file is not wired')
        return None
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(interval_s, enabled), name='desk-tick', daemon=True)
    _thread.start()
    return _thread


def stop(timeout: float = 5.0) -> None:
    global _thread
    _stop.set()
    if _thread is not None:
        _thread.join(timeout)
    _thread = None
