// Harness registry for computer-use sessions. Every harness has the same small shape so the
// client and the tests never care which one they hold:
//
//   kind           'browser' | 'desktop'
//   supported      whether this build can perform steps of this kind on this computer
//   capabilities() the recorder actions it performs
//   perform()      step request → { ok, description, observation, result, evidence, error }
//   close()        release anything the harness holds
//
// Drivers, in order of preference: the Python device sidecar (`sidecar.js` → `vista_device`,
// browser-use / macOS-use observe+act, the same `observe()` the recorder stores frames with)
// when it passed its self-test on this computer; otherwise the in-process JS drivers —
// `browser.js` (a page in the recorder's own partition, over CDP; `browser-electron.js`
// supplies the window) and `desktop.js` (the front window's accessibility tree plus the real
// pointer/keyboard; `desktop-macos.js` supplies the backend). A kind without a driver here
// gets an `UnsupportedHarness`: it advertises no capability, so the server never offers a
// run of that kind to this device, and a step that arrives anyway is refused with
// `harness_unsupported` — the code the server treats as "leave this step for a person".
import { BrowserHarness } from './browser.js';
import { DesktopHarness } from './desktop.js';

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

// `openPage` (browser) and `desktopBackend` (desktop) are the platform pieces main.js
// resolved for this computer; either may be null, in which case that kind is unsupported.
// `sidecar` is `{ browser?, desktop? }` of `SidecarHarness`es that already ran `probe()`;
// one that is `supported` takes the kind.
export function defaultHarnesses({ demo = false, openPage = null, pointer = null, desktopBackend = null, settings, sidecar = {} } = {}) {
  const note = demo ? ' (demo mode: the step is reported back as unsupported so the run pauses for a person)' : '';
  const opts = settings ? { settings } : {};
  const pick = (kind, fallback) => (sidecar[kind]?.supported ? sidecar[kind] : fallback());
  return {
    browser: pick('browser', () =>
      openPage
        ? new BrowserHarness({ open: openPage, pointer, ...opts })
        : new UnsupportedHarness('browser', `The sandboxed browser harness is not available in this recorder build${note}.`, { demo }),
    ),
    desktop: pick('desktop', () =>
      desktopBackend
        ? new DesktopHarness({ backend: desktopBackend, ...opts })
        : new UnsupportedHarness('desktop', `The desktop harness is not available on this computer${note}.`, { demo }),
    ),
  };
}

export function capabilitiesOf(harnesses) {
  const out = {};
  for (const kind of HARNESS_KINDS) out[kind] = !!harnesses[kind]?.supported;
  return out;
}
