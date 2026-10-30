// C4: fresh saved drafts, with destination accounts bounded by the target plan.
(function () {
  const store = () => window.DeskV1Store;
  const copy = (v) => JSON.parse(JSON.stringify(v));
  const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2);
  const pending = new Set();
  let closePicker = null;

  // Demo projection only. Live copies are assembled under the backend store lock.
  function localDraft(source, campaign, id) {
    const seen = new Set();
    const versions = (source.versions || []).filter((v) => {
      if (!(campaign.plan.accounts || []).includes(v.channelId) || seen.has(v.channelId)
          || !store().state().channels.some((c) => c.id === v.channelId)
          || ['archived', 'skipped'].includes(v.state)) return false;
      seen.add(v.channelId); return true;
    }).map((v) => ({ id: 'cv-' + uid(), channelId: v.channelId, state: 'drafting', revision: 0,
      body: v.body || '', format: v.format, approved: null, claimsState: {},
      ...(v.claims ? { claims: v.claims.map((c) => ({ ...copy(c), text: c.original || c.text, verdict: c.source ? 'ok' : 'blocked' })) } : {}) }));
    return { id, campaignId: campaign.id, projectId: campaign.projectId, kind: 'post',
      title: (source.title || 'New post').slice(0, 193) + ' (copy)', body: source.body || '',
      assets: (source.assets || []).map((a) => ({ ...copy(a), id: 'asset-' + uid() })),
      versions, draft: { status: 'drafting' } };
  }

  async function duplicate(source, campaign) {
    if (pending.has(source.id)) return;
    pending.add(source.id);
    try {
      return await window.deskV1QueuePiece(source.id, async () => {
        const draft = localDraft(source, campaign, 'fam-copy-' + uid());
        const families = store().state().families;
        const result = await store().write({
          label: 'Duplicated post as a draft',
          apply: () => { if (!families.includes(draft)) families.push(draft); },
          unapply: () => { const i = families.indexOf(draft); if (i >= 0) families.splice(i, 1); },
          request: async () => {
            const saved = await store().api('POST', '/api/desk/pieces/' + encodeURIComponent(source.id) + '/duplicate', { id: draft.id, campaign_id: campaign.id });
            Object.keys(draft).forEach((key) => { delete draft[key]; }); Object.assign(draft, saved);
            return saved;
          },
          undoRequest: () => window.deskV1QueuePiece(draft.id, () => store().api('DELETE', '/api/desk/pieces/' + encodeURIComponent(draft.id))),
          repaint: () => window.deskV1RepaintWhat(),
        });
        if (result.ok) window.DeskV1Composer.open(campaign, draft);
        return result;
      });
    } catch (e) {
      window.DeskV1Kit.toast('Could not duplicate post: ' + (e.message || e));
    } finally { pending.delete(source.id); }
  }

  function campaignPicker(trigger, source, targets) {
    if (closePicker) closePicker();
    const picker = document.createElement('div');
    picker.className = 'desk-v1-addto-menu';
    picker.setAttribute('role', 'menu'); picker.setAttribute('aria-label', 'Duplicate to campaign');
    const host = trigger.parentElement;
    if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
    const close = (focus = true) => {
      picker.remove(); document.removeEventListener('click', outside); closePicker = null;
      if (focus && trigger.isConnected) trigger.focus();
    };
    const outside = (event) => { if (!picker.contains(event.target)) close(false); };
    closePicker = close;
    const projects = store().state().projects;
    for (const campaign of targets) {
      const button = document.createElement('button');
      button.type = 'button'; button.setAttribute('role', 'menuitem');
      button.textContent = `${campaign.plan.title || 'Untitled campaign'} · ${projects.find((p) => p.id === campaign.projectId)?.name || campaign.projectId || 'No project'}`;
      button.onclick = (event) => { event.stopPropagation(); close(); duplicate(source, campaign); };
      picker.appendChild(button);
    }
    picker.onkeydown = (event) => {
      const buttons = [...picker.querySelectorAll('button')], index = buttons.indexOf(document.activeElement);
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
      else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault(); (buttons[(index + (event.key === 'ArrowDown' ? 1 : buttons.length - 1)) % buttons.length]).focus();
      }
    };
    host.appendChild(picker); window.DeskV1Kit.placePopover(picker, trigger);
    document.addEventListener('click', outside);
    picker.querySelector('button')?.focus();
  }

  function menu(el, close, source, campaign, trigger) {
    if (source.kind !== 'post') return;
    const same = el.querySelector('[data-menu-dup]');
    same.disabled = pending.has(source.id);
    same.onclick = () => { close(); duplicate(source, campaign); };
    const other = document.createElement('button');
    other.type = 'button'; other.dataset.menuDupCampaign = ''; other.textContent = 'Duplicate to campaign…';
    const targets = store().state().campaigns.filter((c) => c.id !== campaign.id && c.state !== 'archived');
    other.disabled = !targets.length || pending.has(source.id);
    other.onclick = (event) => {
      // This click belongs to the removed parent menu, not the new picker.
      event.stopPropagation();
      close();
      campaignPicker(trigger, source, targets);
    };
    same.after(other);
  }
  window.DeskV1ComposerDuplicate = { menu };
})();
