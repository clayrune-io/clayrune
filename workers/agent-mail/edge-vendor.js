// Hash manifest for the edge modules vendored byte-for-byte into the sibling edge Worker
// (MC-1166 7d-2c, decision D3). NOT itself vendored: it only describes and checks the closure of
// edge-policy.js. Hashing is over LF-normalised bytes so a CRLF checkout (core.autocrlf) of the same
// commit yields the same digest as the repo blob.
//   node edge-vendor.js --write            regenerate edge-vendor.manifest.json from this directory
//   node edge-vendor.js --check [dir]      verify dir (default: this directory) against the manifest
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const ENTRY = 'edge-policy.js';
export const MANIFEST_FILE = 'edge-vendor.manifest.json';
const HERE = dirname(fileURLToPath(import.meta.url));
const STATIC = /(?:^|\n)\s*(?:import|export)\b[^'"`;]*?\bfrom\s*(['"])([^'"]+)\1|(?:^|\n)\s*import\s*(['"])([^'"]+)\3/g;
const SAFE_NAME = /^[a-z0-9-]+\.js$/;

export function hashBytes(raw) {
  const lf = Buffer.from(raw.toString('latin1').replace(/\r\n/g, '\n'), 'latin1');
  return createHash('sha256').update(lf).digest('hex');
}
export const hashFile = (path) => hashBytes(readFileSync(path));

// Static relative imports only: an edge module may not reach a package, a node: builtin, a dynamic
// import or a path outside its own directory. Returns { files, problems }.
export function closure(dir, entry = ENTRY) {
  const files = new Set(); const problems = [];
  const visit = (name) => {
    if (files.has(name)) return;
    files.add(name);
    let source;
    try { source = readFileSync(join(dir, name), 'utf8'); } catch { problems.push(`missing:${name}`); return; }
    const body = source.replace(/\/\/[^\n]*/g, '');
    if (/\bimport\s*\(/.test(body) || /\brequire\s*\(/.test(body)) problems.push(`dynamic_import:${name}`);
    for (const m of body.matchAll(STATIC)) {
      const spec = m[2] ?? m[4];
      const next = spec.startsWith('./') ? spec.slice(2) : null;
      if (next === null || !SAFE_NAME.test(next)) { problems.push(`forbidden_import:${name}:${spec}`); continue; }
      visit(next);
    }
  };
  visit(entry);
  return { files: [...files].sort(), problems };
}

export function buildManifest(dir = HERE) {
  const { files, problems } = closure(dir);
  if (problems.length) throw new Error(`closure_invalid:${problems.join(',')}`);
  return { version: 1, entry: ENTRY, hashing: 'sha256 over LF-normalised bytes',
    files: Object.fromEntries(files.map((f) => [f, hashFile(join(dir, f))])) };
}

export function checkManifest(dir, manifest) {
  const problems = [];
  if (!manifest || manifest.version !== 1 || manifest.entry !== ENTRY || typeof manifest.files !== 'object'
      || manifest.files === null) return { ok: false, problems: ['manifest_invalid'] };
  const found = closure(dir);
  problems.push(...found.problems);
  for (const f of found.files) if (!(f in manifest.files)) problems.push(`not_in_manifest:${f}`);
  for (const [f, digest] of Object.entries(manifest.files)) {
    if (!found.files.includes(f)) { problems.push(`not_in_closure:${f}`); continue; }
    let actual;
    try { actual = hashFile(join(dir, f)); } catch { problems.push(`missing:${f}`); continue; }
    if (actual !== digest) problems.push(`hash_mismatch:${f}`);
  }
  return { ok: problems.length === 0, problems };
}

export const loadManifest = (dir = HERE) => JSON.parse(readFileSync(join(dir, MANIFEST_FILE), 'utf8'));

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const [flag, dir = HERE] = process.argv.slice(2);
  if (flag === '--write') {
    writeFileSync(join(HERE, MANIFEST_FILE), `${JSON.stringify(buildManifest(HERE), null, 2)}\n`);
  } else if (flag === '--check') {
    const out = checkManifest(dir, loadManifest(HERE));
    console.log(out.ok ? 'ok' : out.problems.join('\n'));
    process.exitCode = out.ok ? 0 : 1;
  } else { console.error('usage: node edge-vendor.js --write | --check [dir]'); process.exitCode = 2; }
}
