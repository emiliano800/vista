import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

// ui/highlight.js is a classic script for the dashboard; evaluate it here.
const src = fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), '../ui/highlight.js'), 'utf8');
const highlightHtml = new Function(`${src}; return globalThis.highlightHtml;`)();

const marks = (html) => [...html.matchAll(/<mark class="hl hl-(\w+)">([^<]*)<\/mark>/g)].map((m) => [m[1], m[2]]);

test('numbers, documents and session apps are marked', () => {
  const html = highlightHtml('Entered 12 invoices from INV-1.pdf into QuickBooks in 8 minutes.', ['QuickBooks', 'Acrobat']);
  assert.deepEqual(marks(html), [
    ['num', '12 invoices'],
    ['file', 'INV-1.pdf'],
    ['app', 'QuickBooks'],
    ['num', '8 minutes'],
  ]);
});

test('sensitive wording is marked as risk, ahead of the other rules', () => {
  assert.deepEqual(marks(highlightHtml('A password was typed on the sign-in page', [])), [
    ['risk', 'password'],
    ['risk', 'sign-in'],
  ]);
  assert.deepEqual(marks(highlightHtml('card number visible in 1 cell', [])), [['risk', 'card number'], ['num', '1']]);
});

test('quoted text is marked and the sentence survives intact', () => {
  const text = 'Opened the "Vendor bills" list';
  const html = highlightHtml(text, []);
  assert.deepEqual(marks(html), [['quote', '&quot;Vendor bills&quot;']]);
  assert.equal(html.replace(/<\/?mark[^>]*>/g, '').replace(/&quot;/g, '"'), text);
});

test('html in model text is escaped, not rendered', () => {
  const html = highlightHtml('<img src=x onerror=alert(1)> 3 files', ['x']);
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;img'));
  assert.deepEqual(marks(html), [['num', '1'], ['num', '3 files']]);
});

test('empty text and empty term lists are safe', () => {
  assert.equal(highlightHtml('', ['Excel']), '');
  assert.equal(highlightHtml(null), '');
  assert.equal(highlightHtml('plain words only', []), 'plain words only');
  assert.equal(highlightHtml('plain words only', ['a', '', null]), 'plain words only');
});
