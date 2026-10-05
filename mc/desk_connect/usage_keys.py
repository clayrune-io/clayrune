"""Reviewed words for what discovery may say (spec: "usage keys select reviewed
one-line templates"; "never ... display generated instructions as Clayrune advice").

The model never writes a sentence the person reads. It picks a METHOD and a USAGE KEY
from the tables below; the sentence shown is the one written here, in review, and the
guidance under each method is Clayrune's own. A key belongs to one method and to one
kind of evidence (`page` = the text read from the service's own page, `registry` = a
listing in the official MCP registry): a key used with the wrong method or the wrong
kind of evidence is refused by `classifier.validate`, so a page cannot be made to claim
a registry listing and a listing cannot be made to say what a page says.

Data only. Adding a key is a code review, never a runtime change.
"""
from __future__ import annotations

METHODS = ('mcp', 'api_key', 'oauth', 'browser_signin')

METHOD_TITLES = {
    'mcp': 'MCP server',
    'api_key': 'API key',
    'oauth': 'Sign-in for apps (OAuth)',
    'browser_signin': 'Browser sign-in',
}

# Maintained guidance (spec Screen flow step 2), one line per method.
METHOD_GUIDANCE = {
    'mcp': 'An MCP server lets an agent use the service as a tool.',
    'api_key': 'An API key lets Clayrune run supported direct operations on your account.',
    'oauth': 'OAuth lets you grant Clayrune limited access without sharing your password.',
    'browser_signin': 'Browser sign-in is for interactive access through a saved, signed-in browser.',
}

# key -> (method, evidence kind, the sentence). The sentences claim only what the
# evidence kind can show, and say "mentions" / "lists", never "supports".
USAGE = {
    'mcp_registry_listing': ('mcp', 'registry', 'A server with a matching name is listed in the official MCP registry.'),
    'mcp_on_page': ('mcp', 'page', 'The page mentions an MCP server.'),
    'api_key_docs': ('api_key', 'page', 'The page mentions API keys or developer access.'),
    'api_key_account': ('api_key', 'page', 'The page mentions creating a key in an account or settings area.'),
    'oauth_app_signin': ('oauth', 'page', 'The page mentions letting apps sign in to your account.'),
    'oauth_docs': ('oauth', 'page', 'The page mentions OAuth or an authorization flow for developers.'),
    'signin_page': ('browser_signin', 'page', 'The page has a sign-in for people.'),
    'account_login': ('browser_signin', 'page', 'The page mentions logging in to an account.'),
}

# What the person is told for the registry relation (computed in code, not by the model).
RELATION_WORDS = {
    'own_domain': 'listed under the service\'s own domain name',
    'claims_site': 'the listing says it belongs to this service; anyone can write that, so it is not confirmed',
    'name_match': 'matched by name only; not confirmed to be from this service',
}


def describe() -> str:
    """The key table as the classifier prompt shows it (key: method, source)."""
    return '\n'.join(f'- {k}  (method {m}, evidence from a {kind})' for k, (m, kind, _t) in USAGE.items())
