"""`POST /api/distiller/promote` must refuse an unattended caller — F5 of
docs/_review/2026-09-10_security.md: CLAUDE.md's learning rails say
promotion is human-only and the steward fence blocks writes under
`.claude/`, but the fence blocks the `Write` tool and allows the `curl` that
reaches this same install path. An artifact built from a steward's own
output could become a skill loaded into its next cycle — the sharpest
instance of the authority-guard rail this project treats as load-bearing.

Asserts the EFFECT, not a flag: after a refused promote, no skill file may
exist under the skills root, and the proposal must still sit in _proposed/
(not moved to _promoted/).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mc import distiller  # noqa: E402
from mc.state import agent_sessions  # noqa: E402


def _make_artifact(skills_root: Path, scope_dir: str, slug: str):
    d = skills_root / '_proposed' / scope_dir / f"2026-06-05T00-00-00-aaaa-{slug}"
    d.mkdir(parents=True, exist_ok=True)
    (d / 'SKILL.md').write_text(
        "---\n"
        "kind: skill\n"
        f"name: {slug}\n"
        "extraction_fingerprint_exact: deadbeefdeadbeef\n"
        "extraction_scope: cross-project\n"
        "created_at: 2026-06-05T00:00:00Z\n"
        "---\n\n"
        "# A real title\n\nSome real reusable procedure body content here.\n",
        encoding='utf-8')
    return d


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    distiller._skills_root = tmp_path / 'skills'
    distiller._data_root = tmp_path / 'projects'
    (tmp_path / 'projects').mkdir(parents=True, exist_ok=True)
    distiller._atomic_write_text = lambda p, t: Path(p).write_text(t, encoding='utf-8')
    distiller._now_iso = lambda: '2026-06-05T00:00:00Z'

    import mc.skills as _skills
    monkeypatch.setattr(_skills, 'GLOBAL_SKILLS_DIR', tmp_path / 'global_skills')

    from mc.blueprints import distiller_routes
    agent_sessions.clear()
    app = Flask(__name__)
    app.register_blueprint(distiller_routes.bp)

    class Ctx:
        pass
    c = Ctx()
    c.client = app.test_client()
    c.skills_root = tmp_path / 'skills'
    c.global_skills_dir = tmp_path / 'global_skills'
    yield c
    agent_sessions.clear()


def _installed_skill_files(global_skills_dir: Path):
    if not global_skills_dir.is_dir():
        return []
    return list(global_skills_dir.rglob('SKILL.md'))


def test_unattended_promote_is_refused_and_installs_nothing(ctx):
    d = _make_artifact(ctx.skills_root, 'global', 'good-idea')
    agent_sessions['steward-1'] = {'status': 'running',
                                   'trigger_type': 'steward_cycle',
                                   'project_id': None}
    res = ctx.client.post('/api/distiller/promote',
                          json={'directory': str(d), 'scope': 'global'})
    assert res.status_code == 403
    assert res.get_json()['ok'] is False
    # No skill file materialized anywhere under the skills tree.
    assert _installed_skill_files(ctx.global_skills_dir) == []
    # The proposal is untouched — still in _proposed/, not moved to _promoted/.
    assert d.exists()
    assert not (ctx.skills_root / '_promoted').exists()


def test_unidentifiable_running_session_fails_closed(ctx):
    d = _make_artifact(ctx.skills_root, 'global', 'good-idea')
    agent_sessions['unidentifiable-1'] = {'status': 'running',
                                          'trigger_type': '',
                                          'project_id': None}
    res = ctx.client.post('/api/distiller/promote',
                          json={'directory': str(d), 'scope': 'global'})
    assert res.status_code == 403
    assert _installed_skill_files(ctx.global_skills_dir) == []


def test_manual_session_and_no_session_still_promote(ctx):
    d = _make_artifact(ctx.skills_root, 'global', 'good-idea')
    res = ctx.client.post('/api/distiller/promote',
                          json={'directory': str(d), 'scope': 'global'})
    assert res.status_code == 200
    assert res.get_json()['ok'] is True
    assert len(_installed_skill_files(ctx.global_skills_dir)) == 1
    assert not d.exists()
    assert (ctx.skills_root / '_promoted').exists()
