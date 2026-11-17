"""POST /api/mcp/url/preview is gated and uses the hardened clone and scan (MC-1179 pass 11, SF2).

The preview clones a stranger's repository and hands its source to a model. It used to do that
with no gate, through the unhardened branch of `stage_clone` (no hooks/config neutralising), an
npm lockfile generation in the clone, and a tooled `claude -p` scan. Now: an unattended caller is
refused before any clone, the clone carries a `staging_id`, the audit is `read_only`, the scan is
`toolless`, and the panel still gets the same response shape (with a reason where the tool-free
scan or the read-only audit has less to say). Reuses the Desk Connect fixture (`env`).
"""
from __future__ import annotations

import re
import sys

import pytest

sys.path.insert(0, '.')
from tests.test_desk_connect_custom import env  # noqa: E402,F401

URL = 'https://github.com/someone/some-mcp-server'
SHA = 'a' * 40


class Rec:
    """Records every installer call the preview makes."""

    def __init__(self):
        self.calls = []
        self.env = None
        self.scan_result = {'available': True, 'summary': 's', 'network': [], 'filesystem': [],
                            'shell': [], 'secrets': [], 'flags': []}
        self.audit_result = {'kind': 'npm', 'available': True, 'total': 0}


@pytest.fixture()
def rec(env, monkeypatch):
    from mc import mcp_installer as inst
    r = Rec()

    def stage_clone(url, owner, repo, ref=None, **kw):
        r.calls.append(('stage_clone', (url, owner, repo, ref), kw))
        return {'install_dir': str(env.tmp / 'inst'), 'sha': SHA, 'default_branch': 'main'}

    def audit(install_dir, **kw):
        r.calls.append(('dependency_audit', (install_dir,), kw))
        return r.audit_result

    def scan(install_dir, sha, **kw):
        r.calls.append(('security_scan', (install_dir, sha), kw))
        return r.scan_result

    def extract(install_dir, allow_claude_fallback=True):
        r.calls.append(('extract_config', (install_dir,), {'allow_claude_fallback': allow_claude_fallback}))
        return {'servers': {'srv': {'command': 'node', 'args': ['x.js']}}, 'source_tier': 1, 'name_hint': 'srv'}

    monkeypatch.setattr(inst, 'stage_clone', stage_clone)
    monkeypatch.setattr(inst, 'dependency_audit', audit)
    monkeypatch.setattr(inst, 'security_scan', scan)
    monkeypatch.setattr(inst, 'extract_config', extract)
    monkeypatch.setattr(inst, 'fetch_github_signals', lambda o, r_: {'available': False})
    monkeypatch.setattr(inst, 'install_commands', lambda d: [['npm', 'install']])
    r.env = env
    return r


def _agent():
    from mc.state import agent_sessions
    agent_sessions['sched-1'] = {'status': 'running', 'trigger_type': 'scheduled', 'project_id': 'p'}


def _preview(env, url=URL):
    return env.client.post('/api/mcp/url/preview', json={'url': url})


# ── (a) the gate ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize('url', [URL, '@scope/some-package', 'https://example.com/mcp.json'])
def test_preview_by_an_unattended_agent_is_403_and_nothing_is_cloned_or_fetched(rec, url, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('a fetch was made')))
    _agent()
    r = _preview(rec.env, url)
    assert r.status_code == 403 and 'needs a human' in r.get_json()['error']
    assert rec.calls == []


def test_preview_by_a_human_is_not_refused(rec):
    r = _preview(rec.env)
    assert r.status_code == 200 and r.get_json()['kind'] == 'git'


# ── (b) the hardened path ────────────────────────────────────────────────────

def test_preview_clones_with_a_staging_id_and_never_the_bare_branch(rec):
    _preview(rec.env)
    (_, args, kw), = [c for c in rec.calls if c[0] == 'stage_clone']
    assert args[:3] == (URL + '.git', 'someone', 'some-mcp-server')
    assert re.fullmatch(r'[a-f0-9]{32}', kw.get('staging_id') or '')


def test_preview_clones_into_a_fresh_staging_id_each_time(rec):
    _preview(rec.env)
    _preview(rec.env)
    ids = [c[2]['staging_id'] for c in rec.calls if c[0] == 'stage_clone']
    assert len(ids) == 2 and ids[0] != ids[1]


def test_preview_audits_read_only_and_scans_toolless(rec):
    _preview(rec.env)
    (_, _, akw), = [c for c in rec.calls if c[0] == 'dependency_audit']
    (_, sargs, skw), = [c for c in rec.calls if c[0] == 'security_scan']
    assert akw == {'read_only': True}
    assert skw == {'toolless': True}
    assert sargs[1] == SHA


def test_preview_never_reaches_the_tooled_scan(env, monkeypatch):
    """End to end through the real `security_scan`: no `claude -p` process is started."""
    from mc import mcp_installer as inst
    ran = []
    monkeypatch.setattr(inst, '_run', lambda *a, **k: ran.append(a) or (1, '', ''))
    monkeypatch.setattr(inst, 'stage_clone',
                        lambda *a, **k: {'install_dir': str(env.tmp), 'sha': SHA, 'default_branch': 'main'})
    monkeypatch.setattr(inst, 'fetch_github_signals', lambda o, r: {'available': False})
    monkeypatch.setattr(inst, 'extract_config', lambda d, allow_claude_fallback=True:
                        {'servers': {}, 'source_tier': 0, 'name_hint': 'x'})
    monkeypatch.setattr('mc.agent_runtime.run_text_transform',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('no toolless runtime')))
    r = _preview(env)
    body = r.get_json()
    assert r.status_code == 200
    assert body['scan']['available'] is False and 'not been reviewed' in body['scan']['reason']
    assert not [a for a in ran if a and 'claude' in str(a[0][0]).lower()]


# ── the panel still has what it renders ──────────────────────────────────────

PANEL_KEYS = {'kind', 'classified', 'install_dir', 'sha', 'default_branch', 'servers', 'name_hint',
              'source_tier', 'secrets', 'install_commands', 'github', 'audit', 'scan'}


def test_preview_response_keeps_the_panel_shape(rec):
    body = _preview(rec.env).get_json()
    assert PANEL_KEYS <= set(body)
    assert body['scan']['available'] is True and body['scan']['summary'] == 's'
    assert body['servers'] == {'srv': {'command': 'node', 'args': ['x.js']}}


def test_preview_passes_a_reduced_scan_and_audit_through_with_their_reasons(rec):
    rec.scan_result = {'available': False, 'reason': 'Tool-free scan unavailable; this code has not been reviewed.'}
    rec.audit_result = {'available': False, 'reason': 'No existing lockfile; read-only audit does not install or build code.'}
    body = _preview(rec.env).get_json()
    assert body['scan']['available'] is False and 'not been reviewed' in body['scan']['reason']
    assert body['audit']['available'] is False and 'No existing lockfile' in body['audit']['reason']
