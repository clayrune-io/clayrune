#!/usr/bin/env node
/**
 * Capture a real screenshot for a Desk draft's visual.
 *
 * Standing position (2026-09-10): the Desk's default visual is a REAL
 * screenshot of the product, never generated art — LinkedIn suppresses
 * AI-read content and this project already ruled that AI must never render
 * product screens (it hallucinates illegible UI). This is the one tool a
 * drafting agent has for producing that screenshot, so `mc/desk_brief.py`
 * points at it directly.
 *
 * Reuses the SAME Playwright dependency and launch pattern as
 * tools/smoke/*.mjs (see desk.mjs) rather than standing up a second browser
 * harness — one-shot, no server, closes itself when done.
 *
 * Usage:
 *   node tools/smoke/capture-screenshot.mjs <url> <output-name.png> [selector]
 *
 * <output-name.png> must be a bare filename — it always lands under
 * data/media/, the one place `/api/serve-file` and the social-queue `media`
 * validator (mc/blueprints/project_routes.py `_media_violation`) will accept
 * a Desk draft's media path from.
 */
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { resolve, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const MEDIA_DIR = resolve(REPO_ROOT, 'data', 'media');

const [, , url, outName, selector] = process.argv;
if (!url || !outName) {
  console.error('usage: node capture-screenshot.mjs <url> <output-name.png> [selector]');
  process.exit(1);
}
if (basename(outName) !== outName) {
  console.error('output name must be a bare filename (no path separators) — it always lands in data/media/');
  process.exit(1);
}

mkdirSync(MEDIA_DIR, { recursive: true });
const outPath = resolve(MEDIA_DIR, outName);

let browser;
let exitCode = 0;
try {
  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
  if (selector) {
    const el = await page.$(selector);
    if (!el) {
      console.error(`selector not found: ${selector}`);
      exitCode = 1;
    } else {
      await el.screenshot({ path: outPath });
    }
  } else {
    await page.screenshot({ path: outPath });
  }
} catch (e) {
  console.error('capture failed: ' + (e && e.message ? e.message : e));
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}

if (exitCode === 0) console.log(outPath);
process.exit(exitCode);
