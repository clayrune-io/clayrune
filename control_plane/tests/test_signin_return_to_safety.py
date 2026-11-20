"""MC-1179 MB27: the sign-in page's `return_to`.

`_safe_return_to` judged the host with urlparse alone. A backslash makes
urlparse and a browser disagree about the host (the same hole MB26 closed for
the connect callback), so `https://evil.example\\@a.<zone>/` passed as in-zone
and the page navigated to evil.example after sign-in.

The accepted value was then written into the page's inline script through
json.dumps, which leaves `</script>` intact: a `return_to` whose path carried
one closed the page's own script and injected markup after it.
"""
from __future__ import annotations

import pytest

from control_plane.app.routes_auth import _safe_return_to


@pytest.mark.parametrize("url", [
    "https://ron.clayrune.io/",
    "https://clayrune.io/upgrade",
    "https://ron.clayrune.io/p/mission_control?tab=agent&x=1#chat",
    "https://ron.clayrune.io:443/",
    # an @ after the authority is ordinary path/query text
    "https://ron.clayrune.io/share?to=a@b.example",
])
def test_in_zone_destinations_still_pass(url):
    assert _safe_return_to(url) == url


@pytest.mark.parametrize("url", [
    # the reported cases: urlparse says in-zone, a browser says evil.example
    "https://evil.example\\@a.clayrune.io/",
    "https://evil.example\\.clayrune.io/",
    "https://ron.clayrune.io\\@evil.example/",
    # userinfo is never part of a destination the Worker builds
    "https://user@ron.clayrune.io/",
    "https://ron.clayrune.io@evil.example/",
    "https://evil.example:pw@ron.clayrune.io/",
    # a backslash anywhere: browsers read it as a slash in an https URL
    "https://ron.clayrune.io/\\evil.example",
    # whitespace and control characters are stripped or reinterpreted by browsers
    "https://ron.clayrune.io/\tx",
    "https://ron.clayrune.io/\nx",
    "https://ron.clayrune.io/ x",
    "https://ron.clay\x00rune.io/",
    # a port urlparse cannot read
    "https://ron.clayrune.io:notaport/",
    "https://ron.clayrune.io:99999/",
])
def test_destinations_two_parsers_read_differently_are_refused(url):
    assert _safe_return_to(url) is None, url


def test_return_to_cannot_close_the_sign_in_script(client):
    hostile = "https://ron.clayrune.io/</script><img/src=x>"
    r = client.get("/v1/signin", params={"return_to": hostile})
    assert r.status_code == 200
    body = r.text
    assert "<img/src=x>" not in body
    # only the template's own closing tags: nothing reflected may add another
    assert body.count("</script>") == _own_script_closers()
    # the value still reaches the script, as an escaped string
    assert "\\u003c/script\\u003e" in body


def _own_script_closers() -> int:
    from control_plane.app import routes_auth
    return routes_auth._SIGNIN_HTML.count("</script>")
