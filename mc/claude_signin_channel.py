"""How Claude sign-in is carried: ``claude_signin_channel`` (backlog 1d940d0f).

``auto`` (default) is today's behaviour — the server spawns a piped
``claude auth login``, surfaces its OAuth URL, and the user pastes the
one-time code back into the dashboard (``/auth-login-remote/code``).

``terminal`` is for a hosted pod: Clayrune's service is not allowed to
carry Claude sign-in material, so the only route is a real PTY terminal
(MC-928) where the code is typed into the CLI's own prompt, and the code
relay refuses. Rationale: clayrune-cloud/docs/research/SIGNIN_CHANNEL_REVIEW.md.

Config-only (not in the Settings panel). Kept apart from agent_routes.py so
the policy is one small readable unit.
"""

from mc import state

CHANNELS = ('auto', 'terminal')

CODE_RELAY_REFUSAL = (
    'Pasting a sign-in code into Clayrune is disabled on this install '
    '(claude_signin_channel=terminal). Sign in through the terminal that '
    'opens from "Sign in": the one-time code is typed into the Claude CLI '
    'itself, not into this dashboard.')

NO_PTY_ERROR = (
    'claude_signin_channel=terminal requires a real-PTY terminal, and none is '
    'available on this machine (on Windows: pip install pywinpty). Claude '
    'sign-in is not offered another way on this install.')


def signin_channel() -> str:
    """Configured channel, normalised. Unset/blank -> 'auto' (the default).
    A NON-blank unknown value -> 'terminal': this key exists to forbid the
    code relay, so a typo ('termnal') must land on the stricter side."""
    v = str(state.CONFIG.get('claude_signin_channel') or 'auto').strip().lower()
    return v if v in CHANNELS else 'terminal'


def terminal_only() -> bool:
    return signin_channel() == 'terminal'
