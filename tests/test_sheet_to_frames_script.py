"""tools/sheet-to-frames.sh must only ever touch the frame files it writes."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / 'tools' / 'sheet-to-frames.sh'
BASH = shutil.which('bash')

pytestmark = pytest.mark.skipif(not (BASH and shutil.which('ffmpeg')),
                                reason='needs bash and ffmpeg')


def _sheet(tmp_path: Path) -> Path:
    sheet = tmp_path / 'sheet.png'
    subprocess.run(['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=red:s=40x40', '-frames:v', '1',
                    '-hide_banner', '-loglevel', 'error', str(sheet)], check=True, timeout=60)
    return sheet


def _run(tmp_path: Path, *args: str):
    return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True,
                          timeout=120, cwd=str(tmp_path))


def test_existing_output_dir_keeps_files_that_are_not_frames(tmp_path):
    out = tmp_path / 'assets'
    out.mkdir()
    (out / 'logo.svg').write_text('keep me')
    (out / 'frame_9.png').write_bytes(b'stale')
    done = _run(tmp_path, str(_sheet(tmp_path)), '2x2', str(out))
    assert done.returncode == 0, done.stderr
    assert (out / 'logo.svg').read_text() == 'keep me'
    assert not (out / 'frame_9.png').exists()
    assert sorted(p.name for p in out.glob('frame_*.png')) == [f'frame_{i}.png' for i in range(1, 5)]


@pytest.mark.parametrize('grid', ['4', '0x2', '2x0'])
def test_a_grid_that_is_not_cols_x_rows_is_refused(tmp_path, grid):
    out = tmp_path / 'out'
    done = _run(tmp_path, str(_sheet(tmp_path)), grid, str(out))
    assert done.returncode == 2 and 'ERROR' in done.stderr
    assert not out.exists()
