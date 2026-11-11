// Exercise real scroll containers at the test viewport; never enlarge it to hide clipping.
export async function mailLayout(page, root, tag, check, shots, touchTargets = true) {
  const issues = await page.locator(root).evaluate(async (host, touchTargets) => {
    const issues = [];
    if (document.documentElement.scrollWidth > innerWidth + 1) issues.push('document overflow');
    for (const el of [host, ...host.querySelectorAll('button, input, .am-value, .am-outcome, .am-setup-error, .am-setup-blocked')]) {
      if (!el.getClientRects().length) continue;
      const name = el.dataset.amAction || el.dataset.amSetup || el.dataset.am || el.className;
      if (el.scrollWidth > el.clientWidth + 1) issues.push(`${name}: horizontal clipping`);
      if (!el.matches('button, input')) continue;
      el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
      // Modal movement is animated; hit-test its settled position.
      await new Promise((r) => setTimeout(r, 30));
      const b = el.getBoundingClientRect();
      const hit = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
      if (b.top < -1 || b.bottom > innerHeight + 1 || b.left < -1 || b.right > innerWidth + 1 || !el.contains(hit))
        issues.push(`${name}: unreachable (${Math.round(b.top)}..${Math.round(b.bottom)})`);
      if (touchTargets && innerWidth <= 960 && b.height < 40) issues.push(`${name}: touch height ${Math.round(b.height)}`);
    }
    return issues;
  }, touchTargets);
  check(issues.length === 0, `${tag}: controls reachable, text unclipped${touchTargets ? ', mobile targets >=40px' : ''}`, `${tag}: ${issues.join('; ')}`);
  const name = tag.replace(/[^a-z0-9_-]+/gi, '-');
  await page.screenshot({ path: `${shots}/${name}.png` });
}

export async function mailWizardFooter(page, tag, check) {
  const result = await page.locator('#setup-overlay .wt-card').evaluate((card) => {
    const body = card.querySelector('.wt-body');
    const footer = card.querySelector('.wt-actions');
    // At the start of a long step the navigation must already be visible.
    card.scrollTop = 0;
    body.scrollTop = 0;
    const c = card.getBoundingClientRect(), f = footer.getBoundingClientRect();
    const hit = document.elementFromPoint(f.right - 15, f.top + f.height / 2);
    body.scrollTop = body.scrollHeight;
    const pinned = Math.abs(footer.getBoundingClientRect().bottom - f.bottom) <= 1;
    body.scrollTop = 0;
    return { top: f.top, bottom: f.bottom, cardBottom: c.bottom,
      visible: footer.contains(hit), pinned, gap: c.bottom - f.bottom, height: innerHeight };
  });
  check(result.visible && result.pinned && result.top >= 0 && result.bottom <= result.height && result.gap >= 0 && result.gap <= 30,
    `${tag}: wizard navigation stays at card bottom`, `${tag}: wizard footer ${JSON.stringify(result)}`);
}
