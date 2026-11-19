// What the chat does when a send did not go through (FE14, CV2 in
// docs/_review/2026-11-16_04_frontend.md). sendFollowup reaches these through
// window.mcSendRefusal, same as send-timeout.js / send-intent.js; top-level
// names here are module-private.

// Put the text the user typed back in the composer. A plain `inp.value = ...`
// fires no `input` event, and the modal rebuild (index.html refreshModalById)
// treats textareaValues as the source of truth for the kept composer node:
// an id with no entry is wiped to ''. sendFollowup deleted the entry before the
// POST, so write it again here (textareaValues is the SPA's global cache).
function restoreComposer(inp, message) {
  if (!inp) return;
  inp.value = message;
  if (inp.id) textareaValues[inp.id] = message;
  if (inp.id && window.syncComposerAction) window.syncComposerAction(inp.id);
}

// The optimistic bubble must not keep the delivered-looking `agent-echo` class
// once the server refused the message (a later "> " line wipes the first
// `.agent-echo`, so a stale one eats the retry's echo instead).
function markEchoFailed(echo, message) {
  if (!echo || !echo.isConnected) return;
  echo.classList.remove('agent-echo', 'agent-echo-pending', 'agent-echo-queued');
  echo.classList.add('agent-echo-failed');
  echo.style.opacity = '0.6';
  echo.textContent = `> [failed to send] ${message}`;
}

// _dispatchQuestionAnswer marks the card answered and clears the pending
// question BEFORE it calls sendFollowup, so on a refusal nothing could bring
// the form back. It notes what it consumed; sendFollowup takes the note once.
const answering = {};  // sessionId -> { container, qid, pq }

function noteQuestionAnswer(sessionId, container, pq) {
  answering[sessionId] = { container, qid: (container && container.dataset.qid) || '', pq: pq || null };
}

function takeQuestionAnswer(sessionId) {
  const note = answering[sessionId] || null;
  delete answering[sessionId];
  return note;
}

// The send that answered the card was refused: the agent is still waiting, so
// forget the answer (the answered-set otherwise suppresses every redraw until a
// turn_start that never comes) and draw the form again.
function reArmQuestion(projectId, sessionId, note) {
  if (!note) return;
  if (note.qid && _answeredQuestionIds[sessionId]) _answeredQuestionIds[sessionId].delete(note.qid);
  // renderAgentQuestion skips a form whose qid is already on screen, answered or not.
  if (note.container && note.container.isConnected) note.container.remove();
  const cached = agentStatusCache[sessionId];
  if (cached) {
    cached.waitingForQuestion = true;
    if (note.pq) {
      cached.pendingQuestions = (cached.pendingQuestions || []).filter(p => p.question_id !== note.pq.question_id);
      cached.pendingQuestions.push(note.pq);
    }
  }
  const hist = agentHistory.find(h => h.sessionId === sessionId);
  if (hist) hist.waitingForQuestion = true;
  if (note.pq) window.renderAgentQuestion(sessionId, projectId, note.pq.questions || [], note.pq.question_id || '');
}

// Everything a refused send needs, in one call. `delivered` is true when the
// reconcile found the prompt in the server's log anyway: nothing to undo then.
function settleRefusedSend({ projectId, sessionId, echo, message, note, delivered, error }) {
  if (delivered) return;
  markEchoFailed(echo, message);
  // A machine-built answer directive is not something the user typed; the
  // re-armed card is how they retry that one.
  const inp = document.getElementById(`agent-followup-${sessionId}`);
  if (!note && inp && !inp.value) restoreComposer(inp, message);
  reArmQuestion(projectId, sessionId, note);
  window.showToast(`Send failed: ${error || 'the server refused the message'}`, 4500);
}

window.mcSendRefusal = { restoreComposer, noteQuestionAnswer, takeQuestionAnswer, settleRefusedSend, reArmQuestion };
