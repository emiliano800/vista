// An invoice re-keyed from a PDF into QuickBooks, then saved, then a second bill.
// Shared by the compiler tests and `scripts/plan-fixture.mjs`, which writes the compiled
// graph to tests/fixtures/plan_invoice_v3.json for the backend and web tests.

export const T0 = Date.parse('2026-09-19T09:00:00Z');
export const at = (ms) => new Date(T0 + ms).toISOString();
export const ev = (ms, event_type, app, extra = {}) => ({ timestamp: at(ms), event_type, app, window_title: '', url: '', text: '', payload: {}, ...extra });

export function invoiceEvents() {
  return [
    ev(0, 'focus', 'Preview', { window_title: 'INV-1042 ACME.pdf' }),
    ev(1000, 'copy', 'Preview', { element: 'Total', text: '1,250.00', payload: { clip_hash: 'h-total', chars: 8 } }),
    ev(2000, 'focus', 'QuickBooks', { window_title: 'Bills - ACME Corp', url: 'https://qbo.example/bills/new' }),
    ev(3000, 'click', 'QuickBooks', { payload: { x: 412, y: 233 } }),
    ev(4000, 'paste', 'QuickBooks', { element: 'Amount', text: '1,250.00', payload: { clip_hash: 'h-total' } }),
    ev(5000, 'key', 'QuickBooks', { text: 'A' }),
    ev(5100, 'key', 'QuickBooks', { text: 'C' }),
    ev(5200, 'key', 'QuickBooks', { text: 'M' }),
    ev(6000, 'key', 'QuickBooks', { payload: { key: 'Enter' } }),
    ev(7000, 'shortcut', 'QuickBooks', { text: 'Cmd+S' }),
    // A second bill: `Amount` recurs across screens (control vocabulary); `Total` and `Memo` do not.
    ev(8000, 'focus', 'QuickBooks', { window_title: 'Bills - Beta Ltd', url: 'https://qbo.example/bills/new' }),
    ev(9000, 'paste', 'QuickBooks', { element: 'Amount', text: '42.00', payload: { clip_hash: 'h-other' } }),
    ev(9500, 'paste', 'QuickBooks', { element: 'Memo', text: 'Beta Ltd invoice 77', payload: { clip_hash: 'h-memo' } }),
  ];
}

export const invoiceFiles = [{ id: 'abcdef123456', name: 'INV-1042 ACME.pdf', ext: '.pdf', intervals: [{ start: T0, end: T0 + 1500, app: 'Preview' }] }];

// Strings from the recording that must never appear in a compiled graph.
// Coordinates are checked structurally (no x/y/points keys): a 16-hex key may contain any digit run.
export const SECRETS = ['INV-1042', 'ACME', '1,250', 'qbo.example', 'h-total', 'Total', 'Memo', 'Beta', 'Bills', 'invoice 77'];
