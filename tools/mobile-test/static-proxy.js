'use strict';
// Serve THIS checkout's static/ over the emulator-reachable loopback, proxying
// every other request (API, SSE, assets) to the running Clayrune on :5199.
//
// Why: the dev APK / emulator WebView loads the SPA from the live server, which
// serves the MAIN checkout's static/. To test a worktree branch on a real
// WebView without a second Clayrune (single-instance invariant) or a server
// restart, point the WebView at this proxy instead (http://10.0.2.2:<port>/).
//
//   node tools/mobile-test/static-proxy.js [port=5399]
//
// `/` gets the same ?v= cache-bust rewrite server.py applies, with a fresh token
// per request so an edit is picked up on the next reload.
const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = parseInt(process.argv[2] || process.env.MC_PROXY_PORT || '5399', 10);
const UPSTREAM = { host: '127.0.0.1', port: parseInt(process.env.MC_UPSTREAM_PORT || '5199', 10) };
const STATIC = path.resolve(__dirname, '..', '..', 'static');
const TYPES = {
  '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8', '.json': 'application/json', '.png': 'image/png',
  '.svg': 'image/svg+xml', '.webp': 'image/webp', '.jpg': 'image/jpeg', '.ico': 'image/x-icon',
};

function serveFile(res, file, transform) {
  fs.readFile(file, (err, buf) => {
    if (err) { res.writeHead(404); res.end('not found'); return; }
    let body = buf;
    if (transform) body = Buffer.from(transform(buf.toString('utf8')), 'utf8');
    res.writeHead(200, {
      'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream',
      'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
    });
    res.end(body);
  });
}

http.createServer((req, res) => {
  const u = new URL(req.url, 'http://x');
  if (req.method === 'GET' && u.pathname === '/') {
    const ver = String(Date.now());
    return serveFile(res, path.join(STATIC, 'index.html'), (html) =>
      html.replace(/(src|href)="(\/static\/[^"?]+\.(?:js|css))"/g, `$1="$2?v=${ver}"`));
  }
  if (req.method === 'GET' && u.pathname.startsWith('/static/')) {
    const rel = decodeURIComponent(u.pathname.slice('/static/'.length));
    const file = path.resolve(STATIC, rel);
    if (file.startsWith(STATIC + path.sep) && fs.existsSync(file) && fs.statSync(file).isFile()) {
      return serveFile(res, file);
    }
  }
  const up = http.request({ ...UPSTREAM, path: req.url, method: req.method, headers: req.headers }, (ur) => {
    res.writeHead(ur.statusCode, ur.headers);
    ur.pipe(res);
  });
  up.on('error', (e) => { res.writeHead(502); res.end('upstream: ' + e.message); });
  req.pipe(up);
  res.on('close', () => up.destroy());
}).listen(PORT, '127.0.0.1', () => console.log(`static-proxy on 127.0.0.1:${PORT} -> static=${STATIC} upstream=:${UPSTREAM.port}`));
