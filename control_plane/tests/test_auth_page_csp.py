"""Auth page policies must permit the requests needed to finish sign-in."""
from __future__ import annotations

import base64

import pytest


@pytest.fixture(params=["signin", "connect"])
def auth_page(request, client):
    if request.param == "signin":
        response = client.get("/v1/signin")
        endpoints = ["/v1/session/start"]
    else:
        response = client.get("/v1/connect", params={
            "pub": base64.b64encode(b"\x01" * 32).decode(),
            "nonce": "n" * 16,
            "callback": "http://127.0.0.1:5199/api/mc-callback",
        })
        endpoints = ["/v1/signin/start", "/v1/signin/complete"]
    assert response.status_code == 200
    directives = {}
    for directive in response.headers["content-security-policy"].split(";"):
        name, *sources = directive.split()
        directives[name] = sources
    return response.text, directives, endpoints


def test_auth_page_csp_allows_its_session_fetches(auth_page):
    html, directives, endpoints = auth_page
    for endpoint in endpoints:
        assert f'fetch("{endpoint}"' in html
    assert "'self'" in directives["connect-src"], \
        "the page must be allowed to POST its sign-in result to its own origin"


def test_auth_page_csp_allows_firebase_popup_script(auth_page):
    html, directives, _ = auth_page
    assert "signInWithPopup(auth, new GoogleAuthProvider())" in html
    assert "https://apis.google.com" in directives["script-src"], \
        "Firebase popup auth dynamically loads https://apis.google.com/js/api.js"
    assert "https://www.gstatic.com" in directives["script-src"]
    assert "'unsafe-inline'" not in directives["script-src"]
    assert "'unsafe-eval'" not in directives["script-src"]
    assert "*" not in directives["script-src"]
    assert directives["default-src"] == ["'none'"]
