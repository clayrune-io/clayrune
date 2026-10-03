"""Add-on request/approve/install orchestration (MC-1022, spec §4-6).

This module never decides who is allowed to approve: the routes in
`mc/blueprints/addon_routes.py` gate every state-changing human action behind
`_require_human_passcode` before they call in here. What it does own is the
shape of the flow:

  file_request()  agent-callable, installs nothing
  approve()       claim the request, run the install/adoption in a worker thread
  decline()       durable "no" (30 days, keyed on the add-on id)
  remove()        forget an add-on, delete what Clayrune installed

Unattended callers get an email NOTICE with a link, never a reply-to-approve
path (`mc/question_channel.py` cannot authenticate a sender). Approval happens
in the UI.
"""
from __future__ import annotations

import os
import threading
from email.utils import make_msgid
from typing import Callable

from mc.addons import catalogue, installer
from mc.addons import manifest as mf
from mc.addons import request_store as rs
from mc.addons.manifest import AddonError, AddonMissing
from mc.core import _log

_lock = threading.RLock()
_progress: dict[str, dict] = {}            # request id -> {phase, done, total}
_inflight: set[str] = set()                # base add-on ids being installed right now
_resume_hooks: list[Callable[[str], None]] = []


def register_resume_hook(fn: Callable[[str], None]) -> None:
    """`fn(addon_id)` runs after an install/adoption lands, so a parked job
    (e.g. a held Desk render) can pick up where it stopped."""
    if fn not in _resume_hooks:
        _resume_hooks.append(fn)


def _spawn(fn: Callable[[], None]) -> None:
    """The one place the install worker starts; tests replace it."""
    threading.Thread(target=fn, daemon=True, name='addon-install').start()


# ── who is asking ───────────────────────────────────────────────────────────

def caller_identity() -> dict:
    """Who is making this request, read from the server's own session records,
    never from the request body. A Bash-tool `curl` can only exist because a
    live session is mid-turn (the premise of `mc.unattended`), so exactly one
    running session is attributed; several are listed as ambiguous rather than
    guessed at."""
    from mc.state import agent_sessions
    running = [(sid, s) for sid, s in list(agent_sessions.items()) if s.get('status') == 'running']
    if len(running) == 1:
        sid, s = running[0]
        return {'kind': 'agent', 'session_id': sid, 'project_id': s.get('project_id') or '',
                'trigger_type': s.get('trigger_type') or '',
                'unattended': (s.get('trigger_type') or '') != 'manual'}
    if not running:
        return {'kind': 'unknown', 'session_id': '', 'project_id': '', 'unattended': False}
    return {'kind': 'agent', 'session_id': '', 'project_id': '', 'ambiguous': len(running),
            'unattended': any((s.get('trigger_type') or '') != 'manual' for _sid, s in running)}


# ── cards ───────────────────────────────────────────────────────────────────

def _entry_for(addon_ref: str) -> dict:
    try:
        entry = catalogue.get(rs.base_id(addon_ref))
    except catalogue.CatalogueError as e:
        raise AddonError(f'the add-on catalogue is unusable: {e}')
    if entry is None:
        raise AddonError(f'{addon_ref!r} is not in the add-on catalogue')
    if addon_ref.startswith('system:') and not entry.get('system_binaries'):
        raise AddonError(f'{entry["id"]} cannot be adopted from this computer')
    return entry


def card(rec: dict) -> dict:
    """The approval card: catalogue/server facts as trusted fields, the agent's
    reason as quoted untrusted text, plus live progress."""
    entry = _entry_for(rec['addon_id'])
    view = catalogue.public_view(entry)
    out = {
        'id': rec['id'], 'addon_id': rec['addon_id'], 'kind': rec['kind'], 'state': rec['state'],
        'created_at': rec.get('created_at'), 'approved_at': rec.get('approved_at'),
        'error': rec.get('error') or '', 'requested_by': rec.get('requested_by') or {},
        'agent_says': rec.get('reason') or '', 'declined_until': rec.get('declined_until'),
        'addon': view, 'progress': _progress.get(rec['id']) or rec.get('progress'),
    }
    if rec['kind'] == 'adopt':
        ad = rec.get('adoption') or {}
        out['adoption'] = {
            'version': ad.get('version', ''), 'binaries': ad.get('binaries', {}),
            'note': 'Installed outside Clayrune, licence not checked.',
        }
    return out


# ── filing ──────────────────────────────────────────────────────────────────

def file_request(addon_ref: str, reason: str, requested_by: dict | None) -> tuple[dict, str]:
    """`(card, outcome)`; outcome is `created`, `existing`, `declined` or
    `already_installed`. Installs nothing."""
    entry = _entry_for(addon_ref)
    base = entry['id']
    manifest_entry = mf.get_entry(base)
    if manifest_entry is not None and mf.verify_entry(base) == 'ok':
        return {'addon_id': addon_ref, 'state': 'installed', 'addon': catalogue.public_view(entry)}, 'already_installed'
    kind, adoption = 'install', None
    if addon_ref.startswith('system:'):
        kind = 'adopt'
        adoption = installer.inspect_system(entry)
    elif catalogue.static_source(entry) is None:
        raise AddonError(f'{entry["name"]} has no download for this computer ({catalogue.platform_key()}). '
                         f'{entry.get("platform_notes", {}).get(catalogue.os_name(), "")}'.strip())
    rec, outcome = rs.create(addon_ref, kind, reason, requested_by, adoption=adoption)
    if outcome == 'created' and (requested_by or {}).get('unattended'):
        _notify_unattended(rec, entry)
    return card(rec), outcome


def _notify_unattended(rec: dict, entry: dict) -> None:
    who = rec.get('requested_by') or {}
    subject = f'[Clayrune] FYI: {entry["name"]} is waiting for your approval'
    body = (f'An unattended run asked to install {entry["name"]} ({entry["licence"]}).\n'
            f'It is parked until you approve or decline it.\n\n'
            f'Open Settings > Add-ons: http://localhost:{_port()}/\n'
            f'Project: {who.get("project_id") or "unknown"}\n\n'
            f'No reply is needed or accepted. Approval happens in the dashboard with your passcode.\n')

    def send() -> None:
        try:
            from mc import question_channel
            question_channel._send_email(subject, body, None, make_msgid())
        except Exception as e:
            _log(f'[addons] could not send the unattended notice: {e}', flush=True)

    threading.Thread(target=send, daemon=True, name='addon-notice').start()


def _port() -> str:
    try:
        from mc.state import CONFIG
        return str(os.environ.get('MC_PORT') or CONFIG.get('port', 5199))
    except Exception as e:
        _log(f'[addons] could not read the port: {e}', flush=True)
        return '5199'


# ── approve / decline ───────────────────────────────────────────────────────

def approve(request_id: str) -> dict | None:
    """Claim `request_id` and start its install/adoption in a worker. Returns
    the claimed record, or None when another approve already won (route: 409).
    Raises `AddonError` when another request for the same add-on is installing."""
    rec = rs.get(request_id)
    if rec is None:
        raise AddonError('no such request')
    base = rs.base_id(rec['addon_id'])
    with _lock:
        if base in _inflight:
            return None
        claimed = rs.claim(request_id)
        if claimed is None:
            return None
        _inflight.add(base)
    _progress[request_id] = {'phase': 'starting', 'done': 0, 'total': 1}
    _spawn(lambda: _run(request_id))
    return claimed


def _run(request_id: str) -> None:
    rec = rs.get(request_id) or {}
    base = rs.base_id(rec.get('addon_id', ''))
    try:
        entry = _entry_for(rec['addon_id'])

        def progress(phase: str, done: int, total: int) -> None:
            _progress[request_id] = {'phase': phase, 'done': done, 'total': total}

        if rec['kind'] == 'adopt':
            existing = mf.get_entry(base)
            if existing is not None and existing.get('source') == 'catalogue':
                mf.remove(base)             # a broken Clayrune copy is replaced by the adopted one
            new = installer.adopt_system(entry, rec['adoption'], request_id=request_id,
                                         requested_by=rec.get('requested_by'),
                                         approved_at=rec.get('approved_at') or mf.now_iso())
        else:
            src = catalogue.static_source(entry)
            if src is None:
                raise AddonError(f'{entry["name"]} has no download for this computer')
            new = installer.install_from_catalogue(entry, src, request_id=request_id,
                                                   requested_by=rec.get('requested_by'),
                                                   approved_at=rec.get('approved_at') or mf.now_iso(),
                                                   progress=progress)
        mf.put_entry(new)
        rs.update(request_id, state='installed', error='', progress=None)
    except installer.PinnedUrlGone as e:
        _log(f'[addons] {base}: pinned URL is gone, the catalogue entry needs re-pinning', flush=True)
        rs.update(request_id, state='failed', error=str(e), needs_repin=True)
        return _finish(request_id, base, ok=False)
    except AddonError as e:
        rs.update(request_id, state='failed', error=str(e))
        return _finish(request_id, base, ok=False)
    except Exception as e:
        _log(f'[addons] {base}: install crashed: {e}', flush=True)
        rs.update(request_id, state='failed', error='the install stopped unexpectedly; see the server log')
        return _finish(request_id, base, ok=False)
    _finish(request_id, base, ok=True)


def _finish(request_id: str, base: str, *, ok: bool) -> None:
    with _lock:
        _inflight.discard(base)
    _progress.pop(request_id, None)
    if not ok:
        return
    for hook in list(_resume_hooks):
        try:
            hook(base)
        except Exception as e:
            _log(f'[addons] resume hook for {base} failed: {e}', flush=True)


def decline(request_id: str) -> dict | None:
    return rs.decline(request_id)


def install_direct(addon_ref: str, requested_by: dict | None) -> tuple[dict, str]:
    """Settings > Add-ons > Available > Install: file the request as the user
    and claim it in one go. The caller has already passed the passcode gate."""
    view, outcome = file_request(addon_ref, '', requested_by)
    if outcome == 'already_installed':
        return view, outcome
    if outcome == 'declined':
        # The user is asking in person: a decline binds agents, not the human.
        rec = rs.get(view['id'])
        if rec is None:
            raise AddonError('the declined request is gone')
        rs.update(rec['id'], state='pending', declined_until=None)
    claimed = approve(view['id'])
    if claimed is None:
        raise AddonError('this add-on is already being installed')
    return card(claimed), 'approved'


def remove(addon_id: str) -> dict:
    return mf.remove(addon_id)


# ── views for the UI ────────────────────────────────────────────────────────

def installed_view() -> list[dict]:
    out = []
    try:
        cat = catalogue.load()
    except catalogue.CatalogueError:
        cat = {}
    for aid, e in sorted(mf.load_manifest()['addons'].items()):
        c = cat.get(aid) or {}
        out.append({
            'id': aid, 'name': e.get('name') or aid, 'version': e.get('version'), 'source': e.get('source'),
            'licence': e.get('licence'), 'size_on_disk': e.get('size_on_disk'),
            'installed_at': e.get('installed_at'), 'requested_by': e.get('requested_by'),
            'last_verified': e.get('last_verified'), 'status': e.get('status', 'ok'),
            'status_detail': e.get('status_detail', ''),
            'binaries': {n: i.get('path') for n, i in (e.get('binaries') or {}).items()},
            'note': e.get('note', ''), 'in_use_by': mf.holders(aid),
            'description': c.get('description', ''),
        })
    return out


def available_view() -> list[dict]:
    installed = set(mf.load_manifest()['addons'])
    try:
        cat = catalogue.load()
    except catalogue.CatalogueError as e:
        _log(f'[addons] catalogue unusable: {e}', flush=True)
        return []
    return [catalogue.public_view(e) for aid, e in sorted(cat.items()) if aid not in installed]


def pending_cards() -> list[dict]:
    rs.expire_stale()
    out = []
    for r in rs.list_requests(('pending', 'installing', 'failed')):
        try:
            out.append(card(r))
        except AddonError as e:
            _log(f'[addons] dropping a request for an unknown add-on: {e}', flush=True)
    return out


def startup() -> dict:
    """Server startup, off the request path: clear staging leftovers, mark
    entries ok/missing/broken, fail orphaned installs, expire stale cards."""
    swept = mf.startup_sweep()
    swept['interrupted'] = rs.fail_interrupted()
    swept['expired'] = rs.expire_stale()
    return swept
