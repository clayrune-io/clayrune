"""MC-1022: the REAL catalogued ffmpeg, installed through the real installer and
run by absolute path through `mc.desk_stitch`.

Needs the pinned build (~190 MB). It is fetched once into the OS temp dir and
reused; the installer still hashes whatever it is handed against the catalogue
pin, so a stale or corrupt cache fails the install instead of passing. With no
network and no cache the whole module skips. Platforms with no catalogued
download (macOS in v1) skip.
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk_stitch  # noqa: E402
from mc.addons import catalogue, installer  # noqa: E402
from mc.addons import manifest as mf  # noqa: E402
from mc.addons.manifest import AddonError  # noqa: E402

FIXTURES = PROJECT_ROOT / 'tests' / 'fixtures' / 'desk_stitch'
CACHE = Path(tempfile.gettempdir()) / 'clayrune_addon_test_cache'


def _cached_archive(src: dict) -> Path:
    path = CACHE / (src['sha256'] + '.archive')
    if path.is_file():
        return path
    CACHE.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix('.part')
    try:
        req = urllib.request.Request(src['url'], headers={'User-Agent': 'Clayrune-addons-test/1'})
        with urllib.request.urlopen(req, timeout=60) as resp, open(part, 'wb') as f:
            shutil.copyfileobj(resp, f, 1 << 20)
    except OSError as e:
        part.unlink(missing_ok=True)
        pytest.skip(f'no network for the pinned ffmpeg build: {e}')
    os.replace(part, path)
    return path


@pytest.fixture(scope='module')
def real_ffmpeg(tmp_path_factory):
    entry = catalogue.get('ffmpeg')
    src = catalogue.static_source(entry) if entry else None
    if src is None:
        pytest.skip(f'no catalogued ffmpeg download for {catalogue.platform_key()}')
    archive = _cached_archive(src)
    home = tmp_path_factory.mktemp('addons_real')
    mp = pytest.MonkeyPatch()
    mp.setenv('CLAYRUNE_HOME', str(home))
    mp.setenv('CLAYRUNE_ADDONS_DIR', str(home / 'addons'))
    mp.setattr(installer, '_open_url', lambda url: open(archive, 'rb'))
    try:
        new = installer.install_from_catalogue(entry, src, request_id='real', requested_by=None,
                                               approved_at=mf.now_iso())
        mf.put_entry(new)
        yield home / 'addons'
    finally:
        mp.undo()
        shutil.rmtree(home, ignore_errors=True)


def test_install_lands_under_the_addons_root_and_resolves_to_an_absolute_path(real_ffmpeg):
    path = Path(desk_stitch.ffmpeg_path())
    assert path.is_absolute()
    assert real_ffmpeg in path.parents
    assert Path(desk_stitch.ffprobe_path()).is_absolute()
    out = subprocess.run([str(path), '-version'], capture_output=True, text=True, check=True).stdout
    assert out.startswith('ffmpeg version')


def test_a_tampered_real_binary_is_refused(real_ffmpeg):
    path = Path(desk_stitch.ffmpeg_path())
    original = path.read_bytes()
    try:
        with open(path, 'ab') as f:
            f.write(b'\0')
        assert desk_stitch.ffmpeg_path() is None
        assert mf.get_entry('ffmpeg')['status'] == 'broken'
    finally:
        path.write_bytes(original)
    assert desk_stitch.ffmpeg_path() == str(path)


def _probe(path: Path) -> dict:
    import json
    out = subprocess.run([desk_stitch.ffprobe_path(), '-v', 'error', '-show_entries',
                          'stream=codec_type,width,height:format=duration', '-of', 'json', str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_stitch_joins_two_matching_clips(real_ffmpeg, tmp_path):
    out = tmp_path / 'joined.mp4'
    desk_stitch.stitch([FIXTURES / 'clip_a.mp4', FIXTURES / 'clip_b.mp4'], out)
    info = _probe(out)
    assert abs(float(info['format']['duration']) - 4.0) < 0.3
    video = [s for s in info['streams'] if s['codec_type'] == 'video'][0]
    assert (video['width'], video['height']) == (160, 120)


def test_stitch_of_mismatched_clips_reencodes_and_still_works(real_ffmpeg, tmp_path):
    out = tmp_path / 'mixed.mp4'
    desk_stitch.stitch([FIXTURES / 'clip_a.mp4', FIXTURES / 'clip_wide.mp4'], out)
    assert out.is_file() and float(_probe(out)['format']['duration']) > 2.5


def test_crop_square_makes_a_1_to_1_picture(real_ffmpeg, tmp_path):
    out = tmp_path / 'square.mp4'
    desk_stitch.crop_square(FIXTURES / 'clip_wide.mp4', out)
    video = [s for s in _probe(out)['streams'] if s['codec_type'] == 'video'][0]
    assert video['width'] == video['height'] == 180


def test_a_failing_ffmpeg_raises_and_leaves_no_output(real_ffmpeg, tmp_path):
    out = tmp_path / 'bad.mp4'
    with pytest.raises(RuntimeError, match='ffmpeg exited'):
        desk_stitch.stitch([tmp_path / 'does-not-exist.mp4'], out)
    assert not out.exists()
