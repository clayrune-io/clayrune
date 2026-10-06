// ── Passkeys: the browser half of a WebAuthn ceremony (docs/PASSKEYS_SPEC.md) ─
// base64url <-> ArrayBuffer, the server's options -> what navigator.credentials
// wants, a PublicKeyCredential -> the JSON the server's routes take, and the
// assertion round trip that approves one add or revoke (slice 2a).
//
// Pure browser mechanics. It holds no state and never sees the dashboard
// passcode. passkeys-panel.js is the only caller.
//
// An assertion is the proof for ONE operation: `assertFor` asks the server for a
// single-use ceremony bound to (purpose, credential id), has the browser sign its
// challenge, and returns the `proof` object the operation's own route consumes.
// The ceremony cookie the server sets on the way in is what ties the two calls to
// this browser session; fetch sends it back by itself.

(function () {
  function b64urlToBuf(s) {
    const str = String(s);
    const b = atob(str.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((str.length + 3) % 4));
    const out = new Uint8Array(b.length);
    for (let i = 0; i < b.length; i++) out[i] = b.charCodeAt(i);
    return out.buffer;
  }

  function bufToB64url(buf) {
    const bytes = new Uint8Array(buf);
    let s = '';
    for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  // Server options (base64url strings) -> what navigator.credentials.create wants.
  function creationOptions(o) {
    const pk = Object.assign({}, o);
    pk.challenge = b64urlToBuf(o.challenge);
    pk.user = Object.assign({}, o.user, { id: b64urlToBuf(o.user.id) });
    pk.excludeCredentials = (o.excludeCredentials || []).map((c) => Object.assign({}, c, { id: b64urlToBuf(c.id) }));
    return pk;
  }

  // Server options -> what navigator.credentials.get wants.
  function requestOptions(o) {
    const pk = Object.assign({}, o);
    pk.challenge = b64urlToBuf(o.challenge);
    pk.allowCredentials = (o.allowCredentials || []).map((c) => Object.assign({}, c, { id: b64urlToBuf(c.id) }));
    return pk;
  }

  // A create() result -> the JSON shape the finish route takes.
  function creationJSON(cred) {
    const r = cred.response;
    return {
      id: cred.id,
      rawId: bufToB64url(cred.rawId),
      type: cred.type,
      authenticatorAttachment: cred.authenticatorAttachment || undefined,
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      response: {
        clientDataJSON: bufToB64url(r.clientDataJSON),
        attestationObject: bufToB64url(r.attestationObject),
        transports: typeof r.getTransports === 'function' ? r.getTransports() : [],
      },
    };
  }

  // A get() result -> the JSON shape `parse_authentication_credential_json` takes.
  function assertionJSON(cred) {
    const r = cred.response;
    return {
      id: cred.id,
      rawId: bufToB64url(cred.rawId),
      type: cred.type,
      authenticatorAttachment: cred.authenticatorAttachment || undefined,
      clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
      response: {
        clientDataJSON: bufToB64url(r.clientDataJSON),
        authenticatorData: bufToB64url(r.authenticatorData),
        signature: bufToB64url(r.signature),
        userHandle: r.userHandle ? bufToB64url(r.userHandle) : undefined,
      },
    };
  }

  async function _post(url, body) {
    const res = await fetch(API_BASE + url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const out = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, body: out };
  }

  // Resolves to { proof } on success, or { error, response } where `error` is a
  // sentence for the user and `response` is the server's reply when it was the
  // server that said no (so the caller can map its error code to a state).
  async function assertFor(purpose, credentialId) {
    if (!(navigator.credentials && window.PublicKeyCredential)) {
      return { error: 'This browser does not support passkeys.' };
    }
    const started = await _post('/api/passkeys/assert/options',
      credentialId ? { purpose, credential_id: credentialId } : { purpose });
    if (!started.ok) {
      const b = started.body || {};
      return { error: b.message || b.error || ('HTTP ' + started.status), response: started };
    }
    let cred;
    try {
      cred = await navigator.credentials.get({ publicKey: requestOptions(started.body.options) });
    } catch (e) {
      return {
        error: e && e.name === 'NotAllowedError'
          ? 'The passkey prompt was cancelled or timed out. Nothing was changed.'
          : 'The browser could not use your passkey: ' + ((e && e.message) || e),
      };
    }
    if (!cred) return { error: 'The browser did not return a passkey. Nothing was changed.' };
    return { proof: { ceremony_id: started.body.ceremony_id, assertion: assertionJSON(cred) } };
  }

  window.PasskeysWebauthn = {
    b64urlToBuf, bufToB64url, creationOptions, requestOptions, creationJSON, assertionJSON, assertFor,
  };
})();
