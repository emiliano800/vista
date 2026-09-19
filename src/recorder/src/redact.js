// On-device redaction. Mirrors src/taskmining/preprocess.py so nothing sensitive is
// ever written to disk; the Python pipeline re-applies the same rules server-side.
const PATTERNS = [
  [/[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}/g, '[IBAN]'],
  [/\b(?:\d[ -]?){13,19}\b/g, '[CARD]'],
  [/[\w.+-]+@[\w-]+\.[\w.-]+/g, '[EMAIL]'],
  [/\b\d{3}-\d{2}-\d{4}\b/g, '[SSN]'],
  [/\+?\d[\d\s().-]{7,}\d/g, '[PHONE]'],
];

export function redactText(text) {
  if (!text) return '';
  let out = String(text);
  for (const [re, token] of PATTERNS) out = out.replace(re, token);
  return out;
}

export function redactEvent(ev) {
  return {
    ...ev,
    window_title: redactText(ev.window_title),
    text: redactText(ev.text),
    element: redactText(ev.element),
  };
}
