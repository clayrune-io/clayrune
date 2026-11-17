"""tools/notarize-macos.sh (MC-1179 pass 12 second wave, TL40): a rejected Gatekeeper
assessment prints what spctl said before the script dies, and the temp files the run
makes are removed on exit. The script itself needs macOS; the Gatekeeper block is cut
out of it and run under bash with `spctl` stubbed."""
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / 'tools' / 'notarize-macos.sh'
BASH = shutil.which('bash')
pytestmark = pytest.mark.skipif(BASH is None, reason='needs bash')


def _gatekeeper_block():
    lines = SCRIPT.read_text(encoding='utf-8').splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith('GK_INFO='))
    end = next(i for i in range(start, len(lines)) if 'die "Gatekeeper assessment' in lines[i])
    return '\n'.join(lines[start:end + 1])


def test_rejected_assessment_reaches_die_with_spctl_output_instead_of_exiting_silently():
    prog = ('set -euo pipefail\n'
            'APP=/x\n'
            'spctl() { echo "/x: rejected source=Unnotarized Developer ID"; return 3; }\n'
            'die() { echo "DIE: $1"; exit 1; }\n' + _gatekeeper_block() + '\necho UNREACHED\n')
    r = subprocess.run([BASH, '-c', prog], capture_output=True, text=True, timeout=30)
    assert r.returncode == 1
    assert 'DIE: Gatekeeper assessment did not report' in r.stdout
    assert 'rejected source=Unnotarized Developer ID' in r.stdout
    assert 'UNREACHED' not in r.stdout


def test_notarized_assessment_passes():
    prog = ('set -euo pipefail\n'
            'APP=/x\n'
            'spctl() { echo "/x: accepted source=Notarized Developer ID"; }\n'
            'die() { echo "DIE: $1"; exit 1; }\n' + _gatekeeper_block() + '\necho REACHED\n')
    r = subprocess.run([BASH, '-c', prog], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and 'REACHED' in r.stdout


def test_script_still_parses_and_cleans_up_its_temp_files():
    assert subprocess.run([BASH, '-n', str(SCRIPT)], capture_output=True, timeout=30).returncode == 0
    body = SCRIPT.read_text(encoding='utf-8')
    assert 'trap ' in body and 'SUBMIT_DIR' in body
