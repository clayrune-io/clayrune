"""Skill import security scanner — MC-912.

Clayrune imports skills from paste, folder, git and plugin sources. A skill is
executable prompt injection loaded into the system prompt of an agent that
holds vault credentials, live-cookie browser profiles, and git push rights.
Until this module, nothing scanned that inbound path — `distiller.py`'s
``_authority_violation`` only guards what the agent itself WRITES via the
learning loop. This is that guard's inbound twin: same constitutional line
(learning/import may never expand what the agent is allowed to do), same
fail-closed posture, applied to human-supplied skill content instead of
model-generated artifacts.

Design ported from Nous Research's Hermes Agent (``tools/skills_guard.py``,
1,360 lines) per ``docs/research/HERMES_AGENT_COMPETITIVE_READ.md`` §6.2, with
one deliberate correction: their own docstring concedes static regex misses
``open(...,'w')`` / ``pathlib.write_text`` / ``fs.writeFileSync`` aimed at
config files — see ``_WRITE_CALL_RE`` + ``_CONFIG_TARGET_RE`` below, which
this port covers explicitly.

Trust tiers (checked in ``evaluate``):
  - ``builtin``   — ships with Clayrune itself (data/skills/builtin/). Never
                    scanned; the caller must not even invoke this module.
  - ``trusted``   — a small allowlist of known-good git remotes
                    (``TRUSTED_GIT_ALLOWLIST``). Passes on ``caution``-level
                    findings; blocked on ``warning``/``critical``.
  - ``community`` — everything else (paste, local folder, any other git URL,
                    plugin installs). Blocked on ANY finding unless the human
                    passes ``force=True``.

FAIL-CLOSED: a scanner crash is itself a critical finding and quarantines the
skill — it is never treated as "no findings" and let through, even for a
trusted source. ``force`` cannot override a scan crash, only real findings:
we don't know what's in unscanned content, so the human forcing past a
*known* finding is not the same as forcing past an *unknown* one.

Quarantine, not deletion: a blocked import's content is copied to
``~/.claude/skills.quarantine/<id>/`` alongside the verdict (offending lines
quoted) so a human can see why and decide. The ORIGINAL request (paste text,
folder path, or git staging id) still exists after a block — the normal
recovery path is simply re-submitting the same import with ``force: true``,
not replaying from the quarantine copy. Quarantine storage exists for
visibility/audit, not as the only route back.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import distiller as _distiller

# ── Paths ────────────────────────────────────────────────────────────────────


def _home() -> Path:
    return Path(os.environ.get('USERPROFILE') or os.environ.get('HOME') or str(Path.home()))


QUARANTINE_DIR = _home() / '.claude' / 'skills.quarantine'

# Quarantine ids are minted below as uuid4().hex[:12] — always bare lowercase
# hex. Anything else came from a caller, not from us. Mirrors
# skills.staging_dir()'s containment check (tests/test_skills_staging_path.py
# pinned that pattern after a real path-traversal finding on 2026-08-22).
_QUARANTINE_ID_RE = re.compile(r'^[0-9a-f]{1,64}$')


def quarantine_dir(quarantine_id: str) -> Path:
    if not quarantine_id or not _QUARANTINE_ID_RE.match(quarantine_id):
        raise ValueError(f'invalid quarantine id: {quarantine_id!r}')
    return (QUARANTINE_DIR / quarantine_id).resolve()


# ── Trust tiers ──────────────────────────────────────────────────────────────

SEVERITY_ORDER = {'caution': 0, 'warning': 1, 'critical': 2}

# Deliberately narrow. Mirrors Hermes's `trusted` tier (openai/skills,
# anthropics/skills only) — a short allowlist of publishers, not a broad
# heuristic. Widen only by adding an explicit entry, never by relaxing the
# match.
TRUSTED_GIT_ALLOWLIST = [
    re.compile(r'^https://github\.com/anthropics/skills(\.git)?/?$', re.IGNORECASE),
    re.compile(r'^https://github\.com/anthropics/claude-plugins-official(\.git)?/?$', re.IGNORECASE),
]


def classify_git_trust(clone_url: str) -> str:
    """Return 'trusted' if clone_url matches the publisher allowlist, else 'community'."""
    url = (clone_url or '').strip()
    for pat in TRUSTED_GIT_ALLOWLIST:
        if pat.match(url):
            return 'trusted'
    return 'community'


# ── Findings ─────────────────────────────────────────────────────────────────

@dataclass
class Finding:
    category: str
    severity: str  # caution | warning | critical
    file: str
    line: int
    snippet: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            'category': self.category,
            'severity': self.severity,
            'file': self.file,
            'line': self.line,
            'snippet': self.snippet,
            'detail': self.detail,
        }


# ── Detectors ────────────────────────────────────────────────────────────────
#
# Static regex over the skill's own text (SKILL.md + any bundled scripts).
# Cheap and deterministic on purpose — this runs synchronously on import, not
# in a background job, and it must never be the slow part of installing a
# skill.

_INJECTION_RE = re.compile(
    r'(ignore\s+(all\s+|the\s+)?(previous|prior|above)\s+instructions'
    r'|disregard\s+(your|the)\s+(system\s+prompt|instructions|rules)'
    r'|you\s+(are|must)\s+now\s+(act|behave|pretend)'
    r'|new\s+instructions\s*:'
    r'|system\s*:\s*override'
    r'|\bjailbreak\b'
    r'|\bDAN\s+mode\b)',
    re.IGNORECASE,
)

_DESTRUCTIVE_RE = re.compile(
    r'(rm\s+-rf\s+[/~]'
    r'|rm\s+-rf\s+\*'
    r'|del\s+/s\s*/q'
    r'|format\s+[a-zA-Z]:'
    r'|DROP\s+(TABLE|DATABASE)\b'
    r'|git\s+push\s+.*--force'
    r'|git\s+reset\s+--hard\b'
    r'|shutil\.rmtree\('
    r'|:\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:)',
    re.IGNORECASE,
)

_PERSISTENCE_RE = re.compile(
    r'(crontab\s+-[el]\b'
    r'|launchctl\s+(load|bootstrap)\b'
    r'|schtasks\s*/create\b'
    r'|reg(?:istry)?\s+add\s+.*\\Run\b'
    r'|systemctl\s+enable\b'
    r'|\.bashrc\b|\.bash_profile\b|\.zshrc\b|\.profile\b'
    r'|shell:startup'
    r'|Startup\\)',
    re.IGNORECASE,
)

# Language-level write APIs — the exact gap Hermes' own docstring admits their
# regex scanner misses. Paired against `_CONFIG_TARGET_RE` in a proximity
# window rather than matched alone, since `open(x, 'w')` on its own is
# completely ordinary and not a finding by itself.
_WRITE_CALL_RE = re.compile(
    r'(>>?\s*["\']?[\w./\\~$-]'                       # shell redirection
    r'|open\([^)]*[\'"]a?w[b]?[\'"]'                    # Python open(..., 'w'|'a'|'wb')
    r'|\.write_text\('                                  # pathlib.write_text
    r'|writeFileSync\('                                 # Node fs.writeFileSync
    r'|fs\.writeFile\('                                 # Node fs.writeFile
    r'|Set-Content\b|Add-Content\b|Out-File\b)',        # PowerShell
    re.IGNORECASE,
)

_CONFIG_TARGET_RE = re.compile(
    r'(CLAUDE\.md|AGENTS\.md|SKILL\.md'
    r'|settings\.json|\.mcp\.json|\bmcp\.json\b'
    r'|config\.ya?ml|\.env\b'
    r'|\.claude[/\\]|\.claude\.json)',
    re.IGNORECASE,
)

_NETWORK_CALL_RE = re.compile(
    r'(requests\.(post|put|get)\('
    r'|fetch\('
    r'|axios\.(post|get)\('
    r'|urllib\.request'
    r'|curl\s+[^\n]*(-d\s|--data|-F\s)'
    r'|wget\s+[^\n]*--post'
    r'|Invoke-WebRequest\b|Invoke-RestMethod\b)',
    re.IGNORECASE,
)

_SECRET_REF_RE = re.compile(
    r'(os\.environ|process\.env'
    r'|ANTHROPIC_API_KEY|OPENAI_API_KEY'
    r'|\.ssh[/\\]|\.aws[/\\]|id_rsa'
    r'|credentials\.json|secrets_store'
    r'|\bpassword\b|\btoken\b|\bcookie\b|\bAPI_KEY\b)',
    re.IGNORECASE,
)

# Files we don't attempt to text-scan at all (binary formats where regex is
# meaningless). Anything else is read with errors='ignore' and scanned as
# text — a mis-decoded binary just yields no matches, which is safe here
# because it can never suppress a real finding (only add noise).
_BINARY_EXTS = {
    '.png', '.jpg', '.jpeg', '.gif', '.ico', '.bmp', '.webp',
    '.so', '.dll', '.dylib', '.exe', '.bin',
    '.zip', '.tar', '.gz', '.7z', '.pdf', '.woff', '.woff2', '.ttf',
}

_PROXIMITY_WINDOW = 200  # chars either side of a write-call match


def _line_at(text: str, lines: list[str], pos: int) -> tuple[int, str]:
    line_no = text.count('\n', 0, pos) + 1
    line_text = lines[line_no - 1].strip() if 0 < line_no <= len(lines) else ''
    return line_no, line_text[:200]


def scan_text(filename: str, text: str) -> list[Finding]:
    """Scan one file's text content. Pure function — no filesystem access."""
    findings: list[Finding] = []
    lines = text.splitlines()

    for m in _INJECTION_RE.finditer(text):
        line_no, snippet = _line_at(text, lines, m.start())
        findings.append(Finding(
            'prompt_injection', 'critical', filename, line_no, snippet,
            'instruction aimed at overriding the agent\'s own rules',
        ))

    # Constitutional authority-expansion vocabulary, shared verbatim with the
    # Distiller's outbound guard (distiller._authority_violation) rather than
    # a second copy of the same phrase list.
    am = _distiller._AUTHORITY_RE.search(text)
    if am:
        line_no, snippet = _line_at(text, lines, am.start())
        findings.append(Finding(
            'prompt_injection', 'critical', filename, line_no, snippet,
            f'matches authority-expansion phrase: {am.group(0)!r}',
        ))

    for m in _DESTRUCTIVE_RE.finditer(text):
        line_no, snippet = _line_at(text, lines, m.start())
        findings.append(Finding(
            'destructive_command', 'critical', filename, line_no, snippet,
            'destructive/irreversible command',
        ))

    for m in _PERSISTENCE_RE.finditer(text):
        line_no, snippet = _line_at(text, lines, m.start())
        findings.append(Finding(
            'persistence', 'warning', filename, line_no, snippet,
            'installs a mechanism that survives the current session',
        ))

    for m in _WRITE_CALL_RE.finditer(text):
        window = text[max(0, m.start() - _PROXIMITY_WINDOW): m.end() + _PROXIMITY_WINDOW]
        if _CONFIG_TARGET_RE.search(window):
            line_no, snippet = _line_at(text, lines, m.start())
            findings.append(Finding(
                'agent_config_mod', 'critical', filename, line_no, snippet,
                'writes to an agent config/instruction file',
            ))

    net_matches = list(_NETWORK_CALL_RE.finditer(text))
    if net_matches:
        has_secret = bool(_SECRET_REF_RE.search(text))
        for m in net_matches:
            line_no, snippet = _line_at(text, lines, m.start())
            if has_secret:
                findings.append(Finding(
                    'exfiltration', 'critical', filename, line_no, snippet,
                    'outbound network call in a file that also references a credential/secret',
                ))
            else:
                findings.append(Finding(
                    'network_call', 'caution', filename, line_no, snippet,
                    'outbound network call (no local secret reference found in this file)',
                ))

    return findings


def scan_skill_dir(root: Path) -> list[Finding]:
    """Scan every text file under root (SKILL.md + any bundled scripts/assets).

    Does NOT catch exceptions — callers must go through `scan_and_gate` /
    `scan_and_gate_text`, which wrap this and fail closed on a crash.
    """
    root = Path(root)
    findings: list[Finding] = []
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        if path.suffix.lower() in _BINARY_EXTS:
            continue
        if path.stat().st_size > 2_000_000:
            continue  # not a plausible text file; skip rather than choke on it
        text = path.read_text(encoding='utf-8', errors='ignore')
        rel = str(path.relative_to(root)).replace(os.sep, '/')
        findings.extend(scan_text(rel, text))
    return findings


# ── Policy ───────────────────────────────────────────────────────────────────

def evaluate(findings: list[Finding], tier: str, force: bool = False) -> dict[str, Any]:
    """Apply trust-tier policy to a finding set. Does not touch the filesystem."""
    findings_out = [f.to_dict() for f in findings]
    if tier == 'builtin':
        return {'allow': True, 'tier': tier, 'findings': []}
    if not findings:
        return {'allow': True, 'tier': tier, 'findings': []}
    highest = max(SEVERITY_ORDER[f.severity] for f in findings)
    if tier == 'trusted' and highest <= SEVERITY_ORDER['caution']:
        return {'allow': True, 'tier': tier, 'findings': findings_out}
    if force:
        return {'allow': True, 'tier': tier, 'findings': findings_out, 'forced': True}
    return {'allow': False, 'tier': tier, 'findings': findings_out}


class SkillQuarantined(Exception):
    """Raised when an import is blocked by policy. Carries the full verdict."""

    def __init__(self, verdict: dict[str, Any]):
        self.verdict = verdict
        super().__init__(verdict.get('message', 'skill import blocked by security scan'))


def _quarantine_dir_copy(src_dir: Path, verdict: dict[str, Any], *, kind: str, source_label: str) -> str:
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    qid = uuid.uuid4().hex[:12]
    dest_root = quarantine_dir(qid)
    dest_root.mkdir(parents=True, exist_ok=False)
    shutil.copytree(src_dir, dest_root / 'skill')
    record = dict(verdict)
    record['kind'] = kind
    record['source'] = source_label
    record['quarantined_at'] = datetime.now(timezone.utc).isoformat()
    (dest_root / 'verdict.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    return qid


def _quarantine_text_copy(filename: str, content: str, verdict: dict[str, Any], *, kind: str, source_label: str) -> str:
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    qid = uuid.uuid4().hex[:12]
    dest_root = quarantine_dir(qid)
    skill_dir = dest_root / 'skill'
    skill_dir.mkdir(parents=True, exist_ok=False)
    (skill_dir / filename).write_text(content, encoding='utf-8')
    record = dict(verdict)
    record['kind'] = kind
    record['source'] = source_label
    record['quarantined_at'] = datetime.now(timezone.utc).isoformat()
    (dest_root / 'verdict.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    return qid


def scan_and_gate(src_dir: Path, *, tier: str, force: bool = False, source_label: str = '') -> dict[str, Any]:
    """Scan a skill folder on disk and enforce trust-tier policy.

    Raises SkillQuarantined (and writes a quarantine copy) if blocked.
    Returns the verdict dict if allowed. FAIL-CLOSED: a scan crash produces a
    synthetic critical finding and is never treated as force-able — `force`
    only overrides KNOWN findings, not an unknown scan failure.
    """
    if tier == 'builtin':
        return {'allow': True, 'tier': tier, 'findings': []}
    scan_crashed = False
    try:
        findings = scan_skill_dir(src_dir)
    except Exception as e:
        scan_crashed = True
        findings = [Finding('scanner_error', 'critical', '(scan)', 0, '', f'scanner crashed: {e}')]

    verdict = evaluate(findings, tier, force=(force and not scan_crashed))
    verdict['scan_crashed'] = scan_crashed
    if not verdict['allow']:
        qid = _quarantine_dir_copy(src_dir, verdict, kind='folder', source_label=source_label)
        verdict['quarantine_id'] = qid
        verdict['message'] = (
            f"blocked {len(findings)} finding(s) under trust tier {tier!r}; "
            f"quarantined at skills.quarantine/{qid} — re-submit with force=true to install anyway"
        )
        raise SkillQuarantined(verdict)
    return verdict


def scan_and_gate_text(filename: str, content: str, *, tier: str, force: bool = False,
                        source_label: str = '') -> dict[str, Any]:
    """Same as scan_and_gate, for pasted content that has no filesystem source yet."""
    if tier == 'builtin':
        return {'allow': True, 'tier': tier, 'findings': []}
    scan_crashed = False
    try:
        findings = scan_text(filename, content)
    except Exception as e:
        scan_crashed = True
        findings = [Finding('scanner_error', 'critical', '(scan)', 0, '', f'scanner crashed: {e}')]

    verdict = evaluate(findings, tier, force=(force and not scan_crashed))
    verdict['scan_crashed'] = scan_crashed
    if not verdict['allow']:
        qid = _quarantine_text_copy(filename, content, verdict, kind='paste', source_label=source_label)
        verdict['quarantine_id'] = qid
        verdict['message'] = (
            f"blocked {len(findings)} finding(s) under trust tier {tier!r}; "
            f"quarantined at skills.quarantine/{qid} — re-submit with force=true to install anyway"
        )
        raise SkillQuarantined(verdict)
    return verdict


# ── Quarantine inspection (list / read / discard) ───────────────────────────

def list_quarantine() -> list[dict[str, Any]]:
    if not QUARANTINE_DIR.exists():
        return []
    out = []
    for child in sorted(QUARANTINE_DIR.iterdir()):
        if not child.is_dir():
            continue
        verdict_path = child / 'verdict.json'
        if not verdict_path.exists():
            continue
        try:
            record = json.loads(verdict_path.read_text(encoding='utf-8'))
        except Exception:
            continue
        record['quarantine_id'] = child.name
        out.append(record)
    return out


def read_quarantine(quarantine_id: str) -> dict[str, Any] | None:
    root = quarantine_dir(quarantine_id)
    verdict_path = root / 'verdict.json'
    if not verdict_path.exists():
        return None
    try:
        record = json.loads(verdict_path.read_text(encoding='utf-8'))
    except Exception:
        return None
    record['quarantine_id'] = quarantine_id
    return record


def discard_quarantine(quarantine_id: str) -> bool:
    root = quarantine_dir(quarantine_id)
    if not root.exists():
        return False
    shutil.rmtree(root, ignore_errors=True)
    return True
