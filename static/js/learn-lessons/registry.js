// ── Learn lesson registry ────────────────────────────────────────────────────
// Data only: it imports each lesson's descriptor from its own file and lists
// them in hub order. To add a lesson, write learn-lessons/<name>.js and add one
// import and one entry below; nothing in static/js/learn.js changes.
//
// A lesson module exports one descriptor object. The engine reads these fields:
//   id, version, title, subtitle, meta, chip, offer, done, steps[]
//   backLabel, returnLabel, surfaceKey, leftMsg   the window the lesson lives in
//   offerOn?      engine event that makes the lesson offer itself once ('floor-opened')
//   rewindMsg?    copy shown when prereqRewind sends the user back a step
// and calls these hooks, each as hook(ctx) (the engine's state and DOM helpers,
// see _ctx() in learn.js: { S, step, P, dom, tick, renderBubble }):
//   surfaceFront(ctx) -> bool     is the lesson's window frontmost
//   openSurface(ctx)              bring that window up (navigation, never the taught action)
//   resolveStep(ctx) -> {el, cue?, dest?, instant?} | {error}
//   prepare(ctx)                  per-step navigation and fixtures, never the action
//   verify(ctx) -> {ok, evidence} | null     authoritative evidence for the step
//   stepReady?(ctx)               after prepare, before the step goes live (baselines)
//   prereqRewind?(ctx) -> index | -1   earlier step to redo when its setup is gone
//   watchTick?(ctx)               each tick, before the cue is drawn
//   hire?(ctx), fallback?(ctx)    the explicit controls for steps flagged hire / fallback
//   cleanup?(ctx)                 on every leave, whichever lesson ran (shared practice state)
import { floorLesson } from './floor.js';
import { workflowsLesson } from './workflows.js';

const LESSON_LIST = [floorLesson, workflowsLesson];

export const LESSONS = Object.fromEntries(LESSON_LIST.map((l) => [l.id, l]));
export const LESSON_ORDER = LESSON_LIST.map((l) => l.id);
// The lesson `progress()` and local telemetry fall back to when none is running,
// and the one the Floor's own header link points at.
export const DEFAULT_LESSON_ID = floorLesson.id;
