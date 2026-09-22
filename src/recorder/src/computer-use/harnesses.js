// Harness registry for computer-use sessions. Every harness has the same small shape so the
// client and the tests never care which one they hold:
//
//   kind        'browser' | 'desktop'
//   supported   whether this build can perform steps of this kind on this computer
//   perform()   step request → { ok, description, observation, result, evidence, error }
//   close()     release anything the harness holds
//
// This build ships *placeholder* harnesses: they advertise no capability, so the server never
// offers a browser/desktop run to this device, and if a step arrives anyway they refuse it with
// `harness_unsupported` — the same code the server already treats as "leave this step for a
// person". The protocol, consent, limits, logging and kill switch are exercised end to end
// against these; the drivers that actually operate a page or the desktop are a separate
// change (see README "Computer use sessions").

export const HARNESS_KINDS = ['browser', 'desktop'];

export class UnsupportedHarness {
  // `demo: true` advertises the kind anyway so the whole protocol (offer → consent → step →
  // refusal → pause for a person) can be walked through locally without a driver. Never on
  // by default: a device must not attract runs it cannot perform.
  constructor(kind, reason, { demo = false } = {}) {
    this.kind = kind;
    this.supported = demo;
    this.demo = demo;
    this.reason = reason;
  }

  capabilities() {
    return [];
  }

  async perform(step) {
    return {
      ok: false,
      description: `${step.action} refused: ${this.reason}`,
      observation: null,
      result: null,
      evidence: null,
      error: { code: 'harness_unsupported', message: this.reason },
    };
  }

  async close() {}
}

export function defaultHarnesses({ demo = false } = {}) {
  const note = demo ? ' (demo mode: the step is reported back as unsupported so the run pauses for a person)' : '';
  return {
    browser: new UnsupportedHarness('browser', `The sandboxed browser harness is not included in this recorder build${note}.`, { demo }),
    desktop: new UnsupportedHarness('desktop', `The desktop harness is not included in this recorder build${note}.`, { demo }),
  };
}

export function capabilitiesOf(harnesses) {
  const out = {};
  for (const kind of HARNESS_KINDS) out[kind] = !!harnesses[kind]?.supported;
  return out;
}
