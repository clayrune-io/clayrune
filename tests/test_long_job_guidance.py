"""MC-1149 phase 1: every engine is told to hand long background commands to
the server-owned job route, with its own session id filled in.

Before this, only engines without a `run_in_background` facility heard
about `POST /agent/<sid>/job` (as the reason their `&` jobs were orphaned).
A Claude session's `run_in_background` pytest run died with the CLI process
tree at a mid-turn rollover (Dave 92a97b85be6a, 2026-11-04, 41% through,
~25 min lost) and nothing had told it the route existed.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import long_job_guidance as ljg  # noqa: E402

_NO_BG_PREFACE = 'no background-job facility'


def test_claude_note_names_the_route_with_this_session_id_and_the_threshold():
    note = ljg.long_job_note(5199, 'mission_control', 'sess-42', background_jobs=True)
    assert 'http://localhost:5199/api/project/mission_control/agent/sess-42/job' in note
    assert f'~{ljg.LONG_JOB_MINUTES} minutes' in note
    assert 'run_in_background' in note and 'rollover' in note
    # The "you have no facility" preface is wrong for a runtime that has one.
    assert _NO_BG_PREFACE not in note
    # Context budget: this rides on every dispatch.
    assert len(note.encode('utf-8')) < 900, len(note)


def test_no_background_runtime_keeps_its_preface_and_gains_the_threshold():
    note = ljg.long_job_note(5199, 'p1', 's1', background_jobs=False)
    assert _NO_BG_PREFACE in note
    assert 'orphans it' in note
    assert 'http://localhost:5199/api/project/p1/agent/s1/job' in note
    assert f'~{ljg.LONG_JOB_MINUTES} minutes' in note
    assert len(note.encode('utf-8')) < 900, len(note)


def test_route_url_is_one_function_shared_with_the_rollover_block():
    assert ljg.job_route_url(5200, 'p', 's') == \
        'http://localhost:5200/api/project/p/agent/s/job'


@pytest.mark.parametrize('enabled', [False, None, 'true', 1])
def test_reported_test_guidance_is_dark_unless_literal_true(enabled):
    note = ljg.long_job_note(5200, 'p', 's', background_jobs=True,
                             test_run_progress_enabled=enabled, app_dir=PROJECT_ROOT)
    assert 'Reported tests:' not in note and 'test-run.py' not in note


def test_enabled_guidance_uses_installation_path_exact_argv_and_job_route(tmp_path):
    tools = tmp_path / 'tools'
    tools.mkdir()
    (tools / 'test-run.py').write_text('# fixture')
    note = ljg.long_job_note(5201, 'other-project', 'other-session', background_jobs=False,
                             test_run_progress_enabled=True, app_dir=tmp_path)
    assert 'Reported tests:' in note and '--manifest' in note
    assert 'scripts, not assertions' in note and 'Long tests still go through the job route' in note
    import json
    assert json.dumps(str(tools / 'test-run.py')) in note
    assert 'other-project' in note and '5201' in note


def test_enabled_but_missing_launcher_discloses_capture_gap(tmp_path):
    note = ljg.long_job_note(5200, 'p', 's', background_jobs=True,
                             test_run_progress_enabled=True, app_dir=tmp_path)
    assert 'launcher unavailable' in note and 'Reported tests:' not in note


# ── Integration: _build_agent_context injects it for Claude too ─────────────

@pytest.fixture()
def env(tmp_path):
    import server  # noqa: F401 — wires blueprint deps (PORT, memory helpers, …)
    from mc.blueprints import agent_routes as ar

    proj_path = tmp_path / 'proj'
    proj_path.mkdir(parents=True)
    project = {'id': 'tc', 'name': 'TC', 'project_path': str(proj_path),
               'provider': 'claude'}
    return {'ar': ar, 'project': project, 'port': ar.PORT}


def test_claude_context_carries_the_job_route_for_its_own_session(env):
    ctx = env['ar']._build_agent_context(env['project'], task='hi', session_id='sess1',
                                         provider='claude')
    assert f"http://localhost:{env['port']}/api/project/tc/agent/sess1/job" in ctx
    assert f'~{ljg.LONG_JOB_MINUTES} minutes' in ctx
    # The pre-MC-1149 gate is unchanged: Claude has a background facility.
    assert _NO_BG_PREFACE not in ctx


def test_gemini_context_keeps_the_no_facility_preface(env):
    project = dict(env['project'], provider='gemini')
    ctx = env['ar']._build_agent_context(project, task='hi', session_id='sess1',
                                         provider='gemini')
    assert _NO_BG_PREFACE in ctx
    assert f"http://localhost:{env['port']}/api/project/tc/agent/sess1/job" in ctx


def test_no_session_id_or_incognito_means_no_route_to_address(env):
    ctx = env['ar']._build_agent_context(env['project'], task='hi', provider='claude')
    assert '/agent//job' not in ctx and f'~{ljg.LONG_JOB_MINUTES} minutes' not in ctx
    ctx = env['ar']._build_agent_context(env['project'], task='hi', session_id='s',
                                         provider='claude', incognito=True)
    assert '/agent/s/job' not in ctx


@pytest.mark.parametrize('provider', ['claude', 'codex', 'gemini', 'qwen'])
def test_context_guidance_follows_live_flag_for_all_engines(env, monkeypatch, provider):
    from mc import state
    import json
    monkeypatch.setattr(env['ar'], '_APP_DIR', PROJECT_ROOT)
    monkeypatch.setitem(state.CONFIG, 'test_run_progress_enabled', True)
    ctx = env['ar']._build_agent_context(env['project'], task='hi', session_id='s', provider=provider)
    assert 'Reported tests:' in ctx
    assert json.dumps(str(PROJECT_ROOT / 'tools' / 'test-run.py')) in ctx
    monkeypatch.setitem(state.CONFIG, 'test_run_progress_enabled', False)
    ctx = env['ar']._build_agent_context(env['project'], task='hi', session_id='s', provider=provider)
    assert 'Reported tests:' not in ctx
