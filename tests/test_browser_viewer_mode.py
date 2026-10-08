"""Viewer transitions order the CDP reversal before desktop measurement."""
import json
import queue
import threading

from mc.blueprints import browser_routes as br


def test_phone_to_desktop_clears_before_fit_and_reloads_once(monkeypatch):
    session = {'device_mode': 'mobile', 'device_mode_view': (412, 809), 'mobile': True,
               'cmd_queue': queue.Queue(), 'ua_override': {'userAgent': 'desktop'}}
    observations = []

    def fit(s, reset_device=False):
        observations.append((s['device_mode'], reset_device, list(s['cmd_queue'].queue)))

    monkeypatch.setattr(br, '_fit_and_rearm', fit)
    br._apply_view(session, (1120, 685), False)
    assert observations[0][0:2] == ('desktop', True)
    assert observations[0][2] == [
        ('Emulation.clearDeviceMetricsOverride', {}),
        ('Emulation.setTouchEmulationEnabled', {'enabled': False}),
        ('Network.setUserAgentOverride', {'userAgent': 'desktop'})]
    assert list(session['cmd_queue'].queue)[-1] == ('Page.reload', {})
    assert session['mobile'] is False
    session['cmd_queue'] = queue.Queue()
    br._apply_view(session, (1120, 685), False)
    assert list(session['cmd_queue'].queue) == []


def test_fresh_desktop_never_sets_metrics_override(monkeypatch):
    session = {'device_mode': 'desktop', 'cmd_queue': queue.Queue()}
    monkeypatch.setattr(br, '_fit_and_rearm', lambda s: None)
    br._apply_view(session, (1120, 685), False)
    assert session['cmd_queue'].empty()


def test_desktop_to_phone_reloads_once_and_resize_only_updates_metrics(monkeypatch):
    session = {'device_mode': 'desktop', 'cmd_queue': queue.Queue(),
               'ua_override': {'userAgent': 'Mozilla/5.0 Chrome/153.0.0.0'}}
    br._apply_view(session, (412, 809), True)
    commands = list(session['cmd_queue'].queue)
    assert commands[0][0] == 'Emulation.setDeviceMetricsOverride'
    assert commands.count(('Page.reload', {})) == 1
    assert session['mobile'] is True
    session['cmd_queue'] = queue.Queue()
    br._apply_view(session, (412, 600), True)
    assert [m for m, _ in session['cmd_queue'].queue] == ['Emulation.setDeviceMetricsOverride']


def test_fit_acknowledges_reset_before_measure_and_resets_shared_window_tabs(monkeypatch):
    calls = []

    class Connection:
        pending = None

        def send(self, raw):
            self.pending = json.loads(raw)
            calls.append(self.pending)

        def recv(self):
            message = self.pending
            method = message['method']
            result = {}
            if method == 'Browser.getWindowForTarget':
                result = {'windowId': 1, 'bounds': {'width': 1120, 'height': 685}}
            elif method == 'Target.attachToTarget':
                result = {'sessionId': message['params']['targetId']}
            elif method == 'Runtime.evaluate':
                result = {'result': {'value': [1120, 685]}}
            return json.dumps({'id': message['id'], 'result': result})

        def close(self):
            pass

    monkeypatch.setattr(br, '_import_ws', lambda: object())
    monkeypatch.setattr(br, '_cdp_version', lambda *a, **k: {})
    monkeypatch.setattr(br, '_cdp_browser_conn', lambda *a, **k: Connection())
    session = {'view': (1120, 685), 'fit_lock': threading.Lock(), 'status': 'running',
               'tabs': {'root': {}, 'second': {}}}
    br._fit_windows(session, reset_device=True)
    methods = [c['method'] for c in calls]
    assert methods.count('Emulation.clearDeviceMetricsOverride') == 2
    assert methods.count('Emulation.setTouchEmulationEnabled') == 2
    assert methods.count('Runtime.evaluate') == 1
    assert methods.index('Emulation.clearDeviceMetricsOverride') < methods.index('Runtime.evaluate')
    assert methods.index('Emulation.setTouchEmulationEnabled') < methods.index('Runtime.evaluate')
    assert 'Emulation.setDeviceMetricsOverride' not in methods
