'use strict';
// Which page URLs count as "the dashboard" for the CDP driver: the origin the
// WebView was pointed at (MC_BASE, the same variable split4.js reads), not a
// hard-coded :5199 -- so a run through static-proxy.js can attach too.
const DEFAULT_BASE = 'http://10.0.2.2:5199/';

function dashPattern(base) {
  const host = new URL(base || DEFAULT_BASE).host;
  return new RegExp(host.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
}

module.exports = { dashPattern, DEFAULT_BASE };
