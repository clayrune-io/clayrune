import './prompt-time-tag.js';

// Display metadata only: a narration run ends at any non-narration sibling
// (prompt, tool, card, divider). Attribution is transparent. No session state
// survives a DOM rebuild, and no missing timestamp is replaced with "now".
export function agentTimeAttrs(ts) {
  const html = window.promptTimeTagHTML(ts);
  const iso = html.match(/datetime="([^"]+)"/)?.[1] || '';
  return ` data-agent-narration="" data-agent-ts="${iso}"`;
}

function timeElement(ts) {
  const template = document.createElement('template');
  template.innerHTML = window.promptTimeTagHTML(ts);
  const time = template.content.firstElementChild;
  if (!time) return null;
  time.className = 'agent-time-tag';
  // Generated content is visible but cannot enter textContent/innerText,
  // native copy, plan extraction or stop-hook draft similarity checks.
  time.dataset.label = time.textContent;
  time.setAttribute('aria-label', time.title);
  time.textContent = '';
  return time;
}

function isNarration(node) { return node?.hasAttribute('data-agent-narration'); }
function transparent(node) { return node?.classList.contains('msg-attr'); }

function stampRun(nodes) {
  const ts = nodes.find(n => n.dataset.agentTs)?.dataset.agentTs;
  const host = nodes.find(n => n.textContent.trim() || n.querySelector('img, svg, table, iframe'));
  if (!ts || !host) return;
  if (!host.querySelector(':scope > .agent-time-tag')) host.appendChild(timeElement(ts));
}

function decorate(container) {
  let run = [];
  const flush = () => { stampRun(run); run = []; };
  for (const node of container.children) {
    if (isNarration(node)) run.push(node);
    else if (!transparent(node)) {
      flush();
      // Existing collapse/interim wrappers keep their contents and behavior.
      if (node.matches('.agent-interim-wrap, .draft-block, .plan-hidden-block')) decorate(node);
    }
  }
  flush();
}

export function agentTimeTagsHTML(html) {
  const template = document.createElement('template');
  template.innerHTML = html;
  decorate(template.content);
  return template.innerHTML;
}

export function stampAgentNarration(node, ts) {
  node.setAttribute('data-agent-narration', '');
  if (!node.dataset.agentTs) {
    node.dataset.agentTs = agentTimeAttrs(ts).match(/data-agent-ts="([^"]*)"/)[1];
  }
  const run = [];
  for (let n = node; n; n = n.previousElementSibling) {
    if (isNarration(n)) run.unshift(n);
    else if (!transparent(n)) break;
  }
  stampRun(run);
}
