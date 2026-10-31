// Clearing a version's time is supported by M18. Keep the approval record:
// scheduled + null is not due (desk_tick._due_candidates); Undo restores time.
(function () {
  function clear(versionId, repaint) {
    const store = window.DeskV1Store, fx = store.state();
    const fam = fx.families.find((f) => f.versions.some((v) => v.id === versionId));
    const v = fam?.versions.find((v) => v.id === versionId);
    if (!v || fam.archivedAt || window.DeskV1ComposerChannels.sent(v)) return false;
    const previous = { publishAt: v.publishAt, publishedAt: v.publishedAt,
      schedule: fx.calendarSchedule?.[v.id], detail: fx.reviewDetail?.[v.id]?.whenISO };
    const patch = (time) => window.deskV1QueuePiece(fam.id, () => store.api('PATCH',
      `/api/desk/pieces/${encodeURIComponent(fam.id)}/versions/${encodeURIComponent(v.id)}`, { scheduled_at: time }));
    return store.write({
      label: `Unscheduled “${fam.title}”`,
      apply: () => {
        delete v.publishAt; delete v.publishedAt;
        if (fx.calendarSchedule) delete fx.calendarSchedule[v.id];
        if (fx.reviewDetail?.[v.id]) delete fx.reviewDetail[v.id].whenISO;
        repaint();
      },
      unapply: () => {
        for (const key of ['publishAt', 'publishedAt']) {
          if (previous[key] === undefined) delete v[key]; else v[key] = previous[key];
        }
        if (previous.schedule !== undefined) (fx.calendarSchedule ||= {})[v.id] = previous.schedule;
        if (previous.detail !== undefined) fx.reviewDetail[v.id].whenISO = previous.detail;
      },
      repaint,
      request: () => patch(null),
      undoRequest: () => patch(previous.publishAt || null),
    });
  }
  window.DeskV1CalendarUnschedule = { clear };
})();
