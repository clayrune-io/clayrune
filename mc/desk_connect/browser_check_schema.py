"""Reviewed sign-in evidence is inert profile data, never a request script."""
from urllib.parse import urlsplit


def check(raw, hosts: list[str]) -> dict:
    if not isinstance(raw, dict) or set(raw) != {'url', 'selectors'}:
        raise ValueError('signin_check needs only url and selectors')
    url, selectors = raw['url'], raw['selectors']
    try:
        p = urlsplit(url) if isinstance(url, str) else None
        valid = p is not None and p.scheme == 'https' and p.hostname in hosts and not p.username and not p.password and p.port in (None,443) and not p.query and not p.fragment and len(url) <= 300
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('signin_check URL must be a plain HTTPS page on a declared service host')
    if not isinstance(selectors,list) or not 1 <= len(selectors) <= 4 or not all(isinstance(s,str) and 0 < len(s) <= 200 and not any(ord(c)<32 for c in s) for s in selectors):
        raise ValueError('signin_check needs one to four short CSS selectors')
    return {'url':url,'selectors':list(selectors)}
