// Device-side safety for computer-use sessions. Pure: no Electron, no network.
//
// The recorder never plans. It performs exactly the step the server sent, and only when
// that step passes these checks: the harness kind was consented to and exists here, the
// session's own step/time limits are not exceeded (mirrored locally so a misbehaving server
// cannot push past them), the action is one of a closed set with a well-formed value, and a
// targeted action names an element from the observation the server actually saw last.
import { isSensitiveWindow } from '../keylog.js';
import { DEFAULT_SETTINGS } from '../recorder.js';

export const ACTIONS = new Set(['observe', 'navigate', 'click', 'type', 'press', 'extract', 'screenshot', 'wait', 'open_app']);
export const TARGETED = new Set(['click', 'type', 'extract']);
export const KEYS = {
  Enter: { key: 'Enter', code: 'Enter', keyCode: 13, mac: 36 },
  Tab: { key: 'Tab', code: 'Tab', keyCode: 9, mac: 48 },
  Escape: { key: 'Escape', code: 'Escape', keyCode: 27, mac: 53 },
  Backspace: { key: 'Backspace', code: 'Backspace', keyCode: 8, mac: 51 },
  ArrowUp: { key: 'ArrowUp', code: 'ArrowUp', keyCode: 38, mac: 126 },
  ArrowDown: { key: 'ArrowDown', code: 'ArrowDown', keyCode: 40, mac: 125 },
  ArrowLeft: { key: 'ArrowLeft', code: 'ArrowLeft', keyCode: 37, mac: 123 },
  ArrowRight: { key: 'ArrowRight', code: 'ArrowRight', keyCode: 39, mac: 124 },
};
export const MAX_TYPE_LENGTH = 2000;
export const MAX_WAIT_MS = 10000;
export const APP_NAME = /^[A-Za-z0-9 .'&-]{1,64}$/;
export const CONSENT_VERSION = 'computer-use-v1';

export class PolicyError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

export function isPrivateWindow(app, title, settings = DEFAULT_SETTINGS) {
  const a = String(app ?? '').toLowerCase();
  const t = String(title ?? '').toLowerCase();
  const privateApps = settings.privateApps ?? DEFAULT_SETTINGS.privateApps;
  const privateTitles = settings.privateTitles ?? DEFAULT_SETTINGS.privateTitles;
  return (
    privateApps.some((p) => a.includes(String(p).toLowerCase())) ||
    privateTitles.some((p) => t.includes(String(p).toLowerCase())) ||
    isSensitiveWindow({ title: String(title ?? '') })
  );
}

// `session` is the server's view of the session; `local` is what this device knows:
// { capabilities: {browser, desktop}, stepsDone, startedAt (ms), lastObservationId, now() }.
export function validateStep(step, session, local) {
  if (!step || typeof step !== 'object') throw new PolicyError('invalid_value', 'Step must be an object.');
  const kinds = new Set(session?.harness_kinds ?? []);
  if (!kinds.has(step.harness) || !local.capabilities?.[step.harness])
    throw new PolicyError('disallowed_harness', `This session may not use the ${step.harness} harness on this computer.`);
  const limits = session?.limits ?? {};
  if (Number.isFinite(limits.max_steps) && local.stepsDone >= limits.max_steps)
    throw new PolicyError('step_limit', `The session's step limit (${limits.max_steps}) has been reached on this computer.`);
  const now = local.now ? local.now() : Date.now();
  if (Number.isFinite(limits.max_runtime_seconds) && local.startedAt && now - local.startedAt > limits.max_runtime_seconds * 1000)
    throw new PolicyError('runtime_limit', `The session's runtime limit (${limits.max_runtime_seconds} s) has passed on this computer.`);
  if (!ACTIONS.has(step.action)) throw new PolicyError('invalid_value', `Unknown action ${JSON.stringify(step.action)}.`);
  switch (step.action) {
    case 'navigate':
      if (typeof step.value !== 'string' || !/^https?:\/\/\S+$/i.test(step.value))
        throw new PolicyError('invalid_value', 'navigate needs an http(s) address.');
      break;
    case 'press':
      if (!KEYS[step.value]) throw new PolicyError('invalid_value', `press needs one of ${Object.keys(KEYS).join(', ')}.`);
      break;
    case 'wait':
      if (!Number.isFinite(Number(step.value)) || Number(step.value) < 0 || Number(step.value) > MAX_WAIT_MS)
        throw new PolicyError('invalid_value', `wait needs 0..${MAX_WAIT_MS} ms.`);
      break;
    case 'type':
      if (typeof step.value !== 'string' || step.value.length === 0 || step.value.length > MAX_TYPE_LENGTH)
        throw new PolicyError('invalid_value', `type needs a string of at most ${MAX_TYPE_LENGTH} characters.`);
      break;
    case 'open_app':
      if (typeof step.value !== 'string' || !APP_NAME.test(step.value)) throw new PolicyError('invalid_value', 'open_app needs a plain application name.');
      break;
    case 'extract':
      if (step.value != null && !['table', 'text'].includes(step.value)) throw new PolicyError('invalid_value', 'extract mode must be table or text.');
      break;
    default:
      break;
  }
  if (TARGETED.has(step.action) && step.action !== 'extract' && step.target_id == null)
    throw new PolicyError('invalid_value', `${step.action} needs a target.`);
  if (step.target_id != null) {
    if (!step.observation_id) throw new PolicyError('invalid_value', 'A targeted step must cite the observation its target came from.');
    if (step.observation_id !== local.lastObservationId)
      throw new PolicyError('stale_observation', 'The target belongs to an observation that is no longer current; observe again.');
  }
  return true;
}
