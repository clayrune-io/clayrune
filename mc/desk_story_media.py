"""Desk Studio: the pictures and clips the storyboard chat agent can actually SEE.

The chat agent used to be sent each scene's text only: a picture attached to a
scene never reached it, and `desk_read_guard` refuses every gitignored path, which
includes `data/uploads/` (the Desk's own material library). So the agent answered,
honestly, that it could not see what the user had attached.

THIS MODULE gathers what the agent is allowed to look at for ONE turn, and nothing
else in the server decides that:

  * a scene's saved `picture`, looked up in the SAVED board (never a path from the
    request body: the page may send a scene `id`, which is only a key into the
    stored scenes) and resolved through `desk_storyboard_media.resolve_asset`, so it
    is library-contained and realpath-checked;
  * a library file the message points at with a `/api/serve-image?path=` (or
    `/api/serve-file?path=`) URL, resolved the same way;
  * for a VIDEO, a handful of evenly spaced still frames cut with the approved
    ffmpeg into a temp folder that lives only for the turn.

The result is an exact-file allowlist. `desk_research.run` writes it beside the
hook settings and `desk_read_guard` lets `Read` open those absolute paths and no
others; Glob and Grep gain nothing.

Online video stays out of the server (journal 863ee224, DECIDED WITHOUT RON): the
agent is told to ask the user to save a clip into the material library, where this
module can reach it.
"""

from __future__ import annotations

import contextlib
import re
import subprocess
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator, Optional

from mc import desk_pieces as _pieces
from mc import desk_storyboard as _sb
from mc import desk_storyboard_media as _media
from mc.core import _log

MAX_PICTURES = 6      # stills the agent is shown in one turn, the selected scene first
MAX_CLIPS = 2         # video clips cut into frames in one turn
CLIP_FRAMES = 6       # evenly spaced stills per clip
MAX_REFERENCES = 3    # library files the message itself may point at
FRAME_WIDTH = 640     # a frame is for looking at, not for judging pixels
FFMPEG_TIMEOUT = 60

_URL = re.compile(r'/api/serve-(?:image|file)\?[^\s)"\'<>]+')
_DURATION = re.compile(r'Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)')

INSTRUCTION = (
    '\n\nPictures and clips: you cannot watch a video from a web link, and you cannot fetch pictures from a URL. '
    'If the user wants you to see an online clip (for example a stock-footage preview), tell them to save it '
    'into the material library with the Studio upload, then ask again. Never say you looked at a picture or clip '
    'you did not open, and never describe one from its title alone.')
_INSTRUCTION_READ = (
    '\nA scene\'s "picture" with a "file" is an image you can look at: open it with the Read tool, using that exact '
    'path string. A picture with "frames" is a video clip: each frame has a "file" (a still) and "at_sec" (where in '
    'the clip it was taken), so Read them to see the clip. Files in "referenced" are library files the user pointed at '
    'in their message. No other file is readable. Do not open them unless the user\'s message or the work needs it.')
_INSTRUCTION_BLIND = (
    '\nThis agent\'s engine cannot open files in this chat, so the pictures and clips are NOT viewable: the user '
    'attached them but you cannot see them. Say so plainly rather than guessing what is in them.')


@dataclass
class TurnMedia:
    rows: dict[int, dict] = field(default_factory=dict)       # scene number -> its "picture" row
    referenced: list[dict] = field(default_factory=list)      # library files the message pointed at
    readable: list[str] = field(default_factory=list)         # the exact-file allowlist
    notes: list[str] = field(default_factory=list)            # what could not be shown, and why
    blind: bool = False

    @property
    def instruction(self) -> str:
        return INSTRUCTION + (_INSTRUCTION_BLIND if self.blind else '') + (_INSTRUCTION_READ if self.readable else '')

    def apply(self, payload: dict) -> None:
        """Put the rows into the `scenes` of the chat payload, in place."""
        for number, row in self.rows.items():
            payload['scenes'][number - 1]['picture'] = row
        if self.referenced:
            payload['referenced'] = self.referenced
        if self.notes:
            payload['media_notes'] = self.notes


def blind_reason(provider: str, project: Optional[dict]) -> str:
    """Why this engine cannot open files in the chat, or '' when it can. Asks the
    same capability check the model call itself will pass through."""
    from mc import desk_research
    caps = desk_research.capability(provider, project)
    return str(caps.get('reason') or 'no file-reading tools') if caps.get('toolless') else ''


# -- which saved scene is this one ----------------------------------------------------

def _saved_for(index: int, raw: Any, sent: int, saved: list[dict]) -> Optional[dict]:
    """The stored scene the page's scene number `index` (0-based) is. The page sends
    the scene's `id`. A page that does not (an old tab) is matched by position only
    when the lists are the same length AND the labels agree: a picture shown on the
    wrong scene is worse than none."""
    sid = raw.get('id') if isinstance(raw, dict) else None
    if isinstance(sid, str):
        return next((s for s in saved if s.get('id') == sid), None)
    if len(saved) == sent and isinstance(raw, dict) and saved[index].get('label') == raw.get('label'):
        return saved[index]
    return None


def _resolve(pic: Any) -> tuple[Path, str]:
    """A stored AssetRef to its real library file and kind; PieceError when it no
    longer resolves."""
    if not isinstance(pic, dict):
        raise _sb.PieceError('no picture')
    kind = pic.get('kind', 'image')
    real = _sb._resolve_picture(pic.get('path')) if kind == 'image' else _media.resolve_asset(pic.get('path'), kind)
    return real, kind


def _referenced_files(message: str) -> list[tuple[Path, str]]:
    """Library files the message names by an /api/serve-image?path= style URL. The
    URL is only a way of NAMING the file: it must resolve inside the material
    library like any scene asset, or it is ignored."""
    out: list[tuple[Path, str]] = []
    for m in _URL.finditer(message):
        for value in urllib.parse.parse_qs(urllib.parse.urlsplit(m.group(0)).query).get('path', []):
            try:
                real = Path(value).resolve()
                kind = _pieces.asset_kind_for(real.name)
                if kind not in ('image', 'video'):
                    continue
                found = _media.resolve_asset(real.relative_to(_pieces._uploads_root()).as_posix(), kind)
            except (OSError, ValueError, _sb.PieceError):
                continue
            if all(found != f for f, _ in out):
                out.append((found, kind))
    return out[:MAX_REFERENCES]


# -- clips to frames ---------------------------------------------------------------------

def _run(cmd: list[str]) -> tuple[int, str]:
    """One ffmpeg call -> (returncode, stderr). The one place this module starts a
    subprocess, so tests replace it."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=FFMPEG_TIMEOUT, check=False,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        return 124, f'timed out after {FFMPEG_TIMEOUT}s'
    except OSError as e:
        return 127, str(e)[:300]
    return p.returncode, p.stderr or ''


def _ffmpeg() -> Optional[str]:
    from mc import desk_stitch
    return desk_stitch.ffmpeg_path()


def _duration(ffmpeg: str, clip: Path) -> Optional[float]:
    # `ffmpeg -i` with no output exits 1 but still prints the header; ffprobe is a
    # separate optional add-on, so the approved ffmpeg is asked on its own.
    _, err = _run([ffmpeg, '-hide_banner', '-nostdin', '-i', str(clip)])
    m = _DURATION.search(err)
    if not m:
        _log(f'[desk] storyboard chat: no duration for {clip.name}: {err[-200:]!r}', flush=True)
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def _frames(ffmpeg: str, clip: Path, out_dir: Path, tag: str) -> tuple[list[dict], Optional[float]]:
    seconds = _duration(ffmpeg, clip)
    if not seconds or seconds <= 0:
        return [], None
    frames: list[dict] = []
    for k in range(CLIP_FRAMES):
        at = round(seconds * (k + 0.5) / CLIP_FRAMES, 2)
        dest = out_dir / f'{tag}-{k + 1}.jpg'
        code, err = _run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-ss', f'{at:.2f}',
                          '-i', str(clip), '-frames:v', '1', '-vf', f'scale={FRAME_WIDTH}:-2', '-q:v', '4', str(dest)])
        if code != 0 or not dest.is_file():
            _log(f'[desk] storyboard chat: frame {k + 1} of {clip.name} failed (ffmpeg {code}): {err[-200:]!r}', flush=True)
            continue
        frames.append({'file': str(dest), 'at_sec': at})
    return frames, round(seconds, 2)


# -- one turn ---------------------------------------------------------------------------------

@contextlib.contextmanager
def turn_media(saved_scenes: list[dict], sent_scenes: list[Any], message: str, focus: Optional[int],
               provider: str, project: Optional[dict]) -> Generator[TurnMedia]:
    """What the agent may look at this turn. The frames it holds live in a temp
    folder that exists only inside this `with`.

    `saved_scenes` is the board as STORED; `sent_scenes` the page's scene list, used
    only for each scene's `id` and label. Never a picture path."""
    media = TurnMedia()
    wanted: list[tuple[Optional[int], str, Path, str]] = []      # scene number | None, title, file, kind
    for i, raw in enumerate(sent_scenes):
        saved = _saved_for(i, raw, len(sent_scenes), saved_scenes)
        pic = (saved or {}).get('picture')
        if not pic:
            continue
        try:
            real, kind = _resolve(pic)
        except _sb.PieceError as e:
            media.notes.append(f'Scene {i + 1} has a picture that is no longer in the library ({e}).')
            _log(f'[desk] storyboard chat: scene {i + 1} picture no longer resolves: {e}', flush=True)
            continue
        wanted.append((i + 1, str((saved or {}).get('picture', {}).get('title') or real.name), real, kind))
    for real, kind in _referenced_files(message):
        wanted.append((None, real.name, real, kind))
    if not wanted:
        yield media
        return
    # The selected scene is looked at first, then what the message names, then the rest in order.
    wanted.sort(key=lambda w: 0 if (w[0] is not None and w[0] == focus) else 1 if w[0] is None else 2)
    media.blind = bool(blind_reason(provider, project))
    if media.blind:
        for number, title, _, kind in wanted:
            if number is not None:
                media.rows[number] = {'title': title, 'kind': kind, 'viewable': False}
        media.notes.append('Pictures and clips are attached but cannot be viewed on this engine.')
        yield media
        return
    ffmpeg: Optional[str] = None
    ffmpeg_checked = False
    with tempfile.TemporaryDirectory(prefix='desk-story-media-') as tmp:
        out_dir = Path(tmp).resolve()
        pictures = clips = 0
        for n, (number, title, real, kind) in enumerate(wanted):
            row: dict[str, Any] = {'title': title, 'kind': kind}
            if kind == 'image':
                if pictures >= MAX_PICTURES:
                    row['viewable'] = False
                    row['why'] = f'only {MAX_PICTURES} pictures are shown per message'
                else:
                    pictures += 1
                    row['file'] = str(real)
                    media.readable.append(str(real))
            else:
                if not ffmpeg_checked:
                    ffmpeg, ffmpeg_checked = _ffmpeg(), True
                if clips >= MAX_CLIPS:
                    row['viewable'] = False
                    row['why'] = f'only {MAX_CLIPS} clips are shown per message'
                elif not ffmpeg:
                    row['viewable'] = False
                    row['why'] = 'ffmpeg is not installed or not approved, so the clip cannot be cut into frames'
                    media.notes.append('A video clip is attached but ffmpeg is not available, so it cannot be viewed.')
                else:
                    clips += 1
                    frames, seconds = _frames(ffmpeg, real, out_dir, f'clip{n + 1}')
                    if frames:
                        row['frames'] = frames
                        if seconds:
                            row['clip_sec'] = seconds
                        media.readable.extend(f['file'] for f in frames)
                    else:
                        row['viewable'] = False
                        row['why'] = 'ffmpeg could not read frames from this clip'
                        media.notes.append(f'ffmpeg could not read frames from the clip "{title}".')
            if number is not None:
                media.rows[number] = row
            else:
                media.referenced.append(row)
        yield media
