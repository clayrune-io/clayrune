"""tools/provider-live, MC-1179 pass 12 third wave (TL45 remainder).

Offline only: pure functions and fakes, no vendor CLI, no instance, no live
provider pass. What these cannot show is how a real vendor behaves; see the
commit message for the list of changes that were not run live.
"""
import importlib.util
import inspect
import signal
import sys
import types
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / 'tools' / 'provider-live'


def _load(name):
    spec = importlib.util.spec_from_file_location(f'provider_live3_{name}', TOOLS / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


G = _load('live_gates')
D = _load('codex_run')
D.G = G


# ── a. kill_succeeded names THIS image ──────────────────────────────────────

def test_success_line_naming_another_image_does_not_count():
    log = 'SUCCESS: The process "other_tool.exe" with PID 4242 has been terminated.'
    assert D.kill_succeeded(log, '', 'crd_0123456789a.exe') is False


def test_success_line_naming_this_image_counts():
    log = 'SUCCESS: The process "crd_0123456789a.exe" with PID 4242 has been terminated.'
    assert D.kill_succeeded(log, '', 'crd_0123456789a.exe') is True


def test_success_line_with_no_image_name_still_counts():
    # a kill by PID reports no image
    log = 'SUCCESS: The process with PID 4242 has been terminated.'
    assert D.kill_succeeded(log, '', 'crd_0123456789a.exe') is True


def test_no_success_line_is_not_success():
    assert D.kill_succeeded('ERROR: Access denied', 'it failed', 'crd_0123456789a.exe') is False


# ── b. decoy names survive the 15-character cut ─────────────────────────────

def test_decoy_name_fits_the_posix_cut_and_is_unique_per_run_and_decoy():
    names = {D.decoy_name(tag, n, pid)
             for tag in ('1117142400', 'slow11171') for n in (0, 1) for pid in (100, 200)}
    assert len(names) == 8
    assert all(len(n) == 15 for n in names)


def test_by_name_kill_targets_one_decoy_only(monkeypatch, tmp_path):
    monkeypatch.setattr(D, '_win', lambda: False)
    p = D.Procs(tmp_path)
    monkeypatch.setattr(D.shutil, 'copyfile', lambda *a, **k: None)
    monkeypatch.setattr(D.os, 'chmod', lambda *a, **k: None)
    first = p.make('abc123')[1]
    second = p.make('abc123')[1]
    cut = [n.split('.')[0][:15] for n in (first, second)]
    assert cut[0] != cut[1]
    assert D.kill_cmd_by_name(first).endswith(cut[0])


# ── c. persona_held: "as <name>" needs the proper noun ──────────────────────

PERSONA = {'name': 'live-persona', 'agent_name': 'Marlowe'}


def _held(text):
    return G.persona_held(PERSONA, {'name': 'live-persona'}, [text])['verdict']


def test_as_vector_in_ordinary_prose_passes():
    assert _held('The embeddings are stored as vector data in the index.') == G.PASS


def test_as_the_default_name_still_fails():
    assert _held('Speaking as Vector, here is the answer.') == G.FAIL
    assert _held("I'm Vector, happy to help") == G.FAIL


# ── d. --allow-scribe-claude is gone, and the pass rule says why ────────────

def test_allow_scribe_flag_is_removed():
    with pytest.raises(SystemExit) as e:
        D.parse(['--vendor', 'codex', '--allow-scribe-claude'])
    assert e.value.code == 2


def test_memory_cell_pass_rule_says_the_scribe_leg_is_unverifiable():
    rule = next(c for c in D.CELLS if c.id == 'memory').pass_rule
    assert '--allow-scribe-claude' not in rule
    assert 'UNVERIFIABLE' in rule and 'not implemented' in rule


# ── e. restart-resume identity ──────────────────────────────────────────────

def test_same_id_passes():
    assert G.resume_identity('t1', 't1', recalled=False, replied=True)['verdict'] == G.PASS


def test_changed_id_passes_only_with_the_earlier_turn_kept():
    assert G.resume_identity('t1', 't2', recalled=True, replied=True)['verdict'] == G.PASS
    assert G.resume_identity('t1', 't2', recalled=False, replied=True)['verdict'] == G.FAIL


def test_nothing_measurable_is_unverifiable_never_pass():
    assert G.resume_identity('', '', recalled=False, replied=False)['verdict'] == G.UNVERIFIABLE
    assert G.resume_identity('t1', '', recalled=False, replied=False)['verdict'] == G.UNVERIFIABLE


# ── f. token counts: able to not pass ───────────────────────────────────────

def test_token_counts_pass_on_rows_or_recorded_unavailable():
    assert G.token_counts_exposed(3, None)['verdict'] == G.PASS
    assert G.token_counts_exposed(0, False)['verdict'] == G.PASS


def test_token_counts_unverifiable_with_no_rows_and_no_record():
    assert G.token_counts_exposed(0, None)['verdict'] == G.UNVERIFIABLE
    assert G.token_counts_exposed(0, True)['verdict'] == G.UNVERIFIABLE


# ── g. remaining bullets ────────────────────────────────────────────────────

def test_register_project_id_is_empty_not_a_literal_when_nothing_resolves(monkeypatch, tmp_path):
    monkeypatch.setattr(D, 'REPO_ROOT', tmp_path)
    assert D.register_project_id() == ''
    assert 'mission_control' not in inspect.getsource(D.Instance.start)


def test_cleanup_note_no_longer_claims_port_5199_was_never_touched():
    assert 'port 5199 never touched' not in inspect.getsource(D.main)


def test_posix_stop_signals_the_server_group(monkeypatch):
    calls = []
    shim = types.SimpleNamespace(
        name='posix',
        getpgid=lambda pid: pid + 1000,
        killpg=lambda pgid, sig: calls.append((pgid, sig)))
    monkeypatch.setattr(D, 'os', shim)

    class Proc:
        pid = 77
        def poll(self):
            return None if not calls else 0
        def wait(self, t=None):
            return 0
        def terminate(self):
            raise AssertionError('must signal the group, not only the server PID')

    inst = object.__new__(D.Instance)
    inst.proc, inst.log, inst.port = Proc(), None, 5391
    inst.wait_port_free = lambda *a, **k: True
    inst.stop()
    assert calls == [(1077, signal.SIGTERM)]


def test_dead_skill_fields_are_gone_from_the_token_report():
    assert 'skills_declared' not in inspect.signature(G.token_report).parameters
    assert 'skills_declared' not in G.token_report([])
