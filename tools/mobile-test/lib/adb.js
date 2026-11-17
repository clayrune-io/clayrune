'use strict';
// Thin adb wrapper + WebView devtools-socket discovery/forwarding.
// All scenario code drives the emulator through these helpers.
const { execSync, execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// adb is found, in order, from MC_ADB (a full path to the binary),
// ANDROID_HOME/platform-tools, then PATH. Resolved on first use, so a script
// that never calls adb does not need one installed.
function resolveAdb() {
  const names = process.platform === 'win32' ? ['adb.exe', 'adb.cmd', 'adb.bat'] : ['adb'];
  const isFile = (p) => { try { return fs.statSync(p).isFile(); } catch (_) { return false; } };
  if (process.env.MC_ADB) return process.env.MC_ADB;   // as given, as before
  if (process.env.ANDROID_HOME) {
    for (const n of names) {
      const p = path.join(process.env.ANDROID_HOME, 'platform-tools', n);
      if (isFile(p)) return p;
    }
  }
  const dirs = (process.env.PATH || process.env.Path || '').split(path.delimiter).filter(Boolean);
  for (const d of dirs) {
    for (const n of names) {
      const p = path.join(d, n);
      if (isFile(p)) return p;
    }
  }
  throw new Error(
    'adb not found. Set MC_ADB to the adb binary, or ANDROID_HOME to an Android SDK ' +
    '(adb is read from <ANDROID_HOME>/platform-tools), or put adb on PATH.');
}

let _adb;
function adbPath() { return (_adb = _adb || resolveAdb()); }

const SERIAL = process.env.MC_SERIAL || 'emulator-5554';
const APP_ID = process.env.MC_APP_ID || 'io.clayrune.app';
const CDP_PORT = parseInt(process.env.MC_CDP_PORT || '9222', 10);

function adb(args, opts = {}) {
  return execFileSync(adbPath(), ['-s', SERIAL, ...args], {
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
    ...opts,
  });
}

function adbShell(cmd) {
  return adb(['shell', cmd]);
}

// Locate the Capacitor WebView's abstract devtools socket
// (`@webview_devtools_remote_<pid>`) by scanning /proc/net/unix.
function findWebviewSocket() {
  let unix = '';
  try { unix = adbShell('cat /proc/net/unix'); } catch (_) { return null; }
  const names = unix
    .split('\n')
    .map((l) => l.trim())
    .map((l) => {
      const at = l.indexOf('@');
      return at >= 0 ? l.slice(at + 1) : '';
    })
    .filter((n) => /^webview_devtools_remote_\d+$/.test(n));
  // Deduplicate; prefer the most recently created (highest pid) if multiple.
  const uniq = [...new Set(names)];
  uniq.sort((a, b) => parseInt(b.split('_').pop(), 10) - parseInt(a.split('_').pop(), 10));
  return uniq[0] || null;
}

function forwardDevtools(socket, port = CDP_PORT) {
  try { adb(['forward', '--remove', `tcp:${port}`]); } catch (_) {}
  adb(['forward', `tcp:${port}`, `localabstract:${socket}`]);
  return port;
}

function launchApp() {
  // monkey is the most reliable "launch default activity" trigger.
  adbShell(`monkey -p ${APP_ID} -c android.intent.category.LAUNCHER 1`);
}

function forceStop() {
  try { adbShell(`am force-stop ${APP_ID}`); } catch (_) {}
}

// Simulate the user backgrounding the app (HOME), then bringing it back.
function pressHome() { adbShell('input keyevent KEYCODE_HOME'); }
function bringToForeground() {
  adbShell(`monkey -p ${APP_ID} -c android.intent.category.LAUNCHER 1`);
}

function screencap(outPath) {
  const png = adb(['exec-out', 'screencap', '-p'], { encoding: 'buffer' });
  require('fs').writeFileSync(outPath, png);
  return outPath;
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

module.exports = {
  resolveAdb, get ADB() { return adbPath(); }, SERIAL, APP_ID, CDP_PORT,
  adb, adbShell, findWebviewSocket, forwardDevtools,
  launchApp, forceStop, pressHome, bringToForeground, screencap, sleep,
};
