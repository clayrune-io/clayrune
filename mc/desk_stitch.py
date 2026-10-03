"""Desk video stitch and 1:1 crop on the user's machine (MC-1022, spec §7).

The first consumer of `mc.addons`. ffmpeg is never found on PATH and never
installed from here: `ffmpeg_path()` asks the add-on manifest, which re-hashes
the binary and returns its ABSOLUTE path, or raises `AddonMissing` when the user
has not approved it (or it changed since). The Desk render path
(`mc.desk_engines._join_clips`) calls `request_ffmpeg()` to put the approval
card in front of the user and parks the render; `resume_held_renders` is
registered as the add-on resume hook, so approving the install finishes it.

    stitch(clips, out)    join clips (stream copy when they match, else re-encode)
    crop_square(src, out) centre-crop to 1:1, re-encode
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from mc import addons
from mc.addons.manifest import AddonMissing
from mc.core import _log

FFMPEG_TIMEOUT = 600
_PROBE_TIMEOUT = 60
_ENCODE = ['-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'aac']


def ffmpeg_path() -> str | None:
    """Absolute path of the approved ffmpeg, or None (never a PATH lookup)."""
    try:
        return addons.resolve('ffmpeg')
    except AddonMissing:
        return None


def ffprobe_path() -> str | None:
    try:
        return addons.resolve('ffprobe')
    except AddonMissing:
        return None


def run_ffmpeg(cmd: list[str]) -> tuple[int, str]:
    """Run one ffmpeg command -> (returncode, stderr tail). The one place the
    join starts a subprocess, so tests replace it."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=FFMPEG_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return 124, f'timed out after {FFMPEG_TIMEOUT}s'
    except OSError as e:
        return 127, str(e)[:300]
    return p.returncode, (p.stderr or '')[-600:]


def stitch_command(ffmpeg: str, list_file: str, out_path: str, *, crop_square: bool, copy: bool) -> list[str]:
    """The ffmpeg argv that joins the clips named in `list_file` (concat
    demuxer) into `out_path`. Stream copy only when nothing changes the
    pictures (`copy` and no crop); a 1:1 crop is a centre square, which has to
    re-encode the video."""
    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', list_file]
    if copy and not crop_square:
        cmd += ['-c', 'copy']
    else:
        if crop_square:
            cmd += ['-vf', "crop='min(iw,ih)':'min(iw,ih)'"]
        cmd += _ENCODE
    cmd += ['-movflags', '+faststart', out_path]
    return cmd


def concat_list(paths: list[Path]) -> str:
    def q(p: Path) -> str:
        return str(p).replace('\\', '/').replace("'", "'\\''")
    return ''.join(f"file '{q(p)}'\n" for p in paths)


def codecs_match(paths: list[Path], probe: str | None) -> bool:
    """True only when ffprobe says every clip has the same video codec, size,
    pixel format and audio codec, the condition under which the concat demuxer
    can copy streams. No ffprobe, or any probe failure, is a mismatch: the
    caller then re-encodes, which is always correct, only slower."""
    if probe is None:
        return False
    seen = None
    for p in paths:
        cmd = [probe, '-v', 'error', '-show_entries', 'stream=codec_type,codec_name,width,height,pix_fmt',
               '-of', 'json', str(p)]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                 timeout=_PROBE_TIMEOUT, check=False)
            streams = json.loads(res.stdout or '{}').get('streams') or []
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return False
        if res.returncode != 0 or not streams:
            return False
        sig = sorted((s.get('codec_type'), s.get('codec_name'), s.get('width'), s.get('height'), s.get('pix_fmt'))
                     for s in streams)
        if seen is None:
            seen = sig
        elif sig != seen:
            return False
    return True


def _join(paths: list[Path], out: Path, *, crop: bool, label: str) -> None:
    """Shared body of `stitch` and `crop_square`. Raises `AddonMissing` when
    ffmpeg is not approved, `RuntimeError` (one-line reason) when ffmpeg fails."""
    ffmpeg = addons.resolve('ffmpeg')
    probe = ffprobe_path() if not crop else None
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', suffix='.concat.txt', delete=False, encoding='utf-8') as f:
        f.write(concat_list([Path(p) for p in paths]))
        list_file = f.name
    try:
        with addons.hold('ffmpeg', label):
            cmd = stitch_command(ffmpeg, list_file, str(out), crop_square=crop,
                                 copy=codecs_match([Path(p) for p in paths], probe))
            code, tail = run_ffmpeg(cmd)
    finally:
        try:
            Path(list_file).unlink()
        except OSError:
            pass
    if code != 0 or not out.is_file():
        try:
            out.unlink()
        except OSError:
            pass
        raise RuntimeError(f'ffmpeg exited {code}: {tail.strip()[-300:]}')


def stitch(clips: list, out: Path | str, *, label: str = 'desk stitch') -> None:
    """Join `clips` into `out`. Stream copy when every clip shares codec,
    resolution, pixel format and audio codec, otherwise re-encode to H.264/AAC."""
    if not clips:
        raise ValueError('stitch needs at least one clip')
    _join([Path(c) for c in clips], Path(out), crop=False, label=label)


def crop_square(src: Path | str, out: Path | str, *, label: str = 'desk crop') -> None:
    """Centre-crop `src` to 1:1 and re-encode to H.264/AAC."""
    _join([Path(src)], Path(out), crop=True, label=label)


# ── the approval request and the parked render ──────────────────────────────

def request_ffmpeg(reason: str) -> str:
    """File the ffmpeg approval card (installs nothing) and return the command
    the user can run themselves where there is no download for this OS. Offers
    adopting a system ffmpeg only on a platform with no catalogue download.
    Never raises: a render must not fail on this."""
    from mc.addons import catalogue, installer, service
    entry = None
    try:
        entry = catalogue.get('ffmpeg')
        ref = 'ffmpeg'
        if entry is not None and catalogue.static_source(entry) is None:
            try:
                installer.inspect_system(entry)
                ref = 'system:ffmpeg'
            except addons.AddonError:
                return catalogue.command_for_user(entry)     # no card possible: show the command
        service.file_request(ref, reason, {'kind': 'desk', 'session_id': '', 'project_id': '',
                                           'unattended': False})
    except Exception as e:
        _log(f'[desk_stitch] could not file the ffmpeg add-on request: {e}', flush=True)
    return ''


def resume_held_renders(addon_id: str) -> None:
    """Add-on resume hook: ffmpeg just landed, so every render held for want of
    it advances (the clips are already downloaded; nothing is paid for twice)."""
    if addon_id != 'ffmpeg':
        return
    from mc import desk_engines
    with desk_engines._lock:
        held = [rid for rid, r in desk_engines._read_store()['renders'].items()
                if r.get('status') == 'held' and (r.get('hold') or {}).get('kind') == 'ffmpeg_missing']
    for rid in held:
        try:
            desk_engines.poll_render(rid)
        except Exception as e:
            _log(f'[desk_stitch] resuming render {rid} failed: {e}', flush=True)


from mc.addons import service as _addon_service  # noqa: E402

_addon_service.register_resume_hook(resume_held_renders)
