#!/usr/bin/env node
'use strict';
/*
 * film-export: turn one self-contained HTML promo film into an mp4 WITH its sound.
 *
 *   node tools/film-export/export.cjs <film.html> <out.mp4> [--fps 30] [--width 1920 --height 1080]
 *   node tools/film-export/export.cjs --all <films-dir> <out-dir> [same options]
 *
 * Picture: headless Chromium, window.__render(t) for t = n/fps, one screenshot per frame, piped to ffmpeg.
 * Sound:   the film's own Web Audio schedule, rendered offline (see README.md for the contract).
 * Details, the film contract and what the self-check proves: tools/film-export/README.md
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn, spawnSync } = require('child_process');
const { pathToFileURL } = require('url');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const SAMPLE_RATE = 48000;
const CHANNELS = 2;
/* Every film schedules its sound at AC.currentTime + .08 - offset. An offline context sits at 0 while
   scheduling, so the whole sound track starts .08 s late; it is trimmed off after rendering. */
const DEFAULT_AUDIO_LEAD = 0.08;

function usage() {
  return [
    'usage:',
    '  node tools/film-export/export.cjs <film.html> <out.mp4> [options]',
    '  node tools/film-export/export.cjs --all <films-dir> <out-dir> [options]',
    '',
    'options:',
    '  --fps N            frames per second (default 30)',
    '  --width N          viewport width  (default 1920)',
    '  --height N         viewport height (default 1080)',
    '  --duration S       seconds; default is read from the film source (the DUR variable)',
    '  --audio-lead S     seconds the film schedules sound ahead of its clock (default 0.08)',
    '  --fade-out S       fade the sound out over the last S seconds so the cut does not click (default 0.15; 0 = off)',
    '  --no-audio         deliberately export a picture-only mp4 (never the default)',
    '  --skip-existing    with --all: leave a film alone when its mp4 is already in <out-dir>',
    '',
    'Playwright is looked up in: $FILM_EXPORT_PLAYWRIGHT, tools/smoke/node_modules, then a plain require.',
  ].join('\n');
}

function parseArgs(argv) {
  const o = { fps: 30, width: 1920, height: 1080, duration: null, lead: DEFAULT_AUDIO_LEAD, fadeOut: 0.15, audio: true, all: false, skipExisting: false, pos: [] };
  const num = (name, v) => {
    const n = Number(v);
    if (v === undefined || !Number.isFinite(n) || n < 0) throw new Error(`${name} needs a number, got ${JSON.stringify(v)}`);
    return n;
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--fps') o.fps = num(a, argv[++i]);
    else if (a === '--width') o.width = num(a, argv[++i]);
    else if (a === '--height') o.height = num(a, argv[++i]);
    else if (a === '--duration') o.duration = num(a, argv[++i]);
    else if (a === '--audio-lead') o.lead = num(a, argv[++i]);
    else if (a === '--fade-out') o.fadeOut = num(a, argv[++i]);
    else if (a === '--no-audio') o.audio = false;
    else if (a === '--all') o.all = true;
    else if (a === '--skip-existing') o.skipExisting = true;
    else if (a === '-h' || a === '--help') { o.help = true; }
    else if (a.startsWith('--')) throw new Error(`unknown option ${a}`);
    else o.pos.push(a);
  }
  if (!(o.fps >= 1) || Math.round(o.fps) !== o.fps) throw new Error('--fps must be a whole number >= 1');
  if (!(o.width >= 16 && o.height >= 16)) throw new Error('--width and --height must be >= 16');
  return o;
}

function checkFfmpeg() {
  for (const tool of ['ffmpeg']) {
    const r = spawnSync(tool, ['-version'], { encoding: 'utf8' });
    if (r.error || r.status !== 0) {
      throw new Error(`${tool} was not found on PATH. Install ffmpeg (https://ffmpeg.org/download.html) and run again.`);
    }
  }
}

function loadPlaywright() {
  const cands = [];
  const env = process.env.FILM_EXPORT_PLAYWRIGHT;
  if (env) {
    const sub = path.join(env, 'playwright');
    cands.push(fs.existsSync(path.join(sub, 'package.json')) ? sub : env);
  }
  cands.push(path.join(REPO_ROOT, 'tools', 'smoke', 'node_modules', 'playwright'));
  cands.push('playwright');
  for (const c of cands) {
    try {
      const pw = require(c);
      if (pw && pw.chromium) return pw;
    } catch (e) { /* try the next one */ }
  }
  throw new Error([
    'Playwright was not found. Looked in:',
    ...cands.map((c) => '  ' + (c === 'playwright' ? 'require("playwright")' : 'tools/smoke/node_modules or $FILM_EXPORT_PLAYWRIGHT')),
    '',
    'Do not npm install it. tools/smoke/node_modules lives in the MAIN checkout and is gitignored, so a',
    'worktree has none. Point at it instead:',
    '  FILM_EXPORT_PLAYWRIGHT=<main checkout>/tools/smoke/node_modules node tools/film-export/export.cjs ...',
    '(PowerShell: $env:FILM_EXPORT_PLAYWRIGHT = "<main checkout>\\tools\\smoke\\node_modules")',
    'The Chromium build comes from the shared Playwright cache; if it is missing: npx playwright install chromium',
  ].join('\n'));
}

/* The DUR variable lives inside each film's closure, so it is read from the source: the `var` statement
   that assigns it, evaluated as plain arithmetic (promo-bumpers.html has DUR=BL*NB). */
function readDuration(html, file) {
  const m = html.match(/var\s+([^;]*\bDUR\s*=[^;]*);/);
  if (!m) throw new Error(`${file}: no "DUR=" assignment found in the source; pass --duration <seconds>`);
  let v;
  try { v = new Function('var ' + m[1] + '; return DUR;')(); } catch (e) { v = NaN; }
  if (!(typeof v === 'number' && v > 0 && v < 3600)) {
    throw new Error(`${file}: could not evaluate DUR from "var ${m[1].slice(0, 80)}"; pass --duration <seconds>`);
  }
  return v;
}

/* Injected before any film script runs, in the AUDIO page only. */
function audioInit({ frames, rate, channels }) {
  /* Deterministic noise: the films fill their noise buffer with Math.random, so seed it. */
  let s = 0x5eed1234 >>> 0;
  Math.random = function () {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  /* The playback loop is not wanted here, only the scheduling. */
  window.requestAnimationFrame = function () { return 0; };
  window.cancelAnimationFrame = function () {};
  window.__acs = [];
  class FakeAudioContext extends OfflineAudioContext {
    constructor() {
      super({ numberOfChannels: channels, length: frames, sampleRate: rate });
      window.__acs.push(this);
    }
    close() { return Promise.resolve(); }
    resume() { return Promise.resolve(); }
    suspend() { return Promise.resolve(); }
  }
  window.AudioContext = FakeAudioContext;
  window.webkitAudioContext = FakeAudioContext;
}

async function renderAudio(browser, fileUrl, opts, dur) {
  const rate = SAMPLE_RATE;
  const frames = Math.ceil((dur + opts.lead) * rate);
  const ctx = await browser.newContext({ viewport: { width: opts.width, height: opts.height }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  try {
    await page.addInitScript(audioInit, { frames, rate, channels: CHANNELS });
    await page.goto(fileUrl, { waitUntil: 'load' });
    /* The film's own start: a click on its play prompt, which calls its sound scheduler. */
    if (await page.locator('#prompt').count()) await page.locator('#prompt').click({ timeout: 5000 });
    else await page.keyboard.press('Space');
    const made = await page.evaluate(() => window.__acs.length);
    if (made < 1) {
      throw new Error('the film never created an AudioContext after its start was triggered, so there is no sound to render' +
        (errors.length ? ` (page error: ${errors[0]})` : '') + '. Use --no-audio to export picture only, on purpose.');
    }
    const out = await page.evaluate(async ({ lead, dur, fadeOut }) => {
      const ac = window.__acs[window.__acs.length - 1];
      const buf = await ac.startRendering();
      const rate = buf.sampleRate, ch = buf.numberOfChannels;
      const from = Math.round(lead * rate), n = Math.round(dur * rate);
      const fadeN = Math.min(n, Math.round(fadeOut * rate));
      const pcm = new Int16Array(n * ch);
      let peak = 0, nonzero = 0;
      for (let c = 0; c < ch; c++) {
        const d = buf.getChannelData(c);
        for (let i = 0; i < n; i++) {
          let v = d[from + i] || 0;
          if (fadeN && i >= n - fadeN) v *= (n - 1 - i) / fadeN;
          const a = Math.abs(v);
          if (a > peak) peak = a;
          if (a > 1e-4) nonzero++;
          pcm[i * ch + c] = Math.max(-32768, Math.min(32767, Math.round(v * 32767)));
        }
      }
      const bytes = new Uint8Array(pcm.buffer);
      let bin = '';
      for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
      return { b64: btoa(bin), peak, nonzero, contexts: window.__acs.length, samples: n, channels: ch, rate };
    }, { lead: opts.lead, dur, fadeOut: opts.fadeOut });
    if (out.nonzero === 0) {
      throw new Error('the film scheduled sound but the offline render came out silent; refusing to ship it as if it had sound. ' +
        'Use --no-audio to export picture only, on purpose.');
    }
    return out;
  } finally {
    await ctx.close().catch(() => {});
  }
}

function wavFile(file, b64, channels, rate) {
  const pcm = Buffer.from(b64, 'base64');
  const h = Buffer.alloc(44);
  h.write('RIFF', 0); h.writeUInt32LE(36 + pcm.length, 4); h.write('WAVE', 8);
  h.write('fmt ', 12); h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(channels, 22);
  h.writeUInt32LE(rate, 24); h.writeUInt32LE(rate * channels * 2, 28); h.writeUInt16LE(channels * 2, 32); h.writeUInt16LE(16, 34);
  h.write('data', 36); h.writeUInt32LE(pcm.length, 40);
  fs.writeFileSync(file, Buffer.concat([h, pcm]));
}

async function exportFilm(pw, film, outFile, opts) {
  const filmPath = path.resolve(film);
  if (!fs.existsSync(filmPath)) throw new Error(`film not found: ${film}`);
  const html = fs.readFileSync(filmPath, 'utf8');
  const dur = opts.duration || readDuration(html, path.basename(filmPath));
  const fps = opts.fps;
  const total = Math.round(dur * fps);
  const fileUrl = pathToFileURL(filmPath).href;
  const outPath = path.resolve(outFile);
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  const work = fs.mkdtempSync(path.join(os.tmpdir(), 'film-export-'));
  const wav = path.join(work, 'sound.wav');
  const t0 = Date.now();
  let browser;
  try {
    browser = await pw.chromium.launch({ headless: true });
    let audioInfo = null;
    if (opts.audio) {
      audioInfo = await renderAudio(browser, fileUrl, opts, dur);
      wavFile(wav, audioInfo.b64, audioInfo.channels, audioInfo.rate);
      audioInfo.b64 = null;
      console.log(`  sound: ${dur}s rendered offline, peak ${audioInfo.peak.toFixed(3)}, ${audioInfo.contexts} AudioContext(s) created`);
    } else {
      console.log('  sound: --no-audio, picture only');
    }

    const ctx = await browser.newContext({ viewport: { width: opts.width, height: opts.height }, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    const pageErrors = [];
    page.on('pageerror', (e) => pageErrors.push(String(e)));
    await page.goto(fileUrl + '?freeze&mute&t=0', { waitUntil: 'load' });
    const hasHooks = await page.evaluate(() => typeof window.__render === 'function');
    if (!hasHooks) throw new Error('window.__render is not defined; this film does not keep the freeze/render contract');
    await page.evaluate(() => document.fonts && document.fonts.ready);

    const args = ['-y', '-hide_banner', '-loglevel', 'error',
      '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'png', '-i', '-'];
    if (opts.audio) args.push('-i', wav);
    args.push('-map', '0:v:0');
    if (opts.audio) args.push('-map', '1:a:0');
    args.push('-vf', 'scale=out_color_matrix=bt709:out_range=tv,format=yuv420p',
      '-c:v', 'libx264', '-preset', 'medium', '-crf', '18', '-r', String(fps),
      '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-color_range', 'tv');
    if (opts.audio) args.push('-c:a', 'aac', '-b:a', '192k', '-t', String(dur));
    args.push('-movflags', '+faststart', outPath);
    const ff = spawn('ffmpeg', args, { stdio: ['pipe', 'ignore', 'pipe'] });
    let ffErr = '';
    ff.stderr.on('data', (d) => { ffErr += d; });
    const done = new Promise((resolve) => ff.on('close', (code) => resolve(code)));
    let stdinError = null;
    ff.stdin.on('error', (e) => { stdinError = e; });

    let lastLog = Date.now();
    for (let n = 0; n < total; n++) {
      if (stdinError) break;
      await page.evaluate((t) => window.__render(t), n / fps);
      const png = await page.screenshot({ type: 'png' });
      if (!ff.stdin.write(png)) await new Promise((r) => ff.stdin.once('drain', r));
      if (Date.now() - lastLog > 5000) {
        console.log(`  picture: frame ${n + 1}/${total}`);
        lastLog = Date.now();
      }
    }
    ff.stdin.end();
    const code = await done;
    if (code !== 0) throw new Error(`ffmpeg exited ${code}: ${ffErr.trim().slice(-600)}`);
    if (pageErrors.length) console.log(`  note: the film threw ${pageErrors.length} page error(s) while stepping; first: ${pageErrors[0]}`);
    await ctx.close().catch(() => {});
    const secs = ((Date.now() - t0) / 1000).toFixed(1);
    console.log(`  wrote ${outPath} (${total} frames at ${fps} fps, ${dur}s, ${opts.audio ? 'with sound' : 'NO sound'}) in ${secs}s`);
    return { frames: total, dur };
  } finally {
    if (browser) await browser.close().catch(() => {});
    fs.rmSync(work, { recursive: true, force: true });
  }
}

async function main() {
  let opts;
  try { opts = parseArgs(process.argv.slice(2)); } catch (e) { console.error('error: ' + e.message + '\n\n' + usage()); process.exit(2); }
  if (opts.help) { console.log(usage()); return; }
  try { checkFfmpeg(); } catch (e) { console.error('error: ' + e.message); process.exit(2); }
  let pw;
  try { pw = loadPlaywright(); } catch (e) { console.error('error: ' + e.message); process.exit(2); }

  if (opts.all) {
    if (opts.pos.length !== 2) { console.error('error: --all needs <films-dir> <out-dir>\n\n' + usage()); process.exit(2); }
    const [dir, outDir] = opts.pos;
    const films = fs.readdirSync(dir).filter((f) => f.toLowerCase().endsWith('.html')).sort();
    if (!films.length) { console.error(`error: no .html files in ${dir}`); process.exit(2); }
    const results = [];
    for (const f of films) {
      const out = path.join(outDir, f.replace(/\.html$/i, '.mp4'));
      console.log(`== ${f}`);
      if (opts.skipExisting && fs.existsSync(out)) {
        console.log('  skipped: mp4 already exists');
        results.push({ f, ok: true, note: 'skipped, mp4 already exists' });
        continue;
      }
      try {
        const r = await exportFilm(pw, path.join(dir, f), out, opts);
        results.push({ f, ok: true, note: `${r.frames} frames, ${r.dur}s` });
      } catch (e) {
        console.log(`  FAILED: ${e.message}`);
        results.push({ f, ok: false, note: e.message });
      }
    }
    console.log('\nsummary:');
    for (const r of results) console.log(`  ${r.ok ? 'ok    ' : 'FAILED'} ${r.f}  ${r.ok ? r.note : r.note.split('\n')[0]}`);
    process.exit(results.every((r) => r.ok) ? 0 : 1);
  }

  if (opts.pos.length !== 2) { console.error('error: need <film.html> <out.mp4>\n\n' + usage()); process.exit(2); }
  try {
    console.log(`== ${path.basename(opts.pos[0])}`);
    await exportFilm(pw, opts.pos[0], opts.pos[1], opts);
  } catch (e) {
    console.error('error: ' + e.message);
    process.exit(1);
  }
}

if (require.main === module) main();
module.exports = { exportFilm, readDuration, loadPlaywright, checkFfmpeg };
