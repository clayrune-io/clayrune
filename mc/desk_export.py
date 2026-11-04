"""Local timeline exports: immutable scene snapshots, bounded jobs, approved ffmpeg.

Outputs share the existing video/Generated material directory. Job state is
in-memory; a server restart ends polling with an explicit not-found response.
Text-only scenes use black frames and an explicit Windows font when available.
"""
from __future__ import annotations

import copy
import math
import os
import re
import threading
import textwrap
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from mc import addons, desk_pieces as pieces
from mc import desk_file_explorer as explorer, desk_storyboard as boards
from mc import desk_storyboard_media as media, desk_stitch as stitch
from mc.core import _log

_lock = threading.RLock()
_jobs: dict[str, dict] = {}
MAX_JOBS = 32
FPS = 30
WIDTH, HEIGHT = 1280, 720
TEXT_NOTE = 'Text-only scenes use plain black frames; titles are not burned in.'


def title_font() -> Path | None:
    """No fontconfig/PATH guessing: use Windows' known font file only."""
    windows = os.environ.get('WINDIR') if os.name == 'nt' else None
    font = Path(windows) / 'Fonts' / 'segoeui.ttf' if windows else None
    return font if font and font.is_file() and _filter_path(font) is not None else None


def _filter_path(path: Path) -> str | None:
    # ffmpeg's filter and option parsers both interpret quotes/separators. A
    # conservative path vocabulary avoids mis-escaping an unusual user folder;
    # those installations get the disclosed plain-black fallback instead.
    value = path.as_posix()
    if not re.fullmatch(r'[A-Za-z0-9_ /:.\-]+', value):
        return None
    return value.replace(':', r'\:')


def authorize_path(path: Path, *, existing: bool = True) -> Path:
    """Apply the current explorer home/project policy, including link guards."""
    for location in explorer.roots():
        try:
            relative = path.absolute().relative_to(location['root']).as_posix()
        except ValueError:
            continue
        if existing:
            return explorer.authorize(location['id'], relative)[1]
        # Output's parent already exists; no arbitrary destination from a client.
        explorer.authorize(location['id'], path.parent.relative_to(location['root']).as_posix(), directory=True)
        return path.resolve()
    raise pieces.PieceError('Export media must be inside Home or a registered project.', 403)


def segment_command(ffmpeg: str, source: Path | None, kind: str, seconds: float,
                    output: Path, *, audio: bool = False, font: Path | None = None,
                    title_file: Path | None = None) -> list[str]:
    """One exact-length, normalized H264/AAC segment (clips start at zero)."""
    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y']
    if source is None:
        cmd += ['-f', 'lavfi', '-i', f'color=c=black:s={WIDTH}x{HEIGHT}:r={FPS}']
    else:
        cmd += ['-i', str(source)]
    if not audio:
        cmd += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
    # Pad short legacy clips with their last frame and short audio with silence.
    filters = (f'scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,'
               f'pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},'
               f'tpad=stop_mode=clone:stop_duration={seconds}')
    if source is not None and kind == 'image':
        filters = 'trim=end_frame=1,setpts=PTS-STARTPTS,' + filters
    font_path = _filter_path(font) if font else None
    text_path = _filter_path(title_file) if title_file else None
    if source is None and font_path and text_path:
        filters += (f",drawtext=fontfile='{font_path}':textfile='{text_path}':expansion=none:"
                    'fontcolor=white:fontsize=40:x=(w-text_w)/2:y=(h-text_h)/2')
    cmd += ['-map', '0:v:0', '-map', '0:a:0' if audio else '1:a:0',
            '-vf', filters, '-af', 'aresample=48000,apad', '-t', str(seconds),
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-video_track_timescale', '90000',
            '-movflags', '+faststart', str(output)]
    return cmd


def has_audio(ffmpeg: str, source: Path) -> bool:
    """Ask the approved ffmpeg itself; ffprobe is a separate optional add-on."""
    code, tail = stitch.run_ffmpeg([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                                   '-i', str(source), '-map', '0:a:0', '-t', '0.01', '-f', 'null', '-'])
    if code == 0:
        return True
    if 'matches no streams' in tail:
        return False
    raise RuntimeError(f'Could not read clip audio (ffmpeg {code}): {tail}')


def _update(job_id: str, **fields) -> None:
    with _lock:
        _jobs[job_id].update(fields)


def get_job(job_id: str) -> dict:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            raise pieces.PieceError('Export job not found. The server may have restarted; retry Export.', 404)
        return copy.deepcopy(job)


def latest(owner_kind: str, owner_id: str) -> dict | None:
    boards.get_storyboard(owner_kind, owner_id)  # validate owner
    with _lock:
        return next((copy.deepcopy(j) for j in reversed(list(_jobs.values()))
                     if j['owner'] == {'kind': owner_kind, 'id': owner_id}), None)


def _run(job_id: str, scenes: list[dict], output: Path) -> None:
    try:
        ffmpeg = stitch.ffmpeg_path()
        if ffmpeg is None:
            raise RuntimeError('ffmpeg approval is no longer available. Approve in Settings > Add-ons, then retry Export.')
        with addons.hold('ffmpeg', f'Studio export {job_id}'), TemporaryDirectory(prefix='studio-export-', dir=output.parent) as scratch:
            _update(job_id, status='running', message='Preparing scenes')
            segments = []
            title_notes: set[str] = set()
            font = title_font()
            for index, scene in enumerate(scenes):
                authorize_path(output, existing=False)
                picture = scene.get('picture')
                source = authorize_path(media.resolve_asset(picture['path'], picture['kind'])) if picture else None
                kind = picture['kind'] if picture else 'black'
                segment = Path(scratch) / f'{index:04d}.mp4'
                audio = bool(source and kind == 'video' and has_audio(ffmpeg, source))
                title_file = Path(scratch) / f'{index:04d}.txt' if source is None and font else None
                if title_file and _filter_path(title_file) is None:
                    title_file = None
                if title_file:
                    # Title is data, never part of the filter expression. Bound
                    # lines to the 1280px stage so a long title stays readable.
                    title_file.write_text('\n'.join(textwrap.wrap(scene['label'], 45)), encoding='utf-8')
                cmd = segment_command(ffmpeg, source, kind, scene['duration_sec'], segment,
                                      audio=audio, font=font, title_file=title_file)
                code, tail = stitch.run_ffmpeg(cmd)
                if code and title_file:
                    _log(f'[desk-export] scene {index + 1} title unavailable; retrying black: {tail}', flush=True)
                    title_file = None
                    code, tail = stitch.run_ffmpeg(segment_command(ffmpeg, source, kind, scene['duration_sec'], segment, audio=audio))
                if code or not segment.is_file():
                    raise RuntimeError(f'Scene {index + 1}: ffmpeg exited {code}: {tail}')
                if source is None:
                    title_notes.add('Text-only titles use Segoe UI on black frames.' if title_file else TEXT_NOTE)
                segments.append(segment)
                _update(job_id, progress=round(90 * (index + 1) / len(scenes)), message=f'Prepared scene {index + 1} of {len(scenes)}')
            concat = Path(scratch) / 'segments.txt'
            concat.write_text(stitch.concat_list(segments), encoding='utf-8')
            _update(job_id, message='Joining MP4')
            authorize_path(output, existing=False)
            code, tail = stitch.run_ffmpeg(stitch.stitch_command(ffmpeg, str(concat), str(output), crop_square=False, copy=True))
            if code or not output.is_file():
                raise RuntimeError(f'MP4 join: ffmpeg exited {code}: {tail}')
        authorize_path(output)
        _update(job_id, status='done', progress=100, message='MP4 ready. ' + ' '.join(sorted(title_notes)),
                path=str(output), download=f'/api/desk/exports/{job_id}/file')
    except Exception as e:
        _log(f'[desk-export] job {job_id} failed: {e}', flush=True)
        try:
            output.unlink(missing_ok=True)
        except OSError as cleanup_error:
            _log(f'[desk-export] partial output cleanup failed: {cleanup_error}', flush=True)
        _update(job_id, status='failed', message=str(e)[:1000])


def start(owner_kind: str, owner_id: str, raw_scenes: object) -> dict:
    """Snapshot the same scenes Preview reads; reject any invalid input whole."""
    boards.get_storyboard(owner_kind, owner_id)
    if not isinstance(raw_scenes, list):
        raise pieces.PieceError('Export needs the current scene list.')
    problems: list[str] = []
    scenes = boards._clean_scenes([s for s in raw_scenes if not isinstance(s, dict) or not s.get('placeholder')], problems)
    if problems:
        raise pieces.PieceError('; '.join(problems))
    if not scenes:
        raise pieces.PieceError('Add a scene to export.')
    # Reject bad floats here too if validation is ever loosened upstream.
    if any(not math.isfinite(s['duration_sec']) for s in scenes):
        raise pieces.PieceError('Scene durations must be finite.')
    folder = pieces._uploads_root().joinpath(*pieces.LIBRARY_ROOT, 'video', 'Generated')
    # The canonical material folder must itself be browsable before any mkdir.
    parent = folder
    while not parent.exists():
        parent = parent.parent
    for location in explorer.roots():
        try:
            rel = parent.relative_to(location['root']).as_posix()
        except ValueError:
            continue
        explorer.authorize(location['id'], rel, directory=True)
        break
    else:
        raise pieces.PieceError('The material library must be inside Home or a registered project.', 403)
    folder.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex
    output = folder / f'{owner_kind}-{owner_id}-export-{job_id}.mp4'
    authorize_path(output, existing=False)
    for scene in scenes:
        if scene.get('picture'):
            pic = scene['picture']
            authorize_path(media.resolve_asset(pic['path'], pic['kind']))
    with _lock:
        active = next((j for j in _jobs.values() if j['status'] in ('queued', 'running')), None)
        if active:
            if active['owner'] == {'kind': owner_kind, 'id': owner_id}:
                return copy.deepcopy(active)
            raise pieces.PieceError('Another local export is running. Retry after it finishes.', 409)
        while len(_jobs) >= MAX_JOBS:
            del _jobs[next(iter(_jobs))]
        if stitch.ffmpeg_path() is None:
            command = stitch.request_ffmpeg('Export the current Studio timeline to a local MP4.')
            return {'status': 'held', 'progress': 0, 'message':
                    'Approve ffmpeg in Settings > Add-ons, then click Export again. '
                    + (f'Or run this command yourself: {command}' if command else 'The add-on approval card has been requested.')}
        job = {'id': job_id, 'owner': {'kind': owner_kind, 'id': owner_id}, 'status': 'queued',
               'progress': 0, 'message': 'Queued local export.',
               'duration_sec': sum(s['duration_sec'] for s in scenes), 'scene_count': len(scenes)}
        _jobs[job_id] = job
        try:
            threading.Thread(target=_run, args=(job_id, copy.deepcopy(scenes), output),
                             daemon=True, name=f'desk-export-{job_id[:8]}').start()
        except Exception as e:
            _log(f'[desk-export] could not start job: {e}', flush=True)
            job.update(status='failed', message=f'Could not start export: {e}')
        return copy.deepcopy(job)
