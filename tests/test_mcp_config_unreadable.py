"""A present-but-unreadable MCP config file is refused, never treated as empty (MC-1179 pass 11, SF1).

`~/.claude.json` and a project's `.mcp.json` used to be read through a helper that returned `{}` for
"cannot read" as well as "absent", so the next write replaced the file with only the new server (and
for `~/.claude.json` erased the rest of Claude Code's state). These tests use temp files only: the
`env` fixture (Desk Connect) patches `mc.mcp.GLOBAL_CLAUDE_JSON` to a path under `tmp_path`, and the
project file lives in `tmp_path/proj`. The real `~/.claude.json` is never opened.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, '.')
from tests.test_desk_connect_custom import PASSCODE, PID, env  # noqa: E402,F401

CFG = {'command': 'node', 'args': ['x.js']}
OTHER = {'oauthAccount': {'emailAddress': 'someone@example.invalid'}, 'numStartups': 7}
HEALTHY = {**OTHER, 'mcpServers': {'old': {'command': 'node', 'args': ['old.js']}}}


def _put(path: Path, text: str | bytes):
    path.write_bytes(text if isinstance(text, bytes) else text.encode('utf-8'))
    return path.read_bytes()


def _bom(doc) -> bytes:
    return b'\xef\xbb\xbf' + json.dumps(doc, indent=2).encode('utf-8')


def _half(doc) -> bytes:
    raw = json.dumps(doc, indent=2).encode('utf-8')
    return raw[: len(raw) // 2]


def _leftovers(path: Path):
    return sorted(p.name for p in path.parent.iterdir() if p.name.startswith('.mcp-'))


# ── the three measured cases, global scope ───────────────────────────────────

def test_a_healthy_global_file_keeps_its_keys_and_gains_the_server(env):
    from mc import mcp
    _put(env.glob_cfg, json.dumps(HEALTHY))
    mcp.write_server('new', 'stdio', CFG, 'global')
    doc = json.loads(env.glob_cfg.read_text(encoding='utf-8'))
    assert doc['oauthAccount'] == OTHER['oauthAccount'] and doc['numStartups'] == 7
    assert set(doc['mcpServers']) == {'old', 'new'}


def test_a_half_cut_global_file_is_refused_and_left_byte_identical(env):
    from mc import mcp
    from mc.mcp_config_file import McpConfigUnreadable
    before = _put(env.glob_cfg, _half(HEALTHY))
    with pytest.raises(McpConfigUnreadable) as e:
        mcp.write_server('new', 'stdio', CFG, 'global')
    assert env.glob_cfg.name in str(e.value)
    assert env.glob_cfg.read_bytes() == before
    assert _leftovers(env.glob_cfg) == []


def test_a_bom_global_file_keeps_its_server_and_other_key_and_gains_the_new_one(env):
    from mc import mcp
    doc = {'oauthAccount': OTHER['oauthAccount'], 'mcpServers': {'old': CFG}}
    _put(env.glob_cfg, _bom(doc))
    mcp.write_server('new', 'stdio', CFG, 'global')
    out = json.loads(env.glob_cfg.read_text(encoding='utf-8'))
    assert out['oauthAccount'] == OTHER['oauthAccount']
    assert set(out['mcpServers']) == {'old', 'new'}


# ── the same cases, project scope ────────────────────────────────────────────

def test_a_bom_project_file_keeps_its_server_and_other_key_and_gains_the_new_one(env):
    from mc import mcp
    _put(env.proj_cfg, _bom({'note': 'keep', 'mcpServers': {'old': CFG}}))
    mcp.write_server('new', 'stdio', CFG, 'project', project_path=str(env.proj_dir), project_id=PID)
    out = json.loads(env.proj_cfg.read_text(encoding='utf-8'))
    assert out['note'] == 'keep' and set(out['mcpServers']) == {'old', 'new'}


def test_a_half_cut_project_file_is_refused_and_left_byte_identical(env):
    from mc import mcp
    from mc.mcp_config_file import McpConfigUnreadable
    before = _put(env.proj_cfg, _half({'mcpServers': {'old': CFG, 'two': CFG}}))
    with pytest.raises(McpConfigUnreadable):
        mcp.write_server('new', 'stdio', CFG, 'project', project_path=str(env.proj_dir), project_id=PID)
    assert env.proj_cfg.read_bytes() == before
    assert _leftovers(env.proj_cfg) == []


@pytest.mark.parametrize('content', [b'[1, 2]', b'"text"', b'null', b'\xff\xfe\x00bad'])
def test_a_file_that_is_not_a_json_object_is_refused_and_unchanged(env, content):
    from mc import mcp
    from mc.mcp_config_file import McpConfigUnreadable
    before = _put(env.glob_cfg, content)
    with pytest.raises(McpConfigUnreadable):
        mcp.write_server('new', 'stdio', CFG, 'global')
    assert env.glob_cfg.read_bytes() == before


def test_a_read_error_such_as_a_sharing_violation_is_refused_and_unchanged(env, monkeypatch):
    from mc import mcp
    from mc.mcp_config_file import McpConfigUnreadable
    before = _put(env.glob_cfg, json.dumps(HEALTHY))
    real = Path.read_text

    def locked(self, *a, **k):
        if self == env.glob_cfg:
            raise PermissionError(13, 'The process cannot access the file because it is being used by another process')
        return real(self, *a, **k)
    monkeypatch.setattr(Path, 'read_text', locked)
    with pytest.raises(McpConfigUnreadable):
        mcp.write_server('new', 'stdio', CFG, 'global')
    monkeypatch.setattr(Path, 'read_text', real)
    assert env.glob_cfg.read_bytes() == before


@pytest.mark.parametrize('content', [None, b'', b'  \n', b'\xef\xbb\xbf'])
def test_an_absent_or_empty_file_is_still_empty_and_the_write_creates_it(env, content):
    from mc import mcp
    if content is not None:
        _put(env.glob_cfg, content)
    mcp.write_server('new', 'stdio', CFG, 'global')
    assert set(json.loads(env.glob_cfg.read_text(encoding='utf-8'))['mcpServers']) == {'new'}


def test_delete_on_an_unreadable_file_is_refused_not_reported_as_not_found(env):
    from mc import mcp
    from mc.mcp_config_file import McpConfigUnreadable
    before = _put(env.glob_cfg, _half(HEALTHY))
    with pytest.raises(McpConfigUnreadable):
        mcp.delete_server('global', 'old')
    assert env.glob_cfg.read_bytes() == before


def test_overwrite_false_on_an_unreadable_file_is_refused_too(env):
    from mc import mcp
    from mc.mcp_config_file import McpConfigUnreadable
    before = _put(env.glob_cfg, _half(HEALTHY))
    with pytest.raises(McpConfigUnreadable):
        mcp.write_server('new', 'stdio', CFG, 'global', overwrite=False)
    assert env.glob_cfg.read_bytes() == before


# ── read paths: an empty answer, logged once ─────────────────────────────────

def test_listing_a_bom_file_shows_its_servers(env):
    from mc import mcp
    _put(env.glob_cfg, _bom(HEALTHY))
    assert [r['name'] for r in mcp.list_servers()] == ['old']


def test_listing_an_unreadable_file_is_empty_and_logs_the_reason_once(env, monkeypatch):
    from mc import mcp, mcp_config_file
    import mc.core
    lines = []
    monkeypatch.setattr(mc.core, '_log', lambda msg, **k: lines.append(msg))
    mcp_config_file._logged.clear()
    before = _put(env.glob_cfg, _half(HEALTHY))
    assert mcp.list_servers() == [] and mcp.list_servers() == []
    assert len(lines) == 1 and env.glob_cfg.name in lines[0] and 'not valid JSON' in lines[0]
    assert env.glob_cfg.read_bytes() == before
    _put(env.glob_cfg, json.dumps(HEALTHY))                      # reads cleanly: the next failure logs again
    assert len(mcp.list_servers()) == 1
    _put(env.glob_cfg, _half(HEALTHY))
    mcp.list_servers()
    assert len(lines) == 2


# ── boot-time built-in install ───────────────────────────────────────────────

def _builtin_root(tmp: Path, scope: str) -> Path:
    root = tmp / 'builtin'
    root.mkdir()
    (root / 'fs.json').write_text(json.dumps({
        'name': 'fs-builtin', 'scope': scope, 'transport': 'stdio',
        'config': {'command': 'node', 'args': ['fs.js']}}), encoding='utf-8')
    return root


def test_the_boot_install_does_not_overwrite_an_unreadable_global_file(env):
    from mc import mcp
    root = _builtin_root(env.tmp, 'global')
    marker_dir = env.tmp / 'markers'
    marker_dir.mkdir()
    before = _put(env.glob_cfg, _half(HEALTHY))
    result = mcp.install_global_builtins(root, marker_dir)          # must not raise
    assert env.glob_cfg.read_bytes() == before
    assert result['installed'] == [] and result['error'] == ['fs-builtin']
    assert list(marker_dir.iterdir()) == []                          # no marker claims it installed


def test_the_boot_install_still_installs_into_a_bom_global_file(env):
    from mc import mcp
    root = _builtin_root(env.tmp, 'global')
    marker_dir = env.tmp / 'markers'
    marker_dir.mkdir()
    _put(env.glob_cfg, _bom(HEALTHY))
    result = mcp.install_global_builtins(root, marker_dir)
    out = json.loads(env.glob_cfg.read_text(encoding='utf-8'))
    assert result['installed'] == ['fs-builtin']
    assert set(out['mcpServers']) == {'old', 'fs-builtin'} and out['numStartups'] == 7


def test_the_boot_install_does_not_overwrite_an_unreadable_project_file(env):
    from mc import mcp
    root = _builtin_root(env.tmp, 'project')
    before = _put(env.proj_cfg, _half({'mcpServers': {'old': CFG, 'two': CFG}}))
    result = mcp.install_project_builtins(root, str(env.proj_dir))   # must not raise
    assert env.proj_cfg.read_bytes() == before
    assert result['installed'] == [] and result['error'] == ['fs-builtin']
    assert not (env.proj_dir / '.clayrune_builtin_mcps.json').exists()


# ── routes: 409 naming the file ──────────────────────────────────────────────

def _post(env, **over):
    body = {'name': 'new', 'transport': 'stdio', 'scope': 'global', 'config': CFG, 'passcode': PASSCODE}
    body.update(over)
    return env.client.post('/api/mcp', json=body)


def test_post_on_an_unreadable_global_file_is_409_and_names_the_file(env):
    before = _put(env.glob_cfg, _half(HEALTHY))
    r = _post(env)
    assert r.status_code == 409
    assert env.glob_cfg.name in r.get_json()['error'] and 'left unchanged' in r.get_json()['error']
    assert env.glob_cfg.read_bytes() == before


def test_post_on_an_unreadable_project_file_is_409_and_names_the_file(env):
    before = _put(env.proj_cfg, _half({'mcpServers': {'old': CFG, 'two': CFG}}))
    r = _post(env, scope='project', project_id=PID)
    assert r.status_code == 409 and '.mcp.json' in r.get_json()['error']
    assert env.proj_cfg.read_bytes() == before


def test_put_and_delete_on_an_unreadable_file_are_409_and_change_nothing(env):
    before = _put(env.glob_cfg, _half(HEALTHY))
    put = env.client.put('/api/mcp/global/old', json={
        'transport': 'stdio', 'config': {'command': 'node', 'args': ['y.js']}, 'passcode': PASSCODE})
    dele = env.client.delete('/api/mcp/global/old')
    assert (put.status_code, dele.status_code) == (409, 409)
    assert env.glob_cfg.read_bytes() == before


def test_url_install_on_an_unreadable_file_reports_it_and_leaves_the_file(env):
    before = _put(env.glob_cfg, _half(HEALTHY))
    r = env.client.post('/api/mcp/url/install', json={
        'name': 'inst', 'scope': 'global', 'config': CFG, 'passcode': PASSCODE})
    text = r.get_data(as_text=True)
    assert '"type": "error"' in text and env.glob_cfg.name in text and '"type": "done"' not in text
    assert env.glob_cfg.read_bytes() == before


def test_post_on_a_bom_file_succeeds_and_keeps_everything(env):
    _put(env.glob_cfg, _bom(HEALTHY))
    assert _post(env).status_code == 201
    out = json.loads(env.glob_cfg.read_text(encoding='utf-8'))
    assert out['oauthAccount'] == OTHER['oauthAccount'] and set(out['mcpServers']) == {'old', 'new'}
