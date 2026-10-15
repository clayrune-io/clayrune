"""Assisted posting: Clayrune opens a network's own post screen in the signed-in browser pane,
attaches the approved post's media, types its text, and STOPS. A person presses Share/Post.

Standing position (Ron, 2026-10-15): accounts with no usable posting API (Instagram personal
first) get this route instead of an automated publisher. Clayrune NEVER clicks the final submit.
That is enforced here, not left to the route data:

  * a click step whose label is a final-submit word is refused when the route is loaded and
    again at the click, whatever the route says;
  * the element the page offers must carry exactly the label the step names, so a screen that
    swapped "Next" for "Share" is refused instead of clicked;
  * the run ends by checking the final button is present and reporting it, never touching it.

Media: only the files of the one approved piece. Each path comes from the stored piece record
(`assets[].path`, resolved under data/uploads), never from page text or the request. The text
comes from the version's effective body, and a body that changed after approval is refused.

Human-started only (`mc/blueprints/desk_assisted_post_routes.py`). The page is untrusted: no
selector, label, URL or text here is derived from anything the page says.
"""
from __future__ import annotations

import threading
from typing import Callable, Protocol
from urllib.parse import urlparse

from mc import desk as _desk
from mc import desk_assisted_instagram as _instagram
from mc import desk_pieces as _pieces
from mc.core import _log

# Words that publish. Compared casefolded against a whole label.
FINAL_SUBMIT_LABELS = frozenset({'share', 'publish', 'tweet', 'send', 'submit', 'schedule',
                                 'post now', 'schedule post'})

# network -> route module. A new network is a new module and a line here.
ROUTES = {_instagram.NETWORK: _instagram}

_run_locks: dict[str, threading.Lock] = {}
_run_locks_guard = threading.Lock()


class AssistedError(Exception):
    def __init__(self, message: str, code: str, status: int = 409, **extra):
        super().__init__(message)
        self.code, self.status, self.extra = code, status, extra


class _StepFailed(Exception):
    pass


class Driver(Protocol):
    """What the engine needs from a browser. `PaneDriver` (mc/desk_assisted_pane.py) is the
    real one; tests hand in a fake."""

    def navigate(self, url: str) -> None: ...
    def wait(self, css: str, timeout_s: float) -> bool: ...
    def locate(self, css: str, labels: list[str]) -> dict | None: ...   # {'x','y','label'} or None
    def click_at(self, x: float, y: float) -> None: ...
    def attach(self, css: str, paths: list[str]) -> bool: ...
    def insert_text(self, css: str, text: str) -> bool: ...
    def present(self, css: str, label: str) -> bool: ...
    def close(self) -> None: ...


def _norm(label) -> str:
    return ' '.join(str(label or '').split()).casefold()


def is_final_submit(label) -> bool:
    return _norm(label) in FINAL_SUBMIT_LABELS


def validate_route(route) -> None:
    """Raise ValueError if a route could click a final-submit button. Run for every route at
    import, so a bad route module fails the server start, not a person's post."""
    if not is_final_submit(route.FINAL_SUBMIT['label']):
        raise ValueError(f"{route.NETWORK}: FINAL_SUBMIT label {route.FINAL_SUBMIT['label']!r} "
                         'is not in FINAL_SUBMIT_LABELS')
    ops = {'navigate', 'click', 'wait', 'attach', 'caption'}
    for i, step in enumerate(route.STEPS):
        if step.get('op') not in ops:
            raise ValueError(f'{route.NETWORK}: step {i} has unknown op {step.get("op")!r}')
        if step['op'] == 'click':
            for lab in (step.get('label'), *step.get('aliases', ())):
                if is_final_submit(lab):
                    raise ValueError(f"{route.NETWORK}: step {i} clicks {lab!r}, a final-submit button")
        if step['op'] == 'navigate' and (urlparse(str(step.get('url') or '')).hostname or '').lower() not in route.HOSTS:
            raise ValueError(f'{route.NETWORK}: step {i} navigates outside the network')


for _route in ROUTES.values():
    validate_route(_route)


# -- the approved post --------------------------------------------------------------------

def approved_post(piece_id: str, version_id: str) -> dict:
    """The one post a person approved, as {route, profile, body, media[], account_id}, or
    AssistedError. Everything the run types or attaches is read here, from the store."""
    with _desk._store_lock:
        store = _desk._read_store()
    piece = (store.get('pieces') or {}).get(piece_id)
    if not piece:
        raise AssistedError('piece not found', 'not_found', 404)
    ver = next((v for v in piece.get('versions') or [] if v.get('id') == version_id), None)
    if not ver:
        raise AssistedError('version not found', 'not_found', 404)
    if ver.get('state') not in _pieces.APPROVED_STATES:
        raise AssistedError(f"this version is {ver.get('state')}: only an approved version can be "
                            'opened for posting', 'not_approved')
    stamp = ver.get('approved')
    body = _pieces._effective_body(piece, ver)
    if not isinstance(stamp, dict) or stamp.get('account_id') != ver.get('account_id'):
        raise AssistedError('this version carries no approval for this account', 'not_approved')
    if stamp.get('body_sha') != _pieces._body_sha(body):
        raise AssistedError('the text changed after it was approved: approve it again', 'changed_after_approval')
    acc = (store.get('accounts') or {}).get(ver.get('account_id'))
    if not acc:
        raise AssistedError('the account this version is for is not in the workspace', 'no_account')
    route = ROUTES.get(acc.get('platform'))
    if route is None:
        raise AssistedError(f"there is no assisted route for {acc.get('platform')!r}", 'no_route')
    if not body:
        raise AssistedError('there is no text to post', 'no_text')
    if len(body) > route.CAPTION_LIMIT:
        raise AssistedError(f'{route.LABEL} takes at most {route.CAPTION_LIMIT} characters; this is {len(body)}',
                            'text_too_long')
    media = []
    for a in piece.get('assets') or []:
        if a.get('kind') not in route.MEDIA_KINDS:
            raise AssistedError(f"{route.LABEL} cannot take a {a.get('kind')} attachment", 'bad_media')
        try:
            media.append(str(_pieces._resolve_under_uploads(a.get('path'))))
        except _pieces.PieceError as e:
            raise AssistedError(f'attachment {a.get("title") or a.get("id")}: {e}', 'bad_media')
    if not media:
        raise AssistedError(f'{route.LABEL} needs at least one image or video on the post', 'no_media')
    if len(media) > route.MAX_MEDIA:
        raise AssistedError(f'{route.LABEL} takes at most {route.MAX_MEDIA} files; this post has {len(media)}',
                            'bad_media')
    return {'route': route, 'profile': acc.get('browser_profile') or 'main', 'body': body,
            'media': media, 'account_id': acc['id']}


# -- the run ------------------------------------------------------------------------------

def _click(driver: Driver, step: dict, route=None) -> None:
    label = step['label']
    if is_final_submit(label):
        raise AssistedError(f'refused to click {label!r}: Clayrune never presses the final button',
                            'final_submit_refused', 403)
    labels = [label, *step.get('aliases', ())]
    # The route's own final button is refused by name too, whatever the global word list holds.
    own_final = _norm(route.FINAL_SUBMIT['label']) if route is not None else None
    for extra in labels:
        if is_final_submit(extra) or _norm(extra) == own_final:
            raise AssistedError(f'refused to click {extra!r}: Clayrune never presses the final button',
                                'final_submit_refused', 403)
    hit = driver.locate(step['css'], labels)
    if hit is None:
        raise _StepFailed(f'no visible {label!r} control')
    found = hit.get('label')
    # The page offered something other than what the step names (a screen that changed under
    # us): never click it. A final-submit label is refused loudly, anything else is a plain stop.
    if is_final_submit(found) or _norm(found) == own_final:
        raise AssistedError(f'refused to click {found!r}: Clayrune never presses the final button',
                            'final_submit_refused', 403)
    if _norm(found) not in {_norm(x) for x in labels}:
        raise _StepFailed(f'the control labelled {found!r} is not {label!r}')
    driver.click_at(hit['x'], hit['y'])
    if step.get('wait_css') and not driver.wait(step['wait_css'], step.get('timeout_s', 8)):
        raise _StepFailed(f'the page did not move on after {label!r}')


def run(post: dict, driver: Driver) -> dict:
    """Walk the route. Returns the ready-for-a-person result, or raises AssistedError('step_failed')
    carrying how far it got. Never clicks the final-submit button."""
    route = post['route']
    done = 0
    for i, step in enumerate(route.STEPS):
        op = step['op']
        try:
            if op == 'navigate':
                driver.navigate(step['url'])
            elif op == 'wait':
                if not driver.wait(step['css'], step.get('timeout_s', 8)):
                    raise _StepFailed('the page did not show what was expected')
            elif op == 'click':
                _click(driver, step, route)
            elif op == 'attach':
                if not driver.attach(step['css'], post['media']):
                    raise _StepFailed('the file field was not found')
            elif op == 'caption':
                if not driver.insert_text(step['css'], post['body']):
                    raise _StepFailed('the text box was not found')
        except _StepFailed as e:
            raise AssistedError(f'stopped at step {i + 1} ({op}): {e}. The pane is left where it is: '
                                'finish this post by hand.', 'step_failed', 502,
                                step=i + 1, op=op, steps_done=done, steps_total=len(route.STEPS))
        done += 1
    final = route.FINAL_SUBMIT
    return {'ok': True, 'state': 'ready_for_human', 'network': route.NETWORK,
            'steps_done': done, 'media': len(post['media']),
            'final_button_present': bool(driver.present(final['css'], final['label'])),
            'message': f"The post is filled in. Check it in the pane, then press {final['label']} yourself."}


def prepare(piece_id: str, version_id: str, *, driver_factory: Callable[[str], Driver] | None = None) -> dict:
    """Entry point for the route: gate on the approved post, then drive the pane for its
    profile. `driver_factory(profile)` returns a Driver (default: the live pane's)."""
    post = approved_post(piece_id, version_id)
    factory = driver_factory
    if factory is None:
        from mc.desk_assisted_pane import pane_driver_for
        factory = pane_driver_for
    profile = post['profile']
    with _run_locks_guard:
        lock = _run_locks.setdefault(profile, threading.Lock())
    if not lock.acquire(blocking=False):
        raise AssistedError('a post is already being filled in on this browser profile', 'busy')
    driver = None
    try:
        driver = factory(profile)
        _log(f"[desk_assisted_post] start piece={piece_id} version={version_id} "
             f"network={post['route'].NETWORK} media={len(post['media'])}", flush=True)
        out = run(post, driver)
        out.update(piece_id=piece_id, version_id=version_id, profile=profile)
        _log(f"[desk_assisted_post] ready piece={piece_id} version={version_id} "
             f"final_button_present={out['final_button_present']}", flush=True)
        return out
    except AssistedError as e:
        _log(f"[desk_assisted_post] stopped piece={piece_id} version={version_id}: {e.code}", flush=True)
        raise
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception as e:
                _log(f'[desk_assisted_post] closing the driver failed: {type(e).__name__}', flush=True)
        lock.release()
