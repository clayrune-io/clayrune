// How the browser pane receives frames and events (backlog 629d2205).
// Server side: mc/browser_stream.py.
//
// Binary first: GET /api/browser/frames is an ordinary chunked HTTP response of
// length-prefixed messages whose JPEG is raw bytes (no base64, no JSON string
// per frame). It is read with fetch() + a ReadableStream, so it needs nothing
// the SSE path did not -- no WebSocket server, nothing a tunnel or the APK's
// WebView treats specially. If it cannot start, the pane uses the original SSE
// transport (/api/browser/stream), which the server still serves unchanged:
//   - fetch/ReadableStream missing, or localStorage mc_bp_transport = 'sse'
//   - HTTP error, wrong content-type, or a body that does not open with the magic
//     (a proxy rewriting or buffering the response, an older server with no route)
//   - no first bytes within FIRST_BYTES_MS (a proxy that holds a streaming body)
// A binary stream that drops AFTER it worked is reopened (the server re-sends tabs/
// dialog state to every new viewer); network errors before any data give it one retry.
//
// ES module: the pane imports this; nothing here touches window except the
// `_bpStats` counters (transport, frames, bytes, newest seq) the fps bench reads.

const MAGIC = 'CRF1';
const MSG_JSON = 1, MSG_FRAME = 2;
const FIRST_BYTES_MS = 6000;

function _prefTransport() {
  try { return localStorage.getItem('mc_bp_transport'); } catch (e) { return null; }
}

// Open the stream for session `sid`. `onMsg(d)` gets the parsed event object the
// SSE path would have given (`d.img` base64 on SSE, `d.blob` a JPEG Blob on binary).
// Returns { close(), onerror, onclose } -- the same `.close()` an EventSource has.
export function openStream(sid, onMsg) {
  const base = (window.API_BASE || '') + '/api/browser/';
  const q = '?session_id=' + sid;
  const stats = window._bpStats = { transport: '', frames: 0, bytes: 0, seq: 0 };
  const h = { onerror: null, onclose: null, close: null };
  let closed = false, es = null, ctl = null;

  const useSse = () => {
    if (closed) return;
    stats.transport = 'sse';
    es = new EventSource(base + 'stream' + q);
    es.onmessage = ev => {
      let d; try { d = JSON.parse(ev.data); } catch (e) { return; }
      stats.frames += d.img ? 1 : 0; stats.bytes += ev.data.length; if (d.seq) stats.seq = d.seq;
      onMsg(d);
    };
    es.onerror = () => { if (h.onerror) h.onerror(); };
  };

  h.close = () => {
    closed = true;
    if (ctl) { try { ctl.abort(); } catch (e) {} }
    if (es) { try { es.close(); } catch (e) {} }
    if (h.onclose) { try { h.onclose(); } catch (e) {} }
  };

  if (_prefTransport() === 'sse' || typeof fetch !== 'function' || typeof ReadableStream === 'undefined' ||
      typeof AbortController === 'undefined') {
    useSse();
    return h;
  }

  // ── binary ──
  let buf = new Uint8Array(1 << 18), have = 0, off = 0, magic = false, ended = false;
  const dec = new TextDecoder();
  const u32 = (a, o) => ((a[o] << 24) | (a[o + 1] << 16) | (a[o + 2] << 8) | a[o + 3]) >>> 0;
  const push = chunk => {
    if (have + chunk.length > buf.length) {
      const nb = new Uint8Array(Math.max(have + chunk.length, buf.length * 2));
      nb.set(buf.subarray(0, have)); buf = nb;
    }
    buf.set(chunk, have); have += chunk.length;
  };
  // Parse every whole message in the buffer. Frames that pile up in one read are
  // not all decoded: only the newest is handed on, the rest are already stale.
  const drain = () => {
    if (!magic) {
      if (have - off < 4) return;
      if (dec.decode(buf.subarray(off, off + 4)) !== MAGIC) throw new Error('bad magic');
      off += 4; magic = true;
    }
    let newest = null;
    while (have - off >= 5) {
      const type = buf[off], len = u32(buf, off + 1);
      if (have - off < 5 + len) break;
      const body = buf.subarray(off + 5, off + 5 + len);
      if (type === MSG_JSON) {
        let d = null; try { d = JSON.parse(dec.decode(body)); } catch (e) { d = null; }
        if (d) {
          if (d.status && d.status !== 'running') ended = true;
          onMsg(d);
        }
      } else if (type === MSG_FRAME) {
        const ml = u32(body, 0);
        let meta = null; try { meta = JSON.parse(dec.decode(body.subarray(4, 4 + ml))); } catch (e) { meta = null; }
        if (meta) { newest = { meta, jpeg: body.subarray(4 + ml) }; }
      }
      off += 5 + len;
    }
    if (newest) {
      stats.frames++; stats.bytes += newest.jpeg.length; stats.seq = newest.meta.seq;
      const d = newest.meta;
      d.blob = new Blob([newest.jpeg], { type: 'image/jpeg' });   // copies: buf is reused
      onMsg(d);
    }
    if (off) { buf.copyWithin(0, off, have); have -= off; off = 0; }
  };

  const open = async (failures) => {
    if (closed) return;
    ctl = new AbortController();
    let gotData = false, definitive = false;
    const dog = setTimeout(() => { if (!gotData) { definitive = true; ctl.abort(); } }, FIRST_BYTES_MS);
    have = off = 0; magic = false;
    try {
      const resp = await fetch(base + 'frames' + q, { signal: ctl.signal, cache: 'no-store' });
      const ct = resp.headers.get('content-type') || '';
      if (!resp.ok || !resp.body || ct.indexOf('application/x-clayrune-frames') !== 0) {
        definitive = true; throw new Error('not a frame stream: ' + resp.status + ' ' + ct);
      }
      stats.transport = 'bin';
      const rd = resp.body.getReader();
      for (;;) {
        const { value, done } = await rd.read();
        if (done) break;
        if (!gotData) { gotData = true; clearTimeout(dog); }
        push(value);
        try { drain(); } catch (e) { definitive = true; throw e; }
      }
    } catch (e) {
      clearTimeout(dog);
      if (closed) return;
      if (definitive) { useSse(); return; }       // this path will not work here: stop trying it
      if (!gotData && failures >= 1) { useSse(); return; }
      setTimeout(() => open(gotData ? 0 : failures + 1), 500);
      return;
    }
    clearTimeout(dog);
    if (closed || ended) return;                  // session over: the server said so
    if (h.onerror) h.onerror();
    setTimeout(() => open(0), 500);               // dropped without saying why: reopen
  };
  open(0);
  return h;
}

// Shows the newest frame Blob in `img`, one decode at a time. Assigning a new
// src aborts the decode in flight, so a stream faster than the decoder would
// otherwise never complete a frame; instead the newest waits for the current one
// to finish. Revokes each blob URL once the next frame replaces it.
export function makeBlobPainter(img) {
  let url = null, busy = false, busySince = 0, pending = null;
  const show = blob => {
    if (busy && Date.now() - busySince < 1000) { pending = blob; return; }
    busy = true; busySince = Date.now();
    const old = url; url = URL.createObjectURL(blob); img.src = url;
    if (old) URL.revokeObjectURL(old);
  };
  const done = () => { busy = false; if (pending) { const b = pending; pending = null; show(b); } };
  img.addEventListener('load', done);
  img.addEventListener('error', done);
  show.release = () => { pending = null; if (url) { URL.revokeObjectURL(url); url = null; } };
  return show;
}
