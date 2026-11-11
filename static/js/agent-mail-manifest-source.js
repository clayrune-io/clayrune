// ── Agent mail: signed service manifest source (MC-1166 unit 7e-2m) ───────────
// Implements window.AgentMailManifestSource.get(), which agent-mail-setup.js calls when the
// human presses Continue (and not before: viewing or declining the step makes no request).
//
// It asks THIS computer's own server for the manifest. The server fetched it from the mail
// service and verified the offline-root signature before answering, so a successful answer
// is already verified; nothing here checks or produces a signature, and nothing is cached.
//
// get() never throws. It resolves to either
//   { manifest, signature }                       on success, or
//   { ok: false, error, detail, message }         with `message` in plain words.
// `describe(code)` is the same wording, exposed so a caller can word a code it holds.
// Escaping is the caller's job (setup escapes every string it paints). Window-bridged.
(function () {
  const PATH = '/api/agent-mail/enrollment/manifest';

  const WORDS = {
    not_configured: 'This install has no agent mail service address and signing key set up, so setup cannot start. Nothing was sent.',
    unreachable: 'The Clayrune mail service could not be reached, or it does not publish its signed manifest yet. Check your connection and try again. Nothing was sent.',
    bad_signature: 'The mail service sent a manifest whose signature did not verify, so it was refused. Nothing was sent.',
    stale: 'The mail service manifest is out of date or not valid yet. If this computer\'s clock is wrong, fix it and try again. Nothing was sent.',
    refused: 'The mail service sent a manifest that was not in the expected form, so it was refused. Nothing was sent.',
    not_found: 'Agent mail setup is not turned on for this install. Nothing was sent.',
    bad_answer: 'This computer\'s server gave an answer that could not be read, so setup did not start. Nothing was sent.',
    network: 'Could not reach this computer\'s server. Nothing was sent.',
  };
  const FALLBACK = 'The signed service manifest could not be obtained, so nothing was sent.';

  const describe = (code) => Object.prototype.hasOwnProperty.call(WORDS, code) ? WORDS[code] : FALLBACK;
  const fail = (error, detail) => ({ ok: false, error, detail: detail || '', message: describe(error) });

  async function get() {
    const base = typeof API_BASE === 'string' ? API_BASE : '';
    let res;
    try { res = await fetch(base + PATH, { method: 'GET', cache: 'no-store' }); } catch (_) { return fail('network'); }
    const body = await res.json().catch(() => null);
    if (res.status === 200) {
      if (body && Array.isArray(body.manifest) && typeof body.signature === 'string') {
        return { manifest: body.manifest, signature: body.signature };
      }
      return fail('bad_answer');
    }
    if (res.status === 404) return fail('not_found');
    const code = body && typeof body.error === 'string' ? body.error : '';
    return fail(Object.prototype.hasOwnProperty.call(WORDS, code) ? code : 'bad_answer',
      body && typeof body.detail === 'string' ? body.detail : '');
  }

  window.AgentMailManifestSource = { get, describe };
})();
