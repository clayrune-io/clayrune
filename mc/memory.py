"""Memory / Scribe / Condense engine — mop-up extraction (non-blueprint).

The most load-bearing code in the repo: the server-side memory pipeline
(docs/MEMORY_SYSTEM_SPEC.md, docs/CONDENSE_STRUCTURED_DESIGN.md). Moved VERBATIM
out of server.py to drive it toward its <2,000-line target. ZERO behavior
change — a PURE MOVE. The ONLY mechanical edit applied to the moved bodies is
`CONFIG` -> `state.CONFIG` (the live-alias rewrite, the 1.7/1.10/1.11
precedent); every other name resolves identically (state/core names imported
by name; the dispatch-family + path/Popen deps late-bound via wire()).

LOAD-BEARING DISCIPLINE (CLAUDE.md "Memory system"): the MEMORY.md write path is
leaf-locked + atomic. `_commit_managed_entry` (completion / checkpoint /
reconcile) and `_condense_apply` (structured Leg C) are co-equal writers — both
take the SAME per-project `state._get_mem_write_lock`, both write via
`core._atomic_write_text`, both route archive overflow through the shared
`_append_to_archive`. The `<!-- clayrune:wm:<sid> ... -->` watermark markers and
the `<!-- clayrune:managed:begin/end -->` sentinels are load-bearing — never
altered. The permanent archive is append-only cold storage — never truncated.

NO import cycle: this module imports leaf modules only (mc.state, mc.core,
agent_runtime, distiller, stdlib). It NEVER imports server or any blueprint.
Cross-family deps (dispatch helpers + path/config roots) arrive via wire(),
called once by server.py before the blueprints' own wire() stanzas resolve the
memory values they pass on.
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
import hashlib
import json
import os
import re
import subprocess
import tempfile
import math as _math
import threading
import time as _time
import uuid
from contextvars import ContextVar

import mc.agent_runtime as _agent_runtime  # multi-provider runtime (transcript + oneshot)
import mc.skills as _skills                # frontmatter parse for position notes
import mc.distiller as _distiller          # Phase 4 learning observer (best-effort)
import mc.allowance_state as _allowance_state  # live vendor exhaustion state (MC-964 Step D.1)

from mc import state
from mc.core import _atomic_write_text, _log, now_iso, TimestampedLines
from mc.state import (
    _checkpoint_guard,
    _checkpoint_inflight,
    _checkpoint_sema,
    _checkpoint_sema_guard,
    _condense_lock,
    _condense_status,
    _condense_triggered_at,
    _condensing_projects,
    _get_mem_write_lock,
    _scribe_lock,
    _scribing_projects,
    agent_sessions,
)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
# Path/config roots stay home in server.py (other families still read them);
# the dispatch-family fns live in agent_routes (1.12) / project_routes (1.11).
# All late-bound here (the 1.7 SESSION_LABELS_PATH wired-placeholder pattern +
# the 1.10/1.11/1.12 cross-family call seams). CONFIG is NOT wired — it is read
# live via state.CONFIG.
DATA_DIR: Path = None  # type: ignore[assignment]
MEMORY_DIR: Path = None  # type: ignore[assignment]
CLAUDE_HOME: Path = None  # type: ignore[assignment]
_SESSION_SIZE_LIMIT: int = 0
_POPEN_FLAGS: int = 0
_STARTUPINFO = None
# dispatch-family call seams (agent_routes 1.12 / project_routes 1.11). Typed as
# Callable (the 1.13 scheduler_routes precedent) so the placeholder None doesn't
# trip pyright reportOptionalCall at the verbatim call sites below.
load_project: Callable[[str], Optional[dict]] = None  # type: ignore[assignment]
get_manager: Callable[[str], Any] = None  # type: ignore[assignment]
_resolve_claude: Callable[[], str] = None  # type: ignore[assignment]
_register_process: Callable[..., Any] = None  # type: ignore[assignment]
_read_agent_stream: Callable[..., Any] = None  # type: ignore[assignment]
_hide_windows_delayed: Callable[[int], Any] = None  # type: ignore[assignment]
# Session-end topics-digest refresh. Wired by server.py (never imported here —
# mc.memory must not import a blueprint). None = feature absent, hook no-ops.
_topics_refresh_hook: Callable[[str], Any] | None = None
# Injected provider-neutral canonical Scribe reader. None preserves the legacy
# provider transcript/log path exactly; the composition root owns cutover policy.
_canonical_scribe_reader: Callable[[str, dict], Any] | None = None
# Provider-neutral, toolless model seam.  The composition root may inject the
# existing agent_runtime.run_text_transform implementation; leaving it unset
# uses that implementation directly.  ``_scribe_call`` remains the legacy
# compatibility hook for tests and callers that do not have authoritative
# provider context.
_text_transform: Callable[..., str] | None = None
_transform_context: ContextVar[dict[str, Any] | None] = ContextVar(
    'memory_transform_context', default=None)


def wire(*, data_dir, memory_dir, claude_home, session_size_limit,
         popen_flags, startupinfo, load_project_fn, get_manager_fn,
         resolve_claude_fn, register_process_fn, read_agent_stream_fn,
         hide_windows_delayed_fn, topics_refresh_hook=None,
         canonical_scribe_reader_fn=None, text_transform_fn=None):
    """Late-bind path/config roots + dispatch-family deps. Called once by
    server.py BEFORE the blueprint wire() stanzas that pass memory.* values
    (agent_routes' write_session_memory_fn/scribe_call_fn/dispatch_condense_fn
    /..., project_routes' get_memory_path_fn, guide_routes' memory_search_fn).
    """
    global DATA_DIR, MEMORY_DIR, CLAUDE_HOME, _SESSION_SIZE_LIMIT
    global _POPEN_FLAGS, _STARTUPINFO
    global load_project, get_manager, _resolve_claude, _register_process
    global _read_agent_stream, _hide_windows_delayed, _topics_refresh_hook
    global _canonical_scribe_reader, _text_transform
    DATA_DIR = data_dir
    MEMORY_DIR = memory_dir
    CLAUDE_HOME = claude_home
    _SESSION_SIZE_LIMIT = session_size_limit
    _POPEN_FLAGS = popen_flags
    _STARTUPINFO = startupinfo
    load_project = load_project_fn
    get_manager = get_manager_fn
    _resolve_claude = resolve_claude_fn
    _register_process = register_process_fn
    _read_agent_stream = read_agent_stream_fn
    _hide_windows_delayed = hide_windows_delayed_fn
    # Optional: session-end topics-digest refresh. Wired rather than imported so
    # mc.memory keeps its no-blueprint-imports invariant. None = no-op.
    _topics_refresh_hook = topics_refresh_hook
    _canonical_scribe_reader = canonical_scribe_reader_fn
    _text_transform = text_transform_fn


def _provider_transform(provider: str, model: str, instruction: str,
                        body: str, *, cwd: str | None = None,
                        effort: str = '') -> str:
    """Run one provider-selected, toolless transform through the runtime seam.

    The provider and model are caller-owned facts; this helper never guesses a
    provider.  ``text_transform_fn`` is injectable for composition/tests and
    defaults to ``agent_runtime.run_text_transform``.  It intentionally does
    not replace ``_scribe_call``: callers without an authoritative provider
    continue through that compatibility hook.
    """
    fn = _text_transform or _agent_runtime.run_text_transform
    return str(fn(provider, prompt=instruction, model=model,
                  effort=effort, stdin_text=body, cwd=cwd, max_turns=1) or '')


def _model_call(model: str, instruction: str, body: str) -> str:
    """Call a model with exact context when one is active, else legacy hook."""
    ctx = _transform_context.get()
    if not ctx:
        return _scribe_call(model, instruction, body)
    return _provider_transform(
        str(ctx['provider']), model, instruction, body,
        cwd=ctx.get('cwd'), effort=str(ctx.get('effort') or ''))


def _with_transform_context(provider: str | None, cwd: str | None = None,
                            effort: str = ''):
    """Return a context manager-like token pair for exact provider facts."""
    if not provider:
        return None
    return _transform_context.set({
        'provider': str(provider).strip().lower(),
        'cwd': cwd,
        'effort': effort,
    })


def _reset_transform_context(token) -> None:
    if token is not None:
        _transform_context.reset(token)


def _explicit_project_provider(project: dict) -> str | None:
    """Return only a provider explicitly owned by this project.

    Condense and other project-wide jobs may not infer a provider from a
    missing global/default setting in this leaf.  A missing value deliberately
    preserves the legacy `_scribe_call` compatibility path until composition
    supplies an authoritative project engine.
    """
    value = project.get('provider') if isinstance(project, dict) else None
    value = str(value or '').strip().lower()
    return value or None


_CLAUDE_MODEL_ALIASES = frozenset({'haiku', 'sonnet', 'opus'})


def _model_for_provider(config_key: str, provider: str | None,
                        *, default: str = 'haiku') -> str:
    """Resolve a configured transform model without crossing provider lines.

    Scribe/condense historically use Claude tier names as their defaults.  An
    explicit non-Claude session must not inherit those names: they are not
    portable CLI model ids and can turn a harmless memory write into a failed
    provider invocation.  A foreign provider receives an explicit configured
    model only when its runtime positively recognises it; otherwise its native
    default is selected by passing an empty model.
    """
    configured = str(state.CONFIG.get(config_key, '') or '').strip()
    normalized_provider = str(provider or '').strip().lower()
    if normalized_provider in ('', 'claude'):
        return configured or default
    if (not configured or configured.lower() in _CLAUDE_MODEL_ALIASES
            or configured.lower().startswith('claude-')):
        return ''
    try:
        runtime = _agent_runtime.get_runtime(normalized_provider)
        supported = getattr(runtime, 'model_supported', None)
        if callable(supported) and supported(configured):
            return configured
    except Exception as e:
        _log(f"[scribe] model compatibility check failed for "
             f"provider={normalized_provider}: {e}")
    return ''


def _encode_project_path(project_path):
    """Encode a project path to Claude Code's ~/.claude/projects/<encoded>
    directory name.  C:\\Users\\foo\\bar  →  C--Users-foo-bar.

    Returns None when the path is empty or cannot be resolved (callers
    treat that as "no transcript dir").  Extracted from four inline
    duplicates (IMPROVEMENT_PLAN_V2.md P1-2); the underscore→dash
    fallback some callers also try stays at the call site since not all
    of them want it.
    """
    if not project_path:
        return None
    try:
        resolved = str(Path(project_path).resolve())
    except Exception:
        return None
    return resolved.replace(':', '-').replace('\\', '-').replace('/', '-')


def _session_transcript_path(project_path, claude_session_id):
    """Return the .jsonl transcript path for a Claude session, or None if it
    can't be found. Delegates to ClaudeRuntime.transcript_path() — the
    variant- and worktree-aware lookup (see _encoded_dir_candidates()) — so
    non-claude providers automatically return None and callers here don't
    miss sessions that live under a `_`/`.`-flattened or worktree-isolated
    transcript directory.

    NOTE: unlike the name suggests, this DOES check existence (transcript_path()
    only returns a Path that's actually on disk). _build_transcript_path()
    (path construction only, no lookup) was tried here first and silently
    broke auto-fresh on any install whose project path contains `_` or `.`
    (e.g. `_claude`, or the `.clayrune/agents/<sid>` worktree dirs) — the
    primary-only encoding never matched what the CLI actually wrote, so this
    always returned None and auto-fresh never fired. Fixed 2026-09-18.
    """
    return _agent_runtime.get_runtime('claude').transcript_path(  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (mop)
        project_path, claude_session_id)


def _transcript_image_bytes(path):
    """Total base64 payload bytes across every image block in a Claude
    transcript .jsonl — top-level message content and content nested inside
    a `tool_result` (screenshot/Read-image tool output), the two shapes seen
    in practice (verified 2026-09-18 against a live clayrune_website
    transcript: 14 `tool_result`-nested image blocks, 4.7 MB of `source.data`
    out of a 9.9 MB file).

    Used only by `_session_too_large`'s byte-based backstop, itself only
    consulted when no context-tokens figure is available
    (`_auto_fresh_trigger`) — a transcript's raw byte size is not what a
    model re-reads as context when most of those bytes are an inlined image,
    so counting them toward the resume-latency limit over-fires. Best-effort:
    an unparseable line or a missing `source.data` string just contributes 0,
    never raises.
    """
    def _block_bytes(b):
        src = b.get('source')
        data = src.get('data') if isinstance(src, dict) else None
        return len(data) if isinstance(data, str) else 0

    total = 0
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                msg = m.get('message') if isinstance(m, dict) else None
                content = msg.get('content') if isinstance(msg, dict) else None
                if not isinstance(content, list):
                    continue
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get('type') == 'image':
                        total += _block_bytes(b)
                    elif b.get('type') == 'tool_result':
                        c = b.get('content')
                        if isinstance(c, list):
                            for cb in c:
                                if isinstance(cb, dict) and cb.get('type') == 'image':
                                    total += _block_bytes(cb)
    except OSError:
        pass
    return total


def _session_too_large(project_path, claude_session_id):
    """Check if a session transcript exceeds the size limit, net of base64
    image payload bytes (see `_transcript_image_bytes` — those inflate raw
    file size without inflating the tokens a model actually re-reads, and
    this check is the resume-latency backstop, not a cost signal)."""
    p = _session_transcript_path(project_path, claude_session_id)
    if p and p.exists():
        try:
            size = p.stat().st_size - _transcript_image_bytes(p)
            return size > _SESSION_SIZE_LIMIT, size
        except OSError:
            pass
    return False, 0


def _long_session_advisory(s):
    """Advisory (NOT enforced): a long-running Mode-B session may be
    compacting away its own early-session context. Step 6 has captured that
    learning durably to MEMORY.md, so restarting the session reloads it
    fresh (a fresh process re-loads MEMORY.md + gets the read-floor) at
    near-zero loss. Distinct from _session_too_large (that's the 5 MB
    resume-perf HARD cap); this is turn-count keyed, fires far earlier, and
    is a soft human-in-loop nudge for Mode-B sessions only.
    SPEC docs/MEMORY_SYSTEM.md Open item #6.
    """
    if not state.CONFIG.get('long_session_advisory_enabled', True):
        return False
    if s.get('mode') != 'B':
        return False  # Mode A spawns per-turn — no persistent-process amnesia
    if s.get('housekeeping') or s.get('incognito'):
        return False
    if s.get('status') not in ('running', 'idle'):
        return False  # only a live session can be usefully restarted
    thr = int(state.CONFIG.get('long_session_advisory_turns', 25) or 25)
    return int(s.get('num_turns', 0) or 0) >= thr


def _resume_is_fragile(was_resume, resume_confirmed):
    """Decide whether a dead Mode B session that was a `-r` resume must be
    abandoned (fresh restart, losing the transcript) vs. resumed again.

    Only a resume that NEVER produced output is "fragile" — re-`-r`-ing it
    would just loop, so we go fresh. A resume that produced output is healthy:
    if it dies LATER (the AskUserQuestion `proc.kill()`, idle-eviction, or a
    crash) it must be resumed with `-r` so the conversation is preserved.

    Before this guard existed, ANY session that was ever a resume reset to a
    fresh, context-less session on its next process death — which is why an
    AskUserQuestion in a resumed session lost the whole conversation. See the
    followup respawn path and tests/test_resume_revival.py.
    """
    return bool(was_resume) and not bool(resume_confirmed)


def _extract_user_text(msg_field):
    """Extract plain user text from a jsonl message field, skipping tool_result blocks."""
    if not isinstance(msg_field, dict) or msg_field.get('role') != 'user':
        return ''
    content = msg_field.get('content', '')
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'text':
                texts.append(str(block.get('text', '')))
        return ' '.join(t.strip() for t in texts if t).strip()
    return ''


def _recent_claude_transcripts(project_path, limit=5, must_include_csids=None):
    """Scan the Claude transcript directory for a project.

    Returns [{session_id, mtime, first_user, last_user, turns, size}] sorted by mtime desc.
    Delegates to ClaudeRuntime.list_sessions() — scanning logic lives in the runtime.
    `must_include_csids`: session ids that must survive the mtime cut even if
    they don't rank in the freshest `limit` (see list_sessions docstring, D6).
    """
    return _agent_runtime.get_runtime('claude').list_sessions(  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (mop)
        project_path, limit=limit, must_include_csids=must_include_csids)


def _find_transcript_file(project_path, claude_session_id):
    """Locate the Claude Code transcript JSONL for a given csid, or None.
    Delegates to ClaudeRuntime.transcript_path() — path logic lives in the runtime.

    Worktree fallback (b264200a): the CLI keys its transcript directory on the
    process CWD, so an agent isolated in `<project>/.clayrune/agents/<sid>/`
    writes its transcript under the WORKTREE-encoded path, not the project's.
    Without this fallback every isolated session would be invisible to Scribe
    (no memory captured), to resume, and to /reconstruct. Resolved centrally
    here so all ~9 call sites are fixed at once; the scan only runs when the
    primary lookup misses, and only over worktree dirs that actually exist.
    """
    f = _agent_runtime.get_runtime('claude').transcript_path(
        project_path, claude_session_id)
    if f:
        return f
    try:
        agents_dir = Path(project_path) / '.clayrune' / 'agents'
        if not agents_dir.is_dir():
            return None
        for wt in agents_dir.iterdir():
            if not wt.is_dir():
                continue
            f = _agent_runtime.get_runtime('claude').transcript_path(
                str(wt), claude_session_id)
            if f:
                return f
    except Exception:
        pass
    return None


# The persona marker MC injects into a hired agent's prompt context. Only the
# LONG form is a persona: `_build_agent_context` emits it when, and only when,
# a character is resolved. The SHORT form ("Your name is Vector.") is the
# inherited project/global default agent — matching that would stamp every
# ordinary chat as if someone had hired the default, which is the bug this
# exists to undo, not a fix for it.
#
# Matched against BYTES so a chunk boundary can never corrupt the decode, and
# only the captured name is decoded. Held as a literal of the emitted string:
# if the prompt builder's wording changes this simply stops matching and rows
# go unstamped — a miss, never a wrong face on someone's chat.
_PERSONA_MARKER_RE = re.compile(
    rb'Your name is ([^.\r\n"]{1,32})\. '
    rb'Use it when you introduce yourself or sign off')

# Scan the whole transcript, not a prefix. Measured 2026-09-11: in a 4.4 MB
# Mode-A chat the first marker sits at byte 2,295,484 — MC re-injects the
# context into a later USER turn, and the early records carry none of it, so
# any small head window reports "no persona" for exactly the long-running
# chats whose identity matters most. Sequential scan with an early exit on the
# first match; the cap is a backstop against a pathological file, above the
# largest transcript on this box (22 MB).
_PERSONA_SCAN_MAX_BYTES = 32 * 1024 * 1024
_PERSONA_SCAN_CHUNK = 1 << 20
# Longest possible match, so a marker straddling two chunks is still seen.
_PERSONA_SCAN_OVERLAP = 128


def _agent_name_in_transcript(path):
    """The self-chosen name a session ran under, or '' — read from its
    transcript. Never raises: an unreadable transcript is a miss."""
    try:
        with open(path, 'rb') as fh:
            tail = b''
            read = 0
            while read < _PERSONA_SCAN_MAX_BYTES:
                chunk = fh.read(_PERSONA_SCAN_CHUNK)
                if not chunk:
                    break
                read += len(chunk)
                m = _PERSONA_MARKER_RE.search(tail + chunk)
                if m:
                    return m.group(1).decode('utf-8', 'replace').strip()
                tail = chunk[-_PERSONA_SCAN_OVERLAP:]
    except Exception as e:
        _log(f"[persona-scan] {path}: {e}")
    return ''


def persona_ref_for_session(project_path, claude_session_id):
    """`{'name', 'scope'}` for the persona a past chat ran as, or None.

    The character is normally recorded on the agent-log row at dispatch; this
    recovers it for rows that have none — the transcript-synthesized ones
    (MC-946), which otherwise render as the default agent and make every
    hired agent's chat history look like it was someone else's.

    Fails soft in both halves: no transcript, no marker, or a persona that has
    since been deleted or renamed all return None, and the row stays unstamped
    rather than being stamped wrong.
    """
    f = _find_transcript_file(project_path, claude_session_id)
    if not f:
        return None
    agent_name = _agent_name_in_transcript(f)
    if not agent_name:
        return None
    try:
        from mc import characters as _characters
        return _characters.ref_by_agent_name(agent_name, project_path=project_path)
    except Exception as e:
        _log(f"[persona-scan] character lookup for {agent_name!r} failed: {e}")
        return None


def _parse_transcript_messages(f, max_messages=2000):
    """Parse a Claude Code JSONL transcript into [{role, text, tool, timestamp}] for read-only display.

    role: 'user' | 'assistant' | 'tool_call'
    Returns at most max_messages entries; on overflow, keeps the TAIL (most
    recent) — see ClaudeRuntime.parse_transcript_file() for the rationale.
    """
    return _agent_runtime.get_runtime('claude').parse_transcript_file(f, max_messages=max_messages)  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (mop)


def _native_memory_path(project_path):
    """Derive the Claude Code native MEMORY.md path for a project.

    Claude stores memory at ~/.claude/projects/<encoded-path>/memory/MEMORY.md
    where the path encoding replaces : and path separators with -.
    """
    encoded = _encode_project_path(project_path)
    if not encoded:
        return None
    mem_path = CLAUDE_HOME / encoded / 'memory' / 'MEMORY.md'
    # Claude Code may also replace underscores with dashes — check both
    # and prefer whichever was modified most recently
    encoded_alt = encoded.replace('_', '-')
    if encoded_alt != encoded:
        alt_path = CLAUDE_HOME / encoded_alt / 'memory' / 'MEMORY.md'
        if alt_path.exists() and mem_path.exists():
            if alt_path.stat().st_mtime > mem_path.stat().st_mtime:
                return alt_path
        elif alt_path.exists():
            return alt_path
    return mem_path


def _get_memory_path(project):
    """Get the memory file path for a project — native Claude path preferred, fallback to MC data dir."""
    pp = project.get('project_path', '')
    if pp:
        native = _native_memory_path(pp)
        if native:
            return native
    return MEMORY_DIR / f'{project["id"]}.md'


def _get_archive_path(project):
    """Get the MEMORY_ARCHIVE.md path — sibling to the project's MEMORY.md."""
    mem_path = _get_memory_path(project)
    return mem_path.parent / 'MEMORY_ARCHIVE.md'


SESSION_LOG_FILE = 'SESSION_LOG.md'


def _get_session_log_path(project):
    """Get the SESSION_LOG.md path — sibling to the project's MEMORY.md.

    MEMORY_DESIGN_V2_SPEC.md §16 step 4 / §9.1: the sentinel-delimited managed
    region (the '## Session Log' block: `- [` entries + `clayrune:wm:<sid>`
    watermarks) lives HERE now, not inside MEMORY.md. Not auto-loaded by the
    CLI and not injected by `_build_agent_context` — it is a plain sibling
    file the memory-search corpus still reads (see `_mem_corpus`'s
    `session_log_name` param), same as `MEMORY_ARCHIVE.md`.
    """
    mem_path = _get_memory_path(project)
    return mem_path.parent / SESSION_LOG_FILE


_MEM_BEGIN = '<!-- clayrune:managed:begin -->'


_MEM_END = '<!-- clayrune:managed:end -->'


_MEM_LOG_HEADER = '## Session Log'


_MEM_WM_PREFIX = '<!-- clayrune:wm:'


def _mem_split_full(content):
    """Split MEMORY.md into (curated_text, [entry_lines], [wm_marker_lines]).

    Managed region = sentinel-delimited (or a legacy bare '## Session Log').
    `entries` = lines starting with '- [' (curated pointer lines, also
    '- [...]', are never collected — they're above the sentinel).
    `wm_markers` = full lines starting with the Step-6 watermark prefix.
    Pure function.
    """
    content = content or ''
    if _MEM_BEGIN in content and _MEM_END in content:
        i = content.index(_MEM_BEGIN)
        j = content.index(_MEM_END, i)
        curated = content[:i].rstrip()
        mid = content[i + len(_MEM_BEGIN):j]
    elif _MEM_LOG_HEADER in content:
        i = content.index(_MEM_LOG_HEADER)
        curated = content[:i].rstrip()
        mid = content[i + len(_MEM_LOG_HEADER):]
    else:
        return content.rstrip(), [], []
    entries, wm = [], []
    for ln in mid.splitlines():
        s = ln.strip()
        if s.startswith('- ['):
            entries.append(ln)
        elif s.startswith(_MEM_WM_PREFIX):
            wm.append(s)
    return curated, entries, wm


def _mem_split(content):
    """Back-compat 2-tuple (curated, entries) — every pre-Step-6 caller uses
    this. wm markers are dropped from the return but NOT from the file (the
    write path uses _mem_split_full + _mem_compose(..., wm) to preserve them).
    """
    c, e, _w = _mem_split_full(content)
    return c, e


def _mem_compose(curated, entries, wm_markers=None):
    """Rebuild canonical MEMORY.md from curated + entry lines (+ optional wm
    markers). Always one sentinel-delimited managed region. With wm_markers
    falsy, output is byte-identical to the pre-Step-6 form (existing callers
    unaffected). wm markers are emitted after entries, before the END sentinel.
    """
    curated = (curated or '').rstrip()
    block = f'{_MEM_BEGIN}\n{_MEM_LOG_HEADER}\n'
    body = '\n'.join(entries)
    if body:
        block += body + '\n'
    if wm_markers:
        block += '\n'.join(wm_markers) + '\n'
    block += f'{_MEM_END}\n'
    return (curated + '\n\n' + block) if curated else block


def _mem_migrate(content):
    """Idempotent, additive migration to the Leg 0 canonical format.

    Already-migrated content round-trips unchanged. Legacy bare
    '## Session Log' sections get wrapped in sentinels. Files with no managed
    content gain an empty managed region. Curated content is preserved
    verbatim (modulo trailing whitespace); curated lines are never reordered
    or dropped. wm markers (Step 6) are preserved.
    """
    return _mem_compose(*_mem_split_full(content))


_MEM_WM_SUMMARY_CAP = 600


def _wm_line(rec):
    """Build the single physical marker line for a watermark record.

    rec keys: session_id, claude_session_id, transcript_path, byte_offset,
    slice_hash, running_summary. running_summary is sanitized to stay on one
    line and not prematurely close the HTML comment.
    """
    sid = str(rec.get('session_id', ''))
    safe = dict(rec)
    rs = str(safe.get('running_summary', '') or '')
    rs = rs.replace('\n', ' ').replace('\r', ' ').replace('-->', '—>')
    safe['running_summary'] = rs[:_MEM_WM_SUMMARY_CAP]
    js = json.dumps(safe, separators=(',', ':'), ensure_ascii=False)
    return f"{_MEM_WM_PREFIX}{sid} {js} -->"


def _wm_parse(line):
    """Parse a marker line back to a record dict, or None if malformed."""
    line = (line or '').strip()
    if not line.startswith(_MEM_WM_PREFIX) or not line.endswith(' -->'):
        return None
    core = line[len(_MEM_WM_PREFIX):].rsplit(' -->', 1)[0]
    sp = core.split(' ', 1)
    if len(sp) != 2:
        return None
    try:
        rec = json.loads(sp[1])
        return rec if isinstance(rec, dict) else None
    except Exception:
        return None


def _wm_find(wm_markers, session_id):
    """Return the parsed record for session_id from a wm_markers list, or None."""
    for ln in wm_markers or []:
        r = _wm_parse(ln)
        if r and str(r.get('session_id', '')) == str(session_id):
            return r
    return None


def _wm_upsert(wm_markers, rec):
    """Return a new wm_markers list with rec's session replaced (or appended)."""
    sid = str(rec.get('session_id', ''))
    kept = [ln for ln in (wm_markers or [])
            if (_wm_parse(ln) or {}).get('session_id') != sid]
    kept.append(_wm_line(rec))
    return kept


def _wm_remove(wm_markers, session_id):
    """Return a new wm_markers list without session_id's marker (teardown)."""
    sid = str(session_id)
    return [ln for ln in (wm_markers or [])
            if (_wm_parse(ln) or {}).get('session_id') != sid]


# BM25 parameters (standard defaults; k1 saturates term frequency, b controls
# how hard document length is normalized).
_BM25_K1 = 1.2
_BM25_B = 0.75

# How many times a topic file's own name is indexed into its token stream —
# a cheap field boost, since the filename is the note's title (see _mem_corpus).
_TITLE_BOOST = 3


# ── Tunable ranker constants (S4, 2026-08-16) ────────────────────────────────
# Each reads config and DEFAULTS TO THE CONSTANT ABOVE, so landing this code is a
# measurable no-op and each lever can be judged and landed on its own.
#
# TRAP, verified: `update_config` filters on `if k in _CONFIG_EDITABLE_KEYS`, so a
# PUT of an unlisted key returns 200 with `updated: []` — it looks like it worked
# and does nothing. Every key here MUST also be in server.py's defaults dict AND
# in _CONFIG_EDITABLE_KEYS.
#
# These were measured on ONE project while config is global to all of them, so
# treat an offline win here as a hypothesis about the other projects, not a fact
# about them. tools/memory-eval/sweep_constants.py checks across projects.

def _bm25_b():
    """Length-normalisation strength. 1.0 normalises fully."""
    try:
        return float(state.CONFIG.get('bm25_b', _BM25_B))
    except (TypeError, ValueError):
        return _BM25_B


def _title_boost():
    """Filename repetitions folded into a topic file's token stream."""
    try:
        return max(0, int(state.CONFIG.get('bm25_title_boost', _TITLE_BOOST)))
    except (TypeError, ValueError):
        return _TITLE_BOOST


def _archive_quota():
    """Max top-k slots archive-class units may occupy. 0 = off (today).

    The archive outnumbers topic files ~30:1, so it can crowd the slots that
    would otherwise carry a whole note. A quota reserves room for topic files
    without changing the ranking itself.
    """
    try:
        return max(0, int(state.CONFIG.get('read_floor_archive_quota', 0) or 0))
    except (TypeError, ValueError):
        return 0

# Link-expansion decay: a neighbour reached by traversing one `[[wikilink]]` hop
# from a BM25 hit inherits a fraction of that hit's score. Out-links (the note
# itself points there — an authored "read this too") rank above back-links (some
# other note points here), which is the same asymmetry Obsidian's UI implies by
# putting outgoing links in the body and backlinks in a side panel.
_LINK_DECAY_OUT = 0.5
_LINK_DECAY_IN = 0.35

# Parsed corpus cache: mem_dir -> {filename: ((mtime_ns, size), units)}.
# PER-FILE (MEMORY_DESIGN_V2_SPEC.md Condition 53): a write to one topic note
# invalidates only that file's cached units and re-tokenizes just it; every
# other file's units are reused as-is. The prior scheme keyed the whole cache
# on one signature tuple over every *.md file, so ANY write anywhere
# invalidated the lot and re-tokenized the entire vault — cheap at ~100 notes,
# measured to cost ~1.19s on the dispatch critical path at 17MB/~1,700 notes
# (spec §13.3 Break 1). Without SOME cache the read floor re-tokenizes the
# whole vault on every single dispatch; this keeps that guarantee per-file.
_memsearch_cache: dict = {}
_memsearch_cache_lock = threading.Lock()


def _mem_tokens(text):
    """Tokenizer shared by queries and documents — MUST be the same for both.

    Splits on non-alphanumerics INCLUDING underscore, so `memory_system` yields
    `memory`+`system` and `mc/memory.py` yields `mc`+`memory`+`py`. That matters
    here: this corpus is mostly snake_case identifiers and file paths, and a
    query of "memory system" has to reach a note whose text says
    `project_memory_system_redesign`.
    """
    import re  # module has no top-level `re` import (see _re_auth pattern)
    return [t for t in re.findall(r'[a-z0-9]+', (text or '').lower())
            if len(t) >= 3]


def _mem_link_key(s):
    """Canonical key for matching a `[[wikilink]]` target to a topic filename.

    The vault's slugs drifted: filenames are snake_case (`arch_mobile_ui.md`),
    links were written kebab (`[[arch-mobile-ui]]`), and some frontmatter
    `name:` fields are free prose. Rather than demand one true spelling from
    every future note, matching ignores every non-alphanumeric character —
    `arch-mobile-ui`, `arch_mobile_ui` and `Arch Mobile UI` all key the same.

    R2 (MEMORY_DESIGN_V2_SPEC.md §4.4, Condition class A): a trailing `.md` is
    stripped BEFORE that non-alphanumeric strip, not after — the old order
    dropped only the dot and kept the letters, so `[[x.md]]` keyed as `xmd`
    and never matched the `x` key every other call site derives from a bare
    filename stem. Resolution bug fix, not a new authoring convention: no
    note or link needs to change for this to repair itself.
    """
    import re  # module has no top-level `re` import (see _re_auth pattern)
    s = (s or '').strip()
    if s.lower().endswith('.md'):
        s = s[:-3]
    return re.sub(r'[^a-z0-9]', '', s.lower())


def _mem_link_targets(text):
    """Extract `[[wikilink]]` targets from a note body (alias/anchor stripped).

    Code spans and fenced blocks are stripped first: a note that DOCUMENTS the
    link syntax (this system's own notes do) would otherwise register `[[…]]`
    written inside backticks as a real edge, and then report itself dangling.
    Same rule Obsidian applies.
    """
    import re
    body = re.sub(r'```.*?```', ' ', text or '', flags=re.S)
    body = re.sub(r'`[^`\n]*`', ' ', body)
    out = []
    for raw in re.findall(r'\[\[([^\[\]]+)\]\]', body):
        tgt = raw.split('|', 1)[0].split('#', 1)[0].strip()
        if tgt:
            out.append(tgt)
    return out


def _mem_link_graph(units):
    """Build the vault's link graph over topic units.

    Returns {filename: {'out': [...], 'in': [...]}} where every entry is a
    topic filename that actually exists. Unresolvable targets are dropped here
    — `tools/memory-link-check.py` is what reports them, so a dangling link
    costs retrieval nothing but is still visible to a human.

    Only topic files participate: archive/managed units are lines out of a
    container file, so a link found in one has no single owning note to hop
    back to.
    """
    by_key = {}
    for u in units:
        if u.get('cls') == 'topic':
            by_key.setdefault(_mem_link_key(u['file'].rsplit('.', 1)[0]), u['file'])
    graph = {f: {'out': [], 'in': []} for f in by_key.values()}
    for u in units:
        if u.get('cls') != 'topic':
            continue
        src = u['file']
        for tgt in u.get('links') or []:
            dst = by_key.get(_mem_link_key(tgt))
            if not dst or dst == src:
                continue
            if dst not in graph[src]['out']:
                graph[src]['out'].append(dst)
            if src not in graph[dst]['in']:
                graph[dst]['in'].append(src)
    # Condition 17 / §16 step 6: a `supersedes:` edge is declared by the
    # SUCCESSOR and reuses the OUT hop slot — the successor "points at" what
    # it replaced, the same authored-pointer shape as a `[[wikilink]]`. No new
    # decay tier, no new slot: `_mem_expand_links` needs no change to reach a
    # superseded predecessor from its successor (or the successor from the
    # predecessor, via IN) — that is the "priority expansion in one of the
    # two existing hop slots" the build sequence calls for.
    for f, node in _mem_supersede_graph(units).items():
        dst = node.get('supersedes')
        if not dst or dst == f or dst not in graph or f not in graph:
            continue
        if dst not in graph[f]['out']:
            graph[f]['out'].append(dst)
        if f not in graph[dst]['in']:
            graph[dst]['in'].append(f)
    return graph


_SUPERSEDE_HEAD_DEPTH_CAP = 8


def _mem_supersede_graph(units):
    """Build the supersedes/superseded_by graph over topic units (Condition 17,
    MEMORY_DESIGN_V2_SPEC.md §6.1-6.3, MC-944 step 6).

    `supersedes: <slug>` is written ONLY by the successor at mint/edit time
    (`write_topic_note`'s `supersedes` kwarg); the back edge
    (`superseded_by`) is derived HERE, every call, and is never written to
    disk — matching the in-repo backlog-link precedent §6.1 cites ("only the
    direction you post is stored; the inverse is rendered automatically").

    A target that doesn't resolve to a real topic file — a typo, or the
    `unresolved` sentinel §6.5/Condition 22 reserves for MC-944 step 7's
    mint (not built in this step) — is silently dropped, the same posture
    `_mem_link_graph` already takes for a dangling `[[wikilink]]`.

    Returns {filename: {'supersedes': predecessor_file_or_None,
                         'superseded_by': [successor_file, ...]}} for every
    topic file, so callers can test membership without a `.get(x, {})` guard.
    """
    by_key = {}
    for u in units:
        if u.get('cls') == 'topic':
            by_key.setdefault(_mem_link_key(u['file'].rsplit('.', 1)[0]), u['file'])
    edges = {f: {'supersedes': None, 'superseded_by': []} for f in by_key.values()}
    for u in units:
        if u.get('cls') != 'topic':
            continue
        src = u['file']
        raw = str(u.get('supersedes') or '').strip()
        if not raw or raw == 'unresolved':
            continue
        dst = by_key.get(_mem_link_key(raw))
        if not dst or dst == src:
            continue
        edges[src]['supersedes'] = dst
        edges[dst]['superseded_by'].append(src)
    return edges


def _mem_supersede_head(edges, n):
    """Follow `superseded_by` edges from `n` to its terminal HEAD (Condition
    17's `head(x)`) — cycle-guarded by a visited set, depth capped at 8 with
    a log line on hitting the cap (spec's own words: "cycle-guarded by a
    visited set, depth capped at 8 with a log line on hitting the cap").

    Two notes both declaring `supersedes: n` resolves deterministically to
    the lexicographically-first successor — an edge case the spec doesn't
    rule on (one predecessor is expected to gain one successor); picking
    deterministically keeps repeated calls agreeing with each other without
    extra state.

    Returns (head_file, hops_walked). `hops_walked == 0` means `n` is
    already STANDING or is itself a head.
    """
    visited = {n}
    cur = n
    depth = 0
    while depth < _SUPERSEDE_HEAD_DEPTH_CAP:
        succs = (edges.get(cur) or {}).get('superseded_by') or []
        if not succs:
            return cur, depth
        nxt = sorted(succs)[0]
        if nxt in visited:
            _log(f'[supersede] cycle detected walking from {n!r} at '
                 f'{cur!r} -> {nxt!r}; stopping at {cur!r}')
            return cur, depth
        visited.add(nxt)
        cur = nxt
        depth += 1
    _log(f'[supersede] depth cap ({_SUPERSEDE_HEAD_DEPTH_CAP}) hit walking '
         f'from {n!r}; stopping at {cur!r}')
    return cur, depth


_ARCH_LINE_RE = re.compile(r'^- \[(\d{4}-\d{2}-\d{2})\] \*\*(.*?)\*\*')


def _dedupe_archive_lines_legacy(lines):
    """Keep only the LAST archive entry per (day, task) — read-time only.

    RETIRED as the default 2026-09-24 (MC-964 Step A, RC1) — kept only as the
    `archive_dedupe_legacy_enabled` rollback lever. See
    `_dedupe_archive_lines_containment` for why and what replaced it.

    The Step-6 checkpointer appends a fresh session-log line every time it
    runs, so one long conversation leaves a trail of near-identical entries
    that supersede each other. Measured 2026-08-23 on this project:
    **1,684 of 2,222 archive lines (76%) are superseded**, one day/task group
    reaching 47 copies, and 1,561 of the 2,222 are `_(live)_` — mid-session
    checkpoints rather than finished runs.

    That is not merely wasteful, it is WRONG. The early lines in a group are
    the agent's first guess: for "do we have a /goal command?" the first entry
    says "found no /goal command" and the last says it is verified working. The
    ranker had no way to prefer the later one, so a perfectly-matching stale
    line could take every slot on the card.

    RC1 (2026-09-24): grouping by (day, task) alone over-generalised from "same
    answer, revised" to "same chat title" — a single long chat with 87 lines
    under one title silently deleted 86 of them, including a fact (the 2026-09-17
    Codex top-up) that had nothing to do with the line that outlived it. 2,079 of
    2,702 archive-wide hidden lines had over half their content words absent
    from the line that "superseded" them.

    Dedupe happens HERE, on the way into the corpus — never on the file. The
    archive is append-only cold storage and is never truncated (see the module
    docstring); this only changes what retrieval *sees*.

    Grouping key is (day, task). A task repeated on a DIFFERENT day is a
    genuinely separate occasion and is kept — 57 tasks recur across days.
    Within one day the only false merges are generic prompts ("ok", "Hi",
    "restarted"): 45 groups, 149 lines, all of them worthless as retrieval keys
    anyway.
    """
    last = {}          # (day, task) -> index of the most recent line
    order = []
    for ln in lines:
        m = _ARCH_LINE_RE.match(ln)
        key = (m.group(1), m.group(2)[:120]) if m else ('', ln)
        if key in last:
            order[last[key]] = None          # supersede the earlier one
        last[key] = len(order)
        order.append(ln)
    return [ln for ln in order if ln is not None]


def _archive_line_content(ln, m=None):
    """The part of an archive line after its bolded `**task**` — the part
    `_dedupe_archive_lines_containment` measures overlap over. Shared with the
    (day, task) grouping key's regex so both read the same match.
    """
    m = m or _ARCH_LINE_RE.match(ln)
    return ln[m.end():] if m else ln


# Measured 2026-09-24 against the live archive (4,342 lines; see
# docs/_journal/b2d85e51-memory-overhaul.md). Chain-resolved hidden-but-novel
# count (RC1's method — a dropped line whose content is over half absent from
# the line that FINALLY absorbed it, following multi-hop supersession chains
# to their end, not just the immediate absorber):
#   T=0.3 -> 1,246   T=0.4 -> 649   T=0.5 -> 137   T=0.6 -> 9   T=0.7 -> 0
# 0.6 is the chosen cutoff: comfortably under the <200 acceptance bar, while
# still hiding 1,365 of 4,342 lines archive-wide (0.7 hides only 1,164 for the
# same 0-novel result). At 0.6 the 2026-07-29 /goal stale-first-guess group
# (13 near-duplicate progress lines under one chat title) still collapses to
# 7 survivors — see tests/test_memory_archive_dedupe.py.
_ARCHIVE_DEDUPE_CONTAINMENT_THRESHOLD = 0.6


def _dedupe_archive_lines_containment(lines, threshold=_ARCHIVE_DEDUPE_CONTAINMENT_THRESHOLD):
    """Drop an earlier archive line only when a later line in the same
    (day, task) group substantially CONTAINS its content — the RC1 fix
    (MEMORY_OVERHAUL_PLAN.md #6 Step A) for `_dedupe_archive_lines_legacy`'s
    "same chat title" over-generalisation.

    Containment of an earlier line's content in a later one = the fraction of
    the earlier line's content words (the part after `**task**`, tokenized by
    `_mem_tokens` so both sides use the same vocabulary as the ranker) that
    also appear in the later line's content words. An earlier line is dropped
    only when that fraction reaches `threshold` against SOME later,
    still-surviving line in its (day, task) group — a group of genuinely
    distinct facts filed under one chat title now survives side by side
    instead of the newest line silently deleting the others. A line with no
    content (bare title, no `—` body) is treated as fully contained by
    anything, matching the legacy rule's behaviour for that edge case.

    Grouping key is unchanged from the legacy rule: (day, task[:120]).
    """
    order = list(lines)
    wordcache: dict = {}
    groups: dict = {}
    for idx, ln in enumerate(order):
        m = _ARCH_LINE_RE.match(ln)
        key = (m.group(1), m.group(2)[:120]) if m else ('', ln)
        wordcache[idx] = set(_mem_tokens(_archive_line_content(ln, m)))
        survivors = groups.setdefault(key, [])
        kept = []
        w = wordcache[idx]
        for j in survivors:
            wj = wordcache[j]
            contained = (not wj) or (len(wj & w) / len(wj)) >= threshold
            if contained:
                order[j] = None          # substantially contained in the later line
            else:
                kept.append(j)
        kept.append(idx)
        groups[key] = kept
    return [ln for ln in order if ln is not None]


def _dedupe_archive_lines(lines):
    """Corpus-build entry point (`_mem_file_units`). Content-containment
    dedupe is the default (MC-964 Step A); `archive_dedupe_legacy_enabled`
    is the rollback lever back to the retired last-wins rule.
    """
    if state.CONFIG.get('archive_dedupe_legacy_enabled', False):
        return _dedupe_archive_lines_legacy(lines)
    return _dedupe_archive_lines_containment(lines)


# ── Positions: what we decided NOT to do, and why ───────────────────────────
#
# Every capture path we have is downstream of an ARTIFACT — the checkpointer
# summarises what happened, the Scribe extracts from outcomes, the Distiller
# looks for recurrence. Deciding *not* to build something produces no commit,
# no file, no diff, so all three are structurally blind to it. Yet re-proposing
# a settled question costs a whole conversation.
#
# Demonstrated 2026-08-23: Ron named two such decisions ("we evaluated Obsidian
# and declined", "the nightly agent should be a simple cron job"). The Obsidian
# one WAS in the vault and in the always-loaded index — and the next turn the
# agent proposed adopting Obsidian anyway, then invented a justification for it.
# So this is not a storage gap. It is stored as *history*, and history does not
# fire when someone re-proposes the thing it settled.
#
# Hence a distinct note class. A position carries three things a topic note
# does not:
#   subject      — the thing it settles, plus the aliases someone would use
#   reason       — WHY. A bare verdict is dogma; a reason is checkable, and it
#                  is what lets the position be re-opened rather than obeyed.
#   expires_when — what would change our mind. Makes review a test rather than
#                  a re-read of the whole archive.
POSITION_PREFIX = 'position_'

# The subject is indexed this many extra times. A position must win its own
# subject decisively — losing it to an ordinary note that merely mentions the
# word is the exact failure this class exists to prevent. Higher than the topic
# title boost for that reason.
_POSITION_SUBJECT_BOOST = 6

# Fraction of a position's SUBJECT terms the query must contain before it takes
# a reserved slot. Coverage, not score: "is this task about the thing I
# settled?" is a different question from "did we share a word?".
# A position fires on explicit TRIGGER terms, not on a statistical threshold.
#
# Two gates were tried first and both were fragile. Raw term coverage cannot
# tell "should we adopt Obsidian?" from a bare "memory" — against the subject
# "Obsidian as the memory substrate" both cover exactly one term of three.
# IDF-weighting is better in principle and still wrong in practice: it makes
# firing depend on how often the word happens to appear elsewhere in the
# vault, so a position starts or stops working as unrelated notes are written.
#
# The same argument settled routing earlier today (AGENT_TYPES_DESIGN §6b/§7):
# deterministic rules beat inference precisely because a misfire is then a line
# you can read and fix. `triggers:` is that line. It defaults to the subject's
# own words, so most positions need nothing.
_POSITION_STOPWORDS = {
    'a', 'an', 'and', 'as', 'at', 'be', 'by', 'do', 'for', 'from', 'how', 'in',
    'is', 'it', 'of', 'on', 'or', 'our', 'should', 'so', 'that', 'the', 'to',
    'we', 'what', 'when', 'which', 'with',
}


def _position_triggers(rec):
    """Terms that make this position fire. Explicit `triggers:` wins; otherwise
    the subject's own words, minus the ones every sentence contains."""
    raw = str((rec or {}).get('triggers') or '')
    if raw.strip():
        toks = _mem_tokens(raw.replace(',', ' '))
    else:
        toks = _mem_tokens(str((rec or {}).get('subject') or ''))
    return {t for t in toks if t not in _POSITION_STOPWORDS and len(t) > 2}

# Below this many documents a term is rare no matter what the corpus size
# says. Without it the fraction test blocks everything on a small vault.
_POSITION_TRIGGER_MIN_DOCS = 5


def _position_trigger_max_df():
    """How common a SUBJECT-derived trigger may be and still fire a position.

    A fraction of the corpus. Measured 2026-08-24, the day delivery telemetry
    first had numbers: the MC-898 position fired on 108 of 188 real tasks — 57%
    — because its subject contains the word "agent", which appears in 32.7% of
    this corpus. The coverage gate was an OR over subject tokens, so one
    ubiquitous word was enough, and the position became exactly the permanent
    prompt furniture the gate exists to prevent.

    The English stopword list cannot fix this: "agent" is not a stopword, it is
    a word this project happens to say constantly. Commonness is a property of
    the corpus, so the test has to be too. At 0.10, the live positions keep
    obsidian (0.6%), substrate (0.3%), nightly (0.4%) and research (1.5%), and
    lose agent (32.7%) and memory (17.8%).

    An EXPLICIT `triggers:` list is exempt — a human naming a term is stating
    intent, and second-guessing it would make the field pointless.
    """
    try:
        return float(state.CONFIG.get('position_trigger_max_df', 0.10) or 0.10)
    except Exception:
        return 0.10


# Reserved top-k slots a position may take beyond the ordinary cut. Without
# this a strong subject match can still be crowded out by a busy query, which
# is how the Obsidian note lost — it was present, and it never surfaced.
def _position_reserve():
    try:
        return max(0, int(state.CONFIG.get('read_floor_position_reserve', 2) or 0))
    except (TypeError, ValueError):
        return 2


# ── holds_while — one machine predicate, one grammar (§4.5, Condition 7) ─────
#
# "<metric> <op> <number>", op in < <= > >=, joined by a SINGLE homogeneous
# `and` or `or` (deliberately no nesting/precedence — mixing the two in one
# expression is refused as unparseable rather than guessed at). `revisit_if`
# (free prose) is the field for anything this can't express.

_HOLDS_WHILE_SIMPLE_METRICS = {
    'index_bytes', 'index_headroom_bytes', 'corpus_units', 'topic_notes',
    'positions', 'broken_links', 'days_since_decided',
}
# MC-964 Step D.1: the first EXTERNAL-state metric (source is allowance_state,
# not the corpus itself) — 'allowance_exhausted(<vendor>) >= 1' lets a
# position's holds_while reopen on a live vendor-allowance record instead of
# only on corpus-shape numbers. Argument-required, same as `delivered`.
_HOLDS_WHILE_ARG_METRICS = {'delivered', 'allowance_exhausted'}
_HOLDS_WHILE_METRIC_REGISTRY = _HOLDS_WHILE_SIMPLE_METRICS | _HOLDS_WHILE_ARG_METRICS

_HOLDS_WHILE_OPS = {
    '<': lambda a, b: a < b, '<=': lambda a, b: a <= b,
    '>': lambda a, b: a > b, '>=': lambda a, b: a >= b,
}

_HOLDS_WHILE_CLAUSE_RE = re.compile(
    r'^\s*([a-z_]+)(?:\(([^()]*)\))?\s*(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)\s*$')

# Vendor args of `allowance_exhausted(<vendor>)` clauses within a raw
# holds_while expression — used to look up live allowance_state per position
# (see evaluate_positions_holds_while) without re-parsing the whole grammar.
_HOLDS_WHILE_ALLOWANCE_ARG_RE = re.compile(r'allowance_exhausted\(([^()]*)\)')


class HoldsWhileError(ValueError):
    """A non-empty `holds_while` value that does not match the grammar."""


def _parse_holds_while(expr):
    """Parse into (join, [(metric, arg, op, number), ...]).

    Returns `(None, [])` for an empty/absent expression — Condition 8: an
    ABSENT predicate is never refused. Raises `HoldsWhileError` for a
    NON-EMPTY one that fails to parse, naming the registry and `revisit_if`
    as the alternative, so a caller can report (not silently drop) it.
    """
    raw = (expr or '').strip()
    if not raw:
        return None, []
    lowered = raw.lower()
    has_and = re.search(r'\band\b', lowered) is not None
    has_or = re.search(r'\bor\b', lowered) is not None
    if has_and and has_or:
        raise HoldsWhileError(
            f"holds_while {raw!r} mixes 'and' and 'or' — the grammar is flat, "
            f"no nesting or precedence; split it or use `revisit_if` (free prose)")
    join = 'or' if has_or else 'and'
    parts = re.split(r'\band\b' if join == 'and' else r'\bor\b', raw, flags=re.I)
    clauses = []
    for part in parts:
        m = _HOLDS_WHILE_CLAUSE_RE.match(part.strip())
        if not m:
            raise HoldsWhileError(
                f"unparseable holds_while clause {part.strip()!r} in {raw!r} — "
                f"registry: {sorted(_HOLDS_WHILE_METRIC_REGISTRY)}; consider "
                f"`revisit_if` (free prose) instead")
        metric, arg, op, num = m.groups()
        if metric not in _HOLDS_WHILE_METRIC_REGISTRY:
            raise HoldsWhileError(
                f"unknown holds_while metric {metric!r} in {raw!r} — "
                f"registry: {sorted(_HOLDS_WHILE_METRIC_REGISTRY)}; consider "
                f"`revisit_if` (free prose) instead")
        if metric in _HOLDS_WHILE_ARG_METRICS and not (arg or '').strip():
            raise HoldsWhileError(f"{metric}(...) needs an argument in {raw!r}")
        clauses.append((metric, (arg or '').strip(), op, float(num)))
    return join, clauses


def _eval_holds_while(expr, metrics):
    """True/False, or None when the predicate is empty, unparseable, or names
    a metric `metrics` does not carry a value for (e.g. `delivered(<unit>)`
    with no live sidecar — see `evaluate_positions_holds_while`). A caller
    must treat None as "not evaluable", never as either True or False.
    """
    try:
        join, clauses = _parse_holds_while(expr)
    except HoldsWhileError as e:
        _log(f'[holds_while] {e}')
        return None
    if join is None:
        return None
    results = []
    for metric, arg, op, num in clauses:
        key = f'{metric}({arg})' if arg else metric
        if key not in metrics:
            return None
        results.append(_HOLDS_WHILE_OPS[op](metrics[key], num))
    return all(results) if join == 'and' else any(results)


def _count_broken_links(units):
    """How many `[[wikilink]]` targets among topic units resolve to nothing in
    THIS vault (§4.4 class D — the `broken_links` holds_while metric). Class A
    (extension in target) is fixed by R2's canonicaliser and class C
    (cross-vault) is out of scope for this in-vault graph, so neither is
    double-counted here; only genuinely unresolved targets are.
    """
    by_key = set()
    for u in units:
        if u.get('cls') == 'topic':
            by_key.add(_mem_link_key(u['file'].rsplit('.', 1)[0]))
    n = 0
    for u in units:
        if u.get('cls') != 'topic':
            continue
        for tgt in u.get('links') or []:
            if _mem_link_key(tgt) not in by_key:
                n += 1
    return n


_holds_while_metrics_cache: dict = {}
_holds_while_metrics_cache_lock = threading.Lock()


def _holds_while_global_metrics(project):
    """The corpus-wide holds_while metrics: everything in the registry except
    the per-position `days_since_decided` and `delivered(<unit>)` (no live
    production delivery sidecar exists yet — `evaluate_positions_holds_while`
    reports that gap rather than fabricating a number). Cached on the corpus
    signature (Condition 7): recomputed only when a file in the memory dir
    changes, same discipline as `_mem_corpus`'s cache.
    """
    mem_path = _get_memory_path(project)
    mem_dir = mem_path.parent
    arch_path = _get_archive_path(project)
    try:
        sig = tuple(sorted(
            (f.name, f.stat().st_mtime_ns, f.stat().st_size)
            for f in mem_dir.glob('*.md')))
    except OSError:
        sig = ()
    key = str(mem_dir)
    with _holds_while_metrics_cache_lock:
        hit = _holds_while_metrics_cache.get(key)
        if hit and hit[0] == sig:
            return hit[1]
    units = _mem_corpus(mem_dir, mem_path.name, arch_path.name)
    index_bytes = mem_path.stat().st_size if mem_path.exists() else 0
    cap = int(state.CONFIG.get('index_byte_budget', 24576) or 24576)
    metrics = {
        'index_bytes': float(index_bytes),
        'index_headroom_bytes': float(cap - index_bytes),
        'corpus_units': float(len(units)),
        'topic_notes': float(sum(1 for u in units if u.get('cls') == 'topic')),
        'positions': float(sum(1 for u in units if u.get('cls') == 'position')),
        'broken_links': float(_count_broken_links(units)),
    }
    with _holds_while_metrics_cache_lock:
        _holds_while_metrics_cache[key] = (sig, metrics)
    return metrics


def evaluate_positions_holds_while(project):
    """{position filename: {'trips': bool|None, 'expr': str}} for every live
    position carrying a non-empty `holds_while`. `trips` True means the
    reopening condition a human wrote is CURRENTLY satisfied — the case
    §4.5 shows already happened once, silently, because nothing evaluated
    free English against the live metric. REPORT only (§16 step 2): nothing
    here refuses a write or a corpus build, it only makes the fact visible to
    a caller such as the weekly positions-review job.
    """
    base = _holds_while_global_metrics(project)
    out = {}
    for rec in list_positions(project):
        expr = (rec.get('holds_while') or '').strip()
        if not expr:
            continue
        metrics = dict(base)
        # `allowance_exhausted(<vendor>)` (MC-964 Step D.1) is EXTERNAL
        # state, not corpus shape — read fresh per position from the vendors
        # THIS expression actually names, never folded into
        # `_holds_while_global_metrics`'s cache (which only invalidates on
        # the memory dir's *.md signature and would never notice a vendor
        # clearing). Unlike `delivered(<unit>)`'s "no sidecar yet" gap, a
        # real answer (0.0/1.0) exists for every vendor named here, so it is
        # populated rather than left absent-and-unevaluable.
        for _vendor in set(_HOLDS_WHILE_ALLOWANCE_ARG_RE.findall(expr)):
            try:
                exhausted = _allowance_state.is_exhausted(_vendor)
            except Exception as e:
                _log(f'[holds_while] allowance_state read failed for '
                     f'{_vendor!r}: {e}')
                continue
            metrics[f'allowance_exhausted({_vendor})'] = 1.0 if exhausted else 0.0
        decided = (rec.get('decided') or '').strip()
        if decided:
            try:
                d = datetime.strptime(decided[:10], '%Y-%m-%d')
                metrics['days_since_decided'] = float(
                    (datetime.now(timezone.utc).replace(tzinfo=None) - d).days)
            except ValueError:
                pass
        out[rec['file']] = {'trips': _eval_holds_while(expr, metrics), 'expr': expr}
    return out


# ── Provenance stamps (§4.3) — prerequisites for mint (§6.5, build step 7) ──
#
# Nothing calls these yet: no code writes a NEW topic note today (§1.5
# Condition 2 — that write path is mint, step 7, not this one). Built now so
# mint has a tested, spec-correct stamp to call instead of inventing one
# under deadline later.

def _stamp_origin(task, trigger_type):
    """Condition 4: `origin` is SERVER-STAMPED from session identity, never a
    caller-supplied parameter — reuses the Distiller's existing allowlist
    (`mc.distiller.is_unattended_session`) verbatim rather than defining a
    second one. 'interactive' only when trigger_type is literally 'manual'
    and the task text carries no unattended marker; missing/unknown/
    backfilled trigger_type defaults to 'unattended' (fail safe).
    """
    return ('unattended' if _distiller.is_unattended_session(task, trigger_type)
            else 'interactive')


def _stamp_generated(actor):
    """Condition 4's `generated: {by, at}` stamp. `actor` is caller-supplied
    identity (a session id, 'legacy:import', a Scribe model name, ...) — it is
    NOT a trust claim the way `verified[]` is, so it carries no gate.
    """
    return {'by': str(actor or ''), 'at': now_iso()}


def _derive_verified(trigger_type, human_followup, actor=''):
    """Condition 5: `verified[]` is DERIVED, never human-appendable — a field
    only a human can fill, in a system whose founding premise is that the
    human will not curate, stays empty forever and the guard rail depending
    on it never fires. Human-witnessed means: written in a session with
    trigger_type == 'manual' AND the human sent a SUBSEQUENT message in that
    session. `human_followup` is that fact, looked up by the caller (session /
    agent-log join) — this function has no access to live session state and
    never fabricates the answer. `origin: legacy` (§4.3) is a SEPARATE,
    stricter exception made explicitly ineligible for `verified[]` at the
    call site, not here — this function only encodes the general rule.
    """
    if trigger_type != 'manual' or not human_followup:
        return []
    return [{'by': f'human:{actor}' if actor else 'human', 'at': now_iso()}]


def _is_position_file(name):
    return str(name or '').startswith(POSITION_PREFIX) and str(name).endswith('.md')


_DURABILITY_VALUES = {'principle', 'measured', 'provisional'}


def _validate_position_record(rec):
    """Report-only checks over a position record (§4.2 schema superset).
    Returns a list of warning strings; never raises, never mutates `rec`.

    G4's disposition is fail-open (repair what is repairable, log, never
    raise) and Condition 48 defaults `memory_gate_mode` to 'report' in every
    project — MEMORY_DESIGN_V2_SPEC.md §16 step 2 VALIDATES a position's
    frontmatter at write and at build; it does not yet GATE on the result.
    Flipping to a rejecting gate is a later, separately-decided step.
    """
    warnings = []
    durability = str(rec.get('durability') or '').strip()
    holds_while = str(rec.get('holds_while') or '').strip()
    if durability and durability not in _DURABILITY_VALUES:
        warnings.append(
            f'durability {durability!r} is not one of {sorted(_DURABILITY_VALUES)}')
    if durability == 'principle' and holds_while:
        warnings.append(
            'holds_while is forbidden when durability: principle (§4.2) — '
            'a principle is reopened by a human, never by a metric')
    if holds_while:
        try:
            _parse_holds_while(holds_while)
        except HoldsWhileError as e:
            warnings.append(str(e))
    return warnings


def _parse_position(text):
    """Frontmatter of a position note -> dict, or {} when it isn't one.

    §4.2 schema superset (MEMORY_DESIGN_V2_SPEC.md §16 step 2): every new
    field is optional and absent on the 46 pre-V2 files, which stay valid
    records unchanged (Condition 7). `revisit_if` is the canonical name for
    the old `expires_when`; the old key is kept as a PERMANENT read alias —
    a file written under either key reads the same normalized value, and no
    existing file needs migrating. `evidence` is a newline list, stored as one
    block-scalar frontmatter value and split back into a list here. `pin` is
    the only boolean field, written/read as the literal string 'true'.
    """
    try:
        meta, body = _skills.parse_skill_md(text)
    except Exception:
        return {}
    if not isinstance(meta, dict) or not meta.get('subject'):
        return {}
    revisit_if = str(meta.get('revisit_if') or meta.get('expires_when') or '')
    evidence_raw = str(meta.get('evidence') or '')
    rec = {
        'subject': str(meta.get('subject') or ''),
        'triggers': str(meta.get('triggers') or ''),
        'verdict': str(meta.get('position') or meta.get('verdict') or ''),
        'reason': str(meta.get('reason') or ''),
        'expires_when': str(meta.get('expires_when') or ''),
        'decided': str(meta.get('decided') or ''),
        'body': body,
        # §4.2 NEW fields — see _parse_position's docstring.
        'name': str(meta.get('name') or ''),
        'claim': str(meta.get('claim') or ''),
        'evidence': [ln.strip() for ln in evidence_raw.split('\n') if ln.strip()],
        'durability': str(meta.get('durability') or ''),
        'holds_while': str(meta.get('holds_while') or ''),
        'revisit_if': revisit_if,
        'supersedes': str(meta.get('supersedes') or ''),
        'pin': str(meta.get('pin') or '').strip().lower() == 'true',
        # MC-944 step 8 (Cond 11/12): absent on every pre-existing position
        # file (all 15+ predate this stamp) — '' reads as "unknown
        # provenance", never as "interactive", matching `_stamp_origin`'s own
        # fail-safe default everywhere else in this module.
        'origin': str(meta.get('origin') or ''),
    }
    warnings = _validate_position_record(rec)
    if warnings:
        _log(f"[position] {rec['name'] or rec['subject'][:48]}: "
             + '; '.join(warnings))
    return rec


# ── Continuity: what is in flight, and what we owe each other ───────────────
#
# The third memory layer (DAVE_DESIGN §3). FACTS work — the read floor reaches
# 84% of turns that previously got nothing. EPISODIC is thin. CONTINUITY did
# not exist at all: what a session was mid-way through, and what it promised,
# evaporated the moment that session ended. A colleague back from holiday does
# not re-read the archive; they hold a small working set and look the rest up.
#
# BOUNDED BY CONSTRUCTION, which is the whole point. MEMORY.md needs a remover
# because it is an open-ended curated list, and MC-892 proved the remover is
# the hard part — the proposed eviction would have dropped 29-30 lines with no
# surviving delivery channel, and the gate built to catch that returned green.
# A fixed-slot record cannot have that problem: every write REPLACES the whole
# record, caps are enforced here rather than by the model, and nothing
# accumulates. There is no eviction policy because there is no growth.
CONTINUITY_FILE = 'continuity.md'

# Caps are deliberately small. This is a working set, not a log — the moment it
# becomes something you scroll, it has stopped doing its job.
_CONT_MAX_THREADS = 5
_CONT_MAX_COMMITMENTS = 5
_CONT_MAX_ITEM_CHARS = 160
_CONT_MAX_UNDERSTANDING = 400
# How many agents keep a bucket. Structural eviction, the same lever as the
# slot caps: an agent that has not worked here lately is not carrying live
# working state, and the things worth keeping live elsewhere regardless.
_CONT_MAX_OWNERS = 4
# Another agent's work is context, not your list. A handful of lines.
_CONT_MAX_OTHER_LINES = 3
# What an ownerless bucket is called: the record predates owners, or the
# session ran with no character. It belongs to the project, not to whoever
# happens to write next.
_CONT_SHARED_LABEL = '(project)'  # parenthesised so no agent name collides


def _cont_clean(items, cap):
    # A bare string here is a hand-edit or an odd model reply; iterating it
    # would silently produce a list of single CHARACTERS, which is exactly what
    # the frontmatter-list format did before this moved into the body.
    if isinstance(items, str):
        items = [ln.strip('- ').strip() for ln in items.splitlines()]
    out = []
    for it in (items or []):
        t = ' '.join(str(it or '').split())[:_CONT_MAX_ITEM_CHARS]
        if t and t not in out:
            out.append(t)
    return out[:cap]


# Section headings in the body. The record is stored as MARKDOWN, not as
# frontmatter lists: the vault's minimal frontmatter parser has no list type
# and hands `['a','b']` back as a string, which then iterates character by
# character. Markdown sections also keep the file readable and hand-editable in
# the vault, which the whole memory design leans on.
_CONT_H_THREADS = '## In flight'
_CONT_H_COMMITMENTS = '## Promised'
_CONT_H_UNDERSTANDING = '## Where things stand'


def _cont_section(body, heading):
    """Bullet lines under a heading, across ALL owners. Legacy/merged view."""
    out, inside = [], False
    for ln in (body or '').splitlines():
        t = ln.strip()
        if t.startswith('## '):
            inside = (t == heading)
            continue
        if inside and t.startswith('- '):
            out.append(t[2:].strip())
    return out


def _cont_prose(body, heading):
    out, inside = [], False
    for ln in (body or '').splitlines():
        t = ln.strip()
        if t.startswith('## '):
            if inside:
                break
            inside = (t == heading)
            continue
        if inside and t and not t.startswith('### '):
            out.append(t)
    return ' '.join(out)


def _cont_owner_key(owner):
    """The stable per-agent key. '' is the shared/legacy bucket.

    A record written before owners existed, or by a session with no character,
    has no owner and must not be silently attributed to whoever writes next —
    it belongs to the project, and reads as such.
    """
    return ' '.join(str(owner or '').split())[:40]


def _cont_parse_owner(raw):
    """A `### <owner>` heading back to its bucket key.

    The shared bucket is WRITTEN with a visible label so the file reads well by
    hand, and must parse back to '' — otherwise it round-trips as a named agent
    and stops merging into everyone's view, which is the one thing it is for.
    """
    k = _cont_owner_key(raw)
    return '' if k == _CONT_SHARED_LABEL else k


def _cont_owner_sections(body, heading):
    """{owner: [lines]} under `heading`.

    Lines with no `### owner` above them land in the '' bucket, which is what a
    pre-owner file parses as — so the migration is "read the old file", not a
    rewrite step that could lose it.
    """
    out, inside, cur = {}, False, ''
    for ln in (body or '').splitlines():
        t = ln.strip()
        if t.startswith('## '):
            inside = (t == heading)
            cur = ''
            continue
        if not inside:
            continue
        if t.startswith('### '):
            cur = _cont_parse_owner(t[4:].split('—')[0])
            continue
        if t.startswith('- '):
            out.setdefault(cur, []).append(t[2:].strip())
        elif t and heading == _CONT_H_UNDERSTANDING and not t.startswith('_'):
            out.setdefault(cur, []).append(t)
    return out


def _cont_owner_stamps(body):
    """{owner: iso} from the `### <owner> — <iso>` headings, best-effort.

    Kept in the heading rather than the frontmatter because the vault's minimal
    parser has no list or map type — the same reason the sections themselves
    are markdown (see `_CONT_H_THREADS`).
    """
    out = {}
    for ln in (body or '').splitlines():
        t = ln.strip()
        if not t.startswith('### ') or '—' not in t:
            continue
        name, _, stamp = t[4:].partition('—')
        k = _cont_parse_owner(name)
        st = stamp.strip()
        if st > out.get(k, ''):
            out[k] = st
    return out


def _session_owner(session):
    """The continuity owner for a live session, or None when it owns no state.

    Must resolve to the SAME string the prompt builder uses for "Your name is
    …" (`character_name or CONFIG['agent_name']`), or the write side files a
    bucket the read side never asks for and every agent silently gets an empty
    record.

    **A GLOBAL character owns nothing — it returns None.** A global type is
    ephemeral by construction: it works on any project precisely because it
    keeps nothing between calls, which is also why it can be global without
    being a cross-project leak channel (`AGENT_TYPES_DESIGN` §5). Giving one a
    bucket was destructive in two directions — its half-finished thought became
    durable project working state injected into every later prompt, and with
    `_CONT_MAX_OWNERS` at 4 on least-recently-written eviction, three helpers
    passing through would push the project's OWN agent out of its own record.

    A session with no character at all still owns a bucket under the configured
    default name: that is the project's own agent, which is the continuous one.
    """
    try:
        ch = (session or {}).get('character') or {}
        if isinstance(ch, dict) and ch:
            if (ch.get('scope') or '') == 'global':
                return None
            return _cont_owner_key(ch.get('agent_name') or ch.get('name'))
        return _cont_owner_key(state.CONFIG.get('agent_name', ''))
    except Exception:
        return ''


def _cont_empty_slots():
    return {'threads': [], 'commitments': [], 'understanding': '', 'updated': ''}


def read_continuity(project, owner=None):
    """The continuity record. `owner=None` merges every agent's slots.

    The merged view is what the human surface and the older callers want; ONE
    agent's own working state is what belongs at the top of ITS prompt.
    Conflating them is how five shared slots came to hold four different
    sessions' half-finished threads with nothing marking whose.
    """
    empty = {**_cont_empty_slots(), 'body': '', 'by_owner': {},
             'owner': _cont_owner_key(owner)}
    try:
        fp = _get_memory_path(project).parent / CONTINUITY_FILE
        if not fp.is_file():
            return empty
        meta, body = _skills.parse_skill_md(
            fp.read_text(encoding='utf-8', errors='replace'))
    except Exception as e:
        _log(f'[continuity] read failed: {e}')
        return empty
    if not isinstance(meta, dict):
        meta = {}

    th = _cont_owner_sections(body, _CONT_H_THREADS)
    cm = _cont_owner_sections(body, _CONT_H_COMMITMENTS)
    un = _cont_owner_sections(body, _CONT_H_UNDERSTANDING)
    stamps = _cont_owner_stamps(body)
    by_owner = {}
    for k in set(th) | set(cm) | set(un):
        by_owner[k] = {
            'threads': _cont_clean(th.get(k), _CONT_MAX_THREADS),
            'commitments': _cont_clean(cm.get(k), _CONT_MAX_COMMITMENTS),
            'understanding': ' '.join(' '.join(un.get(k) or []).split()
                                      )[:_CONT_MAX_UNDERSTANDING],
            'updated': stamps.get(k, ''),
        }

    key = _cont_owner_key(owner)
    if owner is not None:
        mine = dict(by_owner.get(key) or _cont_empty_slots())
        # The ownerless bucket reads as YOURS, not as another agent's. It holds
        # two things: a record written before owners existed, and whatever a
        # human typed into the Memory modal. Neither belongs to a rival agent,
        # and exiling them to the capped "another agent" block would have made
        # every existing install lose its continuity the day this shipped.
        shared = by_owner.get('') or _cont_empty_slots()
        if key:
            for slot, cap in (('threads', _CONT_MAX_THREADS),
                              ('commitments', _CONT_MAX_COMMITMENTS)):
                mine[slot] = _cont_clean(list(mine[slot]) + list(shared[slot]), cap)
            mine['understanding'] = mine['understanding'] or shared['understanding']
        return {**mine, 'updated': str(meta.get('updated') or ''),
                'body': body, 'by_owner': by_owner, 'owner': key}

    # Merged: every owner's slots as one set, newest-written owner first so a
    # stale bucket cannot crowd out live work.
    order = sorted(by_owner, key=lambda k: by_owner[k]['updated'], reverse=True)
    return {
        'threads': _cont_clean([t for k in order for t in by_owner[k]['threads']],
                               _CONT_MAX_THREADS * 2),
        'commitments': _cont_clean(
            [c for k in order for c in by_owner[k]['commitments']],
            _CONT_MAX_COMMITMENTS * 2),
        'understanding': next((by_owner[k]['understanding'] for k in order
                               if by_owner[k]['understanding']), ''),
        'updated': str(meta.get('updated') or ''),
        'body': body, 'by_owner': by_owner, 'owner': '',
    }


def write_continuity(project, threads=None, commitments=None,
                     understanding=None, owner=None):
    """Replace ONE owner's slots. Returns that owner's record as written.

    REPLACE, never append — that is what keeps the size fixed and removes the
    need for a remover at all. `None` leaves a slot untouched so a caller that
    only learned about commitments does not blank the others; an empty list
    CLEARS a slot, which is how finished work leaves the record.

    WHY AN OWNER (2026-08-24). Every agent on a project shares its notes and
    its positions, and that is the design — a ruling Vector recorded must bind
    Dave, or positions would not work at all. But "what I was part-way through"
    is worker state, not a project fact, and one shared set of slots meant each
    agent's write silently replaced the others'. Measured on this project the
    day this shipped: five threads from four different sessions, none marked
    done, two of them describing work that had already landed.
    """
    key = _cont_owner_key(owner)
    cur = read_continuity(project)
    by_owner = dict(cur.get('by_owner') or {})
    mine = by_owner.get(key) or _cont_empty_slots()
    rec = {
        'threads': _cont_clean(threads if threads is not None else mine['threads'],
                               _CONT_MAX_THREADS),
        'commitments': _cont_clean(
            commitments if commitments is not None else mine['commitments'],
            _CONT_MAX_COMMITMENTS),
        'understanding': ' '.join(str(
            understanding if understanding is not None else mine['understanding']
        ).split())[:_CONT_MAX_UNDERSTANDING],
        'updated': now_iso(),
    }
    by_owner[key] = rec

    # Claiming completes the migration. An agent rewrites the record it was
    # SHOWN, which includes the shared bucket's lines — so a line it kept is
    # now its own, and leaving the original in place would duplicate it in
    # every agent's prompt forever. Anything the human typed and no agent
    # picked up simply stays shared.
    if key and '' in by_owner:
        claimed = set(rec['threads']) | set(rec['commitments'])
        sh = by_owner['']
        by_owner[''] = {
            **sh,
            'threads': [t for t in sh['threads'] if t not in claimed],
            'commitments': [c for c in sh['commitments'] if c not in claimed],
            'understanding': ('' if sh['understanding'] == rec['understanding']
                              else sh['understanding']),
        }

    # Structural eviction, the same lever as the slot caps: keep the N most
    # recently written owners. An agent that has not worked here lately is not
    # carrying live working state, and its facts and positions live elsewhere.
    live = {k: v for k, v in by_owner.items()
            if v['threads'] or v['commitments'] or v['understanding']}
    if len(live) > _CONT_MAX_OWNERS:
        keep = sorted(live, key=lambda k: live[k]['updated'],
                      reverse=True)[:_CONT_MAX_OWNERS]
        live = {k: live[k] for k in keep}
    by_owner = live

    order = sorted(by_owner, key=lambda k: by_owner[k]['updated'], reverse=True)
    body = [
        'Working state for this project — what is in flight and what was '
        'promised, PER AGENT. Rewritten in place at turn boundaries; fixed '
        'slots, so it never grows and never needs pruning.',
        '',
        _CONT_H_UNDERSTANDING,
    ]
    if any(by_owner[k]['understanding'] for k in order):
        for k in order:
            if by_owner[k]['understanding']:
                body += [_cont_heading(k, by_owner[k]['updated']),
                         by_owner[k]['understanding'], '']
    else:
        body += ['_nothing recorded yet_', '']
    for heading, slot, blank in (
            (_CONT_H_THREADS, 'threads', '_nothing in flight_'),
            (_CONT_H_COMMITMENTS, 'commitments', '_nothing outstanding_')):
        body.append(heading)
        wrote = False
        for k in order:
            if not by_owner[k][slot]:
                continue
            wrote = True
            body.append(_cont_heading(k, by_owner[k]['updated']))
            body += [f'- {t}' for t in by_owner[k][slot]]
        if not wrote:
            body.append(blank)
        body.append('')

    mem_dir = _get_memory_path(project).parent
    mem_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(
        mem_dir / CONTINUITY_FILE,
        _skills.dump_skill_md({'name': 'continuity', 'updated': rec['updated']},
                              '\n'.join(body).rstrip() + '\n'))
    return rec


def _cont_heading(owner, updated):
    return f'### {owner or _CONT_SHARED_LABEL} — {updated}'


def render_continuity(project, owner=None):
    """The continuity block for the system prompt, or '' when there is nothing.

    Injected DIRECTLY rather than retrieved: "what am I part-way through" is
    relevant to every turn by definition, so making it compete for a read-floor
    slot would be the wrong question. It is affordable because it is capped.

    YOUR working state comes first and in full; other agents' appear below,
    named and capped. Hiding them would be the wrong call — two agents about to
    edit the same file is precisely what you want to know before you start —
    but presenting them as yours is what made the record actively misleading.
    """
    try:
        rec = read_continuity(project, owner=owner)
    except Exception:
        return ''
    by_owner = rec.get('by_owner') or {}
    key = _cont_owner_key(owner)
    lines = []
    if rec['understanding']:
        lines.append(f"  Where things stand: {rec['understanding']}")
    for t in rec['threads']:
        lines.append(f"  • IN FLIGHT — {t}")
    for c in rec['commitments']:
        lines.append(f"  • YOU SAID YOU WOULD — {c}")

    others = []
    if owner is not None:
        for k in sorted((k for k in by_owner if k and k != key),
                        key=lambda k: by_owner[k]['updated'], reverse=True):
            for t in by_owner[k]['threads']:
                others.append(f"  • {k or _CONT_SHARED_LABEL} — {t}")
            if len(others) >= _CONT_MAX_OTHER_LINES:
                break
        others = others[:_CONT_MAX_OTHER_LINES]
    if not lines and not others:
        return ''
    out = ''
    if lines:
        out = ("--- CONTINUITY (what you were part-way through, and what you "
               "promised; if you finish or drop one, say so) ---\n"
               + "\n".join(lines))
    if others:
        out += (("\n" if out else "")
                + "--- ANOTHER AGENT ON THIS PROJECT IS PART-WAY THROUGH (not "
                  "yours — do not adopt or report these as your own work, "
                  "and coordinate before you touch the same files) ---\n"
                + "\n".join(others))
    return out


def render_position_capture(project, port):
    """The standing instruction to RECORD a position, for the system prompt.

    Positions have storage, retrieval and a route; what they did not have is a
    caller. Capture is deliberately explicit rather than mined from transcripts
    (see `write_position_route`) — but "explicit" only works if the agent is
    told, and an API nobody is told about is dead code. Measured 2026-08-24:
    two positions existed, both hand-written the day the feature shipped.

    Directive, and placed next to continuity rather than in the API reference,
    because the failure this whole class exists to fix was reference material
    failing to fire. The reference describes; this instructs.
    """
    pid = (project or {}).get('id') or ''
    if not pid:
        return ''
    return (
        "--- RECORDING A POSITION (a decision NOT to do something) ---\n"
        "  When a question gets SETTLED — you evaluated something and "
        "recommended against it, or the user declined something you proposed — "
        "record it before the turn ends. Nothing else captures this: every "
        "other capture path (checkpointer, Scribe, Distiller) is downstream of "
        "an artifact, and deciding not to build something produces no commit, "
        "no file, no diff. Unwritten, it costs a whole conversation the next "
        "time someone re-proposes it.\n"
        f"  curl -s -X POST http://localhost:{port}/api/project/{pid}"
        "/memory/positions -H 'Content-Type: application/json' -d "
        "'{\"subject\":\"what the question was\",\"position\":\"declined\","
        "\"reason\":\"why — REQUIRED\",\"expires_when\":\"what would change "
        "our mind\",\"triggers\":\"comma,separated,terms that should make this "
        "fire\"}'\n"
        "  `reason` is mandatory on purpose: a bare verdict is dogma an agent "
        "can only obey, a reason is checkable and can be re-opened honestly. "
        "Recording a position on a subject that already has one SUPERSEDES it — "
        "that is how you reverse a call, not by adding a second one.\n"
        "  Record settled QUESTIONS only. Not preferences, not an in-the-moment "
        "\"no, use tabs\" — every entry costs prompt space in every future turn "
        "that touches its subject, and a position you invented outranks the "
        "notes around it.")


# The extraction prompt. Deliberately asks for the WHOLE record back rather
# than a diff: a model that emits "add this thread" needs the caller to decide
# what falls off, which is the curation problem this design exists to avoid.
# Returning the full record makes supersession the only possible outcome.
_SCRIBE_CONTINUITY = (
    "You maintain a short working-state record for a software project — what "
    "is IN FLIGHT and what was PROMISED. You are given the current record and "
    "a new slice of conversation.\n\n"
    "Return ONLY minified JSON, no prose, no code fence:\n"
    '{"threads":["..."],"commitments":["..."],"understanding":"..."}\n\n'
    "Rules:\n"
    "- threads: work STARTED and NOT finished. Drop anything the slice shows "
    "as completed or abandoned. Max 5, one short line each.\n"
    "- commitments: things the assistant said it would do and has not done "
    "yet. Drop them once done. Max 5.\n"
    "- understanding: 1-2 sentences on where the work stands right now. "
    "Replace it, do not append to it.\n"
    "- Return the COMPLETE updated record, not a diff. Anything you omit is "
    "dropped, which is how finished work leaves the record.\n"
    "- Prefer specific over comprehensive. An empty list is a valid and often "
    "correct answer."
)


def _extract_continuity(project, delta, model, owner=None, *, provider=None,
                        cwd=None):
    """One cheap call: fold a transcript slice into the record. Never raises.

    `owner` scopes the write to ONE agent's slots — the model is shown that
    agent's record and rewrites only it. Showing it the merged view instead
    would invite it to "tidy" another agent's threads, which is the overwrite
    this owner dimension exists to stop.
    """
    token = _with_transform_context(provider, cwd=cwd)
    try:
        cur = read_continuity(project, owner=owner)
        payload = (
            "CURRENT RECORD:\n"
            + json.dumps({k: cur[k] for k in
                          ('threads', 'commitments', 'understanding')},
                         ensure_ascii=False)
            + "\n\nNEW CONVERSATION SLICE:\n" + delta[:12000])
        raw = _model_call(model, _SCRIBE_CONTINUITY, payload)
        txt = (raw or '').strip()
        if txt.startswith('```'):
            txt = txt.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
        i, j = txt.find('{'), txt.rfind('}')
        if i < 0 or j <= i:
            return None
        d = json.loads(txt[i:j + 1])
        if not isinstance(d, dict):
            return None
        return write_continuity(
            project,
            threads=d.get('threads') or [],
            commitments=d.get('commitments') or [],
            understanding=d.get('understanding') or '',
            owner=owner)
    except Exception as e:
        _log(f"[continuity] extraction failed: {e}")
        return None
    finally:
        _reset_transform_context(token)


def write_position(project, subject, verdict, reason,
                   expires_when='', decided='', body='', slug='', triggers='',
                   claim='', evidence=None, durability='', holds_while='',
                   revisit_if='', supersedes='', pin=False,
                   task='', trigger_type=''):
    """Record a decision — usually a decision NOT to do something.

    Returns the note's filename. Supersedes in place: recording a position on a
    subject that already has one REPLACES it rather than adding a second, so a
    reversal reads as one current ruling instead of two contradictory ones. The
    superseded text is kept in the body under a `## Previously` heading, because
    "we declined in August and reversed in November because Y" is worth more
    than either half alone.

    `reason` is required on purpose. A bare verdict is dogma an agent can only
    obey; a reason is checkable, which is what lets a position be re-opened
    honestly rather than either ignored or followed blindly.

    §4.2 schema superset (MEMORY_DESIGN_V2_SPEC.md §16 step 2), all optional:
    `claim` (the sentence a re-proposer would write, for §5.4's matcher —
    distinct from `subject`, which is what supersession keys on); `evidence`
    (a list or a pre-joined newline string); `durability`
    (principle|measured|provisional); `holds_while` (§4.5's machine
    predicate — validated, report-only, via `_validate_position_record`);
    `revisit_if`, the canonical name for `expires_when` (Condition 7: passing
    `revisit_if` writes the new key, passing `expires_when` keeps writing the
    old one — existing callers are unaffected and no existing file needs
    migrating); `supersedes` (this position may itself supersede a TOPIC
    note, distinct from the in-place same-subject supersession below); `pin`
    (ledger override, capped at 5 — the cap is enforced by the ledger reader
    in a later build step, not here).

    `task`/`trigger_type` (MC-944 step 8): `origin` is SERVER-STAMPED via
    `_stamp_origin` (same helper `write_topic_note` uses), never a
    caller-supplied value. Every pre-existing caller omits both, so
    `_stamp_origin('', '')` stamps `unattended` (fail-safe: unknown
    provenance defaults to the more restrictive class, same posture
    `write_topic_note` and the `backlog_done` mint call site already use).
    The negation obligation scan (§5.2, Cond 11/12) is the first caller that
    passes real values. No `generated` stamp here (unlike `write_topic_note`)
    — positions already carry `decided`, and Cond 11/12 need only `origin`
    for the consumer_unattended read-floor gate.

    Leaf-locked (MEMORY_DESIGN_V2_SPEC.md §16 step 1 / §10.3 G4): the
    read-prior / compose / write sequence below is a read-modify-write over
    ONE file, and until now it took no lock at all — two concurrent callers
    superseding the same subject could both read the same `prior`, and the
    second writer's atomic replace would silently drop the first writer's
    supersession, losing a ruling with no error anywhere. Uses the SAME
    per-project `_get_mem_write_lock` as the MEMORY.md writers (this module's
    docstring); a position file is a different leaf under the same memory
    dir, so it does not contend with a MEMORY.md commit, only with another
    concurrent write to a position.
    """
    subject = (subject or '').strip()
    reason = (reason or '').strip()
    if not subject:
        raise ValueError('subject is required — it is what the position settles')
    if not reason:
        raise ValueError(
            'reason is required — a verdict without one cannot be re-evaluated')
    verdict = (verdict or 'declined').strip().lower()
    claim = (claim or '').strip()
    durability = (durability or '').strip()
    holds_while = (holds_while or '').strip()
    revisit_if = (revisit_if or '').strip()
    supersedes = (supersedes or '').strip()
    if isinstance(evidence, (list, tuple)):
        evidence_str = '\n'.join(str(e).strip() for e in evidence if str(e).strip())
    else:
        evidence_str = (evidence or '').strip()

    for w in _validate_position_record(
            {'durability': durability, 'holds_while': holds_while}):
        _log(f'[position] {slug or subject}: {w}')

    mem_dir = _get_memory_path(project).parent
    mem_dir.mkdir(parents=True, exist_ok=True)
    slug = (slug or '').strip() or _mem_link_key(subject)[:48] or 'unnamed'
    path = mem_dir / f'{POSITION_PREFIX}{slug}.md'
    project_id = project.get('id', '') if isinstance(project, dict) else ''

    with _get_mem_write_lock(f'position:{project_id}'):
        prior = ''
        if path.exists():
            try:
                old_txt = path.read_text(encoding='utf-8', errors='replace')
                old_rec = _parse_position(old_txt)
                if old_rec:
                    prior = (f"\n\n## Previously\n\n- **{old_rec.get('verdict')}**"
                             f"{' (' + old_rec['decided'] + ')' if old_rec.get('decided') else ''}"
                             f" — {old_rec.get('reason')}")
                    if old_rec.get('body', '').strip():
                        prior += '\n' + old_rec['body'].strip()
            except Exception as e:
                _log(f'[position] could not read prior {path.name}: {e}')

        front = {'name': slug, 'subject': subject, 'position': verdict,
                 'reason': reason}
        if triggers:
            front['triggers'] = triggers.strip()
        if revisit_if:
            front['revisit_if'] = revisit_if
        elif expires_when:
            front['expires_when'] = expires_when.strip()
        if claim:
            front['claim'] = claim
        if evidence_str:
            front['evidence'] = evidence_str
        if durability:
            front['durability'] = durability
        if holds_while:
            front['holds_while'] = holds_while
        if supersedes:
            front['supersedes'] = supersedes
        if pin:
            front['pin'] = 'true'
        front['origin'] = _stamp_origin(task, trigger_type)
        front['decided'] = (decided or '').strip() or now_iso()[:10]
        text = _skills.dump_skill_md(front, (body or '').strip() + prior + '\n')
        _atomic_write_text(path, text)
    return path.name


def write_topic_note(project, slug, description, body, *, note_type='project',
                      triggers='', task='', trigger_type='', actor='',
                      supersedes='', mint_candidates=''):
    """Mint a NEW topic note (MEMORY_DESIGN_V2_SPEC.md §7 Condition 27 /
    §4.1's `origin`+`generated` provenance, Condition 4/22 "mint WRITEs
    fail-open"). First real caller of `_stamp_origin`/`_stamp_generated`
    (both built ahead of a caller — see the "Provenance stamps" comment
    above `_stamp_origin`) — MC-964 Step E's habit-statement classifier.

    Deliberately NOT `write_position`: a habit is an OBSERVATION about Ron,
    not a settled question an agent must obey, so it gets the plain topic-
    note shape (`metadata: {type: ...}`, matching the 231 existing topic
    files) rather than a subject/verdict/reason ruling.

    Never clobbers: if a file at this slug already exists, this is a no-op
    (logged, not raised) — an automated classifier overwriting a topic note
    a human already curated would be strictly worse than the classifier
    missing one. There is no update path here; a human (or a later mint/
    supersede build step) edits an existing note by hand.

    `origin` is SERVER-STAMPED from `task`/`trigger_type` via `_stamp_origin`
    — never a caller-supplied parameter — so an unattended-origin call can
    only ever produce `origin: unattended`, never impersonate a human. Callers
    that must not mint from unattended evidence (the habit classifier is one)
    check `_stamp_origin(...) == 'interactive'` THEMSELVES before calling this;
    this function stamps honestly either way rather than refusing, so a
    caller with a legitimate unattended use case is not blocked by a rule
    written for a different one.

    `supersedes` (§6.1 Condition 17, MC-944 step 6): the slug of an existing
    topic note this one replaces. Written verbatim — resolution against the
    corpus (name/stem/`aka` aliasing) happens at read time in
    `_mem_supersede_graph`, same split as `[[wikilinks]]`. Never an edit to
    the predecessor: the back edge (`superseded_by`) is derived, not stored.

    `mint_candidates` (§6.5 Condition 22, MC-944 step 7): comma-joined
    predecessor slugs the overlap detector flagged when `supersedes` is the
    literal sentinel `'unresolved'` — the top-3 the RESOLVE question offers.
    Written verbatim, never parsed back into a real edge (`_mem_supersede_
    graph` already drops `unresolved` as a no-edge case); ignored entirely
    when `supersedes` is not `'unresolved'`.

    Returns the note's filename, or '' if skipped (already exists / bad slug).
    """
    slug = (slug or '').strip()
    description = (description or '').strip()
    if not slug or not description:
        return ''
    mem_dir = _get_memory_path(project).parent
    mem_dir.mkdir(parents=True, exist_ok=True)
    path = mem_dir / f'{slug}.md'
    if path.exists():
        _log(f'[memory] write_topic_note skip (exists): {path.name}')
        return ''
    origin = _stamp_origin(task, trigger_type)
    generated = _stamp_generated(actor)
    project_id = project.get('id', '') if isinstance(project, dict) else ''
    with _get_mem_write_lock(f'topic:{project_id}'):
        if path.exists():   # re-check inside the lock — TOCTOU
            return ''
        lines = ['---', f"name: {slug.replace('_', '-')}",
                 f'description: "{description.replace(chr(34), chr(92)+chr(34))}"',
                 'metadata:', f'  type: {note_type}']
        if triggers:
            lines.append(f'triggers: {triggers.strip()}')
        if supersedes:
            lines.append(f'supersedes: {supersedes.strip()}')
        if supersedes.strip() == 'unresolved' and mint_candidates:
            lines.append(f'mint_candidates: {mint_candidates.strip()}')
        lines.append(f'origin: {origin}')
        lines.append('generated:')
        lines.append(f"  by: {generated['by']}")
        lines.append(f"  at: {generated['at']}")
        lines.append('---')
        lines.append('')
        text = '\n'.join(lines) + (body or '').strip() + '\n'
        _atomic_write_text(path, text)
    return path.name


def list_positions(project):
    """Every recorded position, newest decision first."""
    try:
        mem_dir = _get_memory_path(project).parent
    except Exception:
        return []
    if not mem_dir.is_dir():
        return []
    out = []
    for f in sorted(mem_dir.glob(f'{POSITION_PREFIX}*.md')):
        rec = _parse_position(f.read_text(encoding='utf-8', errors='replace'))
        if rec:
            rec['file'] = f.name
            out.append(rec)
    return sorted(out, key=lambda r: r.get('decided', ''), reverse=True)


def delete_position(project, filename):
    """Forget a position entirely. Returns True if a file was removed.

    Deliberately a DELETE and not a demotion, which is the opposite of the rule
    for notes ("never delete to save tokens — demote"). The rule exists because
    a note is an observation and a cold one still costs nothing. A position is a
    RULING: it renders in its own prompt block, outranks the notes around it on
    its subject, and an agent is meant to obey it. A wrong one is not dead
    weight, it is active misdirection — so there has to be a way to take it
    back. Reversing a still-valid question is `write_position` instead, which
    supersedes in place and keeps the old reasoning.
    """
    name = str(filename or '').strip()
    # Name-only, and it must look like a position: a UI-supplied filename is
    # the one string here that reaches the filesystem, and the memory dir also
    # holds MEMORY.md and every topic note.
    if not name or not _is_position_file(name) or '/' in name or '\\' in name:
        raise ValueError('not a position filename')
    mem_dir = _get_memory_path(project).parent
    path = mem_dir / name
    if path.resolve().parent != mem_dir.resolve():
        raise ValueError('not a position filename')
    if not path.is_file():
        return False
    path.unlink()
    return True


def _head(text, n=120):
    """A one-line identifying prefix of a unit, for delivery reports."""
    return ' '.join((text or '').split())[:n]


def _unit_uid(label, text, cls):
    """Stable identity for ONE scoring unit — what delivery telemetry counts.

    A filename is not an identity here. `MEMORY_ARCHIVE.md` is ~2.5k separately
    ranked lines under one label and `MEMORY.md#managed` a few hundred entries,
    so keying on the container would credit every line with its neighbours'
    hits. Whole-file classes (topic, position) keep their filename — that IS
    their identity, and it survives an edit, which is what we want for a note.
    Line classes get a content hash, so an edited line correctly starts a fresh
    history: it is a different claim.
    """
    if cls in ('archive', 'managed'):
        h = hashlib.sha1((text or '').strip().encode('utf-8')).hexdigest()[:10]
        return f'{label}#{h}'
    return label


def _mem_file_units(f, mem_name, arch_name, session_log_name=SESSION_LOG_FILE):
    """The raw (label, text, cls) triples ONE file on disk contributes to the
    corpus, before tokenization. Split out of `_mem_corpus` so the per-file
    cache (Condition 53) can invalidate a single file without re-deriving the
    rest of the vault.

    `session_log_name` (§16 step 4): SESSION_LOG.md carries the SAME managed
    format (`_mem_split`) as MEMORY.md's pre-split managed block did, just in
    its own sibling file — retrieval is unchanged, only the source file moved.
    `mem_name`'s own managed block is still read too (return, not elif) for
    an unmigrated project's MEMORY.md, which may still carry one inline —
    §10.4's "both formats coexist for the whole migration" invariant.
    """
    if f.name == CONTINUITY_FILE:
        # Already injected verbatim into every prompt. Letting it also win a
        # read-floor slot spends one of six on text the agent is guaranteed
        # to have anyway — measured: it displaced real notes on 2 of 3 probe
        # queries the day continuity shipped.
        return []
    try:
        txt = f.read_text(encoding='utf-8', errors='replace')
    except Exception:
        return []
    if f.name == mem_name or f.name == session_log_name:
        return [(f'{f.name}#managed', e, 'managed')
                for e in _mem_split(txt)[1]]
    if f.name == arch_name:
        _arch = [ln.strip() for ln in txt.splitlines()
                 if ln.strip().startswith('- [')]
        return [(f.name, ln, 'archive')
                for ln in _dedupe_archive_lines(_arch)]
    if _is_position_file(f.name):
        return [(f.name, txt, 'position')]
    return [(f.name, txt, 'topic')]


def _mem_tokenize_unit(label, text, cls):
    """Turn one (label, text, cls) triple into a scoring-unit dict, or None
    for an empty document. Split out of `_mem_corpus` for the per-file cache
    (Condition 53) — tokenization is the expensive part being cached.
    """
    toks = _mem_tokens(text)
    subject_terms = set()
    trigger_explicit = False
    if not toks:
        return None
    if cls == 'topic':
        # A topic file's NAME is its title — `decision_step7_semantic_
        # search_deferral.md` says more than most of its body — so index it
        # as part of the document, boosted. Doing it here rather than as a
        # score bonus is what lets a note match on its title ALONE; a bonus
        # applied after the match test can't, because a document with no
        # body hit never gets scored at all.
        #
        # Deliberately NOT done for 'archive'/'managed' units: their label
        # is the container's filename, not a title, so folding it in would
        # make every one of the ~2k archive lines match the query "memory".
        toks = toks + _mem_tokens(label.rsplit('.', 1)[0]) * _title_boost()
    elif cls == 'position':
        # The SUBJECT is what has to match, far more than the prose. A
        # position about Obsidian must beat every note that merely mentions
        # Obsidian in passing, or it loses its own question.
        _pos = _parse_position(text)
        subject_terms = _position_triggers(_pos)
        trigger_explicit = bool(str(_pos.get('triggers') or '').strip())
        toks = toks + _mem_tokens(_pos.get('subject', '')) * _POSITION_SUBJECT_BOOST
        toks = toks + _mem_tokens(label.rsplit('.', 1)[0]) * _title_boost()
    tf = {}
    for t in toks:
        tf[t] = tf.get(t, 0) + 1
    unit = {'file': label, 'text': text, 'tf': tf,
            'len': len(toks), 'cls': cls,
            'uid': _unit_uid(label, text, cls),
            'subject_terms': subject_terms,
            'trigger_explicit': trigger_explicit,
            'links': _mem_link_targets(text) if cls == 'topic' else []}
    if cls == 'topic':
        # MC-944 step 6 (Condition 17/20): parsed once here, at tokenize
        # time, so it rides the same per-file cache (Condition 53) as
        # everything else — `_memory_search` never re-parses frontmatter
        # per query. `_note_frontmatter` is the shared parser; see its
        # docstring for why this stays out of `tf`/BM25 scoring.
        fm = _note_frontmatter(text)
        unit['supersedes'] = fm.get('supersedes', '')
        unit['fm_origin'] = fm.get('origin', '')
        unit['fm_verified'] = fm.get('verified', '')
        unit['fm_description'] = fm.get('description', '')
    return unit


def _mem_corpus(mem_dir, mem_name, arch_name, session_log_name=SESSION_LOG_FILE):
    """Parse + tokenize the memory corpus into scoring units (cached per file).

    Unit classes, which the scorer keeps separate (see _memory_search):
      'topic'   — a whole topic .md file
      'archive' — one '- [' line of MEMORY_ARCHIVE.md
      'managed' — one managed entry of MEMORY.md OR of SESSION_LOG.md
                  (§16 step 4 — the managed region moved file, the corpus
                  unit class and retrieval behaviour did not)

    Cache invalidation is per FILE (Condition 53), not per directory: each
    file's units are kept keyed on that file's own (mtime_ns, size), so a
    write to one topic note re-tokenizes only that note — every other
    cached file's units are reused untouched. Cheap at ~100 notes either
    way; at ~1,700+ (spec §13.3 Break 1) a whole-vault signature makes ANY
    write anywhere pay for re-tokenizing everything.
    """
    try:
        files = sorted(mem_dir.glob('*.md'))
        stats = {f.name: (f.stat().st_mtime_ns, f.stat().st_size) for f in files}
    except OSError:
        return []
    key = str(mem_dir)
    with _memsearch_cache_lock:
        cached = _memsearch_cache.get(key) or {}
    fresh = {}
    out = []
    for f in files:
        name = f.name
        st = stats[name]
        hit = cached.get(name)
        if hit is not None and hit[0] == st:
            fresh[name] = hit
            out.extend(hit[1])
            continue
        units = [_mem_tokenize_unit(label, text, cls)
                 for label, text, cls in
                 _mem_file_units(f, mem_name, arch_name, session_log_name)]
        units = [u for u in units if u is not None]
        fresh[name] = (st, units)
        out.extend(units)
    with _memsearch_cache_lock:
        _memsearch_cache[key] = fresh
    return out


def corpus_uids(project):
    """{uid: (file, cls, head)} for everything in the corpus RIGHT NOW.

    The never-delivered set is the half that matters for demotion, and the
    counters cannot produce it — they only know what arrived. Comparing against
    a live corpus scan is also self-correcting: a unit that has been edited or
    removed simply stops appearing, rather than lingering in the sidecar as a
    phantom demotion candidate.
    """
    try:
        mem_path = _get_memory_path(project)
        mem_dir = mem_path.parent
    except Exception:
        return {}
    if not mem_dir.is_dir():
        return {}
    out = {}
    for u in _mem_corpus(mem_dir, mem_path.name, _get_archive_path(project).name):
        out[u.get('uid') or u['file']] = (u['file'], u.get('cls'), _head(u.get('text')))
    return out


def _memory_search(project, query, topk=3, expand=None, record=None,
                    keep_internal=False, consumer_unattended=False):
    """BM25 ranking over the project's memory corpus (SPEC §3 Leg B).

    `consumer_unattended` (§6.5, MC-944 step 7's hard constraint; mirrors
    `mc.distiller.exploration_read_floor`'s identically-named parameter):
    when True, a topic unit stamped `origin: unattended` is dropped from the
    scored candidates entirely — never delivered, never redirected to, never
    counted toward a slot. Deterministic mints (`mint_topic_node`) can fire
    from an unattended trigger (a scheduled hivemind closing, a steward
    closing a backlog item), and CLAUDE.md's learning-system safety rail
    ("a human must be on at least one side of every learning loop") applies
    here exactly as it does to the Distiller's own artifacts: autonomous
    output must never become autonomous input. Default False preserves every
    existing caller's behaviour unchanged — only a caller building a
    STEWARD/unattended read-floor opts in.

    Corpus = the memory dir's topic *.md files + MEMORY_ARCHIVE.md entries +
    the MANAGED region of MEMORY.md. The curated MEMORY.md index is excluded
    by construction — the agent already auto-loads it. Deterministic, no
    model. Returns [{file, score, snippet}] sorted by score desc.

    `keep_internal` (MC-964 Step B/RC4): when True, `cls`/`uid`/`head` are
    NOT stripped from the returned hits. Default False preserves the public
    shape every existing caller (and `test_internal_keys_never_leak_into_
    the_public_result`) is pinned to. The one caller that needs a specific
    ARCHIVE LINE's own identity — `mc.memory_push`'s push-log observer,
    which RC4 found logging only the container filename
    (`note: "MEMORY_ARCHIVE.md"`, indistinguishable across ~2.5k lines) —
    opts in explicitly rather than the shape changing for everyone.

    WHY BM25 (2026-08-05). The previous scorer was raw term frequency over the
    whole file — `sum(text.count(t))` — with no document-length normalization.
    A long file simply contains more instances of ordinary words than a short
    one, so length beat relevance. Measured by replaying this function over the
    first user message of 206 real past sessions: **55 of 75 topic files never
    entered a top-3 for ANY task**, 29% of tasks surfaced nothing at all, and
    3 files took 403 of ~430 slots — which were, exactly, the 3 largest topic
    files (11.5–19.4KB against a 3.2KB median). The corpus wasn't cold; it was
    unreachable. Length normalization is the specific thing that fixes it, and
    IDF + saturation come with it: this corpus is dominated by exact tokens
    (function names, file paths, SHAs) where rare-term weighting is what
    separates a real hit from an incidental mention.

    ONE ADAPTATION. Textbook BM25 assumes a homogeneous corpus, and ours is
    not: whole topic files (KB) sit alongside single archive lines (~0.4KB),
    and the archive lines outnumber the topic files ~30:1. A single global
    `avgdl` would therefore be set by archive lines and would score every topic
    file as pathologically long, trading the old bias for its mirror image. So
    **length is normalized per unit class** — a topic file competes on length
    against other topic files — while IDF stays global, since term rarity is a
    property of the whole corpus. `_mem_class_avgdl` is separated out so this
    stays testable.

    A topic file's NAME is indexed as part of its text (see `_mem_corpus`),
    which is what lets a note match on its title alone.

    LINK EXPANSION (`expand`, 2026-08-09). BM25 only ever finds notes that share
    vocabulary with the task. The vault also carries ~97 hand-authored
    `[[wikilinks]]` — a previous session's explicit "this is the companion
    note" — which the ranker was blind to: nothing in the codebase parsed them,
    so they were decoration. `expand` appends up to N EXTRA results reached by
    traversing one hop from the BM25 hits (out-links first, then back-links),
    each carrying `via` = the hit it was reached from. They are appended, never
    substituted, so turning this on cannot displace a real lexical match.
    """
    terms = _mem_tokens(query)
    if not terms:
        return []
    try:
        mem_path = _get_memory_path(project)
        mem_dir = mem_path.parent
    except Exception:
        return []
    if not mem_dir.is_dir():
        return []
    units = _mem_corpus(mem_dir, mem_path.name, _get_archive_path(project).name)
    if not units:
        return []

    n_docs = len(units)
    avgdl = _mem_class_avgdl(units)
    # Global IDF, in the always-positive form (the classic
    # ln((N-n+0.5)/(n+0.5)) goes negative for terms in >half the corpus, which
    # would let a common term subtract from an otherwise good match).
    seen = set(terms)
    df = dict.fromkeys(seen, 0)
    for u in units:
        tfu = u['tf']
        for t in seen:
            if t in tfu:
                df[t] += 1
    idf = {t: _math.log(1.0 + (n_docs - df[t] + 0.5) / (df[t] + 0.5))
           for t in seen}

    scored = []
    for u in units:
        tfu = u['tf']
        dl = u['len']
        norm = avgdl.get(u['cls']) or dl or 1.0
        _b = _bm25_b()
        denom_len = _BM25_K1 * (1.0 - _b + _b * (dl / norm))
        score = 0.0
        matched = 0
        for t in terms:
            f = tfu.get(t, 0)
            if not f:
                continue
            matched += 1
            score += idf[t] * (f * (_BM25_K1 + 1.0)) / (f + denom_len)
        if not matched:
            continue
        if consumer_unattended and u.get('cls') in ('topic', 'position'):
            # MC-944 step 8 (Condition 11/12) — the negation obligation scan
            # can write a position from the SAME unattended triggers mint
            # already fires from (hivemind close, backlog->done, docs
            # artifact scan). CLAUDE.md's learning-safety rail ("a human
            # must be on at least one side of every learning loop") makes no
            # exception for cls: this gate is a property of `origin`, not of
            # which write path produced the file, so it must cover positions
            # exactly as it already covers topic mints below.
            if _note_frontmatter(u['text']).get('origin') == 'unattended':
                continue
        _trig = u.get('subject_terms') or set()
        _hit_trig = _trig & set(terms)
        if _hit_trig and not u.get('trigger_explicit'):
            # Subject-derived triggers must DISTINGUISH. One word the whole
            # corpus uses is not evidence the task is about this ruling — see
            # `_position_trigger_max_df`.
            # The floor matters: a fraction is meaningless on a small corpus,
            # where one note out of six is already 17%. A term in five or fewer
            # documents is rare by any measure, so it always passes.
            _maxdf = max(_POSITION_TRIGGER_MIN_DOCS,
                         _position_trigger_max_df() * n_docs)
            _hit_trig = {t for t in _hit_trig if df.get(t, 0) <= _maxdf}
        _cover = 1.0 if _hit_trig else 0.0
        scored.append({'file': u['file'], 'score': round(score, 4),
                       'cls': u['cls'], '_cover': _cover,
                       'uid': u.get('uid'),
                       'head': _head(u['text']),
                       'snippet': _mem_snippet(u['text'], terms)})
    scored.sort(key=lambda r: (-r['score'], r['file']))

    # Head substitution (Condition 19/20, §6.3-6.4, MC-944 step 6). Runs over
    # the FULL ranked list, before the quota/top-k cut, so a duplicate head
    # collapsed by dedupe is backfilled from the next-ranked candidate rather
    # than shrinking the slot budget — see `_mem_apply_supersession`.
    _by_topic_file = {u['file']: u for u in units if u.get('cls') == 'topic'}
    scored = _mem_apply_supersession(scored, _mem_supersede_graph(units), _by_topic_file)

    # Positions are pulled out BEFORE the quota/top-k cut and re-inserted at the
    # front. A position that matches its own subject must reach the prompt even
    # on a busy query — being present and never surfacing is precisely how the
    # Obsidian ruling failed to stop the agent re-proposing Obsidian.
    # Positions never ride the ordinary ranking. The reserve, with its coverage
    # gate, is their ONLY admission path — otherwise a position surfaces on any
    # query that shares a common word with it, which is the prompt-furniture
    # failure the gate exists to stop.
    _positions = [r for r in scored if r.get('cls') == 'position']
    scored = [r for r in scored if r.get('cls') != 'position']

    _reserve = _position_reserve()
    _reserved = []
    if _reserve:
        # A reserved slot requires the query to actually be ABOUT the subject —
        # not merely to share a word with it. Without this gate every position
        # rides along on every task (measured: both fired on "fix the
        # cloudflare tunnel quota alarm"), which is how a standing ruling turns
        # into permanent prompt furniture and stops being read.
        _reserved = [r for r in _positions if r.get('_cover')][:_reserve]

    # Archive quota (S4): the archive outnumbers topic files ~30:1, so archive
    # lines can take slots a whole note would have filled. Cap them, keeping
    # rank order within each class. 0 = off, which is today's behaviour.
    _quota = _archive_quota()
    if _quota:
        kept, n_arch = [], 0
        for r in scored:
            if r['file'].endswith('MEMORY_ARCHIVE.md') or '#archive' in r['file']:
                if n_arch >= _quota:
                    continue
                n_arch += 1
            kept.append(r)
            if len(kept) >= max(1, topk):
                break
        scored = kept
    hits = _reserved + scored[:max(1, topk - len(_reserved))]
    n_expand = max(0, int(expand or 0))
    if n_expand:
        hits = hits + _mem_expand_links(units, hits, terms, n_expand)

    # Delivery telemetry (DAVE_DESIGN §9 phase 4) runs HERE, before the
    # internal keys are stripped: `uid` is the only thing that distinguishes one
    # archive line from the 2.5k others sharing its filename, and the public
    # result shape deliberately does not carry it. Opt-in per caller — a human
    # typing in the memory-search box is not a delivery, and counting it would
    # let anyone inflate a note's residency by searching for it.
    if record:
        try:
            from mc import memory_delivery as _deliv
            _deliv.record(project, hits, context=str(record),
                          corpus_size=len(units))
        except Exception as e:
            _log(f'[delivery] telemetry skipped: {e}')

    # `cls`/`uid`/`head` are internal bookkeeping — read-floor callers unpack
    # these dicts and a test pins the exact key set, so they must not leak out
    # unless a caller explicitly opted in via `keep_internal`.
    for _h in hits:
        _h.pop('_cover', None)
        if not keep_internal:
            _h.pop('cls', None)
            _h.pop('uid', None)
            _h.pop('head', None)
    return hits


_SUPERSEDE_NEGATION_CAP = 7
_SUPERSEDE_NEGATION_CHARS = 120


def _mem_supersede_guard_blocks(predecessor_file, head_file, by_topic_file):
    """Condition 20 / C2 (§4.3) — two independent refusals, either one blocks
    substitution. `origin`/`verified` are parsed once at tokenize time (see
    `_mem_tokenize_unit`) so each check here is a dict lookup, not a re-parse.

    1. **Verified vs. generated.** `generated` is not an `origin` value — per
       §4.1/Condition 4, EVERY note write carries a `generated: {by, at}`
       stamp. "A generated note" (Condition 20's phrase) means a note that
       has NOT separately earned `verified[]` (Condition 5: derived only for
       a human-witnessed `trigger_type: manual` session with a follow-up
       message) — i.e. the ordinary state `write_topic_note` mints today,
       since no caller appends `verified[]` yet (that lands with mint,
       step 7). So: predecessor carries `verified[]`, head does not.
    2. **Origin authority (learning-system rail, CLAUDE.md).** An
       unattended-origin successor must never supersede a predecessor that
       was not itself unattended — that covers `origin: interactive`,
       `origin: legacy` (§4.3's "legacy is read as attended-equivalent"),
       and an unstamped/legacy-import predecessor with no parseable
       `origin` at all (fail-closed, same posture as
       `_stamp_origin`/`is_unattended_session`). An interactive successor
       superseding an unattended predecessor is unaffected — only the
       unattended-over-attended direction is refused.

    True = substitution is REFUSED; the caller keeps the predecessor's own
    hit untouched, which is what "reported alongside both rather than
    performed" means here — the predecessor still surfaces on its own
    merits, unredirected, so a human sees the older/verified record instead
    of the other one silently taking its slot.
    """
    pred = by_topic_file.get(predecessor_file) or {}
    head = by_topic_file.get(head_file) or {}
    if bool(str(pred.get('fm_verified') or '').strip()) and \
            not str(head.get('fm_verified') or '').strip():
        return True
    head_origin = str(head.get('fm_origin') or '').strip()
    pred_origin = str(pred.get('fm_origin') or '').strip()
    if head_origin == 'unattended' and pred_origin != 'unattended':
        return True
    return False


def _mem_negation_entries(head_file, edges, by_topic_file):
    """Every node whose supersede chain resolves to `head_file` (direct or
    transitive), rendered one-per-line for the materialised negation block
    (§6.4). Capped at 7 entries; beyond that, one `(+N more, see <head>)`
    line — Condition 19's bound, ~120 chars/entry so the whole block stays
    near the ~900B the spec budgets.
    """
    preds = sorted(f for f in edges
                    if f != head_file and _mem_supersede_head(edges, f)[0] == head_file)
    lines = []
    overflow = 0
    for f in preds:
        if len(lines) >= _SUPERSEDE_NEGATION_CAP:
            overflow += 1
            continue
        desc = str((by_topic_file.get(f) or {}).get('fm_description') or '').strip()
        entry = f'{f}: {desc}' if desc else f
        lines.append(entry[:_SUPERSEDE_NEGATION_CHARS])
    if overflow:
        lines.append(f'(+{overflow} more, see {head_file})')
    return lines


def _mem_redirect_hit(r, head_file, edges, by_topic_file):
    """Build the redirect stub (§6.3) that replaces an OUTPACED hit's
    delivered snippet. The hit keeps its own score/rank (`r['score']` is
    untouched by the caller) — only `file`/`snippet`/`uid`/`head` change, plus
    `substituted_from` so the agent is told which note it actually matched
    (substitution is "attributed, never silent").
    """
    head_u = by_topic_file.get(head_file) or {}
    head_text = str(head_u.get('text') or '')
    conclusion = str(head_u.get('fm_description') or '').strip() or _head(head_text)
    negations = _mem_negation_entries(head_file, edges, by_topic_file)
    neg_str = ' | '.join(negations) if negations else 'none'
    stub = (f'SUPERSEDED by {head_file}.\n'
            f'CONCLUSION: {conclusion}\n'
            f'NEGATED en route ({len(negations)}): {neg_str}\n'
            f'FULL: {head_file}')
    out = dict(r)
    out['file'] = head_file
    out['snippet'] = stub
    out['uid'] = head_u.get('uid') or head_file
    out['head'] = _head(head_text)
    out['substituted_from'] = r['file']
    return out


def _mem_apply_supersession(scored, edges, by_topic_file):
    """Head-substitute OUTPACED topic hits, dedupe by head, backfill from the
    next-ranked candidate — Condition 19/20 (§6.3-6.4, MC-944 step 6).

    Runs over `scored` in its already-sorted rank order and returns a list in
    the same order, so the caller's existing quota/top-k cut is unaffected.
    Non-topic units (archive/managed/position) pass through unchanged and are
    NEVER deduped by file — every archive line legitimately shares its
    container's filename (`MEMORY_ARCHIVE.md`) with ~2.5k others; only topic
    units resolve to a distinct file per note.

    Dedupe is by the EFFECTIVE file identity a topic hit resolves to, not
    just among substituted entries: a predecessor redirected to head X and
    X's own natural (unsubstituted) hit are the same delivered identity and
    must not both take a slot. The best-ranked occurrence wins — whichever
    reaches this loop first, since `scored` is already sorted — and the
    dropped duplicate's slot is backfilled for free by simply continuing to
    the next real candidate further down the same rank-ordered list.
    """
    used = set()
    out = []
    for r in scored:
        if r.get('cls') != 'topic':
            out.append(r)
            continue
        head_file = r['file']
        node = edges.get(r['file'])
        if node and node.get('superseded_by'):
            resolved, _hops = _mem_supersede_head(edges, r['file'])
            if resolved != r['file'] and not _mem_supersede_guard_blocks(
                    r['file'], resolved, by_topic_file):
                head_file = resolved
        if head_file in used:
            continue  # dedupe: drop, next-ranked candidate fills the slot
        used.add(head_file)
        if head_file != r['file']:
            out.append(_mem_redirect_hit(r, head_file, edges, by_topic_file))
        else:
            out.append(r)
    return out


# ── Minting — WRITE/RESOLVE split (§6.5 Condition 21/22/23, build step 7) ───
#
# WRITE is deterministic and fail-open: a trigger (`mint_topic_node`'s
# caller) fires unconditionally, never a Scribe judgement call — the spec's
# own reason is that Scribe already ran in the traced 2026-09-06 incident and
# produced nothing (§6.5). RESOLVE is the one place a caller waits: the
# binary question posed on the project's next ATTENDED turn
# (`unresolved_mint_block`), answered through `resolve_mint`.
#
# Whole feature gated OFF by default (`memory_mint_triggers_enabled`) —
# report-mode-first per the build brief; no caller in this repo flips it on.

_MINT_OVERLAP_MIN_TERMS = 2   # floor: a single shared word is not overlap
_MINT_OVERLAP_TOP_N = 3       # Condition 22/23's "top 3"
_MINT_DOCS_SIZE_THRESHOLD_DEFAULT = 4096  # bytes; §16 step 7's "size threshold"


def _mint_enabled():
    """Condition 21's deterministic triggers are inert unless this is ON.
    Default OFF — report-mode-first, per the build brief's hard constraint.
    """
    return bool(state.CONFIG.get('memory_mint_triggers_enabled', False))


def _mint_docs_size_threshold():
    try:
        return max(1, int(state.CONFIG.get(
            'memory_mint_docs_size_threshold', _MINT_DOCS_SIZE_THRESHOLD_DEFAULT)))
    except (TypeError, ValueError):
        return _MINT_DOCS_SIZE_THRESHOLD_DEFAULT


def _mint_overlap_terms(name, description, triggers):
    """Term set for Condition 23's overlap test — subject/trigger vocabulary
    ONLY. Deliberately excludes anything popularity-shaped (delivery count,
    recency, corpus rank): §6.5's own incident is a cold predecessor that
    never triggers a popularity-gated check and stays cold forever, so this
    detector must never accept one.
    """
    toks = set(_mem_tokens(name)) | set(_mem_tokens(description)) | \
        set(_mem_tokens((triggers or '').replace(',', ' ')))
    return {t for t in toks if t not in _TRIGGER_STOPWORDS}


def detect_mint_overlap(project, name, description, triggers='', *,
                         top_n=_MINT_OVERLAP_TOP_N,
                         min_overlap=_MINT_OVERLAP_MIN_TERMS, units=None):
    """Condition 23: does an existing topic note already cover roughly the
    same ground as a note about to be minted? Subject/trigger overlap ALONE
    — no delivery count, no popularity term of any kind — scored as the raw
    intersection size between the incoming note's own vocabulary (its own
    `name`+`description`+`triggers`, never anyone else's usage of it) and
    each existing topic note's identical fields.

    Returns up to `top_n` `(file, overlap_count)` pairs, highest first,
    ties broken by filename for determinism. Never mutates anything — this
    is the same function both the inline per-mint WRITE check (§6.5) and a
    standalone corpus-wide report (`mint_overlap_report`, M4) call.
    """
    query_terms = _mint_overlap_terms(name, description, triggers)
    if not query_terms:
        return []
    if units is None:
        mem_path = _get_memory_path(project)
        units = _mem_corpus(mem_path.parent, mem_path.name,
                             _get_archive_path(project).name)
    scored = []
    for u in units:
        if u.get('cls') != 'topic':
            continue
        fm = _note_frontmatter(u.get('text') or '')
        cand_terms = _mint_overlap_terms(
            u['file'].rsplit('.', 1)[0], fm.get('description', ''),
            fm.get('triggers', ''))
        overlap = len(query_terms & cand_terms)
        if overlap >= min_overlap:
            scored.append((u['file'], overlap))
    scored.sort(key=lambda pair: (-pair[1], pair[0]))
    return scored[:top_n]


def mint_overlap_report(project, *, min_overlap=_MINT_OVERLAP_MIN_TERMS):
    """Condition 23's standalone M4 deliverable: a report-only, corpus-wide
    sweep flagging every PAIR of existing topic notes whose subject/trigger
    vocabulary overlaps at or above `min_overlap` — independent of any mint
    event. Never writes anything; feeds a human decision (M5), not built
    here. O(N^2) unit-scorings, same cost class as `retrievability_sweep`
    (D1) — run on demand, not per-write.

    Returns a list of {a, b, overlap} sorted by overlap desc, each pair
    reported once (a < b lexicographically).
    """
    try:
        mem_path = _get_memory_path(project)
        units = _mem_corpus(mem_path.parent, mem_path.name,
                             _get_archive_path(project).name)
    except Exception:
        return []
    topics = [u for u in units if u.get('cls') == 'topic']
    fm_terms = {}
    for u in topics:
        fm = _note_frontmatter(u.get('text') or '')
        fm_terms[u['file']] = _mint_overlap_terms(
            u['file'].rsplit('.', 1)[0], fm.get('description', ''),
            fm.get('triggers', ''))
    pairs = []
    files = sorted(fm_terms.keys())
    for i, a in enumerate(files):
        for b in files[i + 1:]:
            overlap = len(fm_terms[a] & fm_terms[b])
            if overlap >= min_overlap:
                pairs.append({'a': a, 'b': b, 'overlap': overlap})
    pairs.sort(key=lambda r: (-r['overlap'], r['a'], r['b']))
    return pairs


def _mint_slug(trigger_kind, artifact_key):
    """Deterministic slug so a replayed trigger (the same hivemind_id/
    backlog item_id/doc path firing twice) resolves to the SAME filename —
    idempotent-on-replay via `write_topic_note`'s existing never-clobber
    behaviour, with no separate dedupe table to keep in sync.
    """
    h = hashlib.sha1(str(artifact_key or '').encode('utf-8')).hexdigest()[:10]
    kind = re.sub(r'[^a-z0-9]+', '_', (trigger_kind or 'mint').lower()).strip('_')
    return f'mint_{kind}_{h}'


_MINT_TRIGGER_LABELS = {
    'hivemind_close': 'Hivemind closed',
    'backlog_done': 'Backlog item closed',
    'docs_artifact': 'Docs artifact minted',
}


def mint_topic_node(project, *, trigger_kind, subject, artifact_path='',
                     task='', trigger_type='', actor=''):
    """Condition 21/22 — the ONE deterministic mint entry point every WRITE
    trigger (hivemind close, backlog item -> done, an oversized docs/
    artifact) calls. Thin by design: subject, date, artifact path, and — when
    the overlap detector finds a candidate — `supersedes: unresolved` plus
    `mint_candidates`, never a resolved edge (that is RESOLVE's job, not
    WRITE's).

    Gated OFF entirely unless `_mint_enabled()` — report-mode-first.

    Authority guard: the SAME `mc.distiller._authority_violation` check
    Step E's habit classifier runs, on the full rendered description+body,
    before mint — a deterministic trigger is not exempt just because a
    human never phrased the sentence; the artifact's own subject text is
    caller-controlled (a hivemind goal, a backlog item's title) and could
    still contain an authority-violating phrase.

    Never raises — best-effort, like every other Scribe-adjacent side effect
    in this module (`_scribe_classify_habits`'s posture, verbatim).

    Returns the minted filename — including on a replay of the same
    (trigger_kind, artifact_key), where it hands back the EXISTING file
    rather than re-minting or clobbering it. Returns '' only when skipped
    outright: disabled, empty subject, or authority-refused.
    """
    if not _mint_enabled():
        return ''
    try:
        subject = (subject or '').strip()
        if not subject:
            return ''
        project_id = project.get('id', '') if isinstance(project, dict) else ''
        slug = _mint_slug(trigger_kind, artifact_path or subject)
        label = _MINT_TRIGGER_LABELS.get(trigger_kind, trigger_kind or 'Mint')
        snippet = subject if len(subject) <= 160 else subject[:157] + '...'
        description = f'{label}: {snippet}'
        body_lines = [f'**Trigger:** {trigger_kind}', f'**Date:** {now_iso()}']
        if artifact_path:
            body_lines.append(f'**Artifact:** {artifact_path}')
        body_lines.append('')
        body_lines.append(subject)
        body = '\n'.join(body_lines) + '\n'
        violation = _distiller._authority_violation(f'{description}\n{body}')
        if violation:
            _scribe_stat(project_id, 'mint_refused_authority')
            _log(f'[mint] {trigger_kind} mint refused (authority guard hit '
                 f'{violation!r}): {subject[:120]!r}')
            return ''
        candidates = detect_mint_overlap(project, slug.replace('_', '-'),
                                          description)
        supersedes = ''
        mint_candidates = ''
        if candidates:
            supersedes = 'unresolved'
            mint_candidates = ', '.join(c for c, _score in candidates)
        fn = write_topic_note(
            project, slug, description, body, note_type='project',
            task=task, trigger_type=trigger_type, actor=actor or trigger_kind,
            supersedes=supersedes, mint_candidates=mint_candidates)
        if fn:
            _scribe_stat(project_id, f'mint_{trigger_kind}')
            if supersedes:
                _scribe_stat(project_id, 'mint_unresolved')
            _log(f'[mint] {trigger_kind} minted: {fn}'
                 + (f' (unresolved vs {mint_candidates})' if supersedes else ''))
            return fn
        # write_topic_note returns '' both for "bad slug" and "already exists
        # at this deterministic path" — `_mint_slug` guarantees the latter
        # means an EARLIER call for this same (trigger_kind, artifact_key)
        # already minted it, so replaying the trigger must hand back that
        # same filename, not ''. This is what makes the trigger idempotent
        # rather than merely non-clobbering.
        existing = _get_memory_path(project).parent / f'{slug}.md'
        return slug + '.md' if existing.is_file() else ''
    except Exception as e:
        _log(f'[mint] {trigger_kind} mint failed: {e}')
        return ''


def scan_docs_artifacts_for_mint(project, since_ts, until_ts, *, docs_dir=None,
                                  task='', trigger_type=''):
    """Condition 21's fourth deterministic trigger: "a docs/ artifact above a
    size threshold created in the session". Filesystem-only and mtime-gated
    — no transcript/tool-call parsing, so this stays a fact about disk state,
    never a judgement about what an agent meant to do.

    A file counts when its mtime falls inside `[since_ts, until_ts]` (the
    calling session's own window) AND its size exceeds
    `memory_mint_docs_size_threshold` bytes. `since_ts`/`until_ts` are epoch
    seconds; the caller (the Scribe session-end call site) supplies the
    session's own start/end.

    `task`/`trigger_type` are the CALLING session's own provenance (forwarded
    verbatim to `mint_topic_node` -> `_stamp_origin`) — this is the one mint
    trigger with a real session in hand, unlike hivemind close or a bare
    backlog PATCH, so it is the one that can stamp origin honestly instead
    of failing safe to 'unattended'.

    Mints one thin node per qualifying file via `mint_topic_node` — that
    call is itself idempotent-on-replay (`_mint_slug` keys off the file
    path), so re-scanning the same session twice mints nothing twice.
    Returns the list of minted filenames (possibly empty).
    """
    if not _mint_enabled():
        return []
    try:
        pp = project.get('project_path', '') if isinstance(project, dict) else ''
        base = Path(docs_dir) if docs_dir else (Path(pp) / 'docs' if pp else None)
        if not base or not base.is_dir():
            return []
        threshold = _mint_docs_size_threshold()
        minted = []
        for f in sorted(base.rglob('*.md')):
            try:
                st = f.stat()
            except OSError:
                continue
            if st.st_size < threshold:
                continue
            if not (since_ts <= st.st_mtime <= until_ts):
                continue
            rel = str(f.relative_to(Path(pp))) if pp else str(f)
            fn = mint_topic_node(
                project, trigger_kind='docs_artifact',
                subject=f'{rel} ({st.st_size} bytes)', artifact_path=rel,
                task=task, trigger_type=trigger_type)
            if fn:
                minted.append(fn)
        return minted
    except Exception as e:
        _log(f'[mint] docs-artifact scan failed: {e}')
        return []


_FRONTMATTER_BLOCK_RE = re.compile(r'\A---\n(.*?\n)---\n', re.S)


def _rewrite_frontmatter_field(text, field, value):
    """Set (or, when `value` is falsy, remove) one top-level `field: value`
    scalar line inside a note's frontmatter block, leaving everything else
    — including the body and nested blocks like `generated:` — untouched.
    Matching is anchored to line-start so `generated:`'s indented `by`/`at`
    children (which also contain ':') are never mistaken for a top-level key.
    No-op (returns `text` unchanged) if the file has no frontmatter block.
    """
    m = _FRONTMATTER_BLOCK_RE.match(text)
    if not m:
        return text
    block = m.group(1)
    key_re = re.compile(rf'^{re.escape(field)}:.*\n', re.M)
    block = key_re.sub('', block)
    if value:
        block += f'{field}: {value}\n'
    return text[:m.start(1)] + block + text[m.end(1):]


def resolve_mint(project, filename, verdict, candidate=''):
    """RESOLVE half of Condition 22 (§6.5) — answers the one-word question
    `unresolved_mint_block` posed. `verdict` is `'supersedes'` (the mint
    really does replace `candidate`, so the sentinel is rewritten into the
    real edge `_mem_supersede_graph` reads) or `'unrelated_to'` (the cheap
    answer: `supersedes: unresolved` is removed entirely, so the node reverts
    to a plain STANDING mint with no predecessor claim). Either way
    `mint_candidates` is cleared — its only job was carrying the question.

    Refuses (returns False, no write) unless the note's CURRENT frontmatter
    still reads `supersedes: unresolved` — answering an already-resolved or
    never-unresolved note is a no-op, not a way to fabricate an edge on an
    arbitrary note by filename.

    Takes the same per-project topic write lock `write_topic_note` uses, and
    re-checks the sentinel inside it (TOCTOU — two answers racing the same
    question must not both apply).
    """
    if verdict not in ('supersedes', 'unrelated_to'):
        raise ValueError("verdict must be 'supersedes' or 'unrelated_to'")
    name = str(filename or '').strip()
    if not name or '/' in name or '\\' in name or not name.endswith('.md'):
        raise ValueError('not a topic note filename')
    mem_dir = _get_memory_path(project).parent
    path = mem_dir / name
    if path.resolve().parent != mem_dir.resolve():
        raise ValueError('not a topic note filename')
    project_id = project.get('id', '') if isinstance(project, dict) else ''
    with _get_mem_write_lock(f'topic:{project_id}'):
        if not path.is_file():
            return False
        text = path.read_text(encoding='utf-8', errors='replace')
        if _note_frontmatter(text).get('supersedes') != 'unresolved':
            return False
        if verdict == 'supersedes':
            text = _rewrite_frontmatter_field(text, 'supersedes', candidate)
        else:
            text = _rewrite_frontmatter_field(text, 'supersedes', '')
            text = _rewrite_frontmatter_field(text, 'unrelated_to', candidate)
        text = _rewrite_frontmatter_field(text, 'mint_candidates', '')
        _atomic_write_text(path, text)
    return True


def unresolved_mint_block(project, *, task='', trigger_type='', incognito=False):
    """The RESOLVE side's delivery: one reserved-slot, one-word question,
    surfaced ONLY on an ATTENDED turn (§6.5 — "the only place a caller is
    genuinely waiting"). An unattended/scheduled/steward turn gets nothing
    here, same gate `_stamp_origin` uses everywhere else, so an autonomous
    cycle can never be the one that answers (or silently ignores) it.

    Picks at most ONE pending unresolved mint per call (oldest by mtime) —
    this is a question, not a feed; surfacing several at once trains the
    same "prompt furniture" failure the position reserve gate exists to
    avoid. Returns '' when incognito, unattended, disabled, or nothing is
    pending.
    """
    if incognito or not _mint_enabled():
        return ''
    if _distiller.is_unattended_session(task, trigger_type):
        return ''
    try:
        mem_dir = _get_memory_path(project).parent
    except Exception:
        return ''
    if not mem_dir.is_dir():
        return ''
    pending = []
    for f in mem_dir.glob('*.md'):
        if _is_position_file(f.name) or f.name == CONTINUITY_FILE:
            continue
        try:
            text = f.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue
        fm = _note_frontmatter(text)
        if fm.get('supersedes') != 'unresolved':
            continue
        try:
            mtime = f.stat().st_mtime
        except OSError:
            mtime = 0.0
        pending.append((mtime, f.name, fm))
    if not pending:
        return ''
    pending.sort(key=lambda r: r[0])
    _mtime, fname, fm = pending[0]
    candidates = [c.strip() for c in fm.get('mint_candidates', '').split(',') if c.strip()]
    cand_line = ', '.join(candidates) if candidates else '(none recorded)'
    project_id = project.get('id', '') if isinstance(project, dict) else ''
    return (
        "--- MEMORY MINT NEEDS ONE WORD (this project only) ---\n"
        f"  {fname} — \"{fm.get('description', '')}\"\n"
        f"  mint_candidates: {cand_line}\n"
        "  Answer with exactly one, verbatim, in your reply:\n"
        "    supersedes:<candidate-slug>   (this note really does replace that one)\n"
        "    unrelated_to:<candidate-slug> (no relation — pick the closest candidate)\n"
        f"  Recorded via: POST /api/project/{project_id}/memory/mints/{fname}/resolve "
        '{"verdict":"supersedes|unrelated_to","candidate":"<slug>"}')


def _mem_snippet(text, terms):
    """A ~400-char window around the first query term, else the note's head."""
    low = (text or '').lower()
    pos = min((low.find(t) for t in (terms or []) if t in low), default=0)
    start = max(0, pos - 120)
    return (text or '')[start:start + 400].replace('\n', ' ').strip()


def _mem_expand_links(units, hits, terms, n_expand):
    """One `[[wikilink]]` hop out from `hits` — see _memory_search's LINK EXPANSION.

    Returns at most n_expand extra result dicts, each with `via` naming the hit
    it was reached from. Notes already in `hits` are skipped, so expansion never
    duplicates and never displaces a lexical match.
    """
    graph = _mem_link_graph(units)
    if not graph:
        return []
    by_file = {u['file']: u for u in units if u.get('cls') == 'topic'}
    have = {h['file'] for h in hits}
    cand = {}
    for h in hits:
        node = graph.get(h['file'])
        if not node:
            continue
        for direction, decay in (('out', _LINK_DECAY_OUT), ('in', _LINK_DECAY_IN)):
            for nbr in node[direction]:
                if nbr in have:
                    continue
                sc = round(h['score'] * decay, 4)
                prev = cand.get(nbr)
                # A note linked from two different hits keeps the strongest
                # path, and out-beats-in is settled by the decay itself.
                if prev is None or sc > prev['score']:
                    cand[nbr] = {'file': nbr, 'score': sc, 'via': h['file'],
                                 'link': direction,
                                 'uid': (by_file.get(nbr) or {}).get('uid') or nbr}
    out = []
    for c in sorted(cand.values(), key=lambda r: (-r['score'], r['file']))[:n_expand]:
        u = by_file.get(c['file'])
        c['snippet'] = _mem_snippet(u['text'] if u else '', terms)
        c['head'] = _head((u or {}).get('text'))
        out.append(c)
    return out


def _mem_class_avgdl(units):
    """Mean document length per unit class (see _memory_search's adaptation)."""
    tot, cnt = {}, {}
    for u in units:
        c = u['cls']
        tot[c] = tot.get(c, 0) + u['len']
        cnt[c] = cnt.get(c, 0) + 1
    return {c: (tot[c] / cnt[c]) if cnt[c] else 1.0 for c in tot}


# ── D0/D1 — discovery, zero authoring (§7.2, §7.3, §7.4; build step 3) ──────
#
# D0 derives a default `triggers:` vocabulary for a note that has none, from
# its own `name` + `description` — no human types anything (Condition 27).
# D1 is the retrievability gate (§7.4) run as a full-vault sweep: for every
# topic note, would its OWN default vocabulary find it? The failures are the
# work list for whatever authoring step comes next — this step performs none.

_TRIGGER_STOPWORDS = _POSITION_STOPWORDS  # same 28-word list, generalized (Cond 27)


def _trigger_phrase_bigrams_enabled():
    try:
        return bool(state.CONFIG.get('trigger_phrase_bigrams', True))
    except Exception:
        return True


def _note_default_triggers(name, description):
    """Condition 27: default arrival vocabulary derived from `name` +
    `description`, zero authoring cost. Returns (singles, phrases):
    `singles` is the raw token set (the df gate is applied by the caller,
    per unit class — Condition 29); `phrases` is the adjacent-token bigram
    list (Condition 28), which is EXEMPT from the df gate by construction —
    a phrase needs every one of its terms present, and that conjunction is
    its own rarity test.
    """
    name_stem = str(name or '').rsplit('.', 1)[0]
    toks = _mem_tokens(name_stem) + _mem_tokens(description or '')
    kept = [t for t in toks if t not in _TRIGGER_STOPWORDS]
    singles = set(kept)
    phrases = []
    if _trigger_phrase_bigrams_enabled():
        seen = set()
        for a, b in zip(kept, kept[1:]):
            phrase = f'{a} {b}'
            if phrase not in seen:
                seen.add(phrase)
                phrases.append(phrase)
    return singles, phrases


def _class_df(units, cls):
    """Document frequency of every term, computed over ONE unit class only
    (Condition 29). Archive is 87% of the corpus and echoes prompts, which
    would inflate every subject word's df and make the gate stricter than
    anyone chose if it were computed corpus-wide.
    """
    class_units = [u for u in units if u.get('cls') == cls]
    df: dict = {}
    for u in class_units:
        for t in u['tf']:
            df[t] = df.get(t, 0) + 1
    return df, len(class_units)


def _df_gate_pass(term, df, n_docs):
    """A single term passes the rarity gate (Condition 28/29) unless it is
    common enough, IN THIS CLASS, to be prompt furniture. Mirrors
    `_position_trigger_max_df`'s floor: a term in `_POSITION_TRIGGER_MIN_DOCS`
    or fewer documents is rare by any measure and always passes, so the test
    cannot fire meaninglessly on a tiny corpus.
    """
    d = df.get(term, 0)
    if d <= _POSITION_TRIGGER_MIN_DOCS:
        return True
    return d <= _position_trigger_max_df() * max(1, n_docs)


def _note_frontmatter(text):
    """Best-effort name/description/triggers/supersedes read from a topic
    note's frontmatter, for D0/D1, supersession (§6, MC-944 step 6) and audit
    tooling. `_mem_tokenize_unit` DOES call this now (MC-944 step 6) to pull
    `supersedes`/`origin`/`verified`/`description` into the unit dict for
    supersession bookkeeping — but only that bookkeeping. §10.4 point 1's
    invariant still holds where it matters: this parse never feeds `tf`/BM25
    scoring, so topic-note frontmatter stays unparsed by the live ranker.

    `origin`/`verified` are read best-effort for Condition 20's supersession
    guard. `verified` is §4.1's `[ {by:, at:}, … ]` block sequence, which the
    frontmatter parser (`mc/skills.py` — "no nested maps, no flow-style") does
    not structure-parse; a non-empty raw value is treated as a truthy human-
    witness SIGNAL here, never counted or indexed by entry.

    `mint_candidates`/`unrelated_to` (§6.5 Condition 22, MC-944 step 7): read
    for `resolve_mint`/`unresolved_mint_block` only — a comma-joined slug
    list and RESOLVE's own negative answer, respectively. Neither feeds
    scoring or the supersede graph.
    """
    try:
        meta, _b = _skills.parse_skill_md(text)
    except Exception:
        return {}
    if not isinstance(meta, dict) or not meta:
        return {}
    return {'name': str(meta.get('name') or ''),
            'description': str(meta.get('description') or ''),
            'triggers': str(meta.get('triggers') or ''),
            'supersedes': str(meta.get('supersedes') or '').strip(),
            'origin': str(meta.get('origin') or '').strip(),
            'verified': str(meta.get('verified') or '').strip(),
            'mint_candidates': str(meta.get('mint_candidates') or '').strip(),
            'unrelated_to': str(meta.get('unrelated_to') or '').strip()}


def note_triggers(project, name, description, explicit_triggers='', units=None):
    """The full trigger set a note fires on: explicit `triggers:` (always
    kept, exempt from the df gate — Condition 27/§7.2: a human naming a term
    is stating intent) UNIONED with the df-gated D0 default singles, plus the
    always-exempt default phrases returned separately. `units` lets a caller
    supply an already-built corpus (D1's sweep builds it once for every note
    instead of once per note — O(N) instead of O(N^2) just for this part).

    Returns (terms: set[str], phrases: list[str]).
    """
    explicit = set(_mem_tokens((explicit_triggers or '').replace(',', ' ')))
    singles, phrases = _note_default_triggers(name, description)
    if units is None:
        mem_path = _get_memory_path(project)
        units = _mem_corpus(mem_path.parent, mem_path.name,
                             _get_archive_path(project).name)
    df, n_docs = _class_df(units, 'topic')
    gated = {t for t in singles if _df_gate_pass(t, df, n_docs)}
    return explicit | gated, phrases


def _note_retrievable(project, note_file, terms, phrases, topk=None):
    """§7.4's guarantee, CHECKED rather than asserted:

        retrievable(n) <=> n in top_k(query = triggers(n) U tokens(description(n)))

    One search, O(N) — safe at write time for the single note being written.
    Returns (ok: bool, top_hit_files: list[str]) so a caller can report the
    specific miss (Condition: "flagged with the specific miss, and the fix is
    to add triggers:, not to add a pointer").
    """
    topk = topk if topk is not None else int(
        state.CONFIG.get('read_floor_topk', 6) or 6)
    query = ' '.join(sorted(terms) + list(phrases))
    if not query.strip():
        return False, []
    hits = _memory_search(project, query, topk=topk, expand=0)
    hit_files = [h['file'] for h in hits]
    return note_file in hit_files, hit_files


def retrievability_sweep(project):
    """D1 (§7.4, §11.2 phase D1): the full-vault sweep. O(N^2) unit-scorings
    (§13.2 prices it) — never called per-write, only on a schedule or as an
    explicit audit run. For every topic note currently in the corpus, derives
    its D0 vocabulary and checks retrievability under it.

    Returns a list of {file, terms, phrases, retrievable, top_hits}, sorted by
    filename — "the failures are the work list for everything that follows"
    (§16 step 3): D2+ only hand-author `triggers:` for notes that fail here.
    """
    mem_path = _get_memory_path(project)
    mem_dir = mem_path.parent
    units = _mem_corpus(mem_dir, mem_path.name, _get_archive_path(project).name)
    by_file = {u['file']: u for u in units if u.get('cls') == 'topic'}
    out = []
    for fname in sorted(by_file):
        u = by_file[fname]
        meta = _note_frontmatter(u['text'])
        terms, phrases = note_triggers(
            project, fname, meta.get('description', ''),
            meta.get('triggers', ''), units=units)
        ok, hit_files = _note_retrievable(project, fname, terms, phrases)
        out.append({'file': fname, 'terms': sorted(terms), 'phrases': phrases,
                    'retrievable': ok, 'top_hits': hit_files})
    return out


def _condense_combined_bytes(project):
    """Combined size of a project's MEMORY.md + SESSION_LOG.md + archive
    (0 if absent). SESSION_LOG.md added in §16 step 4 — the managed region's
    bytes moved OUT of MEMORY.md, not out of what this gauge should measure."""
    total = 0
    for p in (_get_memory_path(project), _get_session_log_path(project),
             _get_archive_path(project)):
        try:
            if p and p.exists():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def _session_log_ring():
    """§9.2 B2's entry-count ring — an ENTRY count, not a byte cap: legible to
    a human ("the last 20 sessions"), and immune to the mean-entry-size drift
    (325 -> 510 B measured) a byte cap would silently convert into fewer
    entries."""
    try:
        return max(1, int(state.CONFIG.get('session_log_ring', 20) or 20))
    except (TypeError, ValueError):
        return 20


def _session_log_read(project, *, strict=False):
    """(entries, wm_markers) from SESSION_LOG.md — pure read, no migration.
    Empty lists if the file does not exist yet. Writers require strict=True:
    an unreadable existing log must never become an empty replacement."""
    path = _get_session_log_path(project)
    if not path.exists():
        return [], []
    try:
        text = path.read_text(encoding='utf-8')
    except Exception as e:
        _log(f"[memory] session log read failed: {e}")
        if strict:
            raise
        return [], []
    _curated, entries, wm = _mem_split_full(text)
    return entries, wm


def _session_log_entries(project):
    """Just the entries — the read `_should_condense`'s structured-mode
    trigger needs, without the wm-marker bookkeeping."""
    entries, _wm = _session_log_read(project)
    return entries


def _write_curated_only(mem_path, curated):
    """MEMORY.md post-split (§16 step 4): curated pointer lines, nothing
    else — no sentinel, no managed block, ever. Trailing-whitespace-trimmed,
    matching `_mem_migrate`'s existing "curated content preserved verbatim
    modulo trailing whitespace" contract."""
    text = (curated or '').rstrip()
    _atomic_write_text(mem_path, (text + '\n') if text else '')


def _write_session_log(project, entries, wm_markers):
    """SESSION_LOG.md's canonical form — reuses `_mem_compose` with an empty
    curated prefix, so the sentinel/header/wm-marker format is byte-identical
    to what MEMORY.md's managed block used to look like, just in its own
    file."""
    path = _get_session_log_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, _mem_compose('', entries or [], wm_markers or []))


def _wm_merge(primary, secondary):
    """Union of two wm_markers lists, de-duplicated by session_id — `primary`
    wins a collision. Used only by the one-time migration and by
    `_commit_managed_entry`'s tolerance for an unmigrated MEMORY.md that still
    carries markers inline (§10.4: both formats coexist for the whole
    migration)."""
    have = {(_wm_parse(ln) or {}).get('session_id') for ln in (primary or [])}
    return list(primary or []) + [
        ln for ln in (secondary or [])
        if (_wm_parse(ln) or {}).get('session_id') not in have]


def migrate_session_log_split(project):
    """§16 step 4's migration: pull any managed content still inline in
    MEMORY.md (the sentinel-delimited block: entries + wm markers) out into
    SESSION_LOG.md.

    §11 design constraints: (1) NOTHING IS DELETED — entries and wm markers
    move verbatim, curated lines are preserved modulo trailing whitespace
    (the same contract `_mem_migrate` already keeps); (2) RE-RUNNABLE — an
    already-migrated project (MEMORY.md carries no sentinel block) round-trips
    the curated text unchanged and moves zero entries/markers on a second
    run, because the first run already emptied MEMORY.md's managed region.

    Returns a stats dict: moved_entries/moved_wm_markers count what THIS run
    pulled out of MEMORY.md specifically (0 on a re-run), plus the resulting
    file sizes/line counts for reporting.
    """
    project_id = project.get('id', '') if isinstance(project, dict) else ''
    mem_path = _get_memory_path(project)
    with _get_mem_write_lock(project_id):
        existing = mem_path.read_text(encoding='utf-8') if mem_path.exists() else ''
        before_bytes = len(existing.encode('utf-8'))
        before_lines = len(existing.splitlines())
        curated, legacy_entries, legacy_wm = _mem_split_full(_mem_migrate(existing))
        log_entries, log_wm = _session_log_read(project, strict=True)
        merged_entries = legacy_entries + log_entries
        merged_wm = _wm_merge(log_wm, legacy_wm)
        existing_log_sids = {(_wm_parse(ln) or {}).get('session_id') for ln in log_wm}
        moved_wm = sum(1 for ln in legacy_wm
                       if (_wm_parse(ln) or {}).get('session_id')
                       not in existing_log_sids)
        _write_curated_only(mem_path, curated)
        _write_session_log(project, merged_entries, merged_wm)
        after_text = mem_path.read_text(encoding='utf-8') if mem_path.exists() else ''
    return {
        'moved_entries': len(legacy_entries),
        'moved_wm_markers': moved_wm,
        'memory_md_bytes_before': before_bytes,
        'memory_md_lines_before': before_lines,
        'memory_md_bytes_after': len(after_text.encode('utf-8')),
        'memory_md_lines_after': len(after_text.splitlines()),
        'session_log_entries': len(merged_entries),
        'session_log_wm_markers': len(merged_wm),
    }


def _set_condense_status(pid, **kw):
    with _condense_lock:
        cur = _condense_status.get(pid, {})
        cur.update(kw)
        _condense_status[pid] = cur


def _get_condense_status(pid):
    with _condense_lock:
        st = _condense_status.get(pid)
        return dict(st) if st else {'state': 'idle'}


def _has_running_agent(project_id):
    """Return True if any non-housekeeping agent is running or idle for this project."""
    for s in agent_sessions.values():
        if s.get('project_id') == project_id and not s.get('housekeeping'):
            if s.get('status') in ('running', 'idle'):
                return True
    return False


def _should_condense(project, include_claude_md=False):
    """Check whether memory condensation should be triggered for this project.

    If include_claude_md is True, also count the project's CLAUDE.md in the size check.
    This is used by the pre-dispatch context budget check.
    """
    if not state.CONFIG.get('condense_enabled', True):
        return False
    pid = project['id']
    with _condense_lock:
        if pid in _condensing_projects:
            return False
        # Cooldown: don't re-trigger within 1 hour of the last dispatch. This
        # prevents the pre-dispatch check from firing on back-to-back sessions
        # when CLAUDE.md + MEMORY.md keep the total above threshold while the
        # previous condense job is still running or just finished.
        _cooldown = int(state.CONFIG.get('condense_cooldown_secs', 3600) or 3600)
        if _time.time() - _condense_triggered_at.get(pid, 0) < _cooldown:
            return False
    # Skip running-agent check when called from pre-dispatch (agent hasn't started yet)
    if not include_claude_md and _has_running_agent(pid):
        return False
    # The structured executor is line-keyed and only ever acts on MEMORY.md's
    # managed region. Trigger it on the auto-loaded file's LINE count vs. the
    # model-tier budget — NOT on combined bytes. Byte-keying would let a large
    # CLAUDE.md (which structured deliberately doesn't touch) keep the trigger
    # permanently hot, firing a no-op model call every session-end. This also
    # makes the structured trigger and its target agree in units (closes
    # docs/CONDENSE_STRUCTURED_DESIGN.md Open Question #5). The legacy agent
    # path keeps its existing combined-byte trigger below, unchanged.
    if (state.CONFIG.get('condense_mode', 'agent') or 'agent') == 'structured':
        mem_path = _get_memory_path(project)
        if not mem_path.exists():
            return False
        try:
            text = mem_path.read_text(encoding='utf-8')
        except Exception:
            return False  # a trigger check must never raise
        n_lines = len(text.splitlines())
        over_lines = n_lines > int(
            state.CONFIG.get('index_line_budget', 160) or 160)
        # The index budget is BYTES, not lines: a file can pass the line
        # budget and still cost more per prompt than we agreed to spend.
        # Byte pressure is an equally valid trigger (2026-07-15 revisit).
        over_bytes = len(text.encode('utf-8')) > _index_byte_cap()
        if not (over_lines or over_bytes):
            return False
        # Structured condense only ever acts on managed entries — with zero
        # of them, every dispatch is a guaranteed no-op model call. Two
        # curated-bloated projects had racked up 378 such no-ops by
        # 2026-07-15 because this trigger stayed permanently hot. Skip, but
        # make the un-actionable bloat loudly visible (once per run per
        # project): only a human or the condense model tier may shrink the
        # curated region.
        # §16 step 4: the managed region this branch means to check now lives
        # in SESSION_LOG.md, not inline in MEMORY.md's own text (a migrated
        # project's MEMORY.md carries no entries at all, ever). A legacy
        # unmigrated MEMORY.md's inline entries are still merged in — same
        # coexist read shape _condense_plan/_condense_apply use.
        try:
            _c, legacy_entries, _w = _mem_split_full(_mem_migrate(text))
            entries = legacy_entries + _session_log_entries(project)
        except Exception:
            return False
        if not entries:
            if over_bytes and project.get('id', '') not in _curated_cap_warned:
                _curated_cap_warned.add(project.get('id', ''))
                _log(f"[condense] {project.get('id', '')}: MEMORY.md is "
                     f"{len(text.encode('utf-8')) // 1024}KB, over the "
                     f"{_index_byte_cap() // 1024}KB index budget, with NO "
                     f"managed entries to demote — every prompt of every "
                     f"session pays for it. Curated region needs human "
                     f"curation (overflow to MEMORY_ARCHIVE.md); structured "
                     f"condense cannot act.")
            return False
        return True
    mem_path = _get_memory_path(project)
    archive_path = _get_archive_path(project)
    session_log_path = _get_session_log_path(project)
    combined = 0
    if mem_path.exists():
        combined += mem_path.stat().st_size
    if session_log_path.exists():
        combined += session_log_path.stat().st_size
    if archive_path.exists():
        combined += archive_path.stat().st_size
    if include_claude_md:
        pp = project.get('project_path', '')
        if pp:
            claude_md = Path(pp) / 'CLAUDE.md'
            if claude_md.exists():
                try:
                    combined += claude_md.stat().st_size
                except OSError:
                    pass
    threshold = state.CONFIG.get('condense_threshold_kb', 30) * 1024
    return combined > threshold


_MEM_ARCHIVE_HEADER = '## Archived Session Log'

# A CHOSEN BUDGET, NOT A HARD LIMIT (corrected 2026-08-05 by Ron, who set the
# number). Earlier comments here and in CLAUDE.md called ~24KB a "harness read
# cap" past which content "silently vanishes from agent context" — that is
# wrong, and the distinction changes what the failure looks like. MEMORY.md is
# read natively by the Claude CLI; nothing truncates it. 24KB is simply what we
# decided the auto-loaded index is worth spending on EVERY prompt of EVERY
# session (~6k tokens). Going over costs tokens and dilutes context; it does not
# lose data. The two incidents this file remembers (watermark leak 2026-07-11,
# curated bloat 2026-07-15) were real, but the mechanism was OUR OWN floor
# eviction pushing entries to the archive — front-page visibility lost, not
# content destroyed.
#
# So: treat overruns as a cost problem to be designed away (an index whose
# resident size is O(1) in project age), not as a cliff to be defended by
# trimming harder. Line budgets can't see this one because it is bytes.
# Floor eviction aims 1KB under the budget for headroom.
_INDEX_BYTE_CAP_DEFAULT = 24 * 1024


def _index_byte_cap():
    try:
        v = int(state.CONFIG.get('index_byte_budget', 0) or 0)
    except Exception:
        v = 0
    return v if v > 0 else _INDEX_BYTE_CAP_DEFAULT


def _index_byte_floor():
    return max(1024, _index_byte_cap() - 1024)


class MemoryCapExceeded(Exception):
    """A candidate MEMORY.md write would push the file over index_byte_budget.

    Raised only by `_enforce_index_cap` (MC-917) — the hard gate for the
    ATTENDED write paths (the /memory PUT + /memory/append API routes), where
    a request/response caller can read the numbers and consolidate in the
    same turn. Background writers (`_commit_managed_entry`, the structured
    condense fold-into-curated step) are documented never-raise and call
    `_index_overflow()` directly instead — see that function's docstring for
    why a hard raise is wrong there.
    """

    def __init__(self, current_bytes, budget_bytes, overflow_bytes):
        self.current_bytes = current_bytes
        self.budget_bytes = budget_bytes
        self.overflow_bytes = overflow_bytes
        super().__init__(
            f'MEMORY.md write refused: {current_bytes}B would exceed the '
            f'{budget_bytes}B index budget by {overflow_bytes}B.')


def _index_overflow(candidate_text):
    """Single source of truth for 'does this MEMORY.md content fit the
    budget' (MC-917). Every write path that can grow the file's on-disk
    bytes checks here — directly (non-raising callers) or through
    `_enforce_index_cap` (raising callers) — so the cap is defined once.

    Returns None if candidate_text fits within index_byte_budget, else the
    3-tuple (current_bytes, budget_bytes, overflow_bytes).
    """
    n = len(candidate_text.encode('utf-8'))
    cap = _index_byte_cap()
    if n <= cap:
        return None
    return n, cap, n - cap


def _enforce_index_cap(candidate_text):
    """Hard gate: raise MemoryCapExceeded if candidate_text would push
    MEMORY.md over index_byte_budget, so the caller never persists it.

    A refused write leaves the on-disk file byte-for-byte untouched — the
    caller (project_routes.save_memory / append_memory) catches the
    exception before any write happens and returns the numbers to the
    requester, who can shrink the content (trim/merge curated pointer lines,
    or move detail into a topic file and leave a one-line pointer — the
    existing project convention) and retry in the SAME turn. That is the
    forcing function MC-917 asks for, modeled on Hermes's hard-refuse cap
    (docs/research/HERMES_AGENT_COMPETITIVE_READ.md) — same mechanism, our
    24KB budget kept (Hermes's 3,575-char total is not ours to copy; memory
    depth is a differentiator here, see
    discovery-index-byte-cap-curated-bloat).

    Only the attended API routes call this. `_commit_managed_entry` is
    documented "Never raises" (background Scribe/checkpoint/teardown writer
    with no request/response caller to hand an error to — see its
    docstring); it already has its own non-raising overflow response, the
    mechanical line+byte floor that evicts managed entries to the archive.
    Calling this here would either break that contract or be silently
    swallowed, neither of which is the forcing function this exists for.
    """
    ov = _index_overflow(candidate_text)
    if ov is not None:
        raise MemoryCapExceeded(*ov)


# ── The demoter (§9.1 Condition 36/37, §16 step 4) ───────────────────────────
#
# Once MEMORY.md holds nothing but curated content (the split, above), a cap
# with nothing else to evict either evicts curated or is not a cap — but the
# eviction is a DEMOTION, never a deletion: the pointer line goes, the note
# stays, reachable by BM25 and the one hop. A line with no resolvable target
# (59% of curated bytes corpus-wide, measured) is NOT demotable — deleting it
# deletes the only copy of that knowledge — it is MINTABLE, and the refusal
# list below is D4's work list (§11 phase D4, out of scope here).
#
# NOT WIRED TO FIRE AUTOMATICALLY YET (Condition 37): the cap that would
# actually trigger a demotion is the terminal 8,192 B value, gated on D4
# completing; this step's cap stays at the current budget (index_byte_budget,
# 24,576 B by default), which — as the step 1-3 commits' own vault shows — is
# not being exceeded today. This section builds and tests the mechanism; it
# does not flip the switch.

_CURATED_POINTER_RE = re.compile(r'\[([^\]]*)\]\(([^)\s]+)\)')


class DemotionRefused(ValueError):
    """Raised by `demote_line` for a line `demote_candidates` did not list as
    demotable — see that function's docstring."""


def _curated_pointer_lines(curated_text):
    """Every '- ' line in the curated region, tagged with its markdown-link
    target if it has one. Returns [(line_index, line_text, label, target_stem
    or None), ...]. A line with no markdown link, or a link whose target
    can't be resolved to an in-vault note, carries `target=None` — Condition
    37's "not demotable, it is mintable" case.
    """
    out = []
    for i, ln in enumerate(curated_text.splitlines()):
        if not ln.strip().startswith('-'):
            continue
        m = _CURATED_POINTER_RE.search(ln)
        if not m:
            out.append((i, ln, '', None))
            continue
        label, target = m.group(1), m.group(2).split('#', 1)[0].strip()
        stem = target[:-3] if target.lower().endswith('.md') else target
        out.append((i, ln, label, stem or None))
    return out


def demote_candidates(project):
    """{'demotable': [...], 'refused': [...]} — every curated pointer-shaped
    line, sorted into whether it has a target this vault can actually
    resolve to a live topic note (Condition 36) or not (Condition 37).
    Report-only: identifies candidates, removes nothing.
    """
    mem_path = _get_memory_path(project)
    if not mem_path.exists():
        return {'demotable': [], 'refused': []}
    curated, _e, _w = _mem_split_full(mem_path.read_text(encoding='utf-8'))
    units = _mem_corpus(mem_path.parent, mem_path.name,
                        _get_archive_path(project).name)
    topic_keys = {_mem_link_key(u['file'].rsplit('.', 1)[0])
                  for u in units if u.get('cls') == 'topic'}
    demotable, refused = [], []
    for i, ln, label, stem in _curated_pointer_lines(curated):
        row = {'line': i, 'text': ln, 'label': label, 'target': stem}
        if stem and _mem_link_key(stem) in topic_keys:
            demotable.append(row)
        else:
            refused.append(row)
    return {'demotable': demotable, 'refused': refused}


def demote_line(project, line_index):
    """Remove ONE curated pointer line by its line index in the curated
    region — a demotion, never a deletion (the note file itself is never
    touched). Refuses (`DemotionRefused`) a line that is not in
    `demote_candidates`'s `demotable` list — Condition 37. Returns the
    removed line's raw text.
    """
    cand = demote_candidates(project)
    match = next((c for c in cand['demotable'] if c['line'] == line_index), None)
    if match is None:
        is_refused = any(c['line'] == line_index for c in cand['refused'])
        reason = ('no resolvable target — not demotable, only mintable (§11 D4)'
                  if is_refused else 'not a demotable pointer line')
        raise DemotionRefused(f'line {line_index}: {reason}')
    project_id = project.get('id', '') if isinstance(project, dict) else ''
    with _get_mem_write_lock(project_id):
        mem_path = _get_memory_path(project)
        text = mem_path.read_text(encoding='utf-8') if mem_path.exists() else ''
        curated, _e, _w = _mem_split_full(text)
        lines = curated.splitlines()
        if line_index >= len(lines) or lines[line_index] != match['text']:
            raise DemotionRefused(
                f'line {line_index}: curated region changed since it was read')
        demoted = lines.pop(line_index)
        new_curated = '\n'.join(lines)
        _write_curated_only(mem_path, new_curated)
        cap = _index_byte_cap()
        n = len(new_curated.encode('utf-8'))
        _log(f'[mem-index] {project_id}: demoted pointer '
             f'"{match["label"] or demoted.strip()[:60]}" → {match["target"]} '
             f'(index at {n}/{cap} B; note remains searchable)')
    return demoted


# Projects already warned (once per server run) that their curated region
# alone exceeds the harness cap and machinery cannot shrink it.
_curated_cap_warned: set = set()

# Harness-generated tasks carry a long boilerplate prompt as their "task", and
# the entry title is just `task[:80]`. A steward cycle's title is therefore ~85
# bytes of pure prompt with zero information about what the cycle DID. This
# repo's own index had 16 of them (1.4KB of identical titles) by 2026-08-05.
# Pinned to steward.fence.STEWARD_MARKER by test (same discipline as
# distiller.STEWARD_TASK_MARKER) — mc.memory must not import a sibling package.
_STEWARD_TASK_MARKER = '[Steward cycle]'
_STEWARD_LABEL = 'Steward cycle'

# How many managed entries to keep per (date, label) group. The mechanical
# floor evicts strictly oldest-first, which is blind to repetition: on
# 2026-08-05 all 16 managed entries in this repo were same-day steward cycles
# (6.2KB of a 6.9KB region, heavily overlapping), so the floor was evicting
# real multi-day history to make room for redundant same-day noise. Collapsing
# a group first preserves entry DIVERSITY under the same byte budget. Surplus
# goes to the permanent archive verbatim — nothing is deleted, only demoted.
_MANAGED_DUP_KEEP = 3

_ENTRY_HEAD_PAT = r'^- \[([^\]]+)\]\s+\*\*(.*?)\*\*'


def _entry_label(task):
    """Compact title for a managed entry. Known harness prompts collapse to
    their marker; every other task keeps its text verbatim (capped at 80)."""
    t = (task or '').strip()
    if _STEWARD_TASK_MARKER in t[:60]:
        return _STEWARD_LABEL
    return t[:80]


def _entry_group_key(line):
    """(date, normalized-label) for a '- [' entry line, or None if unparseable.

    Normalization is for GROUPING ONLY — the line itself is never rewritten, so
    the byte-preservation guarantee on existing entries holds. It folds legacy
    boilerplate titles onto the compact label so pre-fix and post-fix steward
    entries collapse into one group instead of two."""
    import re  # module has no top-level `re` import (see _re_auth pattern)
    m = re.match(_ENTRY_HEAD_PAT, line or '')
    if not m:
        return None
    date, label = m.group(1), m.group(2).strip()
    if _STEWARD_TASK_MARKER in label[:60]:
        label = _STEWARD_LABEL
    return (date, label)


def _collapse_duplicate_entries(mem_entries, keep=None):
    """Keep at most `keep` entries per (date, label) group, newest wins.

    Returns (kept_entries, overflow) with kept_entries in their original
    chronological order and overflow (the demoted surplus, also in original
    order) destined for the archive. Entries that don't parse are never
    grouped and never dropped — unparseable input must not lose history."""
    keep = _MANAGED_DUP_KEEP if keep is None else keep
    if keep < 1:
        return list(mem_entries), []
    seen = {}
    for idx, line in enumerate(mem_entries):
        key = _entry_group_key(line)
        if key is not None:
            seen.setdefault(key, []).append(idx)
    drop = set()
    for idxs in seen.values():
        if len(idxs) > keep:
            drop.update(idxs[:-keep])  # oldest of the group → archive
    if not drop:
        return list(mem_entries), []
    kept = [ln for i, ln in enumerate(mem_entries) if i not in drop]
    overflow = [ln for i, ln in enumerate(mem_entries) if i in drop]
    return kept, overflow


def _over_floor(text, hard_floor):
    """Mechanical-floor predicate: over the LINE hard floor OR the BYTE
    floor. Both floors evict managed entries only (oldest first, verbatim
    to archive) — the curated region is never touched by machinery."""
    return (len(text.splitlines()) > hard_floor
            or len(text.encode('utf-8')) > _index_byte_floor())


def _append_to_archive(project, lines):
    """Append raw '- [' lines to the project's permanent archive, creating the
    file + header on first write. Read-modify-write under the caller's leaf
    lock; the archive is append-only cold storage — never truncated (SPEC D3).
    Shared by _commit_managed_entry (mechanical floor) and _condense_apply."""
    if not lines:
        return
    ap = _get_archive_path(project)
    ap.parent.mkdir(parents=True, exist_ok=True)
    prev = ap.read_text(encoding='utf-8').rstrip() if ap.exists() else ''
    if _MEM_ARCHIVE_HEADER not in prev:
        prev = (prev + f'\n\n{_MEM_ARCHIVE_HEADER}'
                if prev else _MEM_ARCHIVE_HEADER)
    _atomic_write_text(ap, prev + '\n' + '\n'.join(lines) + '\n')


def _supersedable_hashes(wm_markers):
    """Entry hashes that a LIVE session still intends to replace in place.

    Every watermark carries `last_entry_hash` — the `_(live)_` line its next
    checkpoint will supersede. The floor evicts oldest-first, so under budget
    pressure it could pop exactly that line into the archive; and the archive is
    append-only cold storage that is never truncated. From that moment the
    supersede-by-hash lookup finds nothing and every subsequent checkpoint
    APPENDS instead of replacing.

    That is the whole mechanism behind the pile-up measured 2026-08-23: 1,684
    of 2,222 archive lines superseded, worst single group 47 copies of one
    conversation. Supersession was implemented and correct; it was simply
    unreachable once the line had been relocated.
    """
    out = set()
    for ln in wm_markers or []:
        h = (_wm_parse(ln) or {}).get('last_entry_hash', '')
        if h:
            out.add(h)
    return out


def _commit_managed_entry(p, mem_entry=None, wm_upsert=None, wm_remove_sid=None,
                          supersede_sid=None):
    """Leaf-locked atomic commit — the write path shared by the completion
    scribe, the Step-6 checkpoint worker, and teardown (the structured Leg C
    `_condense_apply` is a co-equal writer under the SAME leaf lock + atomic
    primitive; both route archive overflow through `_append_to_archive`).

    §16 step 4 — THE SPLIT: the managed region lives in SESSION_LOG.md now,
    a sibling of MEMORY.md, not inline in it. In a single per-project
    mem-write-locked operation:
      • optionally drop `supersede_sid`'s previous entry (see below),
      • optionally append `mem_entry` ('- [' line) to SESSION_LOG.md,
      • optionally `_wm_upsert`/`_wm_remove` this session's watermark marker,
      • run the entry-count RING (§9.2 B2, `session_log_ring`, default 20 —
        replaces the old byte/line floor, which existed to protect
        MEMORY.md's per-prompt budget; SESSION_LOG.md is never in the
        prompt, so that budget no longer applies to it),
      • write MEMORY.md (curated only — untouched here except for a
        one-time, lossless pull of any managed content an UNMIGRATED file
        still carries inline, §10.4) and SESSION_LOG.md, each atomically.
    Index overflow never raises here: the ring evicts to the archive instead.
    No scribe call and no condense dispatch inside the lock (the slow/process
    parts stay out). Returns whether condense should fire; caller dispatches it
    OUTSIDE the lock. File failures propagate; callers must not acknowledge
    failed writes. SPEC §3.A.MID committee blocker #3.

    `supersede_sid` fixes the checkpoint pile-up. Step-6 checkpointing folds
    each transcript delta into a CUMULATIVE `running_summary`, so every
    `_(live)_` entry it writes is a strict superset of the one before — yet
    each was appended as a new line. A single long session therefore emitted N
    entries carrying one session's worth of information (16 of them, 6.2KB,
    filled this repo's whole managed region on 2026-08-05). Passing the session
    id drops that session's PREVIOUS entry in the same atomic write, identified
    by the `last_entry_hash` stashed on its watermark record. The
    self-contained-breadcrumb property of SPEC §3.A.MID is preserved: the
    newest entry is always complete on its own, and a hard kill leaves it in
    place. Superseded entries are dropped, not archived — the surviving entry
    already contains their content.
    """
    project_id = p.get('id', '')
    mem_path = _get_memory_path(p)
    mem_path.parent.mkdir(parents=True, exist_ok=True)
    ring = _session_log_ring()
    with _get_mem_write_lock(project_id):
        existing = (mem_path.read_text(encoding='utf-8')
                    if mem_path.exists() else '')
        # Leg 0: idempotent, additive migration on whatever MEMORY.md still
        # holds; `legacy_entries`/`legacy_wm` are non-empty ONLY for a project
        # not yet migrated to the split (§10.4 both-formats-coexist window) —
        # pulling them out here, into SESSION_LOG.md, in the same write this
        # function was going to make anyway, is what makes the migration
        # re-runnable-to-a-no-op rather than needing a separate pass first.
        curated, legacy_entries, legacy_wm = _mem_split_full(_mem_migrate(existing))
        log_entries, log_wm = _session_log_read(p, strict=True)
        mem_entries = legacy_entries + log_entries
        wm_markers = _wm_merge(log_wm, legacy_wm)
        if supersede_sid is not None:
            prev_hash = (_wm_find(wm_markers, supersede_sid)
                         or {}).get('last_entry_hash', '')
            if prev_hash:
                before = len(mem_entries)
                mem_entries = [ln for ln in mem_entries
                               if _sha8(ln) != prev_hash]
                if len(mem_entries) != before:
                    _scribe_stat(project_id, 'entry_superseded')
        if wm_upsert is not None:
            wm_upsert = dict(wm_upsert)
            if mem_entry:
                wm_upsert['last_entry_hash'] = _sha8(mem_entry)
            else:
                # No new entry this round (thin/refused delta still advances
                # the offset) — carry the pointer forward, or the next real
                # checkpoint loses track of what it supersedes.
                carry = (_wm_find(wm_markers, wm_upsert.get('session_id'))
                         or {}).get('last_entry_hash', '')
                if carry:
                    wm_upsert['last_entry_hash'] = carry
        if mem_entry:
            mem_entries.append(mem_entry)
        if wm_remove_sid is not None:
            wm_markers = _wm_remove(wm_markers, wm_remove_sid)
        if wm_upsert is not None:
            wm_markers = _wm_upsert(wm_markers, wm_upsert)
        # Collapse repetition BEFORE the oldest-first ring eviction, so a
        # burst of same-day same-label cycles can't evict unrelated history
        # the log still needs (see _MANAGED_DUP_KEEP).
        mem_entries, overflow = _collapse_duplicate_entries(mem_entries)
        if overflow:
            _log(f"[mem-dedup] {project_id}: demoted {len(overflow)} duplicate "
                 f"managed entr{'y' if len(overflow) == 1 else 'ies'} to archive "
                 f"(keeping {_MANAGED_DUP_KEEP} per date+label)")
        # Oldest-first ring eviction (§9.2 B2), but never the line a live
        # session is about to supersede (see _supersedable_hashes). Skipping
        # past it preserves the ordering; a protected line is released the
        # moment its session ends and `_wm_remove`/`_gc_stale_watermarks`
        # drops the marker, so nothing is pinned permanently.
        _protected = _supersedable_hashes(wm_markers)
        _i, _skipped, _rotated = 0, 0, 0
        while _i < len(mem_entries) and len(mem_entries) > ring:
            if _sha8(mem_entries[_i]) in _protected:
                _i += 1
                _skipped += 1
                continue
            overflow.append(mem_entries.pop(_i))  # oldest evictable → archive
            _rotated += 1
        if _rotated:
            # The bound that must start logging (Condition, §9.2 B2): the
            # ORDINARY case — every day, this ring simply rotating — used to
            # emit nothing at all under the old byte/line floor.
            _log(f"[mem-log] {project_id}: rotated {_rotated} entr"
                 f"{'y' if _rotated == 1 else 'ies'} to archive "
                 f"(ring cap {ring}, oldest first)")
        if _skipped and len(mem_entries) > ring:
            # Every remaining entry belongs to a live session. Going over the
            # ring for a few turns is the cheaper failure: the alternative is
            # archiving a line that is still being updated, which is the bug
            # this guard exists to prevent.
            _log(f"[mem-log] {project_id}: over ring cap "
                 f"({len(mem_entries)}/{ring}) with {_skipped} live "
                 f"entr{'y' if _skipped == 1 else 'ies'} protected from eviction")
        _append_to_archive(p, overflow)
        _write_curated_only(mem_path, curated)
        _write_session_log(p, mem_entries, wm_markers)
        return _should_condense(p, include_claude_md=True)


def _gc_stale_watermarks(projects):
    """Drop `<!-- clayrune:wm:<sid> -->` markers whose session is no longer live.

    A watermark is removed by `_wm_remove` on clean teardown only. A hard MC kill
    (or a startup reconcile that baseline-stamps history without scribing it)
    leaves the marker behind forever, so they accumulate across restarts: 67 of
    them (37.8KB) had piled up in this repo's own MEMORY.md by 2026-07-11 and
    pushed the curated index past the byte budget, so the floor evicted real
    entries to the archive to pay for markers carrying no information.

    LIVE markers are load-bearing (Step-6 checkpointing reads `byte_offset` to
    render only the transcript delta), so a session still in `agent_sessions` is
    NEVER pruned — the membership test is re-done inside the lock so a session
    revived concurrently with this sweep can't lose its marker. A pruned dead
    marker costs nothing: its session can never checkpoint again.

    Same discipline as every other memory writer: per-project leaf lock,
    atomic write, curated + entry lines byte-preserved. Best-effort — never
    raises, never blocks startup.

    §16 step 4: markers live in SESSION_LOG.md now. Also sweeps any markers
    still inline in an UNMIGRATED project's MEMORY.md (§10.4 both-formats-
    coexist) — a project that has not yet had the migration run should not
    silently stop getting GC'd.
    """
    total = 0
    for p in projects or []:
        project_id = p.get('id', '')
        if not project_id:
            continue
        live = {s.get('session_id') or s.get('id')
                for s in agent_sessions.values()}
        try:
            with _get_mem_write_lock(project_id):
                log_path = _get_session_log_path(p)
                if log_path.exists():
                    _c, log_entries, log_wm = _mem_split_full(
                        log_path.read_text(encoding='utf-8'))
                    if log_wm:
                        kept = [ln for ln in log_wm
                                if (_wm_parse(ln) or {}).get('session_id') in live]
                        dropped = len(log_wm) - len(kept)
                        if dropped:
                            _write_session_log(p, log_entries, kept)
                            total += dropped
                            _log(f"[wm-gc] {project_id}: pruned {dropped} stale "
                                 f"watermark(s), kept {len(kept)} live")
                mem_path = _get_memory_path(p)
                if mem_path.exists():
                    existing = mem_path.read_text(encoding='utf-8')
                    curated, mem_entries, mem_wm = _mem_split_full(existing)
                    if mem_wm:
                        kept = [ln for ln in mem_wm
                                if (_wm_parse(ln) or {}).get('session_id') in live]
                        dropped = len(mem_wm) - len(kept)
                        if dropped:
                            _atomic_write_text(
                                mem_path, _mem_compose(curated, mem_entries, kept))
                            total += dropped
                            _log(f"[wm-gc] {project_id}: pruned {dropped} stale "
                                 f"watermark(s) from unmigrated MEMORY.md, kept "
                                 f"{len(kept)} live")
        except Exception as e:
            _log(f"[wm-gc] {project_id}: sweep failed: {e}")
    return total


def _write_session_memory(p, session, status, summary_fallback, ts_date):
    """Shared Leg A/0/C memory write — completion path & startup reconciler.
    Scribe over the full .jsonl → brief (fallback to summary, then a
    guaranteed breadcrumb) → _commit_managed_entry (which also drops this
    session's Step-6 wm marker = clean teardown) → condense trigger. Returns
    True iff a memory entry was written. Never raises.
    SPEC docs/MEMORY_SYSTEM_SPEC.md §3 Leg A/0/C.
    """
    project_id = p.get('id', '')
    task = (session.get('task', '') or '').strip()
    # Scribe model call is the slow (≤180s) part — OUTSIDE the leaf lock.
    scribed, _why = _scribe_extract(p, session)
    if scribed:
        # 'extracted_from_log' is its OWN counter (MC-922) — folding it into
        # 'scribe_extracted' would hide how often the fallback is doing the
        # work, the exact silent-failure-counter-goes-to-zero mistake this
        # fix is required not to repeat.
        _scribe_stat(project_id, 'scribe_extracted_from_log' if _why == 'extracted_from_log'
                     else 'scribe_extracted')
    else:
        _scribe_stat(project_id, f'scribe_fell_back:{_why}')
    # Track how often a session actually yields a causal note. If this sits at
    # ~0% the prompt isn't landing; at ~100% the model is inventing them.
    if scribed:
        _scribe_stat(project_id, 'scribe_why_present' if _SCRIBE_WHY_MARKER
                     in scribed else 'scribe_why_absent')
    fb = (summary_fallback or '')[:300].replace('\n', ' ').strip()
    brief = (scribed or fb
             or f"ended with status={status}, no captured output"
             ).replace('\n', ' ').strip()
    tag = '' if status == 'completed' else f' _({status})_'
    mem_entry = f"- [{ts_date}] **{_entry_label(task)}**{tag} — {brief}"
    # Terminal write also removes this session's live wm marker (clean
    # teardown — SPEC §3.A.MID Fix-B coordination), in the same atomic write.
    # The terminal entry is authoritative for this session, so it also
    # supersedes the last `_(live)_` checkpoint entry rather than sitting
    # next to it saying the same thing.
    _sid = session.get('session_id') or session.get('id')
    do_condense = _commit_managed_entry(
        p, mem_entry=mem_entry, wm_remove_sid=_sid, supersede_sid=_sid)
    if do_condense:
        _dispatch_condense(p)
    # Phase 4 Distiller — daemon-thread dispatch parallel to Scribe (v2.1 §4.8).
    # Best-effort: failure NEVER blocks Scribe / MEMORY.md / completion. The
    # entry point gates itself via _distiller_should_proceed at session_end_extract.
    try:
        csid = session.get('claude_session_id', '')
        sid = session.get('session_id') or session.get('id') or ''
        if not csid:
            _log(f"[distiller] dispatch SKIP project_id={project_id} sid={sid}: "
                 f"no claude_session_id on session object")
        else:
            tf = _find_transcript_file(p.get('project_path', ''), csid)
            jsonl_path = str(tf) if tf else None
            # _UNATTENDED_LOOP_RULE: stamp unattended provenance onto every
            # artifact this session's evidence produces, so the read-floor can
            # keep autonomous output from becoming autonomous input. Allowlist
            # shape (committee M4): interactive ONLY when trigger_type=='manual'
            # and the task text carries no unattended marker; a session with
            # missing/backfilled trigger provenance stamps unattended.
            unattended = _distiller.is_unattended_session(
                task, session.get('trigger_type'))
            _log(f"[distiller] dispatch FIRE project_id={project_id} sid={sid[:12]} "
                 f"csid={csid[:8]} jsonl_path={'yes' if jsonl_path else 'no'} "
                 f"origin={'unattended' if unattended else 'interactive'}")
            threading.Thread(
                target=_distiller._distill_extract_and_aggregate,
                args=(project_id, sid, jsonl_path, unattended),
                daemon=True,
                name=f"distiller-{project_id}",
            ).start()
    except Exception as _dist_disp_err:
        # Was bare `except: pass` — silently swallowed any error in the dispatch
        # path including AttributeError if _distiller wasn't registered. Log it
        # so we can see if dispatch fails.
        _log(f"[distiller] dispatch EXCEPTION project_id={project_id}: "
             f"{type(_dist_disp_err).__name__}: {_dist_disp_err!r}")
    # Beacon — regenerate this project's cross-project heartbeat brief on
    # session-close (the brief is the expensive field, so it regenerates here,
    # not on dashboard load). Threaded + best-effort, exactly like the Distiller
    # dispatch above: failure NEVER blocks Scribe / MEMORY.md / completion.
    try:
        from beacon.hooks import regenerate_brief_async as _beacon_regen
        _beacon_regen(project_id, status)
    except Exception as _beacon_err:
        _log(f"[beacon] dispatch EXCEPTION project_id={project_id}: "
             f"{type(_beacon_err).__name__}: {_beacon_err!r}")
    # Topics digest — same shape and same reasoning as Beacon above: an
    # expensive per-project artifact regenerated when a chat actually changed,
    # threaded + best-effort, never blocking Scribe / MEMORY.md / completion.
    #
    # This is the digest's ONLY automatic writer. Before it, the sole writer was
    # a button, so the digest aged silently — nine days and 177 chats out of
    # date on this install while the UI reported it fresh. Session end is the
    # right trigger because it IS the event ("a chat changed"); a nightly job
    # would refresh when nothing happened and not when a busy afternoon did.
    # The hook self-gates on staleness, a per-project debounce, and the
    # existence of a digest at all, so an idle project costs nothing.
    #
    # Called through a WIRED hook, not an import: mc.memory must never import a
    # blueprint (import-cycle invariant, enforced by
    # tests/test_memory_module.py::test_import_smoke). Beacon can `from
    # beacon.hooks import ...` only because beacon is a top-level package.
    try:
        if _topics_refresh_hook is not None:
            _topics_refresh_hook(project_id)
    except Exception as _topics_err:
        _log(f"[topics] dispatch EXCEPTION project_id={project_id}: "
             f"{type(_topics_err).__name__}: {_topics_err!r}")
    # MC-944 step 7 (Condition 21, trigger 4) — a docs/ artifact created or
    # grown above the size threshold during this session mints a thin topic
    # node. This is the one mint trigger with a real session in hand, so
    # task/trigger_type ride along unchanged (same values the Distiller
    # dispatch above just used) rather than failing safe to unknown, same as
    # every other best-effort hook in this function: never blocks Scribe /
    # MEMORY.md / completion.
    try:
        started_at = session.get('started_at') or ''
        since_ts = (datetime.fromisoformat(started_at.replace('Z', '+00:00')).timestamp()
                    if started_at else None)
        if since_ts is not None:
            scan_docs_artifacts_for_mint(
                p, since_ts, _time.time(),
                task=task, trigger_type=session.get('trigger_type'))
    except Exception as _mint_scan_err:
        _log(f"[mint] docs-artifact session-end scan EXCEPTION project_id={project_id}: "
             f"{type(_mint_scan_err).__name__}: {_mint_scan_err!r}")
    return True


def _sha8(s):
    import hashlib
    return hashlib.sha1((s or '').encode('utf-8', 'replace')).hexdigest()[:8]


def _get_checkpoint_sema(pid):
    with _checkpoint_sema_guard:
        s = _checkpoint_sema.get(pid)
        if s is None:
            s = threading.BoundedSemaphore(2)  # ≤2 concurrent checkpoints/project
            _checkpoint_sema[pid] = s
    return s


def _checkpoint_watermark(p, sid):
    """Read this session's last checkpoint watermark (empty when absent).

    §16 step 4: markers live in SESSION_LOG.md; a legacy MEMORY.md is still
    consulted for a not-yet-migrated project (§10.4 both-formats-coexist).
    """
    try:
        _log_wm = _session_log_read(p)[1]
        r = _wm_find(_log_wm, sid)
        if r:
            return r
        mp = _get_memory_path(p)
        if not mp.exists():
            return 0
        _c, _e, legacy_wm = _mem_split_full(mp.read_text(encoding='utf-8'))
        r = _wm_find(legacy_wm, sid)
        return r or {}
    except Exception:
        return {}


def _checkpoint_prev_offset(p, sid):
    """Compatibility helper returning the legacy byte offset."""
    return int(_checkpoint_watermark(p, sid).get('byte_offset', 0) or 0)


def _maybe_checkpoint(session, force=False):
    """Mode-B turn-boundary hook (clones the _auto_snapshot_notes_on_turn
    precedent). FAST gate only — no model call here: config flags,
    incognito/housekeeping, real-boundary, KB-delta debounce, one-in-flight
    per session. Spawns the worker on a daemon thread. Never raises (must not
    break the reader).

    `force=True` (MC-964 Step C, `agent_routes._maybe_midturn_roll` only)
    bypasses the two POLICY gates — the `scribe_checkpoint_enabled` flag and
    the KB-delta debounce — because a mid-turn roll abandons the outgoing
    transcript for good: the fresh session gets a new `claude_session_id`, so
    the next checkpoint's watermark lookup sees a different `transcript_path`
    and restarts its offset (see `_checkpoint_worker`'s "resume opened a new
    .jsonl" branch) rather than ever coming back for what the old one hadn't
    flushed yet. It does NOT bypass any CORRECTNESS gate (scribe_enabled,
    incognito, missing ids, no-new-content since the last watermark) — those
    stay true regardless of who's asking. Runs the worker INLINE instead of on
    a thread so the caller can learn whether anything actually landed before
    the roll proceeds; returns that as a bool. The non-forced path keeps
    firing the worker on a daemon thread and returns False unconditionally
    (no caller used `_maybe_checkpoint`'s return value until now)."""
    try:
        if not force and not state.CONFIG.get('scribe_checkpoint_enabled', False):
            return False
        if not state.CONFIG.get('scribe_enabled', True):
            return False
        kb = int(state.CONFIG.get('scribe_checkpoint_kb', 0) or 0)
        if not force and kb <= 0:
            return False
        if session.get('incognito') or session.get('housekeeping'):
            return False
        if (session.get('waiting_for_question')
                or session.get('waiting_for_plan_approval')):
            return False  # not a real work boundary
        if not session.get('process_alive', True):
            return False
        pid = session.get('project_id', '')
        sid = session.get('session_id') or session.get('id')
        provider = str(session.get('provider') or '').strip().lower()
        csid = str(session.get('claude_session_id') or '').strip()
        provider_sid = (csid if provider in ('', 'claude') else
                        str(session.get('provider_session_id') or '').strip())
        if not (pid and sid):
            return False
        # Claude's historical field remains authoritative only for Claude (or
        # an old session with no provider stamp).  Other runtimes expose their
        # own native thread/session id and must not be gated by a missing
        # claude_session_id.
        if provider in ('', 'claude'):
            if not csid:
                return False
        elif not provider_sid:
            return False
        p = load_project(pid)
        if not p:
            return False
        canonical_lines = None
        canonical_sequence = 0
        if _canonical_scribe_reader is not None:
            try:
                selection = _canonical_scribe_reader(pid, sid, session)
                history = getattr(selection, 'canonical', None) if selection is not None else None
                if (selection is not None and getattr(selection, 'source', '') == 'canonical'
                        and history is not None and getattr(history, 'complete', False)):
                    canonical_lines = tuple(selection.lines)
                    canonical_sequence = int(history.projection.through_sequence)
            except Exception as e:
                _log(f"[scribe] canonical checkpoint lookup failed: {e}")
        if canonical_lines:
            previous = _checkpoint_watermark(p, sid)
            if canonical_sequence <= int(previous.get('canonical_sequence', 0) or 0):
                return False
            with _checkpoint_guard:
                if sid in _checkpoint_inflight:
                    _scribe_stat(pid, 'checkpoint_coalesced')
                    return False
                _checkpoint_inflight.add(sid)
            snap = {'pid': pid, 'sid': sid, 'csid': csid,
                    'provider_session_id': provider_sid,
                    'provider': provider,
                    'model': _model_for_provider('scribe_model', provider),
                    'task': (session.get('task', '') or '').strip(),
                    'owner': _session_owner(session), 'tf': '',
                    'canonical_lines': canonical_lines,
                    'canonical_sequence': canonical_sequence}
            if force:
                return bool(_checkpoint_worker(snap))
            threading.Thread(target=_checkpoint_worker, args=(snap,),
                             daemon=True).start()
            return False
        pp = p.get('project_path', '')
        if provider in ('', 'claude'):
            tf = _find_transcript_file(pp, csid)
        else:
            try:
                tf = _agent_runtime.get_runtime(provider).transcript_path(
                    pp, provider_sid)
            except Exception as e:
                _log(f'[scribe] {provider} checkpoint transcript lookup failed: {e}')
                return False
        if not tf:
            return False
        try:
            size = os.path.getsize(tf)
        except OSError:
            return False
        if not force and size - _checkpoint_prev_offset(p, sid) < kb * 1024:
            return False  # not enough new transcript yet (debounce)
        with _checkpoint_guard:
            if sid in _checkpoint_inflight:
                _scribe_stat(pid, 'checkpoint_coalesced')
                return False  # previous worker still running; next boundary covers more
            _checkpoint_inflight.add(sid)
        snap = {'pid': pid, 'sid': sid, 'csid': csid,
                'provider_session_id': provider_sid,
                'provider': provider,
                'model': _model_for_provider('scribe_model', provider),
                'task': (session.get('task', '') or '').strip(),
                # Whose working state this turn belongs to. A session with no
                # character writes to the shared bucket rather than claiming
                # one — see `_cont_owner_key`.
                'owner': _session_owner(session),
                'tf': str(tf)}
        if force:
            return bool(_checkpoint_worker(snap))
        threading.Thread(target=_checkpoint_worker, args=(snap,),
                         daemon=True).start()
        return False
    except Exception:
        return False


def _checkpoint_worker(snap):
    """Render the delta since the last watermark, fold it into the running
    summary, append a self-contained `_(live)_` entry + upsert the wm marker
    in one leaf-locked atomic write. SPEC §3.A.MID. Never raises.

    Returns True when a checkpoint was actually committed (a new entry, or a
    thin-delta watermark-only commit that legitimately had nothing new to
    say) — MC-964 Step C's `_maybe_checkpoint(force=True)` reports this back
    as `scribe_flushed` on a mid-turn roll. False for every early bail-out
    (nothing new, gated, or a real failure) and for the async (non-forced)
    callers, who never look at the return value."""
    pid, sid, csid, task, tf = (snap['pid'], snap['sid'], snap.get('csid', ''),
                                snap['task'], snap.get('tf', ''))
    provider_sid = str(snap.get('provider_session_id') or csid or '').strip()
    provider = str(snap.get('provider') or '').strip().lower()
    canonical_lines = tuple(snap.get('canonical_lines') or ())
    canonical_sequence = int(snap.get('canonical_sequence', 0) or 0)
    sema = _get_checkpoint_sema(pid)
    if not sema.acquire(blocking=False):
        _scribe_stat(pid, 'checkpoint_coalesced')  # project at fan-out cap
        with _checkpoint_guard:
            _checkpoint_inflight.discard(sid)
        return False
    try:
        p = load_project(pid)
        if not p:
            return False
        prev_off, prev_summary = 0, ''
        try:
            # §16 step 4: markers live in SESSION_LOG.md; a legacy MEMORY.md
            # is still consulted for a not-yet-migrated project.
            wm = _session_log_read(p)[1]
            r = _wm_find(wm, sid)
            if not r:
                mp = _get_memory_path(p)
                if mp.exists():
                    _c, _e, legacy_wm = _mem_split_full(mp.read_text(encoding='utf-8'))
                    r = _wm_find(legacy_wm, sid)
            if r:
                prev_summary = r.get('running_summary', '') or ''
                if r.get('transcript_path') == tf:
                    prev_off = int(r.get('byte_offset', 0))
                else:
                    # resume opened a new .jsonl → restart offset, KEEP
                    # the running summary as the reduce base (no loss).
                    _scribe_stat(pid, 'checkpoint_offset_reset')
        except Exception as e:
            _log(f'[scribe] checkpoint watermark read failed: {e}')
            prev_off, prev_summary = 0, ''
        if canonical_lines:
            delta, new_off = '\n'.join(canonical_lines), 0
            if not delta.strip() or canonical_sequence <= int(
                    (r or {}).get('canonical_sequence', 0) or 0):
                return False
        else:
            delta, new_off = _scribe_render_delta(tf, prev_off, provider=provider)
            if not delta.strip() or new_off == prev_off:
                return False  # nothing new complete; retry next boundary (offset kept)
        model = (str(snap['model']) if 'model' in snap
                 else _model_for_provider('scribe_model', provider))
        token = _with_transform_context(provider, cwd=p.get('project_path') or None)
        try:
            dsum, reason = _scribe_summarize_text(delta, model)
        finally:
            _reset_transform_context(token)
        rec = {'session_id': sid, 'transcript_path': tf,
               'byte_offset': new_off, 'slice_hash': _sha8(delta)}
        if provider in ('', 'claude'):
            rec['claude_session_id'] = csid
        else:
            rec['provider'] = provider
            rec['provider_session_id'] = provider_sid
        if canonical_lines:
            rec['canonical_sequence'] = canonical_sequence
        if reason != 'extracted':
            # Only explicit content-policy dispositions acknowledge coverage.
            # Operational/unknown failures leave this source span pending.
            if reason != 'parse_empty':
                _scribe_stat(pid, f'checkpoint_pending:{reason}')
                return False
            # Deterministically thin delta: no entry; retain prior summary.
            rec['running_summary'] = prev_summary
            if _commit_managed_entry(p, wm_upsert=rec):
                _dispatch_condense(p)
            _scribe_stat(pid, f'checkpoint_skipped:{reason}')
            return True
        if prev_summary:
            try:
                token = _with_transform_context(provider, cwd=p.get('project_path') or None)
                try:
                    merged = _model_call(
                        model, _SCRIBE_CHECKPOINT_REDUCE,
                        f"PREVIOUS:\n{prev_summary}\n\nNEW:\n{dsum}")
                finally:
                    _reset_transform_context(token)
                merged = (merged or '').strip().replace('\n', ' ').strip()
                if not merged or any(mk in merged.lower() for mk in _SCRIBE_REFUSAL_MARKERS):
                    _scribe_stat(pid, 'checkpoint_pending:reduce_incomplete')
                    return False
            except Exception as e:
                _log(f'[scribe] checkpoint reduce failed: {e}')
                _scribe_stat(pid, 'checkpoint_pending:model_error')
                return False
        else:
            merged = dsum
        merged = merged[:300]
        rec['running_summary'] = merged
        entry = f"- [{now_iso()[:10]}] **{_entry_label(task)}** _(live)_ — {merged}"
        if _commit_managed_entry(p, mem_entry=entry, wm_upsert=rec,
                                 supersede_sid=sid):
            _dispatch_condense(p)
        _scribe_stat(pid, 'checkpoint_extracted')
        # Continuity rides the SAME delta the checkpoint just rendered — no
        # extra transcript read, no second debounce, one cheap model call at a
        # boundary that has already earned one. Best-effort: the checkpoint has
        # already been committed above, so a failure here loses nothing.
        # `owner is None` = an ephemeral (global) type: it writes no working
        # state at all. Note this is NOT the same as `owner == ''`, which is the
        # shared bucket every agent reads — falling through to that would be the
        # worst of the three outcomes rather than a safe default.
        if state.CONFIG.get('continuity_enabled', True) and snap.get('owner') is not None:
            if _extract_continuity(p, delta, model,
                                   owner=snap.get('owner'), provider=provider,
                                   cwd=p.get('project_path') or None) is not None:
                _scribe_stat(pid, 'continuity_updated')
        return True
    except Exception as e:
        _log(f'[scribe] checkpoint failed: {e}')
        return False
    finally:
        sema.release()
        with _checkpoint_guard:
            _checkpoint_inflight.discard(sid)


_SCRIBE_PROMPT = (
    "You are a project-memory scribe. Below is a full agent session transcript "
    "(actions, tool results, reasoning). Write ONE dense line (max 280 chars, no "
    "newlines) for a project memory log: what was done, what was decided/learned, "
    "and any gotcha or follow-up. Be concrete (files, names, decisions). Output "
    "ONLY that line — no preamble, no markdown, no quotes."
)


_SCRIBE_MAP_PROMPT = (
    "This is ONE CHUNK of a longer agent session transcript. In 1-2 tight "
    "sentences, note what was done/decided/learned/broken in THIS chunk only. "
    "Output only those sentences."
)


_SCRIBE_REDUCE_PROMPT = (
    "Below are ordered partial notes from consecutive chunks of one agent "
    "session. Synthesize them into ONE dense line (max 280 chars, no newlines) "
    "for a project memory log: what was done, decided/learned, and any gotcha. "
    "Output ONLY that line."
)


# ── The "why" leg (Hyperagents, 2026-08-02) ─────────────────────────────────
# The scribe records WHAT happened; this asks for the CAUSE as well. Meta's
# Hyperagents paper (arXiv 2603.19461) found self-improving agents converge on
# storing "causal diagnoses and forward-looking plans" rather than raw events —
# that is the memory type that compounds across sessions. See
# docs/RESEARCH_HYPERAGENTS.md.
#
# Deliberately scoped to the TERMINAL entry only. A checkpoint is a mid-session
# running summary that later gets superseded; a causal diagnosis is a
# retrospective. Keeping it off the checkpoint path also leaves that path (and
# _SCRIBE_CHECKPOINT_REDUCE, which merges free text) byte-identical.
_SCRIBE_WHY_SUFFIX = (
    "\n\nTHEN output a line containing only --- and THEN one final line: the "
    "WHY. Name the CAUSE behind this session's main problem or surprise and "
    "what it implies next time (max 160 chars, no newlines). This is a causal "
    "diagnosis, not a recap: \"X broke because Y, so check Y first\" beats "
    "\"fixed X\". Prefer a cause that would generalise to a future session. "
    "If the session was routine and produced no real diagnosis, write NONE — "
    "most sessions have no why, and an invented one is worse than none."
)


_SCRIBE_WHY_MARKER = '_why:_'


_SCRIBE_WHY_CAP = 160


# Below this a "why" is a stub ('n/a', 'none.', 'unclear') rather than a note.
_SCRIBE_WHY_MIN = 12


_SCRIBE_WHY_NULLS = ('none', 'n/a', 'na', 'nothing', 'unclear', 'unknown')


_SCRIBE_CHECKPOINT_REDUCE = (
    "PREVIOUS is the running summary of an IN-PROGRESS agent session so far; "
    "NEW is what happened since. Produce ONE updated dense line (max 280 "
    "chars, no newlines) that SUPERSEDES PREVIOUS by folding in NEW: what's "
    "been done, decided/learned, and open gotchas. Output ONLY that line — "
    "no preamble, no markdown, no quotes."
)


_SCRIBE_SINGLE_LIMIT = 350_000


_SCRIBE_RESULT_CAP = 2000


_SCRIBE_THIN_TEXT_CHARS = 120


_SCRIBE_ACTIVITY_PREFIXES = ('ACTION ', 'RESULT:', 'THINKING:')


_SCRIBE_REFUSAL_MARKERS = (
    "i don't see a transcript", "i do not see a transcript",
    "no transcript", "please paste", "paste the session",
    "paste the transcript", "share the transcript",
    "provide the transcript", "don't have access to",
    "didn't receive", "did not receive", "cannot see any transcript",
    "no session transcript", "there is no transcript",
)


# Counter classes that get a `counters_last[key]` recency stamp.
_STAT_DATED = ('error', 'skip', 'refuse', 'fell_back', 'timeout', 'reject')

_scribe_stat_locks: dict = {}
_scribe_stat_locks_guard = threading.Lock()


def _get_scribe_stat_lock(project_id):
    with _scribe_stat_locks_guard:
        lk = _scribe_stat_locks.get(project_id)
        if lk is None:
            lk = _scribe_stat_locks[project_id] = threading.Lock()
        return lk


def _scribe_stat(project_id, key, n=1):
    """Add n to a scribe-outcome counter (SPEC §8 telemetry). Best-effort;
    n<=0 is a no-op (no file touch).

    Failure-class counters ALSO stamp `counters_last[key]` with the bump time.
    Lifetime totals alone cannot answer "is this still bleeding, or is it an
    old backlog?" — on 2026-08-05 this exact ambiguity caused a live
    misdiagnosis: `condense_rejected:model_error` 96 + `model_timeout` 58
    against 46 successes reads as a 22% success rate and a broken subsystem,
    when in fact 91+58 of those failures predate the 2026-07 switch to the
    haiku default and the post-fix record is 41 successes to 5 errors. A
    direct live `_condense_plan` call returned 'ok'. `distiller._increment_
    counter` already carried this stamp for the same reason; the scribe half
    never got it. Cheap insurance against re-litigating a fixed bug.

    Read-modify-write is now locked + atomic. The previous plain `write_text`
    could lose a concurrent bump or leave a truncated file — and a corrupt
    stats file 500s the /scribe-stats route.
    """
    if n <= 0:
        return
    try:
        fp = DATA_DIR / f'{project_id}_scribe_stats.json'
        with _get_scribe_stat_lock(project_id):
            stats = {}
            if fp.exists():
                try:
                    stats = json.loads(fp.read_text(encoding='utf-8') or '{}')
                except Exception:
                    stats = {}          # corrupt file: restart, don't die
            if not isinstance(stats, dict):
                stats = {}
            stats[key] = int(stats.get(key, 0) or 0) + n
            low = key.lower()
            if any(m in low for m in _STAT_DATED):
                last = stats.get('counters_last')
                if not isinstance(last, dict):
                    last = {}
                last[key] = now_iso()
                stats['counters_last'] = last
            stats['_updated'] = now_iso()
            _atomic_write_text(fp, json.dumps(stats, indent=2,
                                              ensure_ascii=False))
    except Exception:
        pass


def _scribe_render_lines(lines):
    """Render an iterable of raw .jsonl text lines into the compact view.

    Shared core of _scribe_render_transcript (whole file) and
    _scribe_render_delta (Step 6, from a byte offset). Strips base64/image
    blocks, bulk-caps oversized tool_results, skips unparseable lines (so a
    stray leading fragment from a non-boundary offset is harmlessly ignored —
    the leading-partial safety net, SPEC §3.A.MID).
    """
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except Exception:
            continue
        msg = m.get('message') if isinstance(m.get('message'), dict) else None
        if not msg or not isinstance(msg.get('content'), list):
            continue
        mtype = m.get('type', '')
        for b in msg['content']:
            if not isinstance(b, dict):
                continue
            bt = b.get('type', '')
            if bt == 'text' and mtype == 'assistant':
                t = (b.get('text') or '').strip()
                if t:
                    out.append(f"ASSISTANT: {t}")
            elif bt == 'thinking':
                t = (b.get('thinking') or b.get('text') or '').strip()
                if t:
                    out.append(f"THINKING: {t[:2000]}")
            elif bt == 'tool_use':
                inp = b.get('input', {})
                try:
                    s = json.dumps(inp, ensure_ascii=False)
                except Exception:
                    s = str(inp)
                out.append(f"ACTION {b.get('name','?')}: {s[:400]}")
            elif bt == 'tool_result':
                c = b.get('content')
                if isinstance(c, list):
                    parts = []
                    for cb in c:
                        if isinstance(cb, dict) and cb.get('type') == 'text':
                            parts.append(cb.get('text', ''))
                        # image/base64 blocks intentionally dropped
                    c = '\n'.join(parts)
                elif not isinstance(c, str):
                    c = json.dumps(c, ensure_ascii=False) if c else ''
                c = (c or '').strip()
                if not c:
                    continue
                if len(c) > _SCRIBE_RESULT_CAP:
                    half = _SCRIBE_RESULT_CAP // 2
                    c = f"{c[:half]}\n…[{len(c)-_SCRIBE_RESULT_CAP} chars elided]…\n{c[-half:]}"
                out.append(f"RESULT: {c}")
    return '\n'.join(out)


def _scribe_render_transcript(path):
    """Render the whole raw CLI .jsonl into the compact, full-sequence view."""
    with open(path, encoding='utf-8', errors='replace') as fh:
        return _scribe_render_lines(fh)


def _render_log_lines_as_transcript(log_lines):
    """Render `session['log_lines']` — the raw per-turn event log MC keeps for
    EVERY provider, Claude or not — into the same ACTION/RESULT/ASSISTANT-
    prefixed shape `_scribe_render_transcript` produces from a Claude .jsonl,
    so `_scribe_summarize_text` can consume either unchanged.

    Fallback path only (see `_scribe_extract`): a non-Claude provider has no
    transcript file to begin with (`_session_transcript_path` is Claude-only
    by design), so this is the only capture MC has. It is necessarily lossier
    than the real transcript — log_lines records a tool's NAME but not its
    result body — so bracket-wrapped status noise ('[interrupted]', '[hint] …',
    exit-code lines) is dropped rather than guessed at.
    """
    out = []
    for line in log_lines or []:
        if not isinstance(line, str):
            continue
        s = line.strip()
        if not s:
            continue
        if s.startswith('> '):
            out.append(f"USER: {s[2:].strip()}")
        elif s.startswith('['):
            # Every MC-synthesized status/system line starts with '[' (tool
            # markers, [hint]/[error]/[interrupted]/exit-code notices, mcp-sync
            # results). A tool marker is either the older provider-qualified
            # "[codex tool: Bash]" (no preview, so the whole tag sits inside
            # the brackets) or the canonical "[tool: Bash] ls -la" every
            # Mode-A reader now emits (parity audit item 8) — name inside the
            # brackets, an optional argument preview after them. Keep both
            # shapes as an ACTION line; drop everything else as noise, not
            # conversation content.
            close = s.find(']')
            if close != -1 and 'tool: ' in s[:close]:
                tag = s[1:close]
                rest = s[close + 1:].strip()
                out.append(f"ACTION {tag}" + (f": {rest}" if rest else ""))
        else:
            out.append(f"ASSISTANT: {s}")
    return '\n'.join(out)


def _scribe_render_delta(path, byte_offset, provider='claude'):
    """Step 6: render ONLY the transcript bytes after `byte_offset`.

    Returns (rendered_text, new_byte_offset). new_byte_offset is the position
    immediately past the last complete newline consumed — it ONLY ever
    advances to a line boundary, so the next call's start is a clean line
    start (no leading-partial drop needed; an anomalous fragment would just
    fail json parse and be skipped by _scribe_render_lines). Trailing-partial
    rule: never consume past the last '\\n' (the agent may be mid-write). If
    `byte_offset` exceeds the file (rotation/truncation, SPEC S3-1) it resets
    to 0. If no complete new line is available, returns ('', byte_offset)
    unchanged (caller skips this checkpoint, retries next turn).
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return '', byte_offset
    if byte_offset > size:
        byte_offset = 0  # transcript rotated/truncated
    try:
        with open(path, 'rb') as fh:
            fh.seek(byte_offset)
            blob = fh.read()
    except OSError:
        return '', byte_offset
    last_nl = blob.rfind(b'\n')
    if last_nl < 0:
        return '', byte_offset  # no complete line yet
    consumed = blob[:last_nl].decode('utf-8', errors='replace')
    new_offset = byte_offset + last_nl + 1
    if (provider or 'claude').lower() not in ('', 'claude'):
        # Provider rollouts are not Claude's `{message: {content: [...]}}`
        # shape. Reuse the selected runtime's authoritative parser against
        # this complete-line delta, preserving the watermark byte offset.
        tmp_name = ''
        try:
            runtime = _agent_runtime.get_runtime(provider.lower())
            with tempfile.NamedTemporaryFile(mode='wb', suffix='.jsonl', delete=False) as tmp:
                tmp.write(blob[:last_nl + 1])
                tmp_name = tmp.name
            rendered = runtime.render_transcript_for_scribe(Path(tmp_name))
            return (rendered or ''), new_offset
        except Exception as e:
            _log(f'[scribe] {provider} checkpoint render failed: {e}')
            return '', byte_offset
        finally:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError as e:
                    _log(f'[scribe] checkpoint temp cleanup failed: {e}')
    return _scribe_render_lines(consumed.split('\n')), new_offset


def _scribe_call(model, instruction, body):
    """One blocking `claude -p` call (prompt via stdin to dodge arg limits).

    Returns the model's text output, or raises on failure/timeout.
    Delegates to ClaudeRuntime.oneshot() — single source of truth. This is
    the ONE choke point for Scribe, condense AND the Distiller (which calls
    in via the wired _scribe_call hook, mc/distiller.py) — all three
    summarize through Claude specifically regardless of the session's own
    provider (parity note at _scribe_extract above), because it's the only
    runtime with a verified toolless oneshot (see
    agent_runtime.claude_oneshot_available's docstring).
    Callers that catch subprocess.TimeoutExpired should also catch RuntimeError
    since oneshot() normalises all failures to a None return which we raise here.
    """
    if not _agent_runtime.claude_oneshot_available():
        # A Codex/Gemini-only install has no Claude to spawn — every one of
        # these calls would otherwise fail the same way, forever, on every
        # session. Skip with one honest log line per call site instead of a
        # generic subprocess-failure trace that reads like an auth incident.
        _log("[scribe] claude not installed/authenticated — skipping "
             "summarization call (Scribe/condense/Distiller degrade on a "
             "non-Claude install)", flush=True)
        raise RuntimeError("scribe claude call skipped: claude unavailable")
    # Through the transform seam, so the TOOL_FREE_TRANSFORM profile is
    # authorized (execution_policy.authorize_execution) on every Scribe,
    # condense and Distiller call -- not just on the feature routes.
    try:
        return _agent_runtime.run_text_transform(
            'claude',
            prompt=instruction,
            model=model,
            stdin_text=body,
            cwd=str(Path.home()),
        )
    except (RuntimeError, TimeoutError) as e:
        # The seam carries the runtime's WHY (rc + stderr tail / timeout /
        # spawn failure / refusal). Keeping it in the exception is what turns
        # an anonymous counter bump into a diagnosable failure — 78 extraction
        # errors sat unexplained for six weeks behind a generic RuntimeError.
        # TimeoutError is folded in: callers only catch RuntimeError here.
        raise RuntimeError(f"scribe claude call failed ({e})") from e


def _extract_transcript_telemetry(path):
    """Read a JSONL transcript and extract cumulative token usage by model.

    Returns {'model': str, 'input_tokens': int, 'output_tokens': int,
             'cache_read_tokens': int, 'model_tokens': {model: total_tokens}}
    or {} on any failure. Never raises. Indicative, not billing-accurate.
    """
    if not path:
        return {}
    try:
        model_tokens = {}  # model -> {input, output}
        with open(path, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                msg = m.get('message') if isinstance(m.get('message'), dict) else None
                if not msg:
                    continue
                model = msg.get('model', '')
                usage = msg.get('usage')
                if not model or not isinstance(usage, dict):
                    continue
                if model not in model_tokens:
                    model_tokens[model] = {'input': 0, 'output': 0, 'cache_read': 0}
                model_tokens[model]['input'] += int(usage.get('input_tokens') or 0)
                model_tokens[model]['output'] += int(usage.get('output_tokens') or 0)
                model_tokens[model]['cache_read'] += int(
                    usage.get('cache_read_input_tokens') or 0)
        if not model_tokens:
            return {}
        dominant = max(model_tokens.items(),
                       key=lambda x: x[1]['input'] + x[1]['output'])[0]
        return {
            'model': dominant,
            'input_tokens': sum(v['input'] for v in model_tokens.values()),
            'output_tokens': sum(v['output'] for v in model_tokens.values()),
            'cache_read_tokens': sum(v['cache_read'] for v in model_tokens.values()),
            'model_tokens': {m: v['input'] + v['output']
                             for m, v in model_tokens.items()},
        }
    except Exception:
        return {}


# ── MC-964 Step E: habit-statement classifier (RC2, plan §6 Step E) ──────────
#
# The Scribe already writes one archive echo per session (`_scribe_extract`
# above). A user statement of STANDING behaviour ("I topped up", "I always
# ...", "I buy more when...") buried in that one dated line is exactly the
# RC2 failure: reachable only by the day it was said, never by the habit it
# named — the 09-17 top-up line existed and still lost to a stale exhaustion
# record 33 hours later. This section flags such a statement and mints a
# SEPARATE topic note for it, so it is reachable by what Ron does, not just
# by when he said it.
#
# V2 §7 (Condition 27) already gives every topic note default arrival
# vocabulary from its own name + description, at zero authoring cost
# (`_note_default_triggers`, used by `note_triggers`/`_mem_tokenize_unit` for
# every topic file including this one) — so minting via `write_topic_note`
# is sufficient; no bespoke trigger logic is added here. "No new store": the
# note lands in the SAME memory dir as every hand-written topic file.

_HABIT_CUE_RE = re.compile(
    r'\bi(?:\'ve| have)? (?:'
    r'always|usually|typically|often|normally|generally|routinely|habitually'
    r'|never|tend to|like to'
    r'|topped up|top(?:ped)? off|top up'
    r'|added? (?:some |a bit )?more|buy(?:s|ing)? more|bought more'
    r'|re-?up(?:ped)?|re-?stock(?:ed)?'
    r')\b'
    r'|\bwhenever i\b|\bevery time i\b|\beach time i\b',
    re.IGNORECASE)

# Same 28-word list every other default-trigger/slug derivation in this
# module already uses (Condition 27) — a habit slug is not a new vocabulary.
_HABIT_SLUG_MAX_TOKENS = 6


def _habit_note_slug(stmt, cue):
    """Content-derived slug so the SAME habit collapses to one note across
    replays/sessions (write_topic_note's "never clobbers" then does the
    right thing: first mint wins, a later restatement is a no-op, not a
    duplicate) while a genuinely different habit gets its own file. The cue
    phrase words are seeded first — they are the reason this fired at all —
    then the statement's own content words, in order, deduped, capped.
    Returns '' if the statement has no non-stopword content (never mints
    off cue words alone).
    """
    cue_toks = [t for t in _mem_tokens(cue) if t not in _TRIGGER_STOPWORDS]
    body_toks = [t for t in _mem_tokens(stmt) if t not in _TRIGGER_STOPWORDS]
    if not body_toks:
        return ''
    picked = []
    for t in cue_toks + body_toks:
        if t not in picked:
            picked.append(t)
        if len(picked) >= _HABIT_SLUG_MAX_TOKENS:
            break
    return 'project_habit_' + '_'.join(picked) if picked else ''


def _habit_source_statements(session):
    """Real USER turns for the classifier below — read from
    `session['log_lines']` (`"> {label}: {message}"`, captured for EVERY
    provider — see `_render_log_lines_as_transcript`'s docstring), never
    from the transcript file or the rendered `transcript` string.

    Verified against a real session (4ae1b44f, Ron's 2026-09-20T00:20:16Z
    top-up message): the Claude .jsonl 'user' record is NOT Ron's own words
    alone — MC prepends the full per-turn context block (RELEVANT MEMORY,
    STANDING POSITIONS, REPLY SHAPE, ...) to what is actually sent to the
    model, and that whole bundle (5+ KB) lands in the jsonl as ONE 'user'
    message, with Ron's actual sentence as its last few hundred bytes.
    Classifying that raw record would either skip it outright (any sane
    length gate) or scan mostly injected server text for cue phrases —
    both wrong. `message` in the `> {label}: {message}` log line is the
    argument the caller passed BEFORE context injection (see
    `agent_routes.py`'s `_advance_followup`/dispatch call sites), so it is
    reliably just what the human typed.

    Never a source of assistant paraphrase: the whole point of "user
    statements" per the plan is Ron's own words, not a Scribe summary of
    them — an assistant echo could invent or soften the habit and the
    authority guard would have nothing to check it against. `"> {label}: "`
    is written ONLY for the party sending this session its task/follow-up
    (`_SEED_LINE_RE`'s convention, agent_routes.py) — but a DISPATCHED
    session's sender is whichever agent spawned it, not necessarily Ron. A
    line whose label doesn't match the configured user is therefore
    skipped outright, not kept with its label still attached — keeping it
    would hand the classifier another agent's dispatch phrasing as if it
    were Ron's own habit.
    """
    label_prefix = (state.CONFIG.get('user_name') or 'User') + ': '
    out = []
    for line in session.get('log_lines') or []:
        if not isinstance(line, str) or not line.startswith('> '):
            continue
        s = line[2:].strip()
        if not s.startswith(label_prefix):
            continue
        s = s[len(label_prefix):].strip()
        if s:
            out.append(s)
    return out


def _scribe_classify_habits(project, session, statements):
    """Flag a user statement of standing operator behaviour and mint a topic
    note for it — alongside, never instead of, the archive-echo write in
    `_scribe_extract` (a classifier miss must never regress today's capture).

    Authority guard: a habit note is minted straight from a VERBATIM user
    sentence, unlike a Distiller artifact (model-generated from a pattern).
    That is a sharper risk, not a smaller one — the exact 2026-06-22
    incident (CLAUDE.md "Learning-system safety rails") was one literal
    sentence a user typed once ("Full autonomy, no permission/go-ahead
    needed, by any means necessary") becoming an always-loaded global
    instruction. "It's just an observation, not a position" is not a
    defense on its own: nothing downstream re-checks a topic note's own
    prose before a future agent reads it as context. So before mint, the
    FULL rendered note (description + body — a permissive phrase could hide
    in either) is run through `mc.distiller._authority_violation`, the
    SAME deterministic phrase-match the Distiller itself is refused by no
    matter how it is worded. A hit skips the mint entirely (never a
    truncated/softened version) and is logged + counted
    (`habit_note_refused_authority`) so a real refusal is visible, not silent.

    Origin stamping / unattended-loop rail: unlike `write_topic_note` (which
    stamps honestly either way — see its own docstring), THIS caller gates on
    it. `_memory_search`'s corpus has no `origin` filter anywhere (verified:
    `grep -n "'origin'" mc/memory.py` outside this section returns nothing) —
    the origin-stamped read-floor the rail relies on is a different
    subsystem's (`mc.distiller`'s `exploration_read_floor
    (consumer_unattended=True)`, scoped to learning artifacts, not the
    general topic-note corpus every session's memory search reads from. A
    note minted here reaches EVERY consumer, attended or not, the moment it
    exists — so an unattended session's habit statement must never mint at
    all, or it silently becomes exactly the autonomous-output-as-input the
    rail exists to prevent. Checked with `_stamp_origin` itself, off the same
    `task`/`trigger_type` `write_topic_note` will stamp with, so the gate and
    the stamp can never disagree.

    Never raises — best-effort, like every other Scribe side effect.
    """
    if not statements:
        return
    project_id = project.get('id', '') if isinstance(project, dict) else ''
    try:
        task = (session.get('task', '') or '').strip()
        trigger_type = session.get('trigger_type', '')
        if _stamp_origin(task, trigger_type) != 'interactive':
            return
        actor = session.get('session_id') or session.get('id') or 'scribe'
        # CLAUDE.md "nothing operator-specific in the repo": the configured
        # user name is this operator's own data, not a literal to hardcode —
        # same source `_habit_source_statements` already reads for the label
        # match, so the note's wording and the gate that found it agree.
        user_label = state.CONFIG.get('user_name') or 'The user'
        for stmt in statements:
            stmt = (stmt or '').strip()
            if not stmt or len(stmt) > 2000:
                continue
            m = _HABIT_CUE_RE.search(stmt)
            if not m:
                continue
            cue = m.group(0).strip()
            slug = _habit_note_slug(stmt, cue)
            if not slug:
                continue
            snippet = stmt if len(stmt) <= 220 else stmt[:217] + '...'
            description = f'{user_label}, standing behaviour observed: "{snippet}"'
            body = (
                f'{user_label} said, verbatim:\n\n> {stmt}\n\n'
                f'**Why it matters:** flagged as standing operator behaviour '
                f'(cue: "{cue}"), not a one-off request — MC-964 Step E.\n\n'
                f'**How to apply:** treat as a habit to account for when '
                f'related state looks stale or contradicted, not as an '
                f'instruction.\n'
            )
            violation = _distiller._authority_violation(f'{description}\n{body}')
            if violation:
                _scribe_stat(project_id, 'habit_note_refused_authority')
                _log(f'[scribe] habit note refused (authority guard hit '
                     f'{violation!r}): {stmt[:120]!r}')
                continue
            fn = write_topic_note(
                project, slug, description, body, note_type='project',
                task=task, trigger_type=trigger_type, actor=actor)
            if fn:
                _scribe_stat(project_id, 'habit_note_minted')
                _log(f'[scribe] habit note minted: {fn} (cue={cue!r})')
    except Exception as e:
        _log(f'[scribe] habit classification failed: {e}')


def _scribe_extract(project, session):
    """Leg A scribe. Returns (entry_text, outcome_reason).

    entry_text is None when the caller must fall back to the legacy
    stdout-tail summary. Never raises. Dispatch-time incognito/housekeeping
    gate is asserted here too so Phase-2 mid-session triggers inherit it.

    Transcript file is the PREFERRED source, routed through the SESSION'S
    OWN provider rather than hardcoded to Claude (parity audit item 1,
    "Scribe on Codex" — `mc.memory` used to call `get_runtime('claude')`
    unconditionally, so extraction never even looked for a transcript on a
    non-Claude session). Claude keeps its exact prior path
    (`_find_transcript_file` → `_scribe_render_transcript`, Claude-shape
    `.jsonl`). A non-Claude provider is asked for its own transcript via
    `transcript_path(pp, provider_session_id)`, and — because that shape is
    NOT Claude's — for its OWN rendering via `render_transcript_for_scribe()`
    (default None = "no adaptation"; `CodexRuntime` implements it against the
    rollout's `response_item` records). This is deliberately NOT a
    special-case inside the Scribe: the shape-specific parsing lives on the
    runtime, at the boundary, and this function only chooses which one to
    ask. Whenever no transcript is found OR a provider declines to render
    one, this falls back to `session['log_lines']` (captured for every
    provider) rendered into the same shape and fed to the IDENTICAL
    summarizer (MC-922). A successful log-backed extraction is tagged
    'extracted_from_log' so it stays separately countable from a
    transcript-backed 'extracted'.
    """
    if not state.CONFIG.get('scribe_enabled', True):
        return None, 'disabled'
    if session.get('incognito') or session.get('housekeeping'):
        return None, 'gated'
    pid = project.get('id', '')
    pp = project.get('project_path', '')
    provider = (session.get('provider') or 'claude').lower()
    csid = session.get('claude_session_id', '')
    canonical_transcript = None
    if _canonical_scribe_reader is not None and pid and (session.get('session_id') or session.get('id')):
        try:
            selection = _canonical_scribe_reader(
                pid, session.get('session_id') or session.get('id'), session)
            if (selection is not None and getattr(selection, 'source', '') == 'canonical'
                    and getattr(selection, 'lines', ())):
                canonical_transcript = '\n'.join(selection.lines)
        except Exception as e:
            _log(f"[scribe] canonical projection lookup failed: {e}")
    # The id that identifies this session TO its own provider — csid for
    # Claude, the Codex/opencode thread id (provider_session_id) otherwise.
    # Only used to pick the 'no_csid' vs 'no_transcript' outcome reason below;
    # actual lookup is per-provider (tf resolution just under this).
    provider_sid = csid if provider == 'claude' else session.get('provider_session_id', '')
    tf = None
    if canonical_transcript is not None:
        from_log = False
    elif provider == 'claude':
        tf = _find_transcript_file(pp, csid) if csid else None
    elif provider_sid:
        try:
            tf = _agent_runtime.get_runtime(provider).transcript_path(pp, provider_sid)
        except Exception as e:
            _log(f"[scribe] {provider} transcript_path lookup failed: {e}")
    from_log = False
    log_lines = None
    if canonical_transcript is None and not tf:
        log_lines = session.get('log_lines') or []
        if not log_lines:
            return None, ('no_csid' if not provider_sid else 'no_transcript')
        from_log = True
    with _scribe_lock:
        if pid in _scribing_projects:
            return None, 'busy'
        _scribing_projects.add(pid)
    try:
        try:
            if canonical_transcript is not None:
                transcript = canonical_transcript
            elif from_log:
                transcript = _render_log_lines_as_transcript(log_lines)
            elif provider == 'claude':
                transcript = _scribe_render_transcript(tf)
            else:
                # tf is guaranteed non-None here: `from_log` is set True
                # whenever `not tf` (above), and this branch only runs when
                # `from_log` is False — pyright can't correlate the two
                # separate variables across the intervening lock/branches.
                transcript = _agent_runtime.get_runtime(provider).render_transcript_for_scribe(tf)  # pyright: ignore[reportArgumentType]
                if transcript is None:
                    # Runtime declined (unsupported provider, or a real parse
                    # failure) — fall back to log_lines rather than treat an
                    # unparseable transcript as an empty/thin session.
                    log_lines = session.get('log_lines') or []
                    if not log_lines:
                        return None, 'no_transcript'
                    transcript = _render_log_lines_as_transcript(log_lines)
                    from_log = True
        except Exception:
            return None, 'parse_empty'
        model = _model_for_provider('scribe_model', provider)
        # want_why: terminal entries only — see _SCRIBE_WHY_SUFFIX.
        token = _with_transform_context(provider, cwd=pp or None)
        try:
            entry, reason = _scribe_summarize_text(transcript, model, want_why=True)
        finally:
            _reset_transform_context(token)
        if entry is not None and from_log:
            reason = 'extracted_from_log'
        # MC-964 Step E: runs alongside the archive-line write above, never
        # instead of it — a classifier miss must never regress today's
        # archive-echo behaviour (RC2's fix is additive capture shape, not a
        # replacement path). Never affects `entry`/`reason`.
        _scribe_classify_habits(project, session, _habit_source_statements(session))
        return entry, reason
    finally:
        with _scribe_lock:
            _scribing_projects.discard(pid)


def _scribe_split_why(raw):
    """Split a two-part scribe reply into (what, why).

    The parts are separated by a line of only dashes. `why` comes back '' when
    the separator is absent (model ignored the suffix), when the model said
    NONE (the expected case — most sessions have no diagnosis), or when what
    came back is too short to be a real note. Pure function, never raises.
    """
    lines = (raw or '').splitlines()
    cut = -1
    for i, ln in enumerate(lines):
        s = ln.strip()
        if len(s) >= 3 and set(s) == {'-'}:
            cut = i
            break
    if cut < 0:
        return (raw or '').strip(), ''
    what = '\n'.join(lines[:cut]).strip()
    why = ' '.join(ln.strip() for ln in lines[cut + 1:]).strip()
    # Strip a leading "WHY:" label if the model echoed one.
    if why.lower().startswith('why:'):
        why = why[4:].strip()
    if why.rstrip('.').strip().lower() in _SCRIBE_WHY_NULLS:
        why = ''
    if len(why) < _SCRIBE_WHY_MIN:
        why = ''
    return what, why[:_SCRIBE_WHY_CAP]


def _scribe_summarize_text(text, model, want_why=False):
    """Core: rendered-transcript text → (one_line_summary, 'extracted') or
    (None, reason). Thin-transcript guard + single/map-reduce + refusal guard.
    Map coverage is all-or-nothing: any failed/empty/refused chunk returns
    model_error (the existing caller-compatible reason), never partial success.
    No I/O, no locks — shared by _scribe_extract (whole transcript, completion
    path) and the Step-6 checkpoint worker (delta). Never raises.

    want_why=True additionally asks for a causal diagnosis and, when the model
    supplies one, appends it to the returned line as `_why:_ <note>`. Default
    False keeps the checkpoint path's output byte-identical.
    """
    _stripped = (text or '').strip()
    _has_activity = any(
        ln.startswith(_SCRIBE_ACTIVITY_PREFIXES)
        for ln in _stripped.splitlines())
    if not _has_activity and len(_stripped) < _SCRIBE_THIN_TEXT_CHARS:
        # No tool/think activity and only a trivial blip (aborted/no-op).
        # Caller falls back rather than persist a hallucinated reply.
        return None, 'parse_empty'
    want_why = bool(want_why) and state.CONFIG.get('scribe_why_enabled', True)
    try:
        if len(_stripped) <= _SCRIBE_SINGLE_LIMIT:
            out = _model_call(
                model,
                _SCRIBE_PROMPT + (_SCRIBE_WHY_SUFFIX if want_why else ''),
                _stripped)
        else:
            chunks, cur, n = [], [], 0
            for ln in _stripped.split('\n'):
                cur.append(ln)
                n += len(ln) + 1
                if n >= _SCRIBE_SINGLE_LIMIT:
                    chunks.append('\n'.join(cur))
                    cur, n = [], 0
            if cur:
                chunks.append('\n'.join(cur))
            partials = []
            for i, ch in enumerate(chunks):
                try:
                    partial = (_model_call(model, _SCRIBE_MAP_PROMPT, ch) or '').strip()
                    if not partial or any(mk in partial.lower() for mk in _SCRIBE_REFUSAL_MARKERS):
                        _log(f"[scribe] model_error: incomplete map coverage at chunk "
                             f"{i + 1}/{len(chunks)} (model={model})")
                        return None, 'model_error'
                    partials.append(partial)
                except Exception as e:
                    _log(f"[scribe] map chunk {i + 1}/{len(chunks)} failed "
                         f"(model={model}, {len(ch)}c): {e}")
                    # A surviving subset is not a summary of the full span.
                    return None, 'model_error'
            if not partials:
                _log(f"[scribe] model_error: all {len(chunks)} map chunks failed "
                     f"(model={model})")
                return None, 'model_error'
            out = _model_call(
                model,
                _SCRIBE_REDUCE_PROMPT + (_SCRIBE_WHY_SUFFIX if want_why else ''),
                '\n'.join(f"- {p}" for p in partials if p))
    except subprocess.TimeoutExpired as e:
        _log(f"[scribe] model_error: timeout (model={model}, "
             f"{len(_stripped)}c in): {e}")
        return None, 'model_error'
    except Exception as e:
        _log(f"[scribe] model_error: call failed (model={model}, "
             f"{len(_stripped)}c in): {e}")
        return None, 'model_error'
    # Split BEFORE flattening newlines — the two parts are separated by a
    # dashes-only LINE, so collapsing newlines first would destroy it.
    why = ''
    if want_why:
        out, why = _scribe_split_why(out or '')
    out = (out or '').strip().replace('\n', ' ').strip()
    if not out:
        _log(f"[scribe] model_error: empty reply (model={model}, "
             f"{len(_stripped)}c in)")
        return None, 'model_error'
    if any(mk in out.lower() for mk in _SCRIBE_REFUSAL_MARKERS):
        _log(f"[scribe] model_refused (model={model}): {out[:120]}")
        return None, 'model_refused'
    out = out[:300]
    # A refused/hallucinated body must not smuggle a why through with it.
    if why:
        out = f"{out} {_SCRIBE_WHY_MARKER} {why}"
    return out, 'extracted'


def _condense_integrity_check(mem_path, pre_mem, pre_wm, rc):
    """Post-condense safety net for MEMORY.md.

    A condense run is an external `claude` subprocess that rewrites MEMORY.md
    with the Write tool. If it is truncated mid-task (e.g. it hits --max-turns
    before the write step, the failure that motivated this guard) it can leave
    the file empty, drop the managed-region sentinels, nuke the curated index,
    or — worst — delete a `clayrune:wm:` watermark and lose a live session's
    progress. Compare the post-run file against the pre-run snapshot and decide:

      ('ok', ...)      file intact (or no pre-image to protect)
      ('heal', ...)    structure fine but live watermark(s) dropped — caller
                       re-injects them, preserving the agent's curation work
      ('restore', ...) hard corruption — caller rewrites `pre_mem` verbatim

    Returns (action, reason, status_kw). status_kw is merged into the per-
    project condense status so chronic turn-cap failures stay visible in
    telemetry instead of silently self-healing on the next trigger.
    """
    if pre_mem is None:
        # No pre-image captured — can only trust the exit code.
        if rc not in (0, None):
            return 'ok', f'agent exited {rc}', {
                'state': 'error', 'turn_cap': True,
                'error': f'condense agent exited {rc} (likely --max-turns); '
                         'no pre-image captured to verify integrity'}
        return 'ok', '', {}
    try:
        post = mem_path.read_text(encoding='utf-8') if mem_path.exists() else ''
    except Exception as e:
        return 'restore', f'post-read failed ({e})', {
            'state': 'error',
            'error': f'MEMORY.md unreadable after condense ({e}); restored pre-image'}
    if not post.strip():
        return 'restore', 'empty after condense', {
            'state': 'error',
            'error': 'MEMORY.md empty after condense; restored pre-image'}

    if (_MEM_BEGIN in pre_mem and _MEM_END in pre_mem
            and not (_MEM_BEGIN in post and _MEM_END in post)):
        return 'restore', 'managed-region sentinels missing', {
            'state': 'error',
            'error': 'condense dropped the managed-region sentinels; restored pre-image'}

    pre_cur = _mem_split_full(pre_mem)[0]
    post_cur = _mem_split_full(post)[0]
    if len(pre_cur) > 200 and len(post_cur) < 0.25 * len(pre_cur):
        return 'restore', 'curated index lost >75%', {
            'state': 'error',
            'error': 'condense truncated the curated index (>75% lost); '
                     'restored pre-image'}

    post_wm = set(_mem_split_full(post)[2])
    missing_wm = [w for w in (pre_wm or []) if w not in post_wm]
    if missing_wm:
        if rc not in (0, None):
            kw = {'state': 'error', 'turn_cap': True,
                  'wm_repaired': len(missing_wm),
                  'error': f'condense agent exited {rc} (likely --max-turns) and '
                           f'dropped {len(missing_wm)} live-session watermark(s); '
                           're-injected, no progress lost'}
        else:
            kw = {'state': 'done', 'wm_repaired': len(missing_wm)}
        return 'heal', f'{len(missing_wm)} watermark(s) dropped', kw

    if rc not in (0, None):
        return 'ok', f'agent exited {rc}', {
            'state': 'error', 'turn_cap': True,
            'error': f'condense agent exited {rc} (likely --max-turns); '
                     'MEMORY.md integrity OK — no facts or watermarks lost'}
    return 'ok', '', {}


_CONDENSE_ACTIONS = ('keep', 'demote', 'fold')


_CONDENSE_ARCHIVE_TAIL_KB = 4


_CONDENSE_PLAN_PROMPT = (
    "You are the memory-condense decider (SPEC Leg C). You are NOT an agent: "
    "you have no tools, you do not write files. You receive a JSON object and "
    "you return ONLY a JSON object — no prose, no markdown fences.\n\n"
    "INPUT shape:\n"
    "  curated_headings: exact heading lines of the hand-curated pointer index\n"
    "  entries: [{id, text}] — raw machine-written `- [date] ...` session-log lines\n"
    "  archive_tail: recent already-archived lines (dedupe context only)\n"
    "  line_budget: target max lines for the whole auto-loaded file\n\n"
    "For EACH entry decide, by VALUE not recency:\n"
    "  • keep   — recent, not yet foldable; stays in the session log\n"
    "  • demote — no lasting value as a pointer; the raw line is moved to the\n"
    "             permanent archive (still searchable). NOTHING is erased.\n"
    "  • fold   — its durable insight belongs in the curated index. Provide\n"
    "             `fold_into` (an EXACT string from curated_headings) and\n"
    "             `pointer_line` (one new `- [...]` index line, single line,\n"
    "             no newline, must NOT contain the substring 'clayrune:'). The\n"
    "             raw entry is ALSO archived (fact preserved verbatim).\n\n"
    "Rules: never invent a heading; `fold_into` must match curated_headings\n"
    "verbatim. Prefer fold/demote enough that the file trends under\n"
    "line_budget, but never sacrifice a hard-won fact (paths, line numbers,\n"
    "symbol names, config keys, thresholds, gotchas) — those go to fold or\n"
    "demote, never 'keep-and-hope'. Entries you don't mention default to keep.\n\n"
    "OUTPUT exactly: {\"entry_decisions\":[{\"id\":\"..\",\"action\":\"keep|demote|fold\","
    "\"fold_into\":\"..\",\"pointer_line\":\"..\"}],\"curated_rewrite\":null}\n"
    "(`fold_into`/`pointer_line` only on fold entries; `curated_rewrite` must "
    "be null — wholesale curated re-authoring is not permitted in this mode.)"
)


def _condense_parse_json(raw):
    """Extract the JSON object from a model reply (tolerates ``` fences /
    leading prose). Returns dict or None."""
    s = (raw or '').strip()
    if s.startswith('```'):
        s = s.split('```', 2)[1] if s.count('```') >= 2 else s.strip('`')
        if s.lstrip().lower().startswith('json'):
            s = s.lstrip()[4:]
    i, j = s.find('{'), s.rfind('}')
    if i < 0 or j <= i:
        return None
    try:
        v = json.loads(s[i:j + 1])
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def _validate_condense_payload(payload, valid_ids, valid_headings):
    """Schema + invariant gate, applied BEFORE the server writes anything.
    Returns (True, '') or (False, reason). Strictly pre-write: a reject leaves
    MEMORY.md untouched (no pre-image / restore needed)."""
    if not isinstance(payload, dict):
        return False, 'not_object'
    if payload.get('curated_rewrite') is not None:
        return False, 'curated_rewrite_forbidden_v1'
    decs = payload.get('entry_decisions')
    if not isinstance(decs, list):
        return False, 'entry_decisions_not_list'
    seen = set()
    for d in decs:
        if not isinstance(d, dict):
            return False, 'decision_not_object'
        did = d.get('id')
        if did not in valid_ids:
            return False, 'unknown_id'
        if did in seen:
            return False, 'duplicate_id'
        seen.add(did)
        act = d.get('action')
        if act not in _CONDENSE_ACTIONS:
            return False, 'bad_action'
        if act == 'fold':
            fi = d.get('fold_into')
            pl = d.get('pointer_line')
            if fi not in valid_headings:
                return False, 'fold_into_not_a_heading'
            if not isinstance(pl, str) or not pl.strip():
                return False, 'empty_pointer_line'
            if '\n' in pl or '\r' in pl:
                return False, 'multiline_pointer_line'
            if 'clayrune:' in pl:
                return False, 'pointer_line_synthesizes_machinery'
    return True, ''


def _condense_plan(project):
    """Assemble bounded read-only input, make ONE non-agentic model call, parse
    + validate. Returns (payload|None, reason, model_ms). Never raises."""
    t0 = _time.time()
    try:
        mem_path = _get_memory_path(project)
        if not mem_path.exists():
            return None, 'no_memory_file', 0
        # §16 step 4: entries live in SESSION_LOG.md now; a legacy MEMORY.md's
        # inline entries are still folded in for a not-yet-migrated project
        # (§10.4 both-formats-coexist) — same read shape _condense_apply uses,
        # so ids computed here stay valid at apply time.
        curated, legacy_entries, _wm = _mem_split_full(
            _mem_migrate(mem_path.read_text(encoding='utf-8')))
        entries = legacy_entries + _session_log_entries(project)
        if not entries:
            return None, 'noop', 0
        # Collect curated headings as fold targets, but skip any '#' line
        # inside a fenced code block (a shell comment / ATX-looking line in a
        # ``` fence is not a real section) — otherwise a pointer could be
        # folded into code. _condense_apply additionally requires the heading
        # to resolve UNIQUELY at apply time, else it downgrades to demote.
        valid_headings, _in_fence = [], False
        for ln in curated.splitlines():
            if ln.lstrip().startswith('```'):
                _in_fence = not _in_fence
                continue
            if not _in_fence and ln.lstrip().startswith('#'):
                valid_headings.append(ln.strip())
        in_entries, valid_ids = [], set()
        for e in entries:
            eid = _sha8(e)
            valid_ids.add(eid)
            in_entries.append({'id': eid, 'text': e})
        archive_tail = ''
        ap = _get_archive_path(project)
        if ap.exists():
            try:
                blob = ap.read_text(encoding='utf-8')
                archive_tail = blob[-_CONDENSE_ARCHIVE_TAIL_KB * 1024:]
            except Exception:
                pass
        body = json.dumps({
            'curated_headings': valid_headings,
            'entries': in_entries,
            'archive_tail': archive_tail,
            'line_budget': int(state.CONFIG.get('index_line_budget', 160) or 160),
        }, ensure_ascii=False)
        # Default to haiku, NOT sonnet. The structured condense is a one-shot
        # JSON call with no tools and a schema-validated reply — same shape as
        # Scribe, which already defaults to haiku. Sonnet's reasoning depth is
        # wasted here and routinely times out on 30KB+ stdin payloads (live:
        # 91 model_errors + 58 timeouts vs 5 successes before this default
        # was corrected). Users who want sonnet can still set condense_model
        # explicitly in Settings.
        pid = project.get('id', '')
        project_provider = _explicit_project_provider(project)
        model = _model_for_provider('condense_model', project_provider)
        token = _with_transform_context(
            project_provider,
            cwd=project.get('project_path') or None)
        try:
            raw = _model_call(model, _CONDENSE_PLAN_PROMPT, body)
        except subprocess.TimeoutExpired as e:
            _log(f"[condense] {pid}: model_timeout (model={model}, "
                 f"{len(body)}c in, {len(entries)} entries): {e}")
            return None, 'model_timeout', int((_time.time() - t0) * 1000)
        except Exception as e:
            _log(f"[condense] {pid}: model_error (model={model}, "
                 f"{len(body)}c in, {len(entries)} entries): {e}")
            return None, 'model_error', int((_time.time() - t0) * 1000)
        finally:
            _reset_transform_context(token)
        ms = int((_time.time() - t0) * 1000)
        payload = _condense_parse_json(raw)
        if payload is None:
            _log(f"[condense] {pid}: parse_error after {ms}ms (model={model}) "
                 f"— reply head: {(raw or '')[:160]!r}")
            return None, 'parse_error', ms
        ok, why = _validate_condense_payload(
            payload, valid_ids, set(valid_headings))
        if not ok:
            _log(f"[condense] {pid}: rejected '{why}' after {ms}ms "
                 f"(model={model})")
            return None, why, ms
        return payload, 'ok', ms
    except Exception as e:
        # Static reason — keeps the colon-suffixed telemetry key bounded
        # (raw exception text must never become a _scribe_stats.json key).
        # Detail goes to the log + the bounded last-write-wins status field.
        _log(f"[condense] {project.get('id','')}: plan exception — {e}")
        return None, 'plan_exc', int((_time.time() - t0) * 1000)


def _condense_apply(project, payload):
    """Rebased, transactional apply under the SAME leaf lock the completion
    scribe + Step-6 use. Decisions are keyed by _sha8(entry); any decision
    whose entry vanished meanwhile (Step-6 fold / teardown / ring rotation)
    is silently skipped. wm markers pass through untouched. Returns a stats
    dict.

    §16 step 4: entries + wm markers are read from and written back to
    SESSION_LOG.md; curated pointer lines (fold's only way to grow anything)
    are read from and written back to MEMORY.md. The two files are read and
    written together under the SAME leaf lock, so no OTHER writer can observe
    a half-applied decision — not true single-file atomicity across two
    files, but the same accepted risk this function already carried between
    `_append_to_archive` and its own MEMORY.md write.
    """
    pid = project.get('id', '')
    mem_path = _get_memory_path(project)
    ring = _session_log_ring()
    decs = {d['id']: d for d in payload.get('entry_decisions', [])}
    st = {'kept': 0, 'demoted': 0, 'folded': 0,
          'skipped_rebased': 0, 'fold_downgraded': 0, 'curated_lines': 0}
    with _get_mem_write_lock(pid):
        existing = (mem_path.read_text(encoding='utf-8')
                    if mem_path.exists() else '')
        curated, legacy_entries, wm = _mem_split_full(_mem_migrate(existing))
        log_entries, log_wm = _session_log_read(project, strict=True)
        entries = legacy_entries + log_entries
        wm = _wm_merge(log_wm, wm)
        cur_lines = curated.splitlines()
        cur_norm = {ln.strip() for ln in cur_lines}
        present_ids = set()
        new_entries, overflow = [], []
        # Pointer lines this run inserted into curated, in insertion order —
        # fold is the only way _condense_apply grows curated, and the
        # MC-917 cap backstop below pops these LIFO if eviction alone can't
        # bring the file back under budget.
        folded_pointers = []
        for e in entries:
            eid = _sha8(e)
            present_ids.add(eid)
            # Duplicate byte-identical entry lines hash to the same id, so one
            # decision intentionally applies to ALL of them. This is safe and
            # desirable: demote/fold route every copy verbatim to the
            # append-only archive (no fact lost) and collapse the noise; keep
            # is a per-copy no-op. _validate_condense_payload already rejects
            # duplicate ids in the decision LIST, so the model can't disagree
            # with itself across copies.
            d = decs.get(eid)
            act = d.get('action') if d else 'keep'
            if act == 'demote':
                overflow.append(e)
                st['demoted'] += 1
            elif act == 'fold':
                heading = d.get('fold_into')  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (mop)
                pl = d.get('pointer_line', '').strip()  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (mop)
                hits = [k for k, ln in enumerate(cur_lines)
                        if ln.strip() == heading]
                if len(hits) != 1:
                    # Heading vanished, or is ambiguous (0 or >1 matches since
                    # plan time) — never misplace a pointer or lose the fact:
                    # demote the raw entry, skip the curated insert.
                    overflow.append(e)
                    st['fold_downgraded'] += 1
                    continue
                if pl and pl not in cur_norm:
                    cur_lines.insert(hits[0] + 1, pl)
                    cur_norm.add(pl)
                    folded_pointers.append(pl)
                overflow.append(e)        # fact preserved verbatim in archive
                st['folded'] += 1
            else:
                new_entries.append(e)
                st['kept'] += 1
        # Decisions whose target entry is gone (concurrent Step-6 / teardown).
        st['skipped_rebased'] = sum(
            1 for did in decs if did not in present_ids)
        curated2 = '\n'.join(cur_lines)
        # Mechanical ring backstop (§9.2 B2, same rule as
        # _commit_managed_entry — SESSION_LOG.md's own entry count, not a
        # byte/line floor shared with curated: the two no longer share a
        # budget after the split).
        while new_entries and len(new_entries) > ring:
            overflow.append(new_entries.pop(0))
        # MC-917: fold is the only way this function grows curated, and
        # curated has no mechanical drain — evicting every evictable managed
        # entry above can't free a single curated byte. If curated is STILL
        # over the hard index_byte_budget on its OWN (post-split, it no
        # longer shares that budget with entries/wm at all), undo fold
        # pointer inserts, most-recently-inserted first, until it fits or
        # none remain. No fact is lost either way: every folded entry
        # already went to `overflow` (the archive) above regardless of
        # outcome, so a rolled-back insert just lands on the same
        # `fold_downgraded` outcome as an ambiguous/vanished heading.
        rolled_back = 0
        while folded_pointers and _index_overflow(
                '\n'.join(cur_lines)) is not None:
            pl = folded_pointers.pop()
            for _k in range(len(cur_lines) - 1, -1, -1):
                if cur_lines[_k] == pl:
                    del cur_lines[_k]
                    break
            rolled_back += 1
        if rolled_back:
            curated2 = '\n'.join(cur_lines)
            st['folded'] -= rolled_back
            st['fold_downgraded'] += rolled_back
            _log(f"[condense] {pid}: {rolled_back} fold pointer(s) rolled "
                 f"back — MEMORY.md still over the "
                 f"{_index_byte_cap() // 1024}KB index budget after "
                 f"evicting managed entries; fact stays in the archive, "
                 f"curated insert skipped")
        # Post-apply curated size — a gauge (not additive) so soak can watch
        # the model-authored curated index for monotonic low-value drift
        # (additive-only fold has no mechanical eviction path until v2).
        st['curated_lines'] = len(cur_lines)
        _append_to_archive(project, overflow)
        _write_curated_only(mem_path, curated2)
        _write_session_log(project, new_entries, wm)
    return st


def _run_structured_condense(project):
    """Orchestrator for condense_mode='structured'. Mirrors the agent path's
    status/lock discipline; the slow model call is OUTSIDE the leaf lock.
    Caller (_dispatch_condense) already holds the _condensing_projects guard
    and this MUST discard it. Never raises."""
    pid = project['id']
    _set_condense_status(pid, state='running', started_at=now_iso(),
                         finished_at=None, error=None,
                         turn_cap=False, wm_repaired=0,
                         bytes_before=_condense_combined_bytes(project),
                         bytes_after=None)
    try:
        payload, reason, ms = _condense_plan(project)
        if payload is None:
            if reason in ('noop', 'no_memory_file'):
                _scribe_stat(pid, f'condense_{reason}')
                _set_condense_status(pid, state='done', model_ms=ms)
            else:
                _scribe_stat(pid, f'condense_rejected:{reason}')
                _set_condense_status(
                    pid, state='error', model_ms=ms,
                    error=f'structured condense not applied ({reason}); '
                          'MEMORY.md left untouched')
            return
        st = _condense_apply(project, payload)
        _scribe_stat(pid, 'condense_structured_ok')
        for k in ('kept', 'demoted', 'folded'):
            _scribe_stat(pid, f'condense_entries_{k}', st.get(k, 0))
        _scribe_stat(pid, 'condense_decisions_skipped_rebased',
                     st.get('skipped_rebased', 0))
        _scribe_stat(pid, 'condense_fold_downgraded',
                     st.get('fold_downgraded', 0))
        _set_condense_status(pid, state='done', model_ms=ms, **st)
        _log(f"[condense] {pid}: structured ok — "
             f"kept={st['kept']} demoted={st['demoted']} "
             f"folded={st['folded']} skipped_rebased={st['skipped_rebased']}")
    except Exception as e:
        _log(f"[condense] {pid}: structured error — {e}")
        _set_condense_status(pid, state='error', error=str(e))
    finally:
        _set_condense_status(pid, finished_at=now_iso(),
                             bytes_after=_condense_combined_bytes(project))
        with _condense_lock:
            if _condense_status.get(pid, {}).get('state') == 'running':
                _condense_status[pid]['state'] = 'done'
            _condensing_projects.discard(pid)


def _dispatch_condense(project):
    """Launch a housekeeping agent to condense memory + CLAUDE.md for a project."""
    pid = project['id']
    with _condense_lock:
        if pid in _condensing_projects:
            return
        _condensing_projects.add(pid)
        _condense_triggered_at[pid] = _time.time()

    # Leg C executor selection. 'structured' (docs/CONDENSE_STRUCTURED_DESIGN.md)
    # replaces the free claude -p + Write agent below with one non-agentic JSON
    # call applied server-side. The structured runner owns the
    # _condensing_projects discard in its finally, same as the agent _run.
    if (state.CONFIG.get('condense_mode', 'agent') or 'agent') == 'structured':
        threading.Thread(target=_run_structured_condense,
                         args=(project,), daemon=True).start()
        return

    mem_path = _get_memory_path(project)
    archive_path = _get_archive_path(project)
    pp = project.get('project_path', '')

    # P2-1: mark condensation in-flight (bytes_before = pre-condense size).
    _set_condense_status(pid, state='running', started_at=now_iso(),
                         finished_at=None, error=None,
                         turn_cap=False, wm_repaired=0,
                         bytes_before=_condense_combined_bytes(project),
                         bytes_after=None)

    # Check if CLAUDE.md exists and is large enough to warrant condensation
    claude_md_path = Path(pp) / 'CLAUDE.md' if pp else None
    claude_md_big = False
    if claude_md_path and claude_md_path.exists():
        try:
            claude_md_big = claude_md_path.stat().st_size > 15 * 1024  # > 15KB
        except OSError:
            pass

    budget = int(state.CONFIG.get('index_line_budget', 160) or 160)
    prompt_parts = [
        "You are a memory housekeeping agent (SPEC Leg C model tier). Your ONLY "
        "job is to curate the project context files so they stay concise and "
        "effective. You decide by VALUE, never by recency.\n",
        f"## MEMORY.md curation — target: the WHOLE file under {budget} LINES\n"
        f"(The harness only auto-loads ~200 lines; staying under {budget} keeps "
        f"headroom. This is a LINE budget, not a KB target.)\n"
        f"1. Read {mem_path}\n"
        f"2. Read {archive_path} (if it exists)\n"
        "3. MEMORY.md has two regions, treat them differently:\n"
        "   - CURATED region (everything ABOVE the "
        "`<!-- clayrune:managed:begin -->` sentinel): the hand-curated pointer "
        "index. You ARE permitted to compact THIS region (you are the only "
        "agent allowed to): merge overlapping pointers/sections covering the "
        "same subsystem, drop stale 'as of YYYY-MM-DD' notes clearly superseded "
        "by a later section, cut narration but keep the fact.\n"
        "   - MANAGED region (between `<!-- clayrune:managed:begin -->` and "
        "`<!-- clayrune:managed:end -->`, under `## Session Log`): raw "
        "machine-written session entries. For EACH entry decide, by value: "
        "(a) fold its durable insight into the matching curated pointer/topic "
        "then remove the raw entry; (b) if it has no lasting value, DEMOTE it "
        "(move it) to the archive; (c) keep it in the managed region only if "
        "it's recent and not yet foldable. Never keep/drop by recency alone.\n"
        "4. KEEP THE FORMAT: the rewritten file must still have the "
        "`<!-- clayrune:managed:begin -->` / `## Session Log` / "
        "`<!-- clayrune:managed:end -->` structure intact. The managed region "
        "may legitimately end up EMPTY after folding — that is fine; keep the "
        "sentinels and header. CRITICAL: any line beginning "
        "`<!-- clayrune:wm:` is a live-session watermark — PRESERVE IT "
        "VERBATIM, do not fold/move/delete/reformat it (deleting one loses a "
        "running session's progress and forces a re-scribe from zero).\n"
        "5. NEVER hard-delete a fact. The only permitted deletions are exact "
        "duplicates or an entry STRICTLY superseded by a newer one that wholly "
        "contains it. 'Not worth a curated slot' means DEMOTE to the archive "
        "(still searchable cold storage), never erase.\n"
        "6. DO NOT lose hard-won facts. Preserve verbatim: file paths, line "
        "numbers, function/class names, config keys, exact numeric thresholds, "
        "API signatures, command snippets, and any 'gotcha' warnings.\n"
        f"7. Append demoted/overflow entries to {archive_path} (create it if "
        f"needed). NEVER delete or truncate the archive — it is permanent "
        f"searchable cold storage (SPEC D3).\n"
        f"8. Write the curated result back to {mem_path}. Target under {budget} "
        f"lines; if after honest folding it is still slightly over, that is "
        f"acceptable — do NOT delete critical facts just to hit a number.\n",
    ]

    if claude_md_big:
        prompt_parts.append(
            f"\n## CLAUDE.md condensation — target under 15KB\n"
            f"9. Read {claude_md_path}\n"
            "10. This file contains project instructions and context that Claude CLI loads natively. "
            "Condense it while preserving ALL critical information:\n"
            "   - Keep all instructions, rules, and constraints verbatim.\n"
            "   - Merge duplicate/overlapping sections.\n"
            "   - Remove redundant examples, excessive formatting, and verbose explanations.\n"
            "   - Compress session logs / historical notes into brief summaries.\n"
            "   - Preserve code snippets, API references, and config patterns exactly.\n"
            f"11. Write the condensed result back to {claude_md_path}. Target under 15KB; do NOT "
            f"strip critical rules just to hit a number.\n"
        )

    prompt_parts.append(
        "\nBE TURN-EFFICIENT (you have a limited turn budget): read EVERY "
        "input file you need in your FIRST turn using parallel tool calls, "
        "do all the folding/demotion reasoning, then write each output file "
        "EXACTLY ONCE. Do not re-read a file you have already read. The write "
        "step is what matters — do not spend the whole budget exploring.\n"
        "\nDo NOT create any other files. Do NOT modify any code. Only touch the files listed above."
    )
    prompt = '\n'.join(prompt_parts)

    model = state.CONFIG.get('condense_model', '') or 'sonnet'
    # --max-turns 14 (was 5): the workload is read MEMORY.md + read archive
    # (+ optionally read CLAUDE.md) + fold/demote N entries + append archive
    # + rewrite MEMORY.md. 5 turns were routinely exhausted on the reads
    # alone, so the CLI exited 1 *before the write step* and the run was
    # flagged ERROR (it only "self-healed" because the next trigger retried).
    # The post-run integrity guard below makes a truncated run safe; this
    # gives it enough room to actually finish.
    cmd = [_resolve_claude(), '-p', prompt, '--model', model, '--max-turns', '14',
           '--print', '--verbose', '--output-format', 'stream-json',
           '--dangerously-skip-permissions']

    cwd = pp if pp and Path(pp).is_dir() else str(Path.home())

    def _run():
        session_id = f'condense_{uuid.uuid4().hex[:8]}'
        # Pre-image snapshot for the post-run integrity guard. Captured here
        # (just before launch) so a truncated/botched run can never corrupt
        # MEMORY.md or lose a live-session watermark.
        try:
            pre_mem = mem_path.read_text(encoding='utf-8') if mem_path.exists() else None
        except Exception:
            pre_mem = None
        pre_wm = _mem_split_full(pre_mem)[2] if pre_mem else []
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=_POPEN_FLAGS,
                startupinfo=_STARTUPINFO,
            )
            threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
            _register_process(proc, 'Housekeeping (condense)', 'housekeeping',
                              session_id, pid, 'Memory condensation')

            session = {
                'proc': proc,
                'status': 'running',
                'task': 'Memory condensation',
                'log_lines': TimestampedLines(),
                'started_at': now_iso(),
                'session_id': session_id,
                'project_id': pid,
                'mode': 'A',
                'housekeeping': True,
            }
            mgr = get_manager(pid)
            with mgr.lock:
                agent_sessions[session_id] = session
                mgr.session_ids.add(session_id)

            # Reuse existing stream reader (blocks until proc exits)
            _read_agent_stream(proc, session)

            # Post-run safety net: a truncated condense (e.g. --max-turns hit
            # before the write step) must never leave MEMORY.md corrupted or
            # drop a live-session watermark.
            rc = proc.returncode
            action, reason, kw = _condense_integrity_check(
                mem_path, pre_mem, pre_wm, rc)
            if action == 'restore':
                try:
                    mem_path.write_text(pre_mem, encoding='utf-8')  # pyright: ignore[reportArgumentType]  # moved-verbatim typing debt (mop)
                    _log(f"[condense] {pid}: integrity FAIL ({reason}) — "
                         f"restored pre-image")
                except Exception as e:
                    _log(f"[condense] {pid}: RESTORE FAILED ({e}) — {reason}")
            elif action == 'heal':
                try:
                    cur, ent, wm = _mem_split_full(
                        mem_path.read_text(encoding='utf-8'))
                    have = set(wm)
                    for w in pre_wm:
                        if w not in have:
                            wm.append(w)
                            have.add(w)
                    mem_path.write_text(_mem_compose(cur, ent, wm),
                                        encoding='utf-8')
                    _log(f"[condense] {pid}: healed ({reason}) — re-injected "
                         f"dropped watermark(s), kept agent curation")
                except Exception as e:
                    # Heal failed — fall back to full restore to protect the
                    # load-bearing watermark over the agent's curation.
                    try:
                        mem_path.write_text(pre_mem, encoding='utf-8')  # pyright: ignore[reportArgumentType]  # moved-verbatim typing debt (mop)
                    except Exception:
                        pass
                    _log(f"[condense] {pid}: heal FAILED ({e}) — restored "
                         f"pre-image")
                    kw = {'state': 'error',
                          'error': f'watermark heal failed ({e}); '
                                   'restored pre-image'}
            if kw:
                _set_condense_status(pid, **kw)
        except Exception as e:
            _log(f"[condense] error for {pid}: {e}")
            _set_condense_status(pid, state='error', error=str(e),
                                 finished_at=now_iso())
        finally:
            # P2-1: record outcome. bytes_after = post-condense size; a
            # still-'running' state means the body finished without raising.
            _set_condense_status(pid, finished_at=now_iso(),
                                 bytes_after=_condense_combined_bytes(project))
            with _condense_lock:
                if _condense_status.get(pid, {}).get('state') == 'running':
                    _condense_status[pid]['state'] = 'done'
                _condensing_projects.discard(pid)

    threading.Thread(target=_run, daemon=True).start()
