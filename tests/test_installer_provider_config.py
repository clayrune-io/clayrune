import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=[
    ('install.sh', r"<<'PYEOF' \|\| true\n(.*?)\nPYEOF"),
    ('install.ps1', r"\$mergeScript = @'\n(.*?)\n'@"),
])
def merge_script(request):
    name, pattern = request.param
    installer = (ROOT / 'installer' / name).read_text(encoding='utf-8')
    script = re.search(pattern, installer, re.DOTALL)
    assert script
    return script[1]


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig'])
def test_provider_merge_preserves_unicode(tmp_path, merge_script, encoding):
    config = tmp_path / 'config.json'
    before = {'project': '\u6771\u4eac', 'port': 5200}
    config.write_text(json.dumps(before, ensure_ascii=False), encoding=encoding)
    result = subprocess.run([sys.executable, '-c', merge_script, str(config), 'claude'],
                            capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(config.read_text(encoding='utf-8')) == {**before, 'default_provider': 'claude'}


def test_provider_merge_preserves_escaped_surrogates(tmp_path, merge_script):
    config = tmp_path / 'config.json'
    config.write_text(r'{"name":"\ud800"}', encoding='utf-8')
    result = subprocess.run([sys.executable, '-c', merge_script, str(config), 'claude'],
                            capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    assert json.loads(config.read_text(encoding='utf-8')) == {
        'name': '\ud800', 'default_provider': 'claude',
    }


@pytest.mark.parametrize('damaged', [b'{"port":', b'\xff', b'[]'])
def test_provider_merge_leaves_unreadable_config_untouched(tmp_path, merge_script, damaged):
    config = tmp_path / 'config.json'
    config.write_bytes(damaged)
    result = subprocess.run([sys.executable, '-c', merge_script, str(config), 'claude'],
                            capture_output=True, check=False)
    assert result.returncode != 0
    assert config.read_bytes() == damaged
