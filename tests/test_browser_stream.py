"""The browser pane's frame transports (backlog 629d2205): mc/browser_stream.py and the
/api/browser/frames route. No Chromium: the session is a hand-built dict, the way the existing
`_stream_gen` tests build one."""
import base64
import json
import struct
import threading
import time

import pytest
from flask import Flask

from mc import browser_stream as bs
from mc import state
from mc.blueprints import browser_routes as br
from mc.state import browser_sessions

JPEG = bytes(range(256)) * 40          # arbitrary bytes: the transport must not care what is in them


def _session(**kw):
    s = {'session_id': 'sid-1', 'status': 'running', 'frame': base64.b64encode(JPEG).decode(),
         'frame_seq': 1, 'frame_w': 1264, 'frame_h': 649, 'page_scale': 1, 'live_url': 'https://x/'}
    s.update(kw)
    return s


def _parse(chunks):
    """Bytes of a /frames body -> [(type, body)], asserting the magic first."""
    data = b''.join(chunks)
    assert data[:4] == bs.FRAMES_MAGIC
    out, i = [], 4
    while i < len(data):
        t, n = struct.unpack('>BI', data[i:i + 5])
        out.append((t, data[i + 5:i + 5 + n]))
        i += 5 + n
    return out


def _frame(body):
    ml = struct.unpack('>I', body[:4])[0]
    return json.loads(body[4:4 + ml]), body[4 + ml:]


# ---- encoders ----------------------------------------------------------------

def test_binary_frame_carries_raw_jpeg_and_the_same_meta_sse_does():
    s = _session()
    gen = bs.bin_gen(s)
    first = [next(gen), next(gen)]                     # magic, then the frame message
    s['status'] = 'stopped'
    msgs = _parse(first + list(gen))
    frames = [m for m in msgs if m[0] == bs.MSG_FRAME]
    assert len(frames) == 1
    meta, jpeg = _frame(frames[0][1])
    assert jpeg == JPEG                                # raw bytes, not base64
    assert meta == {'seq': 1, 'url': 'https://x/', 'w': 1264, 'h': 649, 's': 1}
    end = [json.loads(b) for t, b in msgs if t == bs.MSG_JSON][-1]
    assert end == {'status': 'stopped', 'error': None}


def test_binary_is_a_quarter_smaller_than_sse_for_the_same_frame():
    s = _session()
    sse = next(bs.sse_gen(s))
    frame_msg = [next(g) for g in [bs.bin_gen(_session())] for _ in range(2)][1]
    assert len(frame_msg) < len(sse) * 0.78            # base64 is +33%: raw is 75% of it, plus headers


def test_sse_frame_payload_is_unchanged():
    """Key order and separators are the wire format older panes and the existing tests see."""
    s = _session()
    chunk = next(bs.sse_gen(s))
    assert chunk == 'data: ' + json.dumps({'seq': 1, 'img': s['frame'], 'url': 'https://x/',
                                           'w': 1264, 'h': 649, 's': 1}) + '\n\n'


def test_tabs_dialog_and_downloads_reach_the_binary_stream_too():
    s = _session(frame=None, tabs={'t1': {'url': 'u', 'title': 'T'}}, active_target_id='t1', tabs_seq=1,
                 dialog={'type': 'alert'}, dialogs_seq=1, downloads={'g': {'guid': 'g'}}, downloads_seq=1)
    gen = bs.bin_gen(s)
    got = [next(gen) for _ in range(4)]                # magic + three JSON messages
    s['status'] = 'stopped'
    msgs = [json.loads(b) for t, b in _parse(got + list(gen)) if t == bs.MSG_JSON]
    assert any('tabs' in m and m['active_target_id'] == 't1' for m in msgs)
    assert any(m.get('dialog') == {'type': 'alert'} for m in msgs)
    assert any('downloads' in m for m in msgs)


def test_a_frame_that_will_not_decode_is_skipped_not_fatal():
    s = _session(frame='@@not base64@@')
    gen = bs.bin_gen(s)
    assert next(gen) == bs.FRAMES_MAGIC
    s['status'] = 'stopped'
    assert [t for t, _ in _parse([bs.FRAMES_MAGIC] + list(gen))] == [bs.MSG_JSON]   # only the end status


# ---- wake-up -----------------------------------------------------------------

def test_a_bump_wakes_a_parked_generator_instead_of_waiting_for_a_poll(monkeypatch):
    """With the poll interval pushed out to 5s, only the Condition can deliver this frame in time."""
    monkeypatch.setattr(bs, '_WAKE_TIMEOUT_S', 5.0)
    s = _session(frame=None, frame_seq=0)
    bs.new_wake_state(s)
    gen = bs.events(s)
    got = {}

    def reader():
        for ev in gen:                                 # the first thing out is the initial downloads message
            if ev[0] == 'frame':
                got['ev'], got['t'] = ev, time.monotonic()
                return
    th = threading.Thread(target=reader, daemon=True)
    th.start()
    time.sleep(0.2)                                    # let it park
    s['frame'] = base64.b64encode(JPEG).decode()
    t0 = time.monotonic()
    bs.bump(s, 'frame_seq')
    th.join(3)
    assert not th.is_alive(), 'generator never woke'
    assert got['ev'][0] == 'frame'
    assert got['t'] - t0 < 0.5


def test_bump_on_a_hand_built_session_just_counts():
    s = {}
    bs.bump(s, 'tabs_seq')
    bs.bump(s, 'tabs_seq')
    assert s == {'tabs_seq': 2}


# ---- fps cap -----------------------------------------------------------------

def test_the_fps_cap_holds_back_a_faster_source(monkeypatch):
    monkeypatch.setitem(state.CONFIG, 'browser_stream_max_fps', 10)
    s = _session(frame_seq=0)
    bs.new_wake_state(s)
    sent = []

    def reader():
        for ev in bs.events(s):
            if ev[0] == 'frame':
                sent.append(ev[1]['seq'])
    th = threading.Thread(target=reader, daemon=True)
    th.start()
    t_end = time.monotonic() + 1.0
    while time.monotonic() < t_end:                    # ~100 fps source
        bs.bump(s, 'frame_seq')
        time.sleep(0.01)
    s['status'] = 'stopped'
    th.join(3)
    assert 6 <= len(sent) <= 13, sent                  # 10/s for 1s, newest frame each time
    assert sent == sorted(sent) and sent[-1] > 50      # it skipped ahead, never queued stale frames


def test_max_fps_is_clamped_and_survives_a_bad_value(monkeypatch):
    monkeypatch.setitem(state.CONFIG, 'browser_stream_max_fps', 'fast')
    assert bs.max_fps() == 30
    monkeypatch.setitem(state.CONFIG, 'browser_stream_max_fps', 100000)
    assert bs.max_fps() == 120
    monkeypatch.setitem(state.CONFIG, 'browser_stream_max_fps', 0)
    assert bs.max_fps() == 1


# ---- the route ---------------------------------------------------------------

@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(br.bp)
    browser_sessions.clear()
    yield app.test_client()
    browser_sessions.clear()


def test_frames_route_serves_the_binary_content_type_and_magic(client):
    browser_sessions['sid-1'] = s = _session()
    resp = client.get('/api/browser/frames?session_id=sid-1', buffered=False)
    assert resp.status_code == 200
    assert resp.mimetype == bs.FRAMES_MIMETYPE
    assert 'no-transform' in resp.headers['Cache-Control']
    it = iter(resp.response)
    assert next(it) == bs.FRAMES_MAGIC
    first = next(it)
    while first[0] != bs.MSG_FRAME:                    # the initial downloads message may come first
        first = next(it)
    assert first[0] == bs.MSG_FRAME
    s['status'] = 'stopped'
    resp.close()


def test_frames_route_404s_an_unknown_session(client):
    assert client.get('/api/browser/frames?session_id=nope').status_code == 404


def test_frames_attach_resumes_a_minimize_paused_screencast(client):
    import queue
    browser_sessions['sid-1'] = s = _session(screencast_paused=True, cmd_queue=queue.Queue(),
                                             screencast_params={'format': 'jpeg', 'quality': 70})
    client.get('/api/browser/frames?session_id=sid-1', buffered=False).close()
    assert s['screencast_paused'] is False
    assert s['cmd_queue'].get_nowait() == ('Page.startScreencast', {'format': 'jpeg', 'quality': 70})


def test_the_legacy_sse_route_is_still_served(client):
    browser_sessions['sid-1'] = _session()
    resp = client.get('/api/browser/stream?session_id=sid-1', buffered=False)
    assert resp.mimetype == 'text/event-stream'
    resp.close()
