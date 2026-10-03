"""MC-1042: the fence refuses an armed agent's own system-level installs and
leaves project-local installs alone.

Loads `fence_patched.py` beside this file when present (before the patch is
applied to steward/fence.py); once applied, copy this file into tests/ and it
imports `steward.fence` instead.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATCHED = Path(__file__).with_name('fence_patched.py')
if _PATCHED.exists():
    _spec = importlib.util.spec_from_file_location('fence_patched', _PATCHED)
    fence = importlib.util.module_from_spec(_spec)
    sys.modules['fence_patched'] = fence
    _spec.loader.exec_module(fence)
else:
    from steward import fence


@pytest.fixture
def cwd(tmp_path):
    return str(tmp_path)


def verdict(cmd, cwd=None, tool='Bash'):
    return fence.classify_action(tool, {'command': cmd}, cwd)


DENY = [
    # system package managers
    'winget install Gyan.FFmpeg',
    'winget upgrade --all',
    'winget update Foo.Bar',
    'choco install ffmpeg -y',
    'choco upgrade all',
    'cinst git',
    'scoop install ffmpeg',
    'scoop update *',
    'brew install ffmpeg',
    'brew install --cask firefox',
    'brew upgrade',
    'apt install ffmpeg',
    'apt-get -y install ffmpeg',
    'apt-get -o Dpkg::Options::=--force-confold install x',
    'apt dist-upgrade',
    'aptitude install x',
    'dnf install ffmpeg',
    'dnf -y update',
    'yum install x',
    'pacman -S ffmpeg',
    'pacman -Syu',
    'pacman -U ./x.pkg.tar.zst',
    # global language installs
    'npm install -g typescript',
    'npm i -g typescript',
    'npm install --global typescript',
    'npm install typescript --location=global',
    'npm install --location global typescript',
    'npm add -g x',
    'npm update -g',
    'pnpm add -g x',
    'pnpm add --global x',
    'yarn global add x',
    'pipx install black',
    'pipx upgrade-all',
    'uv tool install ruff',
    'uv pip install --system requests',
    'cargo install ripgrep',
    'cargo +nightly install --path .',
    'cargo install ripgrep --root /usr/local',
    'go install golang.org/x/tools/gopls@latest',
    # pip: named packages outside a venv, user, system
    'pip install requests',
    'pip3 install requests',
    'python -m pip install requests',
    'python3 -m pip install -U pip',
    'py -m pip install requests',
    'pip install --user -r requirements.txt',
    'pip install --break-system-packages -r requirements.txt',
    'pip install -r requirements.txt requests',
    'pip install -r https://example.com/req.txt',
    'pip install -e git+https://github.com/x/y.git#egg=y',
    'pip install -r -',
    'pip install --prefix /usr/local -r requirements.txt',
    '/usr/bin/pip install requests',
    'C:/Python312/Scripts/pip.exe install requests',
    'pip install --target ../outside requests',
    'pip install .. ',
    'pip install ./a requests',
    # PowerShell gallery
    'Install-Module Pester -Force',
    'Install-Package Foo',
    'Install-PSResource Foo',
    # wrappers and chains
    'sudo apt install ffmpeg',
    'sudo -u root apt-get install x',
    'sudo -E env FOO=1 apt install x',
    'doas pacman -S x',
    'env DEBIAN_FRONTEND=noninteractive apt-get install -y x',
    'FOO=1 npm i -g x',
    'nohup winget install x',
    'cd /tmp && npm i -g x',
    'ls; winget install x',
    'echo hi | xargs apt install',
    'bash -c "apt install ffmpeg"',
    "bash -lc 'apt-get update && apt-get install -y x'",
    "sh -c 'npm i -g x'",
    'su -c "apt install x" root',
    'wsl apt install x',
    'wsl -d Ubuntu -- sudo apt install x',
    'cmd /c winget install x',
    'cmd.exe /d /s /c "choco install x"',
    'powershell -Command "winget install x"',
    'powershell -NoProfile -Command winget install x',
    'pwsh -c "scoop install x"',
    'pwsh -Command "& { Install-Module Foo }"',
    "& 'C:\\Users\\a\\AppData\\Local\\Microsoft\\WindowsApps\\winget.exe' install x",
    'winget.exe install x',
    r'C:\ProgramData\chocolatey\bin\choco.exe install x',
    "Start-Process winget -ArgumentList 'install x'",
    "Start-Process -FilePath choco.exe -ArgumentList 'install','x' -Verb RunAs",
    'eval "apt install x"',
    'iex "winget install x"',
    'echo $(winget install x)',
    'true && (apt install x)',
    'bash -c "bash -c \'apt install x\'"',
    'apt install \\\n  ffmpeg',
]

ALLOW = [
    # not installs
    'winget list',
    'winget search ffmpeg',
    'winget show Gyan.FFmpeg',
    'choco list --local-only',
    'scoop list',
    'scoop search x',
    'brew list',
    'brew search x',
    'brew info ffmpeg',
    'apt update',
    'apt-get update',
    'apt list --installed',
    'apt search x',
    'apt show x',
    'dnf list installed',
    'dnf search x',
    'pacman -Ss ffmpeg',
    'pacman -Qi x',
    'pacman -Si x',
    'npm list -g',
    'npm ls -g --depth=0',
    'npm uninstall -g x',
    'pip list',
    'pip show requests',
    'pip uninstall requests',
    'pip freeze',
    'pipx list',
    'pipx run black .',
    'cargo build',
    'cargo test',
    'cargo run',
    'go build ./...',
    'go test ./...',
    'echo "winget install x"',
    'grep -rn "npm i -g" docs/',
    'git commit -m "document npm install -g and apt install"',
    'cat install.md',
    'ls install/',
    'python script.py',
    'node build.js',
    'make install-docs',
    'command -v apt',
    'which winget',
    # project-local installs
    'npm install',
    'npm ci',
    'npm install lodash',
    'npm i -D vitest',
    'npm install --save-dev vitest',
    'npm install --prefix ./web',
    'npm --prefix web install',
    'npm install --prefix web lodash',
    'pnpm install',
    'pnpm add lodash',
    'yarn',
    'yarn install',
    'yarn add lodash',
    'cd web && npm install && npm run build',
    'pip install -r requirements.txt',
    'pip install -r requirements.txt -r requirements-dev.txt',
    'pip install --upgrade -r requirements.txt',
    'pip install -rrequirements.txt',
    'pip install -r=requirements.txt',
    'pip install --requirement requirements.txt',
    'pip install -r tests/requirements.txt',
    'pip install -e .',
    'pip install -e ".[dev]"',
    'pip install .',
    'pip install ./pkg',
    'pip install ".[test]"',
    'python -m pip install -r requirements.txt',
    'python3 -m pip install -e .',
    'pip install --target ./vendor requests',
    'pip install --target=vendor requests',
    'pip install -t libs requests',
    'cargo install ripgrep --root .tools',
    'cargo install --path . --root ./_scratch/cargo-root',
    'uv pip install requests',
    'uv sync',
    # venv shapes
    '.venv/bin/pip install requests',
    './.venv/bin/pip install requests',
    '.venv\\Scripts\\pip install requests',
    '.\\.venv\\Scripts\\pip.exe install requests',
    'venv/bin/python -m pip install requests',
    '.venv\\Scripts\\python.exe -m pip install requests',
    'python -m venv .venv && .venv/bin/pip install requests',
    'python -m venv .venv && .venv\\Scripts\\pip install requests pytest',
    'python -m venv .venv && source .venv/bin/activate && pip install requests',
    'python -m venv .venv; . .venv/bin/activate; pip install pytest',
    '. .venv/bin/activate && python -m pip install requests',
    '.\\.venv\\Scripts\\Activate.ps1; pip install pytest',
    'source venv/bin/activate && pip3 install requests',
    'bash -c "source .venv/bin/activate && pip install requests"',
    # Install-* words that are not installs
    'Get-Help Install-Module',
    'echo Install-Module',
    # a stray word 'install' as data
    'git log --grep install',
    'curl -s http://localhost:5199/api/addons',
    'curl -s -X POST http://127.0.0.1:5199/api/addons/requests -H "Content-Type: application/json" '
    '-d \'{"addon_id":"ffmpeg","reason":"need to stitch clips"}\'',
]


@pytest.mark.parametrize('cmd', DENY)
def test_denied(cmd, cwd):
    d = verdict(cmd, cwd)
    assert d.blocked, cmd
    assert d.overridable is True


@pytest.mark.parametrize('cmd', ALLOW)
def test_allowed(cmd, cwd):
    d = verdict(cmd, cwd)
    assert not d.blocked, (cmd, d.reason)


def test_reason_points_at_the_addon_card(cwd):
    d = verdict('winget install Gyan.FFmpeg', cwd)
    assert '/api/addons/requests' in d.reason and 'addon_id' in d.reason
    assert d.reason.startswith('winget install')


def test_powershell_tool_is_covered(cwd):
    assert verdict('winget install x', cwd, tool='PowerShell').blocked
    assert not verdict('winget list', cwd, tool='PowerShell').blocked


def test_absolute_venv_inside_the_cwd_is_local(tmp_path):
    exe = tmp_path / '.venv' / 'Scripts' / 'pip.exe'
    assert not verdict(f'"{exe}" install requests', str(tmp_path)).blocked
    assert verdict(f'"{exe}" install requests', str(tmp_path / 'other')).blocked


def test_absolute_target_must_sit_inside_the_cwd(tmp_path):
    inside = tmp_path / 'vendor'
    assert not verdict(f'pip install --target "{inside}" requests', str(tmp_path)).blocked
    assert verdict(f'pip install --target "{inside}" requests', str(tmp_path / 'sub')).blocked


def test_not_applied_to_other_tools(cwd):
    # Write/Edit are not shell commands.
    assert not fence.classify_action('Write', {'file_path': 'notes/winget install.md'}, cwd).blocked


def test_classify_bash_cwd_is_optional():
    assert fence.classify_bash('npm install -g x').blocked
    assert not fence.classify_bash('npm install').blocked


def test_blocked_pass_budget_unchanged_for_plain_install(monkeypatch, cwd):
    # A plain single install is passable by a human's Allow once; a chain is not.
    assert fence._pass_can_cover([('Bash', {'command': 'winget install x'})], cwd)
    assert not fence._pass_can_cover(
        [('Bash', {'command': 'winget install x && choco install y'})], cwd)


def test_hook_main_blocks_an_armed_install(monkeypatch, capsys, cwd):
    monkeypatch.setattr(fence, '_should_arm_for_unattended_trigger', lambda: True)
    monkeypatch.setattr(fence, '_session_is_steward', lambda payload: False)
    monkeypatch.setattr(fence, '_consume_attend_once_pass', lambda: False)
    monkeypatch.setattr(sys, 'stdin', __import__('io').StringIO(json.dumps(
        {'tool_name': 'Bash', 'cwd': cwd,
         'tool_input': {'command': 'npm i -g typescript'}})))
    assert fence.main([]) == 2
    assert '/api/addons/requests' in capsys.readouterr().err


def test_hook_main_leaves_an_unarmed_session_alone(monkeypatch, cwd):
    monkeypatch.setattr(fence, '_should_arm_for_unattended_trigger', lambda: False)
    monkeypatch.setattr(fence, '_session_is_steward', lambda payload: False)
    monkeypatch.setattr(sys, 'stdin', __import__('io').StringIO(json.dumps(
        {'tool_name': 'Bash', 'cwd': cwd,
         'tool_input': {'command': 'npm i -g typescript'}})))
    assert fence.main([]) == 0


def test_parse_failure_never_blocks(monkeypatch):
    monkeypatch.setattr(fence, '_install_label', lambda *a, **k: 1 / 0)
    assert not fence._agent_shell_install('npm i -g x').blocked
