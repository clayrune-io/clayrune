"""Topics auto-refresh gates (mc/blueprints/topics_routes.py).

The digest had exactly one writer — a button — so it aged silently: nine days
and 177 chats out of date on this install while the UI called it fresh. It now
also refreshes on SESSION END, alongside Scribe / Distiller / Beacon.

Every test here is about a GATE, because the thing being gated is a model call.
Ungated, this fires once per finished session per project; the gates are what
make it "when a chat actually changed" instead of "constantly".

The other candidate trigger was the coordination loop. It is the wrong one and
that is worth recording: it only acts on projects with >= 2 live agents, so a
single-agent project would never refresh.
"""
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import topics_routes as tr  # noqa: E402

NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Fresh data dir + debounce table per test; never touch the real one."""
    monkeypatch.setattr(tr, '_DATA_DIR', tmp_path)
    monkeypatch.setattr(tr, '_last_auto_refresh', {})
    monkeypatch.setattr(tr, '_auto_lock', threading.Lock())
    monkeypatch.setattr(tr, '_publish_refresh_event', lambda pid, n: None)


def _write_cache(pid, generated_at=None, chat_count=1):
    tr._atomic_write(tr._topics_path(pid), {
        'topics': [{'id': 't', 'title': 'T', 'gist': '', 'chat_count': 1}],
        'chat_count': chat_count,
        'generated_at': (generated_at or NOW.isoformat()),
    })


def _stub_synthesis(monkeypatch, calls, sigs=1):
    monkeypatch.setattr(tr, '_gather_signals',
                        lambda pid, **kw: [{'csid': f'c{i}'} for i in range(sigs)])

    def _synth(s):
        calls.append(len(s))
        return [{'id': 'new', 'title': 'New', 'gist': '', 'chat_count': len(s)}]

    monkeypatch.setattr(tr, '_synthesize', _synth)


def _force_stale(monkeypatch, stale=True):
    monkeypatch.setattr(tr, '_staleness',
                        lambda pid, cache: (stale, 'reason' if stale else '', 3))


def test_refreshes_when_stale(monkeypatch):
    calls = []
    _write_cache('p')
    _force_stale(monkeypatch, True)
    _stub_synthesis(monkeypatch, calls, sigs=4)
    tr._maybe_refresh('p')
    assert calls == [4]
    written = tr._load_json(tr._topics_path('p'), {})
    assert written['topics'][0]['id'] == 'new' and written['chat_count'] == 4


def test_no_call_when_not_stale(monkeypatch):
    """The whole point: a finished session on an unchanged digest is free."""
    calls = []
    _write_cache('p')
    _force_stale(monkeypatch, False)
    _stub_synthesis(monkeypatch, calls)
    tr._maybe_refresh('p')
    assert calls == []


def test_never_builds_a_digest_unbidden(monkeypatch):
    """No cache = the user has never opened Topics here. Building one would be
    a model call per project on the first session after an update, for a
    surface they may never use. "Build it" is the opt-in; this only maintains."""
    calls = []
    _force_stale(monkeypatch, True)
    _stub_synthesis(monkeypatch, calls)
    tr._maybe_refresh('never-opened')
    assert calls == []
    assert not tr._topics_path('never-opened').exists()


def test_debounced_per_project(monkeypatch):
    """A burst of short sessions is one refresh, not five."""
    calls = []
    _write_cache('p')
    _force_stale(monkeypatch, True)
    _stub_synthesis(monkeypatch, calls)
    monkeypatch.setattr(tr, '_cfg',
                        lambda k, d: 900 if 'interval' in k else True)
    for _ in range(5):
        tr._maybe_refresh('p')
    assert len(calls) == 1


def test_debounce_is_per_project_not_global(monkeypatch):
    calls = []
    _write_cache('a'); _write_cache('b')
    _force_stale(monkeypatch, True)
    _stub_synthesis(monkeypatch, calls)
    tr._maybe_refresh('a')
    tr._maybe_refresh('b')
    assert len(calls) == 2


def test_debounce_expires(monkeypatch):
    calls = []
    _write_cache('p')
    _force_stale(monkeypatch, True)
    _stub_synthesis(monkeypatch, calls)
    monkeypatch.setattr(tr, '_cfg', lambda k, d: 0 if 'interval' in k else True)
    tr._maybe_refresh('p')
    tr._maybe_refresh('p')
    assert len(calls) == 2


def test_kill_switch(monkeypatch):
    calls = []
    _write_cache('p')
    _force_stale(monkeypatch, True)
    _stub_synthesis(monkeypatch, calls)
    monkeypatch.setattr(tr, '_cfg',
                        lambda k, d: False if k == 'topics_auto_refresh_enabled' else d)
    tr._maybe_refresh('p')
    assert calls == []


def test_synthesis_failure_never_escapes(monkeypatch):
    """Same posture as Scribe/Distiller/Beacon — a failed digest must not touch
    the session that triggered it, which is a completion path."""
    _write_cache('p')
    _force_stale(monkeypatch, True)
    monkeypatch.setattr(tr, '_gather_signals', lambda pid, **kw: [{'csid': 'c'}])
    monkeypatch.setattr(tr, '_synthesize',
                        lambda s: (_ for _ in ()).throw(RuntimeError('model down')))
    tr._maybe_refresh('p')          # must not raise
    # And the previous digest survives rather than being half-written.
    assert tr._load_json(tr._topics_path('p'), {})['topics'][0]['id'] == 't'


def test_no_signals_leaves_cache_alone(monkeypatch):
    _write_cache('p')
    _force_stale(monkeypatch, True)
    monkeypatch.setattr(tr, '_gather_signals', lambda pid, **kw: [])
    monkeypatch.setattr(tr, '_synthesize',
                        lambda s: pytest.fail('should not synthesize with no signals'))
    tr._maybe_refresh('p')
    assert tr._load_json(tr._topics_path('p'), {})['topics'][0]['id'] == 't'


def test_dispatch_is_non_blocking_and_safe(monkeypatch):
    """The session-end hook calls the async wrapper; it must never raise into
    the completion path even if the worker explodes immediately — AND must not
    leave an unhandled exception on the daemon thread, which shows up as a
    fault next to a completion it had nothing to do with."""
    seen = []
    monkeypatch.setattr(tr, '_log', lambda m: seen.append(m))
    monkeypatch.setattr(tr, '_maybe_refresh',
                        lambda pid: (_ for _ in ()).throw(RuntimeError('boom')))
    escaped = []
    threading.excepthook = lambda args: escaped.append(args)
    try:
        tr.maybe_refresh_async('p')     # must not raise
        for t in threading.enumerate():
            if t.name == 'topics-p':
                t.join(timeout=5)
    finally:
        threading.excepthook = threading.__excepthook__
    assert not escaped, 'exception escaped to the thread top level'
    assert any('crashed' in m for m in seen), 'the crash must still be logged'


def test_real_staleness_path_end_to_end(monkeypatch):
    """Not stubbing _staleness: prove the gate works against the real
    implementation, so a change to either side can't silently decouple them."""
    calls = []
    _write_cache('p', generated_at=(NOW - timedelta(minutes=30)).isoformat(),
                 chat_count=1)
    monkeypatch.setattr(tr, '_load_project', lambda pid: {'project_path': '/p'})
    # One long chat, modified AFTER the digest was generated and since SETTLED
    # (outside topics_settle_seconds — an in-progress chat deliberately does
    # not count, or the banner would be on for the whole conversation).
    monkeypatch.setattr(tr, '_recent_transcripts', lambda pp, limit=50: [
        {'session_id': 'c1', 'turns': 5, 'first_user': 'a long opening message',
         'mtime': (NOW - timedelta(minutes=10)).timestamp()}])
    _stub_synthesis(monkeypatch, calls)
    tr._maybe_refresh('p')
    assert calls, 'real staleness check should have reported stale'
