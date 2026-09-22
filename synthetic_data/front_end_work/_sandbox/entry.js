// The sandbox destination for the "Enter Bills" demo workflow. Plain DOM, no framework.
// Storage: localStorage only. Every control has an accessible name (label/for, button
// text), because that is what an accessibility-tree-first agent enumerates.
(function () {
  'use strict';
  const KEY = 'vista-sandbox-invoices';
  const FIELDS = ['SUPPLIER_NAME', 'SUPPLIER_INVOICE_N', 'PO_NUMBER', 'INVOICE_DATE', 'DUE_DATE', 'MERCHANDISE_TOTAL', 'FREIGHT', 'SALES_TAX', 'INVOICE_TOTAL', 'GL_ACCOUNT'];
  const REQUIRED = ['SUPPLIER_NAME', 'SUPPLIER_INVOICE_N', 'INVOICE_DATE', 'MERCHANDISE_TOTAL', 'INVOICE_TOTAL'];
  const $ = (id) => document.getElementById(id);
  const form = $('invoice-form');

  function load() {
    try {
      const rows = JSON.parse(localStorage.getItem(KEY) || '[]');
      return Array.isArray(rows) ? rows : [];
    } catch {
      return [];
    }
  }
  function save(rows) {
    localStorage.setItem(KEY, JSON.stringify(rows));
  }
  function money(v) {
    const n = Number(String(v).replace(/[$,\s]/g, ''));
    return Number.isFinite(n) ? n.toFixed(2) : null;
  }
  function render() {
    const rows = load();
    $('count').textContent = String(rows.length);
    $('rows').innerHTML = rows
      .map((r, i) => `<tr>${[i + 1, ...FIELDS.map((f) => r[f] ?? ''), r.saved_at ?? ''].map((c) => `<td>${escapeHtml(String(c))}</td>`).join('')}</tr>`)
      .join('');
  }
  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }
  function setStatus(text) {
    $('status').textContent = text;
    $('alert').hidden = true;
    $('alert').textContent = '';
  }
  function setAlert(text) {
    $('alert').textContent = text;
    $('alert').hidden = false;
    $('status').textContent = '';
  }
  function csv() {
    const rows = load();
    const header = [...FIELDS, 'saved_at'];
    const cell = (v) => {
      const s = String(v ?? '');
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    return [header.join(','), ...rows.map((r) => header.map((h) => cell(r[h])).join(','))].join('\n') + '\n';
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const record = {};
    let firstBad = null;
    for (const f of FIELDS) {
      const el = form.elements[f];
      const raw = el.value.trim();
      el.removeAttribute('aria-invalid');
      if (REQUIRED.includes(f) && !raw) {
        el.setAttribute('aria-invalid', 'true');
        firstBad = firstBad || el;
      }
      record[f] = raw;
    }
    if (firstBad) {
      setAlert(`${firstBad.labels[0].textContent} is required.`);
      firstBad.focus();
      return;
    }
    for (const f of ['MERCHANDISE_TOTAL', 'FREIGHT', 'SALES_TAX', 'INVOICE_TOTAL']) {
      const m = money(record[f] || '0');
      if (m === null) {
        form.elements[f].setAttribute('aria-invalid', 'true');
        setAlert(`${form.elements[f].labels[0].textContent} must be a number.`);
        form.elements[f].focus();
        return;
      }
      record[f] = m;
    }
    const rows = load();
    if (rows.some((r) => r.SUPPLIER_INVOICE_N.toLowerCase() === record.SUPPLIER_INVOICE_N.toLowerCase())) {
      form.elements.SUPPLIER_INVOICE_N.setAttribute('aria-invalid', 'true');
      setAlert(`Invoice ${record.SUPPLIER_INVOICE_N} was already submitted. Left for a person to check.`);
      return;
    }
    record.saved_at = new Date().toISOString();
    rows.push(record);
    save(rows);
    render();
    form.reset();
    setStatus(`Saved invoice ${record.SUPPLIER_INVOICE_N} for ${record.SUPPLIER_NAME} as row ${rows.length}.`);
    form.elements.SUPPLIER_NAME.focus();
  });
  $('reset-form').addEventListener('click', () => {
    form.reset();
    for (const f of FIELDS) form.elements[f].removeAttribute('aria-invalid');
    setStatus('');
  });
  $('export-csv').addEventListener('click', () => {
    const blob = new Blob([csv()], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'sandbox-invoices.csv';
    a.click();
    URL.revokeObjectURL(a.href);
  });
  // Two clicks to clear: the first arms the button, the second (within 5 s) clears.
  let disarm = null;
  $('clear-all').addEventListener('click', (e) => {
    const b = e.currentTarget;
    if (b.dataset.armed !== 'true') {
      b.dataset.armed = 'true';
      b.textContent = 'Click again to clear';
      disarm = setTimeout(() => {
        b.dataset.armed = 'false';
        b.textContent = 'Clear all';
      }, 5000);
      return;
    }
    clearTimeout(disarm);
    save([]);
    render();
    b.dataset.armed = 'false';
    b.textContent = 'Clear all';
    setStatus('Cleared all submitted invoices.');
  });

  if (new URLSearchParams(location.search).get('reset') === '1') save([]);
  render();
  window.VISTA_SANDBOX = { rows: load, csv };
})();
