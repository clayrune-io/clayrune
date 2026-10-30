import './desk-v1-composer-duplicate.js';
// C1: text, bounded destinations, draft persistence and Review handoff. Media
// and preview have their own later slices. No publish/approval route is used.
import './desk-v1-composer-preview.js';
(function () {
  const drafts = new Map();
  const esc = (s) => window.esc(String(s == null ? '' : s));
  const store = () => window.DeskV1Store;
  const channels = () => window.DeskV1ComposerChannels;
  const base = (id) => '/api/desk/pieces/' + encodeURIComponent(id);
  let mounted = null;
  const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2);

  function open(camp, fam) {
    window.deskV1Nav('composer', { campaignId: camp.id, projectId: camp.projectId, familyId: fam?.id || 'fam-post-' + uid() });
  }
  function draft(camp, id) {
    const fam = store().state().families.find((f) => f.id === id) || null;
    let d = drafts.get(id);
    if (!d) {
      const bound = channels().accounts(camp).map((c) => c.id);
      d = { id, fam, text: fam?.body || '', saved: fam?.body || '', selected: new Set(fam ? channels().versions(fam).map((v) => v.channelId).filter((id) => bound.includes(id)) : bound), status: '', failed: false, busy: false };
      drafts.set(id, d);
    } else if (!d.busy) {
      // Workspace refreshes replace objects. Keep unsaved typing, but follow
      // saved state and calendar/channel changes made on the other screens.
      if (d.text === d.saved) d.text = d.saved = fam?.body || '';
      d.fam = fam;
      if (fam && !d.failed) d.selected = new Set(channels().versions(fam).map((v) => v.channelId).filter((id) => (camp.plan.accounts || []).includes(id)));
    }
    return d;
  }
  function current(d) { return mounted && mounted.d === d && mounted.el.isConnected && mounted.el.querySelector('[data-composer]'); }
  function update(d) {
    const root = current(d);
    if (!root) return;
    root.querySelector('[data-composer-status]').textContent = d.status;
    root.querySelector('[data-composer-status]').toggleAttribute('data-failed', d.failed);
    root.querySelector('[data-composer-save]').disabled = d.busy || !!d.fam?.archivedAt || !d.text.trim() && !d.fam;
    root.querySelector('[data-composer-text]').readOnly = d.locked || !!d.reviewing;
    const reviewable = [...d.selected].some((id) => {
      const v = channels().versions(d.fam).find((v) => v.channelId === id);
      return !v || !channels().sent(v);
    });
    root.querySelector('[data-composer-review]').disabled = d.busy || !reviewable || !d.text.trim() || !!d.fam?.archivedAt;
    root.querySelector('[data-composer-when]').disabled = d.busy || !d.fam || !!d.fam.archivedAt || !channels().versions(d.fam).length;
    root.querySelectorAll('[data-composer-channel]').forEach((b) => {
      b.disabled = d.busy || !!d.fam?.archivedAt || !!channels().versions(d.fam).find((v) => v.channelId === b.dataset.composerChannel && channels().sent(v));
    });
    const n = [...d.text].length;
    root.querySelector('[data-composer-count]').textContent = [...d.selected].map((id) => {
      const ch = store().state().channels.find((c) => c.id === id);
      return ch?.platform === 'x' ? `X ${n}/280${n > 280 ? ' · over limit' : ''}` : `${ch?.label || ch?.identity || id}: ${n}`;
    }).join(' · ');
    root.querySelector('[data-composer-count]').toggleAttribute('data-failed', n > 280 && channels().accounts(mounted.camp).some((c) => c.platform === 'x' && d.selected.has(c.id)));
  }
  function check(result) { if (!result.ok) throw new Error(result.error); return result.result; }

  async function persist(camp, d) {
    d.fam = store().state().families.find((f) => f.id === d.id) || null;
    if (!d.fam) {
      if (!d.text.trim()) return;
      const text = d.text;
      const fam = { id: d.id, campaignId: camp.id, kind: 'post', title: 'New post', body: text, assets: [], versions: [] };
      const result = check(await store().write({
        label: 'Created a post draft',
        apply: () => { store().state().families.push(fam); },
        unapply: () => { const a = store().state().families; const i = a.findIndex((f) => f.id === fam.id); if (i >= 0) a.splice(i, 1); },
        request: () => store().api('POST', '/api/desk/pieces', { id: fam.id, campaign_id: camp.id, kind: 'post', title: fam.title, body: text }),
        undoRequest: () => window.deskV1QueuePiece(fam.id, () => store().api('DELETE', base(fam.id))),
      }));
      if (result) Object.assign(fam, result);
      d.fam = fam;
      d.saved = fam.body;
    }
    if (d.text !== d.saved) {
      const text = d.text, prev = d.fam.body;
      const result = check(await store().write({
        label: 'Saved post draft',
        apply: () => { d.fam.body = text; },
        unapply: () => { d.fam.body = prev; },
        request: () => store().api('PATCH', base(d.id), { body: text }),
        undoRequest: () => window.deskV1QueuePiece(d.id, () => store().api('PATCH', base(d.id), { body: prev })),
      }));
      d.saved = result?.body ?? text;
      d.fam.body = d.saved;
    }
    await channels().sync(camp, d);
  }

  async function save(camp, d, review) {
    if (d.busy) return;
    d.busy = true; d.reviewing = !!review; d.failed = false; d.status = 'Saving…'; update(d);
    try {
      await window.deskV1QueuePiece(d.id, async () => {
        await persist(camp, d);
        if (!review || !d.fam) return;
        const selected = channels().versions(d.fam).filter((v) => d.selected.has(v.channelId));
        if (!selected.length) throw new Error('Choose at least one campaign account.');
        for (const v of selected.filter((v) => !channels().sent(v) && v.state !== 'needs_review')) {
          const previous = { state: v.state, approved: v.approved };
          check(await store().write({
            label: 'Sent post to review',
            apply: () => { v.state = 'needs_review'; v.approved = null; },
            unapply: () => Object.assign(v, previous),
            request: () => store().api('PATCH', base(d.id) + '/versions/' + encodeURIComponent(v.id), { state: 'needs_review' }),
            undoRequest: ['approved', 'scheduled'].includes(previous.state) ? undefined : () => window.deskV1QueuePiece(d.id, () => store().api('PATCH', base(d.id) + '/versions/' + encodeURIComponent(v.id), { state: previous.state })),
            irreversible: ['approved', 'scheduled'].includes(previous.state),
          }));
        }
        const previous = d.fam.draft?.status || null;
        check(await store().write({
          label: 'Sent post to review',
          apply: () => { d.fam.draft = { status: 'in_review' }; },
          unapply: () => { d.fam.draft = previous ? { status: previous } : undefined; },
          request: () => store().api('PATCH', base(d.id), { draft_status: 'in_review' }),
          undoRequest: () => window.deskV1QueuePiece(d.id, () => store().api('PATCH', base(d.id), { draft_status: previous })),
        }));
      });
      d.status = d.text !== d.saved ? 'Unsaved changes' : store().live() ? 'Saved' : 'Saved in demo only';
      if (review) {
        const v = channels().versions(d.fam).find((v) => d.selected.has(v.channelId) && v.state === 'needs_review');
        if (v) window.deskV1Nav('review', { campaignId: camp.id, projectId: camp.projectId, versionId: v.id });
        else d.status = 'No editable destinations to send to review.';
      }
    } catch (e) {
      d.failed = true; d.status = `Save incomplete: ${e.message || e}. Your text is kept here.`;
    } finally {
      d.busy = false; d.reviewing = false; update(d);
      const schedule = current(d)?.querySelector('[data-composer-schedule]');
      if (schedule && !schedule.hidden && d.fam) window.DeskV1ComposerWhen.render(schedule, d.fam);
    }
  }

  function render(el, params) {
    const camp = store().state().campaigns.find((c) => c.id === params.campaignId);
    if (!camp) { el.textContent = 'Campaign not found.'; return; }
    const d = draft(camp, params.familyId);
    mounted = { el, camp, d };
    const locked = d.fam?.archivedAt || (d.fam?.versions || []).some((v) => ['approved', 'scheduled'].includes(v.state) || channels().sent(v));
    d.locked = !!locked;
    el.innerHTML = `<section class="desk-v1-composer" data-composer>
      <div class="desk-v1-composer-top"><div class="desk-v1-composer-breadcrumb">Desk <span>›</span> ${esc(camp.plan.title || 'Campaign')} <span>›</span> <strong>${esc(d.fam?.title || 'New post')}</strong></div>
        <div class="desk-v1-composer-actions"><button type="button" class="btn-secondary" data-composer-save>Save draft</button><button type="button" class="btn-secondary" data-composer-when aria-expanded="false">When</button><button type="button" class="btn-add" data-composer-review>Send to Review</button></div></div>
      <div data-composer-schedule hidden></div>
      <div class="desk-v1-composer-sheet"><h2>Post to</h2><div data-composer-channels>${channels().html(camp, d)}</div>
      <label for="desk-v1-composer-text">Write</label><textarea id="desk-v1-composer-text" data-composer-text maxlength="100000" rows="12" placeholder="What would you like to share?" aria-describedby="desk-v1-composer-count" ${locked ? 'readonly' : ''}>${esc(d.text)}</textarea>
      ${locked ? '<p class="desk-v1-composer-hint">Approved posts must return to review before editing. Sent posts and archived pieces cannot be edited here.</p>' : ''}
      <div id="desk-v1-composer-count" data-composer-count class="desk-v1-composer-hint"></div></div>
      <p data-composer-status role="status" aria-live="polite"></p></section>`;
    const input = el.querySelector('[data-composer-text]');
    input.oninput = () => {
      d.text = input.value; d.status = 'Unsaved changes'; d.failed = false; update(d);
      if (!d.fam && d.text.trim()) save(camp, d, false);
    };
    el.querySelector('[data-composer-save]').onclick = () => save(camp, d, false);
    el.querySelector('[data-composer-review]').onclick = () => save(camp, d, true);
    el.querySelector('[data-composer-when]').onclick = () => {
      const host = el.querySelector('[data-composer-schedule]');
      host.hidden = !host.hidden;
      el.querySelector('[data-composer-when]').setAttribute('aria-expanded', String(!host.hidden));
      if (!host.hidden) window.DeskV1ComposerWhen.render(host, d.fam);
    };
    el.querySelector('[data-composer-channels]').onclick = (e) => {
      const b = e.target.closest('[data-composer-channel]');
      if (!b || b.disabled) return;
      const id = b.dataset.composerChannel;
      if (d.selected.has(id)) d.selected.delete(id); else d.selected.add(id);
      el.querySelector('[data-composer-channels]').innerHTML = channels().html(camp, d);
      d.status = 'Unsaved changes'; update(d);
      if (d.fam) save(camp, d, false);
    };
    update(d);
    window.DeskV1ComposerPreview.mount(el.querySelector('[data-composer]'), camp, d);
    if (!d.fam) input.focus({ preventScroll: true });
  }
  window.DeskV1Composer = { open };
  window.deskV1RenderComposer = render;
})();
