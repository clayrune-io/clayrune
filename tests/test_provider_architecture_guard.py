"""Static guard for provider coupling in Clayrune feature modules.

This is intentionally a small AST check rather than a grep.  It catches the
two regressions that matter at the feature boundary:

* a feature starts constructing an inference subprocess with a vendor binary;
* a feature imports a concrete runtime/parser and starts decoding its wire
  format itself.

The runtime and the installer are allowed to know vendors.  The feature layer
is not.  Existing exceptions are exact source locations below, with an owner
and a removal gate.  Adding an exception is therefore a visible test change,
not a silent expansion of a broad file allowlist.
"""

from __future__ import annotations

import ast
import pytest
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]

# These are operations, not feature inference.  They remain outside this
# guard's scope so an unrelated git/browser/terminal/installer subprocess
# cannot make the provider-neutral test noisy.
_NON_INFERENCE_BLUEPRINTS = frozenset({
    "backup_routes.py",
    "browser_routes.py",
    "coordination_routes.py",
    "floor_routes.py",
    "local_auth.py",
    "mcp_routes.py",
    "media_routes.py",
    "push_mobile.py",
    "remote_routes.py",
    "scheduler_routes.py",
    "skills_routes.py",
    "system_routes.py",
    "terminal_routes.py",
})
_NON_INFERENCE_MODULES = frozenset({
    "agent_worktree.py",
    "backup.py",
    "core.py",
    "desk_harvest.py",
    "github_sync.py",
    "mcp_installer.py",
    "project_sync.py",
    "pty_backend.py",
    "question_channel.py",
    "skills.py",
    "workflows.py",
})
_CONCRETE_RUNTIME_NAMES = frozenset({
    "AgentRuntime", "ClaudeRuntime", "CodexRuntime", "GeminiRuntime",
    "QwenRuntime",
})
_RAW_VENDOR_MODULES = frozenset({
    "mc.claude_qwen_capture",
    "mc.codex_capture",
    "mc.codex_rollout_capture",
    "mc.gemini_capture",
})
_PROVIDER_WORDS = frozenset({"claude", "codex", "gemini", "qwen"})


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    function: str
    kind: str
    detail: str

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.path, self.line, self.kind)


@dataclass(frozen=True)
class AllowlistedException:
    path: str
    line: int
    kind: str
    owner: str
    removal_gate: str

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.path, self.line, self.kind)


# Existing legacy paths only.  Keep this list precise: a new call in one of
# these functions gets a new line number and fails until somebody documents
# why it must remain and when it will be removed.
ALLOWLIST: tuple[AllowlistedException, ...] = (
    AllowlistedException("mc/blueprints/agent_routes.py", 1853,
                         "inference-subprocess", "agent-runtime owner",
                         "move Claude auth probing behind Runtime.auth_status"),
    AllowlistedException("mc/blueprints/agent_routes.py", 4362,
                         "inference-subprocess", "agent-runtime owner",
                         "remove legacy Claude auto-recovery after generic resume is complete"),
    AllowlistedException("mc/blueprints/agent_routes.py", 4402,
                         "inference-subprocess", "agent-runtime owner",
                         "remove legacy Claude auto-recovery after generic resume is complete"),
    AllowlistedException("mc/blueprints/agent_routes.py", 4882,
                         "inference-subprocess", "agent-runtime owner",
                         "route cold revival through the durable provider-neutral lifecycle"),
    AllowlistedException("mc/blueprints/agent_routes.py", 4984,
                         "inference-subprocess", "agent-runtime owner",
                         "route cold revival through the durable provider-neutral lifecycle"),
    AllowlistedException("mc/blueprints/agent_routes.py", 6168,
                         "inference-subprocess", "agent-runtime owner",
                         "route follow-up turns through the provider runtime"),
    AllowlistedException("mc/blueprints/agent_routes.py", 7531,
                         "inference-subprocess", "agent-runtime owner",
                         "delete the legacy Claude dispatch path after runtime parity"),
    AllowlistedException("mc/blueprints/agent_routes.py", 7677,
                         "inference-subprocess", "agent-runtime owner",
                         "delete the legacy Claude dispatch path after runtime parity"),
    AllowlistedException("mc/blueprints/agent_routes.py", 9028,
                         "inference-subprocess", "agent-runtime owner",
                         "delete the legacy Claude follow-up path after runtime parity"),
    AllowlistedException("mc/blueprints/agent_routes.py", 9290,
                         "inference-subprocess", "agent-runtime owner",
                         "delete the legacy Claude interrupt respawn after runtime parity"),
    AllowlistedException("mc/blueprints/agent_routes.py", 9341,
                         "inference-subprocess", "agent-runtime owner",
                         "delete the legacy Claude interrupt respawn after runtime parity"),
    AllowlistedException("mc/memory.py", 4804,
                         "inference-subprocess", "memory owner",
                         "replace legacy agent-condense with the provider-neutral publication service"),
)


def _is_feature_module(path: Path, root: Path) -> bool:
    """Return whether *path* is in the guarded feature boundary."""
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return False
    parts = rel.split("/")
    if parts[:2] == ["mc", "blueprints"]:
        return parts[-1] not in _NON_INFERENCE_BLUEPRINTS
    return len(parts) == 2 and parts[0] == "mc" and parts[1] not in _NON_INFERENCE_MODULES


def _text_has_provider(value: ast.AST) -> bool:
    """Whether an expression contains a concrete provider executable/name."""
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value.strip().lower() in _PROVIDER_WORDS
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        return value.func.id in {"_resolve_claude", "_resolve_provider_binary"}
    return any(_text_has_provider(child) for child in ast.iter_child_nodes(value))


class _Scanner(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.findings: list[Finding] = []
        self.functions: list[str] = []
        self.tainted_names: set[str] = set()
        self.subprocess_modules = {'subprocess'}
        self.subprocess_functions: set[str] = set()
        self.runtime_factories = {'get_runtime'}

    @property
    def function(self) -> str:
        return ".".join(self.functions) or "<module>"

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if module == 'subprocess':
            self.subprocess_functions.update(alias.asname or alias.name for alias in node.names
                if alias.name in {'Popen', 'run', 'call', 'check_output', 'check_call'})
        if module in {"mc.agent_runtime", "agent_runtime"}:
            for alias in node.names:
                if alias.name == 'get_runtime':
                    self.runtime_factories.add(alias.asname or alias.name)
                if alias.name in _CONCRETE_RUNTIME_NAMES:
                    self.findings.append(Finding(
                        self.path, node.lineno, self.function,
                        "concrete-adapter-import", alias.name))
        elif module in _RAW_VENDOR_MODULES:
            self.findings.append(Finding(
                self.path, node.lineno, self.function,
                "raw-vendor-parser-import", module))
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == 'subprocess':
                self.subprocess_modules.add(alias.asname or alias.name)
            if alias.name in _RAW_VENDOR_MODULES:
                self.findings.append(Finding(
                    self.path, node.lineno, self.function,
                    "raw-vendor-parser-import", alias.name))
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if _text_has_provider(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.tainted_names.add(target.id)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None and _text_has_provider(node.value) and isinstance(node.target, ast.Name):
            self.tainted_names.add(node.target.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        is_subprocess = (isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in self.subprocess_modules
                and node.func.attr in {"Popen", "run", "call", "check_output", "check_call"})
        is_subprocess = is_subprocess or (isinstance(node.func, ast.Name)
                and node.func.id in self.subprocess_functions)
        if is_subprocess:
            provider_args = any(
                (isinstance(arg, ast.Name) and arg.id in self.tainted_names)
                or _text_has_provider(arg)
                for arg in list(node.args) + [kw.value for kw in node.keywords if kw.arg == 'args']
            )
            if provider_args:
                self.findings.append(Finding(
                    self.path, node.lineno, self.function,
                    "inference-subprocess", ast.unparse(node.func)))
        if (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call)
                and node.func.attr in {'oneshot', 'oneshot_via_hook', 'dispatch',
                                       'write_followup', 'stream_text_transform'}):
            factory = node.func.value
            factory_name = (factory.func.id if isinstance(factory.func, ast.Name)
                            else factory.func.attr if isinstance(factory.func, ast.Attribute) else '')
            if factory_name in self.runtime_factories and any(
                    _text_has_provider(arg) for arg in list(factory.args) + [kw.value for kw in factory.keywords]):
                self.findings.append(Finding(self.path, node.lineno, self.function,
                                             'concrete-runtime-call', ast.unparse(node.func)))
        self.generic_visit(node)


def scan_modules(paths: Iterable[Path], *, root: Path = ROOT) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for path in paths:
        if path.suffix != ".py" or not _is_feature_module(path, root):
            continue
        rel = path.resolve().relative_to(root.resolve()).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        scanner = _Scanner(rel)
        scanner.visit(tree)
        findings.extend(scanner.findings)
    return tuple(findings)


def feature_modules(root: Path = ROOT) -> tuple[Path, ...]:
    paths = list((root / "mc" / "blueprints").glob("*.py"))
    paths.extend(root / "mc" / name for name in ("memory.py", "distiller.py"))
    return tuple(path for path in paths if _is_feature_module(path, root))


def test_feature_boundary_has_no_undocumented_provider_coupling() -> None:
    findings = scan_modules(feature_modules())
    allowed = {entry.key: entry for entry in ALLOWLIST}
    unexpected = [finding for finding in findings if finding.key not in allowed]
    assert not unexpected, "undocumented provider coupling: " + repr(unexpected)

    current_keys = {finding.key for finding in findings}
    stale = [entry for entry in ALLOWLIST if entry.key not in current_keys]
    assert not stale, "stale allowlist entry; remove it: " + repr(stale)
    assert all(entry.owner and entry.removal_gate for entry in ALLOWLIST)


def test_new_provider_subprocess_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "mc" / "blueprints" / "new_feature.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "import subprocess\n"
        "def answer(prompt):\n"
        "    cmd = ['claude', '-p', prompt]\n"
        "    return subprocess.Popen(cmd)\n",
        encoding="utf-8",
    )
    finding = scan_modules((path,), root=tmp_path)
    assert [(item.kind, item.line) for item in finding] == [("inference-subprocess", 4)]


@pytest.mark.parametrize('source,kind', [
    ("import subprocess as sp\nsp.run(['codex', 'exec'])\n", 'inference-subprocess'),
    ("from subprocess import Popen as spawn\nspawn(['qwen'])\n", 'inference-subprocess'),
    ("import subprocess\nsubprocess.run(args=['claude', '-p', 'x'])\n", 'inference-subprocess'),
    ("from mc.agent_runtime import get_runtime\nget_runtime('claude').oneshot(prompt='x')\n", 'concrete-runtime-call'),
])
def test_fenn_escape_shapes_are_detected(tmp_path: Path, source: str, kind: str) -> None:
    path = tmp_path / 'mc' / 'blueprints' / 'feature.py'
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding='utf-8')
    findings = scan_modules((path,), root=tmp_path)
    assert any(f.kind == kind for f in findings)


def test_concrete_runtime_and_raw_parser_imports_are_detected(tmp_path: Path) -> None:
    path = tmp_path / "mc" / "blueprints" / "new_feature.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "from mc.agent_runtime import ClaudeRuntime\n"
        "from mc.codex_capture import CodexCapture\n",
        encoding="utf-8",
    )
    finding = scan_modules((path,), root=tmp_path)
    assert {(item.kind, item.line) for item in finding} == {
        ("concrete-adapter-import", 1),
        ("raw-vendor-parser-import", 2),
    }


def test_non_inference_subprocesses_are_outside_the_guard(tmp_path: Path) -> None:
    for name, command in (
        ("mcp_installer.py", "['git', 'status']"),
        ("github_sync.py", "['git', 'fetch']"),
        ("media_routes.py", "['ffmpeg', '-version']"),
    ):
        path = tmp_path / ("mc" if name.endswith(".py") else "mc") / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"import subprocess\nsubprocess.run({command})\n", encoding="utf-8")
    assert scan_modules(tuple(tmp_path.rglob("*.py")), root=tmp_path) == ()
