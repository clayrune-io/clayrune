"""The User-Agent every agent-mail HTTP request sends.

Cloudflare's edge refuses urllib's default ``Python-urllib/<ver>`` signature with
error 1010 (403) before the request reaches the Worker, so a request without this
header never arrives. Measured 2026-11-14 against the deployed Worker: default
UA -> 403, any named UA -> 200.
"""
from __future__ import annotations

USER_AGENT = 'Clayrune-agent-mail/1'
