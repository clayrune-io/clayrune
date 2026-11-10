import test from 'node:test';
import assert from 'node:assert/strict';
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { buildManifest, checkManifest, closure, hashBytes, loadManifest, ENTRY, MANIFEST_FILE } from '../edge-vendor.js';

const SRC = fileURLToPath(new URL('..', import.meta.url));
const manifest = loadManifest(SRC);
const CLOSURE = ['delivery-capability.js', 'edge-policy.js', 'enrollment-transcript.js', 'signing.js'];

function vendored(mutate = () => {}) {
  const dir = mkdtempSync(join(tmpdir(), 'edge-vendor-'));
  for (const f of CLOSURE) cpSync(join(SRC, f), join(dir, f));
  mutate(dir);
  return dir;
}
const check = (dir) => checkManifest(dir, manifest);

test('the checked-in manifest matches the working tree (drift gate: rerun `node edge-vendor.js --write`)', () => {
  assert.deepEqual(check(SRC), { ok: true, problems: [] });
  assert.deepEqual(buildManifest(SRC), manifest);
});

test('the closure is exactly edge-policy.js and what it imports, nothing else', () => {
  assert.equal(manifest.entry, ENTRY);
  assert.deepEqual(closure(SRC), { files: CLOSURE, problems: [] });
  assert.deepEqual(Object.keys(manifest.files), CLOSURE);
});

test('Worker-only modules are not in the vendored closure', () => {
  for (const f of ['delivery-proof.js', 'cp-client.js', 'replay.js', 'ledger.js', 'ingress.js', 'worker.js']) {
    assert.equal(f in manifest.files, false, f);
  }
});

test('a byte-identical copy verifies', () => {
  const dir = vendored();
  try { assert.deepEqual(check(dir), { ok: true, problems: [] }); } finally { rmSync(dir, { recursive: true }); }
});

test('a CRLF checkout of the same content verifies', () => {
  const dir = vendored((d) => {
    for (const f of CLOSURE) writeFileSync(join(d, f), readFileSync(join(d, f), 'utf8').replace(/\r?\n/g, '\r\n'));
  });
  try { assert.deepEqual(check(dir), { ok: true, problems: [] }); } finally { rmSync(dir, { recursive: true }); }
  assert.equal(hashBytes(Buffer.from('a\r\nb')), hashBytes(Buffer.from('a\nb')));
});

for (const f of CLOSURE) {
  test(`one changed byte in ${f} is reported`, () => {
    const dir = vendored((d) => writeFileSync(join(d, f), `${readFileSync(join(d, f), 'utf8')} `));
    try { assert.deepEqual(check(dir), { ok: false, problems: [`hash_mismatch:${f}`] }); } finally { rmSync(dir, { recursive: true }); }
  });
  test(`a missing ${f} is reported`, () => {
    const dir = vendored((d) => rmSync(join(d, f)));
    try {
      const out = check(dir);
      assert.equal(out.ok, false); assert.ok(out.problems.some((p) => p.endsWith(`:${f}`)), out.problems.join());
    } finally { rmSync(dir, { recursive: true }); }
  });
}

test('a new import that is not in the manifest is reported, never silently vendored', () => {
  const dir = vendored((d) => {
    writeFileSync(join(d, 'extra.js'), 'export const x = 1;\n');
    writeFileSync(join(d, 'edge-policy.js'), `import './extra.js';\n${readFileSync(join(d, 'edge-policy.js'), 'utf8')}`);
  });
  try {
    const out = check(dir);
    assert.equal(out.ok, false);
    assert.deepEqual(out.problems.sort(), ['hash_mismatch:edge-policy.js', 'not_in_manifest:extra.js']);
  } finally { rmSync(dir, { recursive: true }); }
});

for (const [name, line] of [['a package', "import x from 'postal-mime';"], ['a node builtin', "import fs from 'node:fs';"],
  ['a parent path', "import x from '../ledger.js';"], ['a dynamic import', "const m = await import('./signing.js');"],
  ['a bare side-effect import', "import 'node:crypto';"], ['require', "const f = require('fs');"],
  ['a re-export of a package', "export { y } from 'left-pad';"]]) {
  test(`${name} in an edge module is refused`, () => {
    const dir = vendored((d) => writeFileSync(join(d, 'edge-policy.js'), `${line}\n${readFileSync(join(d, 'edge-policy.js'), 'utf8')}`));
    try {
      const out = check(dir);
      assert.equal(out.ok, false); assert.ok(out.problems.some((p) => /^(forbidden_import|dynamic_import)/.test(p)), out.problems.join());
    } finally { rmSync(dir, { recursive: true }); }
  });
}

test('an invalid manifest is refused', () => {
  for (const bad of [null, {}, { version: 2, entry: ENTRY, files: {} }, { version: 1, entry: 'x.js', files: {} }, { version: 1, entry: ENTRY }]) {
    assert.deepEqual(checkManifest(SRC, bad), { ok: false, problems: ['manifest_invalid'] });
  }
});

test('the CLI check passes here and fails on a modified copy', () => {
  const script = join(SRC, 'edge-vendor.js');
  const ok = spawnSync(process.execPath, [script, '--check'], { encoding: 'utf8' });
  assert.equal(ok.status, 0); assert.equal(ok.stdout.trim(), 'ok');
  const dir = vendored((d) => writeFileSync(join(d, 'signing.js'), `${readFileSync(join(d, 'signing.js'), 'utf8')}//`));
  try {
    const bad = spawnSync(process.execPath, [script, '--check', dir], { encoding: 'utf8' });
    assert.equal(bad.status, 1); assert.equal(bad.stdout.trim(), 'hash_mismatch:signing.js');
  } finally { rmSync(dir, { recursive: true }); }
  assert.equal(spawnSync(process.execPath, [script], { encoding: 'utf8' }).status, 2);
});

test('the manifest file carries no operator-specific path', () => {
  assert.equal(/[A-Za-z]:\|\/Users\/|\/home\//.test(readFileSync(join(SRC, MANIFEST_FILE), 'utf8')), false);
});
