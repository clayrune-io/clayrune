// Shared read-only document renderer for plan files and chat file links.
function renderMarkdownDocument(raw) {
  let bodyHTML = '';
  let tableLines = [];
  function flushTable() {
    if (tableLines.length === 0) return;
    if (isPipeTable(tableLines)) {
      bodyHTML += `<div class="hl-table">${buildPipeTable(tableLines)}</div>`;
    } else {
      bodyHTML += `<div class="hl-table-pre">${tableLines.map(l => formatTableLine(esc(l))).join('\n')}</div>`;
    }
    tableLines = [];
  }
  for (const line of raw.split('\n')) {
    if (isTableLine(line)) {
      tableLines.push(line);
    } else if (tableLines.length > 0 && line.trim() === '') {
      tableLines.push(line);
    } else {
      flushTable();
      bodyHTML += `<div class="agent-line">${formatAgentText(line)}</div>`;
    }
  }
  flushTable();
  return bodyHTML;
}

window.renderMarkdownDocument = renderMarkdownDocument;
