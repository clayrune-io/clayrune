// Desk v1 — Connect: the browser-sign-in side of Read (MC-1062 ticket 07,
// docs/desk_v1/connect_flow_tickets/07-browser-permission.md). Window-bridged module, no `import`
// (ground rule 1). It has NO screen of its own: the Permissions step (desk-v1-connect-permissions-step.js) shows ONE
// Read row for every account, and when the account is read through a browser sign-in, ticking it and saving calls
// `apply` here to add (or remove) that service's OWN domain on the sign-in's agent-read list. The old per-site row,
// its editor and its sharing list were retired on 2026-10-28 (Ron: the Desk follows the user's own accounts, it is not
// a place to choose arbitrary pages); `facts` survives for the Details section only.
//
// The grant is ONE domain on a saved browser profile's agent-read list (`GET /api/browser/agent-read`,
// `PUT /api/browser/profiles/<name>/agent-read`). That list belongs to the PROFILE, not the connection, so two
// connections on one profile share it, and other domains on the list are never touched. It is not a new store, not a
// per-account sandbox and not raw-page access: an agent gets an answer about a page, never the page, and cannot click,
// type or post.
//
// It reuses `DeskV1ConnectAgentRead` for the read of the list and for the write (`put`: the same PUT
// behind the same `humanProofFetch` passcode prompt the Browser pane's menu uses). This write is its OWN prompt, never
// the Review step's: cancelling it, or a wrong passcode, changes nothing. A profile that is not saved yet is not
// launched and not written to: `apply` answers `deferred` and the Permissions step holds the choice until the
// sign-in has saved the profile.
//
// The PUT replaces the profile's whole list and the server has no compare-and-set, so `apply` re-reads
// the list immediately before asking for the passcode and sends that list with ONE site added or
// removed. A list edited elsewhere while the passcode prompt is open would be overwritten; the saved
// list the server answers with is shown back, so it is never hidden.
//
// Never throws out of `apply`: a failure is a status the caller can show.
(function () {
  const MAX_DOMAINS = 20;                                   // mc/browser_agent_read.py MAX_DOMAINS
  const _LABEL = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;

  // A bare hostname, lowercased, or '' (the server's `normalize_domain`, which stays the authority).
  function siteOf(value) {
    let d = String(value == null ? '' : value).trim().toLowerCase();
    if (d.startsWith('*.')) d = d.slice(2);
    d = d.replace(/^\.+/, '').replace(/\.+$/, '');
    if (!d || d.length > 253) return '';
    const labels = d.split('.');
    if (labels.length < 2 || !labels.every((l) => _LABEL.test(l)) || /^\d+$/.test(labels[labels.length - 1])) return '';
    return d;
  }

  // The listed domains that already cover `site` (exactly it, or a parent of it).
  function covering(domains, site) { return domains.filter((d) => site === d || site.endsWith('.' + d)); }

  // What changing the list would do. `policy` is `{enabled, domains}` as the server stores it: a
  // switched-off profile reads nothing, so its leftover domains are not part of the grant. `sharedWith` names the other
  // saved connections reading through the same sign-in.
  //
  // REMOVING (Dave 2026-10-28): only the EXACT service domain ever leaves the list, and only when no other saved connection
  // reads through this sign-in. A parent domain (linkedin.com for www.linkedin.com) is never removed, and neither is a
  // domain someone else relies on; `stays` says which of the two kept it, so the screen can say so plainly.
  function plan(policy, site, want, sharedWith) {
    const active = policy && policy.enabled === true ? (policy.domains || []).slice() : [];
    const hit = covering(active, site);
    if (want) {
      if (hit.length) return { change: false, next: active, coveredBy: hit[0] };
      if (active.length >= MAX_DOMAINS) return { error: `This sign-in already allows ${MAX_DOMAINS} sites, the most it can. Remove one first.` };
      return { change: true, next: active.concat([site]) };
    }
    const parent = hit.find((d) => d !== site) || '';
    if (!hit.includes(site)) return { change: false, next: active, stays: parent ? { kind: 'parent', by: parent } : null };
    const sharers = sharedWith || [];
    if (sharers.length) return { change: false, next: active, stays: { kind: 'shared', by: sharers.join(', ') } };
    return { change: true, next: active.filter((d) => d !== site), removed: [site], stays: parent ? { kind: 'parent', by: parent } : null };
  }

  // Other saved connections that read through the same profile (they share this grant).
  function sharing(channels, profile, selfId) {
    const p = String(profile || '').trim().toLowerCase();
    if (!p) return [];
    return (channels || []).filter((c) => c && c.id !== selfId && c.read_via !== 'api'
      && String(c.browser_profile || '').trim().toLowerCase() === p).map((c) => c.label || c.identity || c.id);
  }

  function _reader() {
    const R = window.DeskV1ConnectAgentRead;
    if (!R || typeof R.put !== 'function' || typeof R.loadPolicies !== 'function') throw new Error('the agent-read editor is not loaded');
    return R;
  }

  // The current picture for one (profile, site): does the profile exist, is the site granted, and
  // what else rides on the same grant. `profile` '' means the connection names none yet.
  async function load(profile, site, opts) {
    const o = opts || {};
    const p = String(profile || '').trim().toLowerCase();
    const s = siteOf(site);
    const st = { profile: p, site: s, exists: false, enabled: false, domains: [], granted: false, coveredBy: '', others: [], sharedWith: sharing(o.channels, p, o.selfId) };
    if (!s || !p) return st;
    const { policies, saved } = await _reader().loadPolicies();
    st.exists = saved.map((n) => String(n).toLowerCase()).includes(p);
    const rec = policies[p] || {};
    st.enabled = rec.enabled === true;
    st.domains = st.enabled ? (rec.domains || []).slice() : [];
    const hit = covering(st.domains, s);
    st.granted = hit.length > 0;
    st.coveredBy = hit[0] || '';
    st.others = st.domains.filter((d) => !hit.includes(d));
    return st;
  }

  // The plain sentence for a domain that is still readable after Read was turned off (`stays` of plan/apply).
  function staysText(site, stays) {
    if (!stays) return '';
    if (stays.kind === 'shared') return `${site} stays readable for ${stays.by}, which uses the same sign-in.`;
    return `${site} stays readable because this sign-in allows all of ${stays.by}.`;
  }

  // Grant or revoke `site` on `profile`. -> { status, domains?, error?, stays? }
  //   `opts.channels` / `opts.selfId`: the saved connections, so a site another connection relies on is not removed.
  //   saved      the server stored the new list      unchanged   it already said that: nothing sent
  //   cancelled  the passcode prompt was dismissed   deferred    the profile is not saved yet: nothing sent
  //   failed     refused or unreachable: nothing changed (`error` says why)
  async function apply(profile, site, want, opts) {
    try {
      const st = await load(profile, site, opts || {});
      if (!st.site) return { status: 'failed', error: 'That is not a site name an agent can be allowed to read.' };
      if (!st.profile || !st.exists) return { status: 'deferred' };
      const pl = plan({ enabled: st.enabled, domains: st.domains }, st.site, !!want, st.sharedWith);
      if (pl.error) return { status: 'failed', error: pl.error };
      if (!pl.change) return { status: 'unchanged', domains: pl.next, site: st.site, stays: pl.stays || null };
      const keep = pl.next.length ? ` Other sites on it (${pl.next.filter((d) => d !== st.site).join(', ') || 'none'}) are not changed.` : '';
      const said = want
        ? `Re-enter your dashboard passcode to let agents read ${st.site} through "${st.profile}".${keep}`
        : `Re-enter your dashboard passcode to stop agents reading ${st.site} through "${st.profile}".${keep}`;
      const saved = await _reader().put(st.profile, pl.next, said);
      if (saved === null) return { status: 'cancelled' };
      return { status: 'saved', domains: saved, site: st.site, stays: pl.stays || null };
    } catch (e) {
      return { status: 'failed', error: (e && e.message) || 'That did not work.' };
    }
  }

  // The words for a Result/Review screen when the connection was saved and the permission was not
  // (or the other way round): never "connected" when a part failed.
  function outcome(connectionSaved, result) {
    const r = result || { status: 'unchanged' };
    if (connectionSaved && r.status === 'failed') return { kind: 'partial', text: `Your connection is saved. Browser reading was not changed: ${r.error || 'it did not save'}.` };
    if (connectionSaved && r.status === 'cancelled') return { kind: 'partial', text: 'Your connection is saved. Browser reading was not changed: the passcode was not entered.' };
    if (connectionSaved && r.status === 'deferred') return { kind: 'pending', text: 'Your connection is saved. Browser reading starts after you sign in.' };
    if (!connectionSaved && r.status === 'saved') return { kind: 'partial', text: 'Browser reading is saved, but the connection itself did not save.' };
    return { kind: 'ok', text: r.stays ? staysText(r.site, r.stays) : '' };
  }

  // ── words (Details only) ────────────────────────────────────────────────
  function _facts(st, wish) {
    const f = [];
    if (!st.profile) return ['Choose a browser sign-in first. Reading is allowed on a sign-in, not on an account.'];
    if (!st.exists) f.push(`The sign-in "${st.profile}" is not saved yet. Nothing changes until you sign in${wish ? '; this choice is kept for then' : ''}.`);
    else f.push(`Applies to every connection using the sign-in "${st.profile}" on ${st.site} and its subdomains.`);
    if (st.coveredBy && st.coveredBy !== st.site) f.push(`Allowed through ${st.coveredBy}, which covers ${st.site}. Turning Read off does not remove ${st.coveredBy}.`);
    if (st.sharedWith.length) f.push(`Also read through this sign-in: ${st.sharedWith.join(', ')}. ${st.site} stays readable while they use it.`);
    if (st.others.length) f.push(`Other sites on it stay as they are: ${st.others.join(', ')}.`);
    f.push('Agents get an answer about a page, never the page. They cannot click, type or post.');
    if (st.site === 'linkedin.com' || st.site.endsWith('.linkedin.com')) f.push('This does not read your LinkedIn feed or notifications by itself.');
    return f;
  }

  window.DeskV1ConnectBrowserPermission = { siteOf, covering, plan, sharing, load, apply, outcome, staysText, facts: _facts };
})();
