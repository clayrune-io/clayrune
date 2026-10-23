"""Inert human-described sign-in method, shared by browser Save branches."""
from __future__ import annotations

METHODS = ('google', 'phone_code', 'browser_password', 'other')


def clean(value='other') -> str:
    if not isinstance(value, str) or value not in METHODS:
        raise ValueError('signin_method must be google, phone_code, browser_password or other')
    return value


def stored(metadata: dict) -> str:
    value = metadata.get('signin_method')
    return value if isinstance(value, str) and value in METHODS else 'other'
