// Shared information-only setup; unknown options now use options-step.
import { registerReferenceStep } from './desk-v1-connect-reference-step.js';
registerReferenceStep(window.DeskV1ConnectWizard);
window.DeskV1ConnectUnknownStep = { unknown: info => !!info && !info.service?.recognised, details: () => '', bindDetails: () => {} };
