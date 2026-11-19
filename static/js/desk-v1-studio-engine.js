// Desk v1 Studio: the Model / Effort pickers in the Studio agent box (Ron 2026-11-18:
// "I see the additions under the campaign managers, but missing those settings under
// the studio"). Window-bridged, no `import`. The pickers themselves are
// desk-v1-how-model.js's (mounted on a target); this file only says what a Studio
// item's target is.
//
//   standalone item (a video or an article with no campaign): the pick is the item's
//     own `engine = {provider?, model?, effort?}`, editable, with Undo like its agent.
//   item that belongs to a campaign: the pick is the CAMPAIGN's (`how.engine`), shown
//     read-only with where to change it. The item's own pick is not consulted, on the
//     server either (mc/desk_studio_engine.py), so what is shown is what runs.
//
// A video's pick is held by the page like its agent is and sent with each storyboard
// call (`engine` beside `agent`); an article's is saved with the article.
(function () {
  'use strict';

  const HOST = '<div class="desk-v1-sb-model" data-studio-model-host></div>';
  function hostHTML() { return HOST; }

  function _set(item, pick) {
    if (pick && Object.keys(pick).length) item.engine = pick; else delete item.engine;
  }

  // A video: `ctx` = {fam, camp}; `o` = {standalone, agentRef(), register(), repaint()}.
  function mountVideo(box, ctx, o) {
    const host = box && box.querySelector('[data-studio-model-host]');
    if (!host || !window.DeskV1HowModel) return;
    const { fam, camp } = ctx;
    const base = { projectId: (camp && camp.projectId) || '', agentRef: o.agentRef() || null, noun: 'item' };
    if (!o.standalone) {
      window.DeskV1HowModel.mountTarget(host, Object.assign(base, {
        noun: 'campaign', get: () => (camp && camp.how && camp.how.engine) || null, readOnlyBy: "on the campaign's Brief",
      }));
      return;
    }
    window.DeskV1HowModel.mountTarget(host, Object.assign(base, {
      get: () => fam.engine || null,
      set: (next, label) => {
        const prev = fam.engine ? Object.assign({}, fam.engine) : null;
        o.register();
        window.DeskV1Kit.commandBus.run({
          label,
          do: () => { _set(fam, next); o.repaint(); },
          undo: () => { _set(fam, prev); o.repaint(); },
        });
      },
    }));
  }

  // The pick to send with a storyboard call: only a standalone item has one of its own.
  function videoPick(ctx, standalone) {
    return standalone && ctx && ctx.fam && ctx.fam.engine ? Object.assign({}, ctx.fam.engine) : null;
  }

  // An article: `a` is the page's article record, `o` = {save(), repaint()}. The
  // picker goes under the agent's name in the conversation box.
  function mountArticle(aside, a, o) {
    const head = aside && aside.querySelector('.desk-thread-head');
    if (!head || !window.DeskV1HowModel) return;
    head.insertAdjacentHTML('afterend', HOST);
    const host = aside.querySelector('[data-studio-model-host]');
    const store = window.DeskV1Store.state();
    const project = (store.projects || []).find((p) => p.id === a.projectId) || null;
    const camp = a.campaignId ? (store.campaigns || []).find((c) => c.id === a.campaignId) : null;
    const base = {
      projectId: a.projectId || '', noun: 'item',
      agentRef: window.DeskV1Kit.deskAgentRef({ project }) || null,
    };
    if (camp) {
      window.DeskV1HowModel.mountTarget(host, Object.assign(base, {
        noun: 'campaign', get: () => (camp.how && camp.how.engine) || null, readOnlyBy: "on the campaign's Brief",
      }));
      return;
    }
    window.DeskV1HowModel.mountTarget(host, Object.assign(base, {
      get: () => a.engine || null,
      set: (next, label) => {
        const prev = a.engine ? Object.assign({}, a.engine) : null;
        window.DeskV1Kit.commandBus.run({
          label,
          do: () => { _set(a, next); return o.save().then((ok) => { if (!ok) { _set(a, prev); } o.repaint(); }); },
          undo: () => { _set(a, prev); return o.save().then(() => o.repaint()); },
        });
      },
    }));
  }

  window.DeskV1StudioEngine = { hostHTML, mountVideo, mountArticle, videoPick };
})();
