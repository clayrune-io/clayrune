"""`POST /api/distiller/reject` must refuse an unattended caller.

MC-1179 pass 03, ML11. Rail 3 makes a rejection durable, and global for a
cross-project artifact. The route called `reject_proposed` for any caller, so
an unattended session could empty the human's review queue and permanently
suppress patterns the human never saw. It is the mirror image of the promote
route and now carries the same `is_unattended_caller()` refusal with the same
status code. No passcode: the decision is reversible by editing the sidecar.

Asserts the EFFECT: after a refused reject the proposal still sits in
`_proposed/`, nothing is in `_rejected/`, and no suppression was written.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mc import distiller  # noqa: E402
from mc.state import agent_sessions  # noqa: E402

EXACT = 'deadbeefdeadbeef'


def _make_artifact(skills_root: Path, scope_dir: str, slug: str):
    d = skills_root / '_proposed' / scope_dir / f"2026-06-05T00-00-00-aaaa-{slug}"
    d.mkdir(parents=True, exist_ok=True)
    (d / 'SKILL.md').write_text(
        "---\n"
        "kind: skill\n"
        f"name: {slug}\n"
        f"extraction_fingerprint_exact: {EXACT}\n"
        "extraction_scope: cross-project\n"
        "created_at: 2026-06-05T00:00:00Z\n"
        "---\n\n"
        "# A real title\n\nSome real reusable procedure body content here.\n",
        encoding='utf-8')
    return d


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(distiller, '_skills_root', tmp_path / 'skills')
    monkeypatch.setattr(distiller, '_data_root', tmp_path / 'projects')
    (tmp_path / 'projects').mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(distiller, '_atomic_write_text',
                        lambda p, t: Path(p).write_text(t, encoding='utf-8'))
    monkeypatch.setattr(distiller, '_now_iso', lambda: '2026-06-05T00:00:00Z')

    from mc.blueprints import distiller_routes
    agent_sessions.clear()
    app = Flask(__name__)
    app.register_blueprint(distiller_routes.bp)

    yield SimpleNamespace(client=app.test_client(),
                          skills_root=tmp_path / 'skills',
                          data_root=tmp_path / 'projects')
    agent_sessions.clear()


def _suppressions(data_root: Path) -> dict:
    out = {}
    for f in data_root.glob('*_skill_stats.json'):
        out.update(json.loads(f.read_text(encoding='utf-8')).get('suppressions') or {})
    return out


def _assert_untouched(ctx, d):
    assert d.exists()
    assert not (ctx.skills_root / '_rejected').exists()
    assert _suppressions(ctx.data_root) == {}


def test_unattended_reject_is_refused_and_records_nothing(ctx):
    d = _make_artifact(ctx.skills_root, 'global', 'good-idea')
    agent_sessions['steward-1'] = {'status': 'running',
                                   'trigger_type': 'steward_cycle',
                                   'project_id': None}
    res = ctx.client.post('/api/distiller/reject', json={'directory': str(d)})
    assert res.status_code == 403
    assert res.get_json()['ok'] is False
    _assert_untouched(ctx, d)


def test_unidentifiable_running_session_fails_closed(ctx):
    d = _make_artifact(ctx.skills_root, 'global', 'good-idea')
    agent_sessions['unidentifiable-1'] = {'status': 'running',
                                          'trigger_type': '',
                                          'project_id': None}
    res = ctx.client.post('/api/distiller/reject', json={'directory': str(d)})
    assert res.status_code == 403
    _assert_untouched(ctx, d)


def test_reject_and_promote_refuse_with_the_same_status(ctx):
    """One caller check, one status code, on both halves of the human gate."""
    d = _make_artifact(ctx.skills_root, 'global', 'good-idea')
    agent_sessions['steward-1'] = {'status': 'running',
                                   'trigger_type': 'steward_cycle',
                                   'project_id': None}
    rej = ctx.client.post('/api/distiller/reject', json={'directory': str(d)})
    pro = ctx.client.post('/api/distiller/promote',
                          json={'directory': str(d), 'scope': 'global'})
    assert rej.status_code == pro.status_code == 403


def test_no_session_still_rejects_and_the_no_is_recorded(ctx):
    d = _make_artifact(ctx.skills_root, 'global', 'good-idea')
    res = ctx.client.post('/api/distiller/reject', json={'directory': str(d)})
    assert res.status_code == 200
    assert res.get_json()['ok'] is True
    assert not d.exists()
    assert (ctx.skills_root / '_rejected').exists()
    assert _suppressions(ctx.data_root)[f'{EXACT}:skill']['decision'] == 'no'


def test_manual_session_still_rejects(ctx):
    d = _make_artifact(ctx.skills_root, 'global', 'good-idea')
    agent_sessions['chat-1'] = {'status': 'running', 'trigger_type': 'manual',
                                'project_id': None}
    res = ctx.client.post('/api/distiller/reject', json={'directory': str(d)})
    assert res.status_code == 200
    assert not d.exists()
