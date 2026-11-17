#!/usr/bin/env node
/**
 * One runner for every smoke in tools/smoke/ (MC-1179 pass 08, T8-2).
 *
 * WHY. package.json's `npm test` chains 12 smokes; 300+ others gated nothing.
 * This runs every tools/smoke/*.mjs except the ones named in ci-skip.json, one
 * child process per smoke, each with a timeout, and exits 1 if any is red.
 *
 *   node run-all.mjs                       run everything not skipped
 *   node run-all.mjs --shard 2/4           the 2nd of 4 slices (sorted names, index % N)
 *   node run-all.mjs --timeout 300         per-smoke limit in seconds (default 120)
 *   node run-all.mjs --list                print the selected names, run nothing
 *   node run-all.mjs --dir D --skip-file F  point at another smoke dir / skip map (tests)
 *
 * ci-skip.json is a map of file name -> one-line reason. It holds ONLY smokes
 * that cannot run on a clean runner (live Clayrune port, process registration,
 * a signed-in profile, a provider CLI) and aliases. A smoke that is merely red
 * is NOT put there.
 *
 * Timeouts end the smoke's WHOLE process tree (a smoke spawns Python servers
 * and browsers), by the PID of the child this runner started: `taskkill /T` on
 * Windows, the child's own process group elsewhere. Never by image name.
 *
 * Exit: 0 all green, 1 any red (fail or timeout), 2 bad usage / bad skip file.
 */
import { spawn, spawnSync } from 'node:child_process';
import { readdirSync, readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { basename, delimiter, dirname, join, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SELF = basename(fileURLToPath(import.meta.url));
const TAIL_LINES = 15;
const KEEP_BYTES = 64 * 1024; // per-smoke output kept for the red-list tail

function usage(msg) {
  console.error(`run-all: ${msg}`);
  console.error('usage: node run-all.mjs [--shard I/N] [--timeout SECONDS] [--list] [--dir D] [--skip-file F]');
  process.exit(2);
}

export function parseArgs(argv) {
  const o = { shard: null, timeout: 120, list: false, dir: HERE, skipFile: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const val = () => (i + 1 < argv.length ? argv[++i] : usage(`${a} needs a value`));
    if (a === '--shard') {
      const m = /^(\d+)\/(\d+)$/.exec(val());
      if (!m || +m[1] < 1 || +m[2] < 1 || +m[1] > +m[2]) usage('--shard wants I/N with 1 <= I <= N');
      o.shard = { index: +m[1], count: +m[2] };
    } else if (a === '--timeout') {
      o.timeout = Number(val());
      if (!(o.timeout > 0)) usage('--timeout wants a positive number of seconds');
    } else if (a === '--list') o.list = true;
    else if (a === '--dir') o.dir = resolve(val());
    else if (a === '--skip-file') o.skipFile = resolve(val());
    else usage(`unknown argument ${a}`);
  }
  if (!o.skipFile) o.skipFile = join(o.dir, 'ci-skip.json');
  return o;
}

/** ci-skip.json -> Map(file -> reason). Throws on a malformed file. */
export function loadSkips(path) {
  if (!existsSync(path)) return new Map();
  const raw = JSON.parse(readFileSync(path, 'utf8'));
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('must be an object of file -> reason');
  for (const [k, v] of Object.entries(raw)) {
    if (typeof v !== 'string' || !v.trim()) throw new Error(`"${k}" has no reason; every skip needs a one-line reason`);
  }
  return new Map(Object.entries(raw));
}

/** Sorted *.mjs names in dir minus this runner and the skip map; then the shard slice. */
export function selectSmokes(dir, skips, shard) {
  const all = readdirSync(dir).filter((n) => n.endsWith('.mjs') && n !== SELF).sort();
  const kept = all.filter((n) => !skips.has(n));
  const mine = shard ? kept.filter((_, i) => i % shard.count === shard.index - 1) : kept;
  return { all, kept, mine };
}

function killTree(child) {
  if (!child.pid) return;
  try {
    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      process.kill(-child.pid, 'SIGKILL'); // the child leads its own group (detached)
    }
  } catch { /* already gone */ }
}

let current = null; // the child being run, so a signal to this runner cleans up
for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, () => { if (current) killTree(current); process.exit(130); });
}

function runOne(dir, name, timeoutSec) {
  return new Promise((done) => {
    const t0 = Date.now();
    // Smokes build fixtures with `python -c 'from mc... import ...'` from this cwd, so the
    // repo root (two up from tools/smoke) must be importable; a developer's own PYTHONPATH stays.
    const root = resolve(dir, '..', '..');
    const env = { ...process.env, PYTHONPATH: [root, process.env.PYTHONPATH].filter(Boolean).join(delimiter) };
    const child = spawn(process.execPath, [join(dir, name)], {
      cwd: dir, env, stdio: ['ignore', 'pipe', 'pipe'], detached: process.platform !== 'win32', windowsHide: true,
    });
    current = child;
    let out = '';
    const keep = (d) => { out += d; if (out.length > KEEP_BYTES) out = out.slice(-KEEP_BYTES); };
    child.stdout.on('data', keep);
    child.stderr.on('data', keep);
    let settled = false;
    const finish = (status, code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      current = null;
      done({ name, status, code, secs: (Date.now() - t0) / 1000, out });
    };
    const timer = setTimeout(() => {
      killTree(child);
      // Grandchildren that survived could keep the pipes open; do not wait on them.
      child.stdout.destroy(); child.stderr.destroy();
      finish('timeout', null);
    }, timeoutSec * 1000);
    child.on('error', (e) => { out += `spawn error: ${e.message}\n`; finish('fail', null); });
    child.on('exit', (code, signal) => {
      // Let buffered output drain, but never wait on a pipe a grandchild holds open.
      setTimeout(() => finish(code === 0 ? 'pass' : 'fail', code ?? signal), 200);
    });
  });
}

async function main() {
  const o = parseArgs(process.argv.slice(2));
  let skips;
  try { skips = loadSkips(o.skipFile); } catch (e) { usage(`${o.skipFile}: ${e.message}`); }
  const { all, mine } = selectSmokes(o.dir, skips, o.shard);
  for (const k of skips.keys()) if (!all.includes(k)) console.error(`warning: skip list names ${k}, which does not exist`);

  if (o.list) { for (const n of mine) console.log(n); return 0; }

  const label = o.shard ? ` (shard ${o.shard.index}/${o.shard.count})` : '';
  console.log(`run-all: ${mine.length} smokes${label}, ${skips.size} on the skip list, timeout ${o.timeout}s each`);
  const t0 = Date.now();
  const results = [];
  for (const name of mine) {
    const r = await runOne(o.dir, name, o.timeout);
    results.push(r);
    console.log(`${r.status.toUpperCase().padEnd(7)} ${name.padEnd(54)} ${r.secs.toFixed(1)}s`);
  }

  const red = results.filter((r) => r.status !== 'pass');
  const n = (s) => results.filter((r) => r.status === s).length;
  console.log(`\nran ${results.length}: ${n('pass')} pass, ${n('fail')} fail, ${n('timeout')} timeout, in ${((Date.now() - t0) / 1000).toFixed(0)}s`);
  if (red.length) {
    console.log(`\n${red.length} RED:`);
    for (const r of red) {
      const why = r.status === 'timeout' ? `timeout after ${o.timeout}s` : `exit ${r.code}`;
      console.log(`\n--- ${r.name} (${why}) ---`);
      const lines = r.out.split(/\r?\n/).filter((l, i, a) => l !== '' || i < a.length - 1);
      console.log(lines.slice(-TAIL_LINES).join('\n'));
    }
  }
  return red.length ? 1 : 0;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exit(await main());
}
