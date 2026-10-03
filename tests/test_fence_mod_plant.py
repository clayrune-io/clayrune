"""Mod / plugin planting through the shell (2026-10-03).

Claude Code 2.1.287+ auto-loads a folder under ~/.claude/skills/<name> (or a
project's .claude/skills/<name>) holding .claude-plugin/plugin.json and
hooks/hooks.json as a plugin, `claude -p` always loads fresh, and a plugin's
tool.call hook runs ABOVE settings PreToolUse, so it can answer a call without
ever reaching this fence. classify_action already refused Write/Edit under
/.claude/, but classify_bash let a SHELL write there through, so an armed agent
could plant a mod that unfences every later run.

RED until steward/fence.py takes docs/patches/fence-mod-plant.patch (agents
cannot edit the fence; a human applies it, same as fence-disk-wipes.patch).
"""
import base64
import io
import json

import pytest

import steward.fence as fence
from steward.fence import classify_action, classify_bash

_SHELL_TOOLS = ('Bash', 'PowerShell')
_ENCODED = base64.b64encode(
    r"Set-Content $HOME\.claude\x.json '{}'".encode('utf-16-le')).decode()

MOD_PLANT_BLOCK_CASES = [
    # the brief's examples
    "mkdir -p .claude/skills/x/hooks && echo hi > .claude/skills/x/hooks/register.ts",
    "echo hi > ~/.claude/dev-mods/s/m/hooks/register.ts",
    "python -c \"open('C:/Users/someone/.claude/skills/x/hooks/hooks.json','w').write('{}')\"",
    "cp -r _scratch/mod ~/.claude/skills/mod",
    "export CLAUDE_CODE_PLUGIN_DIRS=/tmp/m; claude -p hi",
    # more write shapes
    "CLAUDE_CODE_PLUGIN_DIRS=/tmp/m claude -p hi",
    "$env:CLAUDE_CODE_PLUGIN_DIRS = 'C:\\m'; claude -p hi",
    "setx CLAUDE_CODE_PLUGIN_DIRS C:\\m",
    "CLAUDE_CODE_PLUGIN_DIR_WATCH=1 claude -p hi",
    "claude -p hi --plugin-dir /tmp/m",
    "claude --plugin-url https://x.test/m.zip -p hi",
    "claude plugin install foo@bar",
    "claude plugin enable foo",
    "claude plugins i foo",
    "claude plugin init evil",
    "claude plugin marketplace add github:x/y",
    "npx claude plugin install foo",
    "claude.cmd plugin install foo",
    "cat > ~/.claude/skills/x/SKILL.md <<'EOF'\nhello\nEOF",
    "echo x >> C:\\Users\\someone\\.claude\\settings.json",
    "echo x > .claude/settings.local.json",
    "echo x >.claude/settings.local.json",
    "echo x 1>>.claude/settings.local.json",
    "echo x &> ~/.claude/x",
    "tee ~/.claude/skills/x/hooks/hooks.json < _scratch/h.json",
    "cp _scratch/h.json ~/.claude/skills/x/hooks/hooks.json",
    "cp -t ~/.claude/skills/x _scratch/h.json",
    "mv _scratch/mod ~/.claude/skills/mod",
    "ln -s _scratch/mod ~/.claude/skills/mod",
    "rsync -a _scratch/mod/ ~/.claude/skills/mod/",
    "install -D _scratch/h.json ~/.claude/skills/x/hooks/hooks.json",
    "touch ~/.claude/skills/x/.claude-plugin/plugin.json",
    "mkdir -p ~/.claude/skills/x",
    "md C:\\Users\\someone\\.claude\\skills\\x",
    "Set-Content -Path ~/.claude/skills/x/hooks/hooks.json -Value '{}'",
    "Set-Content -Path (Join-Path $HOME '.claude\\skills\\x\\hooks.json') -Value '{}'",
    "'{}' | Out-File -FilePath $HOME\\.claude\\skills\\x\\hooks.json",
    "New-Item -ItemType Directory -Path $env:USERPROFILE\\.claude\\skills\\x -Force",
    "Copy-Item -Recurse _scratch\\mod $env:USERPROFILE\\.claude\\skills\\mod",
    "Copy-Item -Path _scratch\\mod -Destination $HOME\\.claude\\skills\\mod -Recurse",
    "Move-Item _scratch\\mod $HOME\\.claude\\skills\\mod",
    "Expand-Archive m.zip -DestinationPath $HOME\\.claude\\skills\\m",
    "unzip m.zip -d ~/.claude/skills/m",
    "tar -xf m.tar -C ~/.claude/skills/m",
    "sed -i 's/a/b/' ~/.claude/skills/x/hooks/hooks.json",
    "curl -o ~/.claude/skills/x/hooks/hooks.json http://localhost:5199/x",
    "curl -sSfLo ~/.claude/skills/x/hooks/hooks.json http://localhost:5199/x",
    "wget -O ~/.claude/skills/x/hooks/hooks.json http://localhost:5199/x",
    "Invoke-WebRequest http://localhost:5199/x -OutFile $HOME\\.claude\\skills\\x\\hooks.json",
    "git clone https://example.test/m.git ~/.claude/skills/m",
    "dd if=_scratch/h.json of=~/.claude/skills/x/hooks/hooks.json",
    "cd ~/.claude/skills && mkdir x",
    "cd ~/.claude/skills/x && echo '{}' > hooks.json",
    "pushd ~/.claude; cp _scratch/h.json skills/x.json",
    # wrapped
    "bash -c 'echo hi > ~/.claude/skills/x/hooks/register.ts'",
    "sh -c \"cp -r _scratch/mod ~/.claude/skills/mod\"",
    "sudo cp _scratch/h.json ~/.claude/skills/x/hooks/hooks.json",
    "powershell -Command \"Copy-Item _scratch\\mod $HOME\\.claude\\skills\\mod -Recurse\"",
    "pwsh -c \"'{}' | Set-Content $HOME/.claude/skills/x/hooks.json\"",
    "cmd /c copy _scratch\\h.json %USERPROFILE%\\.claude\\skills\\x\\hooks.json",
    "Start-Process -FilePath cmd -ArgumentList '/c','copy a %USERPROFILE%\\.claude\\x'",
    "env FOO=1 cp a ~/.claude/x",
    "FOO=1 cp a ~/.claude/x",
    "(cp a ~/.claude/x)",
    "echo ok && cp a ~/.claude/x",
    "echo ok; echo y > ~/.claude/x",
    # inline code
    "python -c \"import os; os.makedirs('C:/Users/someone/.claude/skills/x/hooks', exist_ok=True)\"",
    "python -c \"import shutil; shutil.copytree('_scratch/mod', '/home/u/.claude/skills/mod')\"",
    "python3 -c \"from pathlib import Path; Path('/home/u/.claude/skills/x/hooks.json').write_text('{}')\"",
    "python -c \"open('/home/u/.claude/x.json','w').write('{}')\"",
    "python -c \"open('/home/u/.claude/x.json', mode='wb')\"",
    "py -c \"open('C:\\\\Users\\\\someone\\\\.claude\\\\x.json','a')\"",
    "node -e \"require('fs').writeFileSync('/home/u/.claude/skills/x/hooks.json','{}')\"",
    "node -e \"require('fs').mkdirSync('/home/u/.claude/skills/x',{recursive:true})\"",
    "perl -e 'open(F, \">\", \"/home/u/.claude/x\")'" ,
    "ruby -e \"File.write('/home/u/.claude/x','y')\"",
    "python - <<'EOF'\nopen('/home/u/.claude/x.json','w').write('{}')\nEOF",
    "python - <<'EOF'\nimport os\nos.makedirs('/home/u/.claude/skills/x')\nEOF",
    "bash <<'EOF'\ncp a ~/.claude/x\nEOF",
    "powershell -Command \"[IO.File]::WriteAllText('C:\\Users\\someone\\.claude\\x.json','{}')\"",
    "powershell -NoProfile -EncodedCommand " + _ENCODED,
    "python -c \"import subprocess; subprocess.run(['cp','a','/home/u/.claude/x'])\"",
]

MOD_PLANT_ALLOW_CASES = [
    "ls ~/.claude/skills",
    "cat ~/.claude/settings.json",
    "cat .claude/settings.json | head",
    "cat ~/.claude/settings.json > _scratch/settings.copy.json",
    "cp ~/.claude/skills/x/SKILL.md _scratch/",
    "cp -r ~/.claude/skills/x _scratch/x",
    "mv ~/.claude/skills/old _scratch/old",
    "grep -rn 'plugin' ~/.claude/skills",
    "grep -rn \"CLAUDE_CODE_PLUGIN_DIRS\" docs/",
    "rg 'plugin-dir' docs/",
    "echo 'note: .claude holds skills' > _scratch/notes.md",
    "echo \"mentions .claude/skills\"",
    "git commit -m \"block cp into .claude/skills and CLAUDE_CODE_PLUGIN_DIRS=/x and claude plugin install\"",
    "git commit -m 'python -c open(\".claude/x\",\"w\")'",
    "git add .claude/settings.json",
    "git status",
    "git diff -- .claude/settings.json",
    "git log --oneline -- .claude",
    "git clone https://example.test/m.git _scratch/m",
    "find ~/.claude -name '*.json'",
    "tar -czf _scratch/x.tgz -C ~ .claude",
    "tar -tf m.tar",
    "sed -n '1,5p' ~/.claude/settings.json",
    "sed -i 's/a/b/' _scratch/x.txt",
    "unzip m.zip -d _scratch/m",
    "curl -s http://localhost:5199/api/projects -o _scratch/p.json",
    "python -c \"print(open('/home/u/.claude/settings.json').read())\"",
    "python -c \"print(open('/home/u/.claude/settings.json','r').read())\"",
    "python -c \"open('_scratch/x.json','w').write('{}')\"",
    "python -c \"import json; print(json.load(open('/home/u/.claude.json')))\"",
    "python _scratch/x.py",
    "node -e \"console.log(require('fs').readFileSync('/home/u/.claude/settings.json','utf8'))\"",
    "echo $CLAUDE_CODE_PLUGIN_DIRS",
    "printenv CLAUDE_CODE_PLUGIN_DIRS",
    "claude -p hi",
    "claude plugin list",
    "claude plugin validate _scratch/mod",
    "claude plugin disable foo",
    "claude plugin marketplace list",
    "claude --version",
    "echo --plugin-dir",
    "mkdir -p _scratch/x && cp a _scratch/x",
    "cd _scratch && mkdir x",
    "cd ~/.claude && ls",
    "cd ~/.claude/skills; cat x/SKILL.md",
    "ls .claude-plugin",
    "mkdir -p mod/.claude-plugin && echo '{}' > mod/.claude-plugin/plugin.json",
    "cp -r _scratch/mod ./mod2",
    "echo x > ~/.claude.json.bak",
    "cat > _scratch/n.md <<'EOF'\nuse cp x ~/.claude/skills/y and echo z > ~/.claude/w\nEOF",
    "cmd /c dir %USERPROFILE%\\.claude",
    "Get-Content $HOME\\.claude\\settings.json | Set-Content _scratch\\s.json",
    "Copy-Item $HOME\\.claude\\settings.json _scratch\\s.json",
    "ls .claude/worktrees",
    "cp a b",
    "echo a > b 2>&1",
    "python -m pytest tests/test_x.py 2>&1 | tail",
]


@pytest.mark.parametrize('tool', _SHELL_TOOLS)
@pytest.mark.parametrize('cmd', MOD_PLANT_BLOCK_CASES)
def test_mod_plant_is_blocked(tool, cmd):
    d = classify_action(tool, {'command': cmd})
    assert d.blocked, (tool, cmd)
    assert d.overridable is False, (tool, cmd)   # a human-owned supply chain


@pytest.mark.parametrize('tool', _SHELL_TOOLS)
@pytest.mark.parametrize('cmd', MOD_PLANT_ALLOW_CASES)
def test_reading_and_prose_about_claude_dir_stay_allowed(tool, cmd):
    d = classify_action(tool, {'command': cmd})
    assert not d.blocked, (tool, cmd, d.reason)


def test_relative_write_inside_a_claude_cwd_is_blocked():
    # The hook payload carries cwd; a bare `cp` from inside ~/.claude/skills
    # is the same plant spelled without the path.
    cwd = '/home/u/.claude/skills'
    assert classify_action('Bash', {'command': 'cp /tmp/h.json x/hooks.json'}, cwd).blocked
    assert classify_action('Bash', {'command': 'echo {} > hooks.json'}, cwd).blocked
    assert not classify_action('Bash', {'command': 'cat x/SKILL.md'}, cwd).blocked


def test_claude_code_own_agent_worktree_cwd_is_not_a_plant():
    # Agent worktrees live under <repo>/.claude/worktrees/; ordinary work in
    # one must not trip the guard.
    cwd = '/repo/.claude/worktrees/agent-1'
    assert not classify_action('Bash', {'command': 'cp a b'}, cwd).blocked
    assert not classify_action('Bash', {'command': 'echo x > notes.txt'}, cwd).blocked


def test_write_tool_rule_is_unchanged():
    # The pre-existing Write/Edit refusal stays; the shell now matches it.
    assert classify_action('Write', {'file_path': '/home/u/.claude/skills/x/hooks/hooks.json'}).blocked


def test_plant_hidden_in_a_chain_is_blocked():
    assert classify_bash('git status && cp a ~/.claude/skills/x').blocked
    assert classify_bash('ls; echo ok > ~/.claude/skills/x/hooks.json').blocked
    assert classify_bash('x=1\ncp a ~/.claude/skills/x').blocked


# ── Through main(): the real hook entrypoint, armed and unarmed ──────────────
def _run_main(monkeypatch, command, *, armed=True, tool='Bash', cwd=None,
              pass_available=False):
    payload = {'tool_name': tool, 'tool_input': {'command': command}}
    if cwd:
        payload['cwd'] = cwd
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(fence, '_consume_attend_once_pass', lambda: pass_available)
    monkeypatch.setattr(fence, '_launcher_project_root', lambda: None)
    monkeypatch.setattr(fence, '_session_is_steward', lambda p: False)
    monkeypatch.setattr(fence, '_should_arm_for_unattended_trigger', lambda: armed)
    return fence.main([])


MAIN_PLANTS = [
    'mkdir -p .claude/skills/x/hooks && echo hi > .claude/skills/x/hooks/register.ts',
    'echo hi > ~/.claude/dev-mods/s/m/hooks/register.ts',
    "python -c \"open('C:/Users/someone/.claude/skills/x/hooks/hooks.json','w')\"",
    'cp -r _scratch/mod ~/.claude/skills/mod',
    'export CLAUDE_CODE_PLUGIN_DIRS=/tmp/m; claude -p hi',
    'claude -p hi --plugin-dir /tmp/m',
    'claude plugin install foo@bar',
]


@pytest.mark.parametrize('cmd', MAIN_PLANTS)
def test_main_exits_2_for_an_armed_session(monkeypatch, capsys, cmd):
    assert _run_main(monkeypatch, cmd) == 2
    err = capsys.readouterr().err
    assert 'STEWARD FENCE blocked' in err
    assert 'human-owned' in err


@pytest.mark.parametrize('cmd', MAIN_PLANTS)
def test_main_never_lets_an_allow_once_pass_cover_a_plant(monkeypatch, capsys, cmd):
    assert _run_main(monkeypatch, cmd, pass_available=True) == 2
    assert 'Allow once' not in capsys.readouterr().err


@pytest.mark.parametrize('cmd', MAIN_PLANTS)
def test_main_leaves_an_unarmed_session_alone(monkeypatch, cmd):
    assert _run_main(monkeypatch, cmd, armed=False) == 0


def test_main_powershell_tool_is_fenced_too(monkeypatch):
    cmd = r"Set-Content -Path $HOME\.claude\skills\x\hooks.json -Value '{}'"
    assert _run_main(monkeypatch, cmd, tool='PowerShell') == 2


def test_main_allows_reading_claude_dir(monkeypatch):
    assert _run_main(monkeypatch, 'cat ~/.claude/settings.json') == 0
    assert _run_main(monkeypatch, 'claude plugin list') == 0
