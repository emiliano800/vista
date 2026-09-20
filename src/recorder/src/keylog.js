// Typed text, when the employee turns "Typed characters" on in Settings.
//
// With the setting off (the default) a key event carries only a count or the
// name of a non-printable key, and everything here yields nothing. With it on,
// the individual characters in the event log are stitched back into the lines
// that were actually typed, so the review can show "what went into this field"
// next to the clicks and copy/paste.
//
// Three rules hold whatever the setting says:
//   - a sign-in, payment or private window never contributes characters; the
//     recorder masks them at capture time and this module drops any that slip
//     through (an older log, a title that only became sensitive later),
//   - card, IBAN, SSN, e-mail and phone patterns are masked in the text even
//     when on-device redaction is off, and the entry is flagged,
//   - the text is never sent to a model; only counts and flags are.
export const SENSITIVE_TITLE = /\b(?:sign[ -]?in|log[ -]?in|login|password|passcode|authenticat|verify your identity|two[ -]factor|2fa|one[ -]time code|checkout|payment|billing|credit card|bank|banking|payroll|salary|tax return|medical|patient)\b/i;
export const SENSITIVE_URL = /(?:\/login|\/signin|\/auth|\/sso|\/oauth|\/checkout|\/payment|\/billing|accounts\.google|login\.microsoft|okta\.com|auth0\.com)/i;

const SECRET_PATTERNS = [
  [/[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}/g, '[IBAN]', 'iban'],
  [/\b(?:\d[ -]?){12,18}\d\b/g, '[CARD]', 'card_number'],
  [/\b\d{3}-\d{2}-\d{4}\b/g, '[SSN]', 'ssn'],
  [/[\w.+-]+@[\w-]+\.[\w.-]+/g, '[EMAIL]', 'email'],
  [/\+?\d[\d\s().-]{7,}\d/g, '[PHONE]', 'personal'],
];

const MAX_TEXT = 200;
const ts = (e) => Date.parse(e.timestamp);

export function isSensitiveWindow({ title = '', url = '' } = {}) {
  return title === '(private)' || SENSITIVE_TITLE.test(title ?? '') || SENSITIVE_URL.test(url ?? '');
}

// Mask anything that looks like an identifier or account number; report which
// kinds were found so the entry can be flagged.
export function maskSecrets(text) {
  let out = String(text ?? '');
  const kinds = [];
  for (const [re, token, kind] of SECRET_PATTERNS) {
    if (re.test(out)) kinds.push(kind);
    re.lastIndex = 0;
    out = out.replace(re, token);
  }
  return { text: out, kinds };
}

// Replay one burst of key events into the line it produced. Backspace deletes,
// Space and Tab are characters, arrows and Enter just end the edit.
function replay(keys) {
  let out = '';
  for (const k of keys) {
    const named = k.payload?.key;
    if (named === 'Backspace') out = out.slice(0, -1);
    else if (named === 'Space') out += ' ';
    else if (named === 'Tab') out += '\t';
    else if (named) continue;
    else out += k.text ?? '';
  }
  return out.replace(/\s+/g, ' ').trim().slice(0, MAX_TEXT);
}

/**
 * Stitch key events back into typed entries, one per burst of typing in the
 * same window (`gapMs` of quiet, Enter or Tab ends a burst).
 * @returns {Array<{at, app, title, url, keys, chars, text, masked, kinds}>}
 */
export function typedEntries(events, { gapMs = 1500 } = {}) {
  const out = [];
  let cur = null;
  for (const e of [...events].sort((a, b) => ts(a) - ts(b))) {
    if (e.event_type !== 'key') continue;
    const t = ts(e);
    const same = cur && cur.app === e.app && cur.title === e.window_title && !cur.closed && t - cur.end <= gapMs;
    if (!same) {
      cur = { start: t, end: t, app: e.app, title: e.window_title, url: e.url, keys: [] };
      out.push(cur);
    }
    cur.end = t;
    cur.keys.push(e);
    const named = e.payload?.key;
    if (named === 'Enter' || named === 'Tab') cur.closed = true;
  }
  return out
    .map((b) => {
      const sensitive = isSensitiveWindow({ title: b.title, url: b.url });
      const raw = sensitive ? '' : replay(b.keys);
      const { text, kinds } = maskSecrets(raw);
      return {
        at: new Date(b.start).toISOString(),
        app: b.app,
        title: b.title,
        url: b.url ?? '',
        keys: b.keys.length,
        chars: text.length,
        text,
        masked: sensitive || (!text && b.keys.length > 0),
        kinds,
      };
    })
    .filter((x) => x.text || x.masked);
}

// What the review card shows and what the report carries: counts always, the
// lines themselves only when characters were captured.
export function typingSummary(events, { limit = 20 } = {}) {
  const entries = typedEntries(events);
  const captured = entries.filter((x) => !x.masked);
  return {
    enabled: captured.length > 0,
    entries: entries.length,
    captured: captured.length,
    masked: entries.length - captured.length,
    chars: captured.reduce((s, x) => s + x.chars, 0),
    lines: captured.slice(0, limit).map(({ at, app, title, text, keys, kinds }) => ({ at, app, title, text, keys, kinds })),
  };
}

// One flag per typed line that contained an account or identity number, so the
// employee decides before it is submitted.
export function typedFlags(events) {
  const out = [];
  for (const e of typedEntries(events)) {
    if (!e.kinds.length) continue;
    out.push({
      kind: e.kinds[0],
      at: e.at,
      app: e.app,
      title: e.title,
      reason: `${e.kinds.join(', ').replace(/_/g, ' ')} typed into "${String(e.title ?? e.app).slice(0, 60)}" — masked in the log`,
    });
  }
  return out;
}
