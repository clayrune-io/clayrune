"""Locate the launched CLI's own hook implementation for offline preflight.

No model/auth call: Node loads settings, registers the injected entry, then
runs its real adapter through the native hook runner and denial aggregator.
Unknown packaging or APIs fail closed rather than guessing compatibility.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import List, Tuple

NODE_PROBE = r'''
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
// Native imports may log unrelated user configuration or diagnostic stacks.
// Return a bounded verdict instead; failures always have an explicit reason.
const report = process.stderr.write.bind(process.stderr);
process.stderr.write = () => true;
async function verify() {
const [vendor, coreUrl, loaderUrl, cwd, settingsPath, testCommand, selectorPath] = process.argv.slice(2);
process.env[vendor === 'gemini' ? 'GEMINI_CLI_SYSTEM_SETTINGS_PATH' : 'QWEN_CODE_SYSTEM_SETTINGS_PATH'] = settingsPath;
if (selectorPath) {
  // Run only the installed standalone launcher's entry-selection prefix.
  // It may select a managed npm update instead of its bundled cli.js. Never
  // probe a bundled older copy while launching that different active build.
  const source = fs.readFileSync(selectorPath, 'utf8');
  const end = source.indexOf('\nconst cliPath =');
  if (end < 0) throw new Error('unsupported standalone CLI entry selector');
  const prefix = source.slice(0, end).replace(/^#![^\n]*\n/, '')
    .replaceAll('import.meta.url', JSON.stringify(pathToFileURL(selectorPath).href));
  const selector = prefix + '\nexport default cliPathCandidates.find(x => existsSync(x));';
  const selected = (await import('data:text/javascript;base64,' + Buffer.from(selector).toString('base64'))).default;
  if (path.resolve(selected) !== path.join(path.dirname(selectorPath), 'cli.js'))
    throw new Error('standalone CLI selected an unverified managed npm update');
}
const core = await import(coreUrl);
const loader = await import(loaderUrl);
if (vendor === 'qwen' && (typeof core.isSafeModeEnv !== 'function' || core.isSafeModeEnv()))
  throw new Error('Qwen safe-mode hook suppression cannot be ruled out');
const loaded = await loader.loadSettings(cwd);
const expected = JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
const event = vendor === 'gemini' ? 'BeforeTool' : 'PreToolUse';
const command = expected.hooks[event].at(-1).hooks[0].command;
const config = {
  getHooks: () => loaded.merged.hooks,
  getSystemHooks: () => loaded.getSystemHooks(),
  getUserHooks: () => undefined, getProjectHooks: () => undefined,
  getExtensions: () => [], getDisabledHooks: () => loaded.merged.hooksConfig?.disabled || [],
  isTrustedFolder: () => true, getTargetDir: () => cwd,
  sanitizationConfig: {}, storage: {getPlansDir: () => cwd},
};
const registry = new core.HookRegistry(config);
await registry.initialize();
const entry = registry.getHooksForEvent(event).find(x =>
  x.config.name === 'clayrune-steward-fence' && x.config.command === command);
if (!entry) {
  // Never dump settings, credentials, or unrelated user hooks into logs.
  const rejected = loaded.errors?.some(x => String(x.message).includes('Skipping system settings'));
  throw new Error(rejected ? 'native CLI rejected per-launch system settings (ownership/permissions)' :
    'injected steward fence did not load in the native registry');
}
if (vendor === 'gemini' && loaded.merged.hooksConfig?.enabled !== true)
  throw new Error('native CLI hooks are disabled');
if (vendor === 'qwen' && loaded.merged.disableAllHooks)
  throw new Error('native CLI hooks are disabled');
const runner = vendor === 'gemini' ? new core.HookRunner(config) : new core.HookRunner();
const input = {session_id: 'clayrune-offline-fence-probe', cwd,
  hook_event_name: event, tool_name: 'run_shell_command', tool_input: {command: 'echo okay'}};
const denied = result => {
  const final = new core.HookAggregator().aggregateResults([result], event).finalOutput;
  return final?.decision === 'deny' || final?.decision === 'block' ||
    final?.hookSpecificOutput?.permissionDecision === 'deny' || final?.isBlockingDecision?.();
};
const test = await runner.executeHook({...entry.config, command: testCommand}, event, input);
if (test.exitCode !== 2 || !test.stderr?.includes('CLAYRUNE-FENCE-SELF-TEST-OK') || !denied(test))
  throw new Error('native hook runner did not preserve fence self-test denial');
const allow = await runner.executeHook(entry.config, event, input);
if (allow.exitCode !== 0 || denied(allow)) throw new Error('native fence allow did not survive');
const block = await runner.executeHook(entry.config, event,
  {...input, tool_input: {command: 'git push'}});
if (block.exitCode !== 2 || !denied(block)) throw new Error('native fence refusal did not survive');
// The shared Python runner requires the real fence token plus exit 2. Print
// the actual self-test stderr only after all native-load/allow/deny checks pass.
report(test.stderr);
process.exitCode = 2;
}
try { await verify(); }
catch (error) {
  report(`CLAYRUNE native fence preflight failed: ${String(error.message).slice(0, 240)}\n`);
  process.exitCode = 1;
}
'''


def _package(vendor: str, binary: Path) -> Path:
    candidates = [binary.resolve()]
    # Standalone installers use a tiny shim forwarding to another shim.
    if binary.is_file() and binary.suffix.lower() in ('.cmd', '.bat'):
        text = binary.read_text(encoding='utf-8', errors='replace')
        candidates.extend(Path(p) for p in re.findall(r'"([A-Za-z]:[^"\r\n]+\.(?:cmd|js))"', text))
    name = '@google/gemini-cli' if vendor == 'gemini' else '@qwen-code/qwen-code'
    for candidate in candidates:
        for parent in list(candidate.parents)[:4]:
            for root in (parent, parent / 'node_modules' / name):
                manifest = root / 'package.json'
                if manifest.is_file() and json.loads(manifest.read_text(encoding='utf-8')).get('name') == name:
                    return manifest
    raise RuntimeError(f'{vendor} installed CLI package could not be verified')


def _modules(manifest_path: str, vendor: str) -> Tuple[str, str]:
    manifest = Path(manifest_path)
    metadata = json.loads(manifest.read_text(encoding='utf-8'))
    entry = metadata['bin']
    if isinstance(entry, dict):
        entry = entry[vendor]
    entry_path = manifest.parent / entry
    if not entry_path.is_file() and vendor == 'qwen':
        # Qwen's standalone package retains development bin metadata; its
        # shipped launcher explicitly names lib/cli-entry.js instead.
        shim = manifest.parent / 'bin' / 'qwen.cmd'
        if shim.is_file() and r'\lib\cli-entry.js' in shim.read_text(encoding='utf-8'):
            entry_path = manifest.parent / 'lib' / 'cli.js'
    pending = [entry_path]
    seen = set()
    core = loader = ''
    # Follow only the active entry's import graph; stale bundled chunks and
    # backup installs must never stand in for the CLI we are about to launch.
    while pending and len(seen) < 2000:
        path = pending.pop(0).resolve()
        if path in seen:
            continue
        seen.add(path)
        source = path.read_text(encoding='utf-8')
        exports = ' '.join(re.findall(r'export\s*\{([^}]+)\}', source))
        if 'HookRunner' in exports and 'HookRegistry' in exports:
            core = path.as_uri()
        if 'loadSettings' in exports and 'function loadSettings(' in source:
            loader = path.as_uri()
        if core and loader:
            return core, loader
        imports = re.findall(r'\b(?:from\s*|import\s*(?:\(\s*)?)["\'](\./[^"\']+\.js)["\']', source)
        pending.extend(path.parent / p for p in imports if (path.parent / p).is_file())
    raise RuntimeError(f'{vendor} native hook/loader exports could not be verified')


def self_test_argv(vendor: str, binary: Path, cwd: str, settings: Path,
                   test_command: str) -> List[str]:
    manifest = _package(vendor, binary)
    core, loader = _modules(str(manifest), vendor)
    root = manifest.parent
    node = next((str(p) for p in (root / 'node' / 'node.exe', root / 'node.exe')
                 if p.is_file()), None) or shutil.which('node')
    if not node:
        raise RuntimeError(f'{vendor} native hook preflight needs its Node runtime')
    probe = settings.parent / 'vendor-fence-probe.mjs'
    # Content is identical for every launch; writing unique temporary files
    # would leave one behind per respawn. Atomic writes are owned by caller.
    if not probe.is_file() or probe.read_text(encoding='utf-8') != NODE_PROBE:
        import os
        import threading
        tmp = probe.with_name(f'{probe.name}.{os.getpid()}.{threading.get_ident()}.tmp')
        tmp.write_text(NODE_PROBE, encoding='utf-8')
        tmp.replace(probe)
    selector = root / 'lib' / 'cli-entry.js'
    return [node, str(probe), vendor, core, loader, cwd, str(settings), test_command,
            str(selector) if vendor == 'qwen' and selector.is_file() else '']
