// Highlighting for model-written text (section explanations, the session
// summary, workflow reasons) so the eye lands on the parts that matter before
// the sentence is read: the apps and documents involved, the numbers, and
// anything about sensitive content.
//
// Classic script on purpose — the dashboard is one inline script, and the unit
// test evaluates this file directly.
(function (root) {
  const RISK =
    /\b(?:passwords?|passcode|credentials?|sign[- ]?in|log[- ]?in|sensitive|confidential|personal (?:data|information)|private|redact(?:ed|ion)?|masked?|card numbers?|credit cards?|bank(?:ing)? details?|IBAN|SSN|social security|salary|payroll|tax returns?|medical)\b/i;
  const FILE = /\b[\w][\w.()-]{0,60}\.(?:pdf|xlsx?|csv|tsv|docx?|pptx?|txt|md|json|xml|msg|eml|zip)\b/i;
  const NUM = /(?:[$€£]\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:%|×|x\b|minutes?|mins?|seconds?|secs?|hours?|hrs?|times?|files?|documents?|invoices?|steps?|pastes?|clicks?|keys?|sessions?|records?)|\b\d[\d,]*(?:\.\d+)?\b)/i;
  const QUOTE = /(?:"[^"\n]{1,60}"|“[^”\n]{1,60}”|'[^'\n]{2,60}')/;

  const esc = (s) =>
    String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const escRe = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  /**
   * Escape `text` and wrap the notable parts in <mark class="hl hl-…">.
   * `terms` are session-specific words worth spotting (app names, window
   * titles, workflow apps); everything else is matched by shape.
   * @returns {string} HTML
   */
  function highlightHtml(text, terms = []) {
    const s = String(text ?? '');
    if (!s) return '';
    const words = [...new Set(terms.map((t) => String(t ?? '').trim()).filter((t) => t.length > 2))]
      .sort((a, b) => b.length - a.length)
      .slice(0, 40);
    const parts = [
      ['risk', RISK],
      ['file', FILE],
      ['quote', QUOTE],
      ...(words.length ? [['app', new RegExp(`\\b(?:${words.map(escRe).join('|')})\\b`, 'i')]] : []),
      ['num', NUM],
    ];
    const re = new RegExp(parts.map(([, r]) => `(${r.source})`).join('|'), 'gi');
    let out = '';
    let last = 0;
    for (const m of s.matchAll(re)) {
      const kind = parts[m.slice(1).findIndex((g) => g !== undefined)][0];
      out += esc(s.slice(last, m.index)) + `<mark class="hl hl-${kind}">${esc(m[0])}</mark>`;
      last = m.index + m[0].length;
    }
    return out + esc(s.slice(last));
  }

  root.highlightHtml = highlightHtml;
})(typeof globalThis !== 'undefined' ? globalThis : this);
