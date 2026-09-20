import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { deflateRawSync } from 'node:zlib';

import { extractText, reviewFiles, scanSensitive, zipEntries } from '../src/filereview.js';

// Minimal zip writer (deflate) so Office fixtures need no dependency.
function zip(files) {
  const locals = [], centrals = [];
  let offset = 0;
  for (const [name, content] of Object.entries(files)) {
    const raw = Buffer.from(content, 'utf8'), data = deflateRawSync(raw), n = Buffer.from(name, 'utf8');
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(20, 4); local.writeUInt16LE(8, 8);
    local.writeUInt32LE(data.length, 18); local.writeUInt32LE(raw.length, 22); local.writeUInt16LE(n.length, 26);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0); central.writeUInt16LE(8, 10);
    central.writeUInt32LE(data.length, 20); central.writeUInt32LE(raw.length, 24); central.writeUInt16LE(n.length, 28); central.writeUInt32LE(offset, 42);
    locals.push(local, n, data);
    centrals.push(central, n);
    offset += local.length + n.length + data.length;
  }
  const cd = Buffer.concat(centrals);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0); eocd.writeUInt16LE(centrals.length / 2, 8); eocd.writeUInt16LE(centrals.length / 2, 10);
  eocd.writeUInt32LE(cd.length, 12); eocd.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, cd, eocd]);
}

test('zipEntries reads deflated entries back', () => {
  const z = zipEntries(zip({ 'a.txt': 'hello', 'b/c.xml': '<x>1</x>' }));
  assert.deepEqual(z.names, ['a.txt', 'b/c.xml']);
  assert.equal(z.read('a.txt').toString(), 'hello');
  assert.equal(z.read('nope'), null);
});

test('extractText: plain text, xlsx (shared strings + inline), docx, pptx', () => {
  assert.equal(extractText(Buffer.from('a,b\n1,2'), '.csv').text, 'a,b\n1,2');
  const xlsx = zip({
    'xl/sharedStrings.xml': '<sst><si><t>Vendor</t></si><si><t>Carrier &amp; Co</t></si></sst>',
    'xl/worksheets/sheet1.xml': '<sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>42</v></c></row><row r="2"><c t="s"><v>1</v></c><c t="inlineStr"><is><t>x</t></is></c></row></sheetData>',
  });
  const x = extractText(xlsx, '.xlsx');
  assert.equal(x.status, 'parsed');
  assert.equal(x.text, 'Vendor\t42\nCarrier & Co\tx');
  const d = extractText(zip({ 'word/document.xml': '<w:body><w:p><w:r><w:t>Hello</w:t></w:r><w:r><w:t xml:space="preserve"> world</w:t></w:r></w:p><w:p><w:r><w:t>Second</w:t></w:r></w:p></w:body>' }), '.docx');
  assert.equal(d.text, 'Hello world\nSecond');
  const p = extractText(zip({ 'ppt/slides/slide2.xml': '<p:sld><a:t>Two</a:t></p:sld>', 'ppt/slides/slide1.xml': '<p:sld><a:t>One</a:t><a:t>A</a:t></p:sld>' }), '.pptx');
  assert.equal(p.text, 'One A\nTwo');
});

test('extractText: pdf and legacy formats are unsupported, garbage zip fails', () => {
  const pdf = extractText(Buffer.from('%PDF-1.4'), '.pdf');
  assert.equal(pdf.status, 'unsupported');
  assert.match(pdf.note, /PDF/);
  assert.equal(extractText(Buffer.from(''), '.xls').status, 'unsupported');
  assert.equal(extractText(Buffer.from('not a zip'), '.docx').status, 'failed');
});

test('scanSensitive flags one entry per kind with the first line', () => {
  const flags = scanSensitive('Vendor,Email\nAcme,ap@acme.com\nBeta,bill@beta.io\nIBAN: DE89 3704 0044 0532 0130 00\npassword: hunter2');
  const kinds = Object.fromEntries(flags.map((f) => [f.kind, f]));
  assert.equal(kinds.email.line, 2);
  assert.match(kinds.email.reason, /2 lines/);
  assert.equal(kinds.iban.line, 4);
  assert.equal(kinds.credential.line, 5);
  assert.equal(scanSensitive('nothing here').length, 0);
});

test('reviewFiles parses included snapshots, skips excluded/reviewed ones, records model output without raw text', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'vista-fr-'));
  fs.mkdirSync(path.join(dir, 'files'));
  fs.writeFileSync(path.join(dir, 'files', 'a.csv'), 'Employee,Salary\nJane,90000\n');
  fs.writeFileSync(path.join(dir, 'files', 'b.pdf'), '%PDF');
  const files = [
    { id: 'a', name: 'a.csv', ext: '.csv', snapshot: 'files/a.csv', include: true },
    { id: 'b', name: 'b.pdf', ext: '.pdf', snapshot: 'files/b.pdf', include: true },
    { id: 'c', name: 'c.csv', ext: '.csv', snapshot: 'files/a.csv', include: false },
    { id: 'd', name: 'd.csv', ext: '.csv', snapshot: null, include: true },
    { id: 'e', name: 'e.csv', ext: '.csv', snapshot: 'files/a.csv', include: true, review: { status: 'parsed', flags: [] } },
  ];
  const calls = [];
  const fetchFn = async (_url, init) => {
    calls.push(JSON.parse(init.body));
    return { ok: true, json: async () => ({ model: 'm', usage: { total_tokens: 10 }, choices: [{ message: { content: JSON.stringify({ summary: 'A pay list.', kind: 'list', sensitive: [{ kind: 'payroll', reason: 'salaries' }, { kind: 'personal', reason: 'names' }], confidence: 0.9 }) } }] }) };
  };
  const seen = [];
  await reviewFiles(dir, files, { key: 'k', model: 'm' }, { fetchFn, onFile: (f) => seen.push(f.id) });
  assert.deepEqual(seen, ['a', 'b']);
  assert.equal(calls.length, 1, 'model only sees parsed documents');
  assert.match(calls[0].messages[1].content, /Jane,90000/);
  assert.equal(files[0].review.status, 'parsed');
  assert.equal(files[0].review.ai.summary, 'A pay list.');
  assert.equal(files[0].review.excerpt, undefined);
  const kinds = files[0].review.flags.map((f) => f.kind).sort();
  assert.deepEqual(kinds, ['payroll', 'personal']);
  assert.equal(files[0].review.flags.find((f) => f.kind === 'payroll').source, 'scan');
  assert.equal(files[1].review.status, 'unsupported');
  assert.equal(files[2].review, undefined);
  assert.equal(files[3].review, undefined);
  assert.equal(files[4].review.status, 'parsed');

  // model error is recorded per file, parse result kept
  await reviewFiles(dir, [files[0]], { key: 'k', model: 'm' }, { force: true, fetchFn: async () => ({ ok: false, status: 429, text: async () => 'slow down', statusText: 'x' }) });
  assert.equal(files[0].review.status, 'parsed');
  assert.match(files[0].review.ai.error, /429/);

  // no key: parse + scan only
  await reviewFiles(dir, [files[0]], null, { force: true });
  assert.equal(files[0].review.ai, null);
  assert.equal(files[0].review.flags.length, 1);
});
