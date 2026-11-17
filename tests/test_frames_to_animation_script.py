"""tools/frames-to-animation.sh (MC-1179 pass 12 second wave, TL40): a frame whose file
name holds a single quote is written to the concat list in escaped form, so ffmpeg can
read the list. ffmpeg itself is replaced by a shim that keeps the list it was given."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / 'tools' / 'frames-to-animation.sh'
BASH = shutil.which('bash')
ESCQ = "'" + chr(92) + "''"   # how a single quote is written inside a quoted concat entry
pytestmark = pytest.mark.skipif(BASH is None, reason='needs bash')

SHIM = r'''#!/bin/sh
prev=""; list=""
for a in "$@"; do
  if [ "$prev" = "-i" ] && [ -z "$list" ]; then list="$a"; fi
  if [ "$a" = "-hide_banner" ]; then : > "$prev"; fi
  prev="$a"
done
cp "$list" "$CAPTURE_LIST"
'''


def _run(tmp_path, names):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    shim = bin_dir / 'ffmpeg'
    shim.write_bytes(SHIM.encode('utf-8'))
    shim.chmod(0o755)
    frames = []
    for n in names:
        f = tmp_path / n
        f.write_bytes(b'x')
        frames.append(f)
    env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ['PATH'],
               CAPTURE_LIST=str(tmp_path / 'captured.txt'), SCRIPT=str(SCRIPT),
               OUT=str(tmp_path / 'out.webp'))
    # Frame names travel in the environment: on Windows the command line of a bash
    # started from Python is re-parsed by the MSYS runtime, which eats a single quote.
    for i, f in enumerate(frames):
        env[f'FRAME{i}'] = str(f)
    refs = ' '.join(f'"$FRAME{i}"' for i in range(len(frames)))
    r = subprocess.run([BASH, '-c', f'bash "$SCRIPT" -o "$OUT" {refs}'],
                       capture_output=True, text=True, env=env, timeout=60)
    cap = tmp_path / 'captured.txt'
    return r, (cap.read_text(encoding='utf-8') if cap.exists() else '')


def test_a_single_quote_in_a_frame_name_is_escaped_in_the_concat_list(tmp_path):
    r, listing = _run(tmp_path, ["it's_a.png", 'b.png'])
    assert r.returncode == 0, r.stderr
    assert 'it' + ESCQ + "s_a.png'" in listing
    # every entry is a balanced quoted string once the escapes are taken out
    for line in listing.splitlines():
        if line.startswith('file '):
            assert line.replace(ESCQ, '').count("'") == 2, line


def test_the_last_frame_is_repeated_without_a_duration(tmp_path):
    r, listing = _run(tmp_path, ['a.png', "b'q.png"])
    assert r.returncode == 0, r.stderr
    lines = listing.splitlines()
    assert lines[-1].startswith('file ') and 'b' + ESCQ + "q.png'" in lines[-1]
    assert lines[-2].startswith('duration ')
