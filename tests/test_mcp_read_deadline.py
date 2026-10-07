"""Transport deadlines with fake clocks/sockets; no network."""
import io
from types import SimpleNamespace

import pytest

from mc import desk_engines as eng, mcp_read_deadline as bounded


def test_sse_heartbeats_cannot_keep_discovery_alive(monkeypatch):
    clock = [0.0]
    timeouts = []
    monkeypatch.setattr(bounded.time, 'monotonic', lambda: clock[0])

    class Stream:
        def read1(self, size):
            clock[0] += 6
            return b': heartbeat\n\n'

    response = Stream()
    response.fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=timeouts.append)))
    with pytest.raises(TimeoutError):
        list(bounded.lines(response, deadline=15, maximum=1024))
    assert timeouts == [15, 9, 3]


def test_split_lines_and_byte_bound():
    deadline = bounded.time.monotonic() + 10
    assert list(bounded.lines(io.BytesIO(b'data: a\n\nlast'), deadline=deadline, maximum=100)) == [b'data: a', b'', b'last']
    with pytest.raises(ValueError, match='too large'):
        list(bounded.chunks(io.BytesIO(b'abcdef'), deadline=deadline, maximum=5))


@pytest.mark.parametrize('sse', [True, False])
def test_mcp_rpc_reads_matching_result_and_closes_response(monkeypatch, sse):
    payload = b'{"jsonrpc":"2.0","id":2,"result":{"tools":[]}}'
    response = io.BytesIO(b': heartbeat\n\ndata: ' + payload + b'\n\n' if sse else payload)
    response.status = 200
    response.headers = {'content-type': 'text/event-stream' if sse else 'application/json'}
    monkeypatch.setattr(eng, '_OPENER', SimpleNamespace(open=lambda *a, **kw: response))
    assert eng._mcp_post('fake', {'id': 2}, expect_id=2, timeout=1) == {'tools': []}
    assert response.closed
