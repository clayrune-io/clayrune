// Permission/hook denial log lines. Native details works in live and history
// renders; transcript strings enter DOM nodes only through textContent.
const DENIED_LINE_RE = /^\[denied: ([^\]\r\n]+)\](?: ([^\r\n]*))?$/;
const lastLiveChip = new WeakMap();

function deniedLine(line) {
  const match = DENIED_LINE_RE.exec(line);
  return match ? { tool: match[1], command: match[2] || '' } : null;
}

function newChip() {
  const chip = document.createElement('details');
  chip.className = 'denied-chip';
  chip.appendChild(document.createElement('summary'));
  chip.appendChild(document.createElement('ul'));
  return chip;
}

function appendDenial(chip, denial) {
  const row = document.createElement('li');
  const tool = document.createElement('span');
  tool.className = 'denied-chip-tool';
  tool.textContent = denial.tool;
  row.appendChild(tool);
  if (denial.command) {
    const command = document.createElement('code');
    command.textContent = denial.command;
    row.appendChild(command);
  }
  const list = chip.querySelector('ul');
  list.appendChild(row);
  const count = list.children.length;
  chip.querySelector('summary').textContent = count === 1 ? '1 command blocked' : `${count} commands blocked`;
}

// Read one consecutive run in the cold renderer. Serialize DOM-built text,
// rather than interpolating the untrusted tool or command into markup.
function deniedChipRun(lines, start) {
  if (!deniedLine(lines[start])) return null;
  const chip = newChip();
  let end = start;
  for (; end < lines.length; end++) {
    const denial = deniedLine(lines[end]);
    if (!denial) break;
    appendDenial(chip, denial);
  }
  return { html: chip.outerHTML, end: end - 1 };
}

// Called for every live log line, including invisible markers: those still
// break a consecutive run. A cold-rendered trailing chip can accept live lines.
function handleDeniedLine(text, container) {
  const denial = deniedLine(text);
  if (!denial) {
    lastLiveChip.set(container, null);
    return false;
  }
  let chip = lastLiveChip.has(container) ? lastLiveChip.get(container) : container.lastElementChild;
  if (!chip || chip !== container.lastElementChild || !chip.classList.contains('denied-chip')) {
    chip = newChip();
    container.appendChild(chip);
  }
  appendDenial(chip, denial);
  lastLiveChip.set(container, chip);
  return true;
}

window.deniedChipRun = deniedChipRun;
window.handleDeniedLine = handleDeniedLine;
