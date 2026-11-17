"""tools/mobile-test/lib/dash-pattern.js: the CDP driver must accept the origin it was told to use."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parent.parent / 'tools' / 'mobile-test' / 'lib' / 'dash-pattern.js'

pytestmark = pytest.mark.skipif(not shutil.which('node'), reason='needs node')


def _matches(base, url):
    code = ('const {dashPattern}=require(process.argv[1]);'
            'console.log(JSON.stringify(dashPattern(process.argv[2]||undefined).test(process.argv[3])))')
    done = subprocess.run(['node', '-e', code, str(MODULE), base or '', url],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_default_is_the_live_server_origin():
    assert _matches('', 'http://10.0.2.2:5199/')
    assert not _matches('', 'http://10.0.2.2:5399/')


def test_a_proxy_origin_is_accepted_and_the_live_one_is_not_assumed():
    assert _matches('http://10.0.2.2:5399/', 'http://10.0.2.2:5399/?v=1')
    assert not _matches('http://10.0.2.2:5399/', 'http://10.0.2.2:5199/')
