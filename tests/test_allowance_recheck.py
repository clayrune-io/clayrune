"""A stale allowance record must be escapable (2026-09-19 deadlock).

Codex stayed refused for five days after the user bought more credits: a
record clears only at its reset time or on a successful run, and dispatch
refuses before it can run. Pins the two ways out (the user's re-check route,
and a user-initiated dispatch re-probing the vendor) and — just as important —
that a genuinely exhausted vendor still refuses and never falls back.
"""
import sys
import textwrap

import pytest

from mc import allowance_state as al
from mc import allowance_probe
from tests.test_revive_notify_carry import ar, _project


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    al.wire(tmp_path / 'allowance_state.json')
    from mc import agent_runtime
    # No test in this file may reach a real vendor CLI; each sets its own answer.
    for rt in agent_runtime._RUNTIMES.values():
        monkeypatch.setattr(rt, 'probe_allowance', lambda: None)
    al._LAST_PROBE.clear()
    yield
    al._STATE = {}


def _client():
    import server
    return server.app.test_client()


def _exhaust(vendor='codex'):
    al.record_exhaustion(vendor, limit_kind='usage_limit',
                         resets_at_display='Sep 24, 2026 7:58 AM')


# ── (1) the user's "I topped up" route ────────────────────────────────────


def test_recheck_route_clears_the_record(ar):
    _exhaust('codex')
    assert al.is_exhausted('codex')
    with _client() as c:
        resp = c.post('/api/agent/codex/allowance/recheck')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True and body['was_exhausted'] is True
    assert not al.is_exhausted('codex')
    assert al.refusal_message('codex') == ''


def test_recheck_route_reports_what_the_probe_saw(ar, monkeypatch):
    from mc import agent_runtime
    rt = agent_runtime.get_runtime('codex')
    for answer, label in ((True, 'usable'), (False, 'limited'), (None, 'unavailable')):
        _exhaust('codex')
        monkeypatch.setattr(rt, 'probe_allowance', lambda a=answer: a)
        with _client() as c:
            body = c.post('/api/agent/codex/allowance/recheck').get_json()
        assert body['probe'] == label
        # A 'limited' answer does not veto the user's click (it can coexist
        # with purchased credits); the next run re-records if truly out.
        assert not al.is_exhausted('codex')


def test_recheck_route_only_touches_the_named_vendor(ar):
    _exhaust('codex')
    _exhaust('gemini')
    with _client() as c:
        c.post('/api/agent/codex/allowance/recheck')
    assert not al.is_exhausted('codex')
    assert al.is_exhausted('gemini')


def test_recheck_route_unknown_provider_404(ar):
    with _client() as c:
        assert c.post('/api/agent/nope/allowance/recheck').status_code == 404


def test_recheck_on_a_healthy_vendor_is_a_noop(ar):
    with _client() as c:
        body = c.post('/api/agent/codex/allowance/recheck').get_json()
    assert body['was_exhausted'] is False and body['probe'] == 'unavailable'


# ── (2) a user-initiated dispatch re-probes ───────────────────────────────


def test_dispatch_proceeds_when_probe_says_vendor_is_usable(ar, tmp_path, monkeypatch):
    from mc import agent_runtime
    _exhaust('codex')
    monkeypatch.setattr(agent_runtime.get_runtime('codex'), 'probe_allowance',
                        lambda: True)
    assert ar._allowance_refusal('codex', user_initiated=True) == ''
    assert not al.is_exhausted('codex')


@pytest.mark.parametrize('answer', [False, None])
def test_refusal_stands_when_vendor_is_still_out_or_unknowable(ar, monkeypatch, answer):
    from mc import agent_runtime
    _exhaust('codex')
    monkeypatch.setattr(agent_runtime.get_runtime('codex'), 'probe_allowance',
                        lambda: answer)
    msg = ar._allowance_refusal('codex', user_initiated=True)
    assert 'codex is out of allowance' in msg
    assert 'no fallback to another vendor' in msg
    assert al.is_exhausted('codex')


def test_probe_that_raises_leaves_the_refusal_standing(ar, monkeypatch):
    from mc import agent_runtime

    def boom():
        raise RuntimeError('probe exploded')
    _exhaust('codex')
    monkeypatch.setattr(agent_runtime.get_runtime('codex'), 'probe_allowance', boom)
    assert ar._allowance_refusal('codex', user_initiated=True) != ''


def test_unattended_dispatch_never_probes(ar, monkeypatch):
    from mc import agent_runtime
    calls = []
    _exhaust('codex')
    monkeypatch.setattr(agent_runtime.get_runtime('codex'), 'probe_allowance',
                        lambda: calls.append(1) or True)
    assert ar._allowance_refusal('codex', user_initiated=False) != ''
    assert calls == []
    assert al.is_exhausted('codex')


def test_probes_are_throttled_per_vendor(ar, monkeypatch):
    from mc import agent_runtime
    calls = []
    _exhaust('codex')
    monkeypatch.setattr(agent_runtime.get_runtime('codex'), 'probe_allowance',
                        lambda: calls.append(1) or False)
    for _ in range(5):
        ar._allowance_refusal('codex', user_initiated=True)
    assert len(calls) == 1


def test_genuinely_exhausted_dispatch_still_raises_with_no_fallback(ar, tmp_path, monkeypatch):
    from mc import agent_runtime
    project = _project(tmp_path)
    monkeypatch.setattr(ar, 'load_project', lambda _: project)
    _exhaust('claude')
    monkeypatch.setattr(agent_runtime.get_runtime('claude'), 'probe_allowance',
                        lambda: False)
    with pytest.raises(ValueError) as exc:
        ar._dispatch_agent_internal('p1', 'work', provider_override='claude')
    assert 'no fallback to another vendor' in str(exc.value)


def test_vendors_without_a_probe_have_none():
    """The default is 'cannot tell' — no invented probe for Claude/Gemini/Qwen."""
    from mc import agent_runtime
    for name in ('claude', 'gemini', 'qwen'):
        rt = agent_runtime.get_runtime(name)
        assert type(rt).probe_allowance is agent_runtime.AgentRuntime.probe_allowance, name


# ── restart survival ──────────────────────────────────────────────────────


def test_state_survives_a_restart_and_so_does_a_clear(ar, tmp_path):
    path = tmp_path / 'allowance_state.json'
    _exhaust('codex')
    al._STATE = {}
    al.wire(path)                      # what server.py does at startup
    assert al.is_exhausted('codex')

    with _client() as c:
        c.post('/api/agent/codex/allowance/recheck')
    al._STATE = {'stale': 'in-memory only'}
    al.wire(path)
    assert not al.is_exhausted('codex')


# ── the Codex probe itself, against a fake app-server ─────────────────────


def _fake_server(tmp_path, result_json):
    script = tmp_path / 'fake_app_server.py'
    script.write_text(textwrap.dedent(f'''
        import sys, json
        for line in sys.stdin:
            m = json.loads(line)
            if m.get("id") == 1:
                print(json.dumps({{"id": 1, "result": {{}}}}), flush=True)
            elif m.get("id") == 2:
                print(json.dumps({{"id": 2, "result": json.loads({result_json!r})}}), flush=True)
    '''), encoding='utf-8')
    return [sys.executable, str(script)]


@pytest.mark.parametrize('result, expected', [
    ('{"ordinaryUsageAllowed": true, "rateLimits": {"rateLimitReachedType": null}}', True),
    ('{"ordinaryUsageAllowed": false, "rateLimits": {}}', False),
    ('{"ordinaryUsageAllowed": true, "rateLimits": '
     '{"rateLimitReachedType": "rate_limit_reached"}}', False),
    ('{"ordinaryUsageAllowed": null, "rateLimits": {}}', None),
    ('{"rateLimits": {}}', None),
])
def test_codex_probe_reads_ordinary_usage_allowed(tmp_path, result, expected):
    cmd = _fake_server(tmp_path, result)
    assert allowance_probe.codex_ordinary_usage_allowed(cmd, timeout_s=15) is expected


def test_codex_probe_returns_none_when_the_server_never_answers(tmp_path):
    script = tmp_path / 'silent.py'
    script.write_text('import sys\nfor _ in sys.stdin: pass\n', encoding='utf-8')
    assert allowance_probe.codex_ordinary_usage_allowed(
        [sys.executable, str(script)], timeout_s=1.5) is None


def test_codex_probe_returns_none_when_the_binary_is_missing():
    assert allowance_probe.codex_ordinary_usage_allowed(
        ['definitely-not-a-real-binary-xyz'], timeout_s=2) is None
