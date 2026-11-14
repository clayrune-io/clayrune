"""Desk Studio image engine `gemini_web`: make a picture on the person's own Gemini plan
by driving the Gemini web app (Images mode) in the browser pane. Ron, 2026-11-14.

Same four adapter methods as every engine (mc/desk_engines.py). It is a SYNC engine: the
submit call drives the pane, waits for the picture, reads it back and returns the bytes,
so the picture lands in the Material library the same way an OpenAI one does.

Rules that hold here (each pinned by tests/test_desk_gemini_web.py):
  * Drives a pane that is ALREADY OPEN on the named profile. It never launches a browser
    or a profile, and it never types into a sign-in page; a signed-out page stops the run.
  * Only a human reaches it. The route needs the dashboard passcode and refuses an
    unattended caller; `_creds` refuses an unattended caller again, here.
  * Page text is untrusted. It is only matched against the needles in
    `desk_gemini_web_selectors.py` and never copied into a message or handed to an agent.
    Every failure message below is fixed text.
  * No selector, label or URL lives here; they are all in `desk_gemini_web_selectors.py`.
  * It will not quietly do less than was asked: a request field Gemini's page has no control
    for (resolution, seed, negative prompt) is refused, and a picture that cannot be read
    at full size fails instead of being replaced by a smaller one.
Cost is 0 (the plan pays). The per-job limit gate on Connections still applies, as for any
engine; this module does not touch it.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from mc import desk_gemini_web_selectors as S

if TYPE_CHECKING:
    from mc.desk_engines import Estimate, GenerationRequest, ModelDescriptor, _Creds

ENGINE_ID = 'gemini_web'
AUTH_KIND = 'browser_pane'
DEFAULT_PROFILE = 'gemini'
CONFIG_KEY = 'desk_gemini_web_profile'
_PROFILE_OK = re.compile(r'^[a-z0-9][a-z0-9_-]{0,40}$')

# Tests replace these two. `DRIVER_FACTORY(profile)` returns a driver for the open pane or
# raises AssistedError; `_clock` supplies monotonic()/sleep().
DRIVER_FACTORY: Callable[[str], Any] | None = None
_clock: Any = time

_run_locks: dict[str, threading.Lock] = {}
_run_locks_guard = threading.Lock()


def profile() -> str:
    """The named browser profile that holds the Gemini sign-in."""
    from mc import state
    name = str(state.CONFIG.get(CONFIG_KEY) or '').strip().lower()
    return name if _PROFILE_OK.match(name) else DEFAULT_PROFILE


def _factory(name: str):
    if DRIVER_FACTORY is not None:
        return DRIVER_FACTORY(name)
    from mc.desk_gemini_web_driver import pane_driver_for
    return pane_driver_for(name)


def _open_pane_action(name: str) -> dict:
    # The existing launch route opens the pane; a person clicks it. Nothing here launches.
    return {'label': 'Open Gemini in the pane', 'endpoint': '/api/browser/launch', 'method': 'POST',
            'body': {'profile': name, 'url': S.HOME}}


def connection_state() -> dict:
    """`connection()` for this engine: ready when a pane is open on the profile and the page
    is not a sign-in screen. One cheap read; a page that cannot be read counts as ready, the
    run itself says what is wrong."""
    from mc.desk_assisted_post import AssistedError
    name = profile()
    base = {'vault_entry': '', 'profile': name}
    try:
        driver = _factory(name)
    except AssistedError:
        return {**base, 'ready': False, 'exists': False, 'state': 'pane_closed', 'action': _open_pane_action(name),
                'reason': f"No browser pane is open on the '{name}' profile. Open Gemini in the pane and sign in."}
    try:
        if _signed_out(driver):
            return {**base, 'ready': False, 'exists': True, 'state': 'signed_out', 'action': _open_pane_action(name),
                    'reason': f"The '{name}' pane is not signed in to Gemini. Sign in there yourself."}
    except Exception as e:
        _log(f'[desk_gemini_web] sign-in check failed: {type(e).__name__}')
    finally:
        _close(driver)
    return {**base, 'ready': True, 'exists': True, 'reason': None}


def _log(msg: str) -> None:
    from mc.core import _log as log
    log(msg, flush=True)


def _close(driver) -> None:
    try:
        driver.close()
    except Exception as e:
        _log(f'[desk_gemini_web] pane connection close failed: {type(e).__name__}')


def _host(url: str) -> str:
    m = re.match(r'^https?://([^/:?#]+)', url or '', re.I)
    return m.group(1).lower() if m else ''


def _signed_out(driver) -> bool:
    host = _host(driver.url())
    if host == 'accounts.google.com':
        return True
    return host in S.HOSTS and driver.visible(S.SIGNED_OUT_CSS)


def _unsupported(req: GenerationRequest) -> str | None:
    """A field the Gemini page has no control for: refused, never silently dropped."""
    for field, label in (('resolution', 'a resolution'), ('seed', 'a seed'), ('negative_prompt', 'a negative prompt')):
        if getattr(req, field, None) not in (None, ''):
            return f'Gemini on the web has no control for {label}; remove it or use another engine'
    if req.first_frame or req.last_frame:
        return 'Gemini on the web takes reference pictures only, not first or last frames'
    return None


def _reference_paths(req: GenerationRequest) -> list[str]:
    from mc import desk_engines as eng
    paths = []
    for ref in req.reference_images:
        eng._read_asset(ref)                    # refuses anything outside data/uploads, or not a picture
        root = Path(eng.UPLOADS_ROOT).resolve()  # type: ignore[arg-type]
        raw = Path(ref['path'])
        paths.append(str((raw if raw.is_absolute() else root / raw).resolve()))
    return paths


class _Stop(Exception):
    def __init__(self, step: str):
        super().__init__(step)
        self.step = step


def _locate_wait(driver, css: str, labels: list[str], timeout: float) -> dict | None:
    end = _clock.monotonic() + timeout
    while True:
        hit = driver.locate(css, labels)
        if hit:
            return hit
        if _clock.monotonic() >= end:
            return None
        _clock.sleep(S.TIMEOUTS['poll'])


def _click(driver, css: str, labels: list[str], step: str, timeout: float | None = None) -> None:
    hit = _locate_wait(driver, css, labels, S.TIMEOUTS['step'] if timeout is None else timeout)
    if not hit:
        raise _Stop(step)
    driver.click_at(hit['x'], hit['y'])


def _choose(driver, trigger: dict, current: list[str], option: dict, wanted: list[str], step: str) -> None:
    """Open a dropdown and pick the entry labelled `wanted`; nothing to do when the control
    already shows it."""
    if any(driver.present(trigger['css'], w) for w in wanted):
        return
    _click(driver, trigger['css'], current, step + ' menu')
    _click(driver, option['css'], wanted, step)


def _ensure_images_mode(driver) -> None:
    m = S.IMAGES_MODE
    if driver.present(m['active']['css'], m['active']['labels'][0]):
        return
    if not driver.locate(m['choose']['css'], m['choose']['labels']):
        _click(driver, m['open']['css'], m['open']['labels'], 'Images mode menu')
    _click(driver, m['choose']['css'], m['choose']['labels'], 'Images mode')
    if not _locate_wait(driver, m['active']['css'], m['active']['labels'], S.TIMEOUTS['step']):
        raise _Stop('Images mode')


def _attach(driver, paths: list[str]) -> None:
    a = S.ATTACH
    if not driver.locate(a['choose']['css'], a['choose']['labels']):
        _click(driver, a['open']['css'], a['open']['labels'], 'attach menu')
    _click(driver, a['choose']['css'], a['choose']['labels'], 'attach entry')
    if not driver.attach(a['file_input_css'], paths):
        raise _Stop('attach file')
    if not driver.wait(a['thumb_css'], S.TIMEOUTS['step'] * 2):
        raise _Stop('attached picture')


def _unsent(step: str):
    from mc.desk_engines import EngineError
    return EngineError('engine', f'The Gemini page did not match what Clayrune expects at "{step}", '
                                 'so nothing was sent. The selectors in desk_gemini_web_selectors.py need a look.',
                       definitive=True, not_sent=True, plain=True)


def _sent(kind: str, text: str, driver, *, definitive: bool = False):
    from mc.desk_engines import EngineError
    chat = driver.url()
    if re.match(S.CHAT_URL, chat or ''):
        text += f' The chat is {chat}'
    return EngineError(kind, text, definitive=definitive, plain=True)


def _wait_for_pictures(driver, baseline: int) -> int:
    from mc.desk_engines import EngineError
    r = S.RESULT_IMAGE
    end = _clock.monotonic() + S.TIMEOUTS['generate']
    last, since = baseline, None
    while _clock.monotonic() < end:
        n = driver.image_count(r['css'], r['min_px'])
        if n > baseline:
            if n != last:
                last, since = n, _clock.monotonic()
            elif _clock.monotonic() - since >= S.TIMEOUTS['settle']:
                return n
        else:
            for notice in S.NOTICES:
                if driver.page_has_text(S.RESULT_SCOPE_CSS, list(notice['needles'])):
                    kind = notice['kind']
                    raise EngineError(kind, {
                        'quota': 'Gemini says the plan limit for pictures has been reached. Try again later.',
                        'moderation': 'Gemini declined to make this picture.'}[kind], definitive=True, plain=True)
        _clock.sleep(S.TIMEOUTS['poll'])
    raise _sent('engine', f"No picture appeared within {S.TIMEOUTS['generate']} seconds. "
                          'Gemini may still finish it, so check before sending again.', driver)


def _magic_mime(data: bytes) -> str | None:
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:2] == b'\xff\xd8':
        return 'image/jpeg'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def _download(driver, baseline: int, total: int) -> list[tuple[bytes, str]]:
    from mc import desk_engines as eng
    r = S.RESULT_IMAGE
    new = list(range(baseline, total))
    if len(new) > 1:
        if S.ON_MULTIPLE_IMAGES == 'first':
            new = new[:1]
        elif S.ON_MULTIPLE_IMAGES == 'refuse':
            raise _sent('engine', f'Gemini made {len(new)} pictures and this engine is set to refuse several.', driver)
    out = []
    for i in new:
        got = driver.fetch_image(r['css'], i, r['min_px'], S.FULL_SIZE, list(S.IMAGE_HOSTS),
                                 eng._MAX_ASSET_BYTES, S.TIMEOUTS['download'])
        mime = _magic_mime(got[0]) if got else None
        if not got or not mime:
            raise _sent('engine', 'Gemini made the picture but its full-size file could not be read from the page, '
                                  'so none was saved.', driver)
        out.append((got[0], mime))
    return out


def generate(driver, req: GenerationRequest, model_id: str, ref_paths: list[str]) -> list[tuple[bytes, str]]:
    """The whole flow against any driver. Raises EngineError: `not_sent` while nothing has
    left the page, non-definitive once Send was pressed."""
    from mc.desk_engines import EngineError
    try:
        driver.navigate(S.HOME)
        if _signed_out(driver):
            raise EngineError('auth', 'The Gemini pane is not signed in. Sign in there yourself and try again.',
                              definitive=True, not_sent=True, plain=True)
        if not driver.wait(S.COMPOSER_CSS, S.TIMEOUTS['page']):
            raise _Stop('the prompt box')
        _ensure_images_mode(driver)
        picker = list(S.MODEL_LABELS[model_id])
        _choose(driver, S.MODEL_PICKER, [*S.MODEL_PICKER['labels'], *[w for ls in S.MODEL_LABELS.values() for w in ls]], S.MODEL_OPTION,
                picker, 'model picker')
        ratio = list(S.RATIO_LABELS[req.aspect_ratio])
        _choose(driver, S.RATIO_TRIGGER, [S.RATIO_TRIGGER['base_label'], *[w for ls in S.RATIO_LABELS.values() for w in ls]],
                S.RATIO_OPTION, ratio, 'aspect ratio')
        if ref_paths:
            _attach(driver, ref_paths)
        baseline = driver.image_count(S.RESULT_IMAGE['css'], S.RESULT_IMAGE['min_px'])
        if not driver.insert_text(S.COMPOSER_CSS, req.prompt):
            raise _Stop('typing the prompt')
        _click(driver, S.SEND['css'], S.SEND['labels'], 'the send button')
    except _Stop as s:
        raise _unsent(s.step) from None
    total = _wait_for_pictures(driver, baseline)
    return _download(driver, baseline, total)


class GeminiWebAdapter:
    def estimate(self, model: ModelDescriptor, req: GenerationRequest, creds: _Creds) -> Estimate:
        from mc import desk_engines as eng
        why = _unsupported(req)
        if why:
            raise eng.Refused('invalid_input', why, 400)
        return eng.Estimate(usd=0.0, basis='plan', read=eng.now_iso(),
                            note='Runs on your Gemini plan: no per-picture charge. Your plan\'s own limits still apply.')

    def submit(self, model: ModelDescriptor, req: GenerationRequest, creds: _Creds) -> dict:
        from mc import desk_engines as eng
        why = _unsupported(req)
        if why:
            raise eng.Refused('invalid_input', why, 400)
        if model.model_id not in S.MODEL_LABELS or req.aspect_ratio not in S.RATIO_LABELS:
            raise eng.Refused('invalid_input', f'{model.model_id} at {req.aspect_ratio} is not in the Gemini page table', 400)
        paths = _reference_paths(req)
        name = profile()
        with _run_locks_guard:
            lock = _run_locks.setdefault(name, threading.Lock())
        if not lock.acquire(blocking=False):
            raise eng.EngineError('engine', 'A picture is already being made in the Gemini pane. Wait for it to finish.',
                                  definitive=True, not_sent=True, plain=True)
        try:
            from mc.desk_assisted_post import AssistedError
            try:
                driver = _factory(name)
            except AssistedError:
                raise eng.EngineError('engine', f"No browser pane is open on the '{name}' profile.",
                                      definitive=True, not_sent=True, plain=True) from None
            try:
                outputs = generate(driver, req, model.model_id, paths)
            finally:
                _close(driver)
        finally:
            lock.release()
        return {'outputs': outputs, 'cost_usd': None}

    def poll(self, model, ref, creds) -> dict:
        from mc.desk_engines import EngineError
        raise EngineError('engine', 'gemini_web jobs finish inside submit and are never polled')

    def fetch(self, model, item, creds) -> list:
        from mc.desk_engines import EngineError
        raise EngineError('engine', 'gemini_web jobs finish inside submit and are never fetched')
