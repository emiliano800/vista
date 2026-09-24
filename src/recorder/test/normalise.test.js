import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { Vocabulary, checkLeakage, classify, describeFailures, keyName, normalise, recordingContext, rowName } from '../src/normalise.js';

const fixture = JSON.parse(readFileSync(fileURLToPath(new URL('../../../tests/fixtures/normalise_parity.json', import.meta.url)), 'utf8'));

test('the JS normaliser agrees with the Python reference on every parity case', () => {
  const vocab = Vocabulary.build(fixture.screens, { extra: fixture.extra });
  assert.deepEqual(vocab.toJSON(), fixture.vocabulary);
  for (const c of fixture.cases) {
    assert.equal(classify(c.text), c.classify, `classify(${JSON.stringify(c.text)})`);
    assert.equal(normalise(c.text), c.normalise, `normalise(${JSON.stringify(c.text)})`);
    assert.equal(normalise(c.text, vocab), c.normalise_vocab, `normalise+vocab(${JSON.stringify(c.text)})`);
  }
  for (const k of fixture.keys) assert.equal(keyName(k.text), k.key_name, `keyName(${JSON.stringify(k.text)})`);
  for (const r of fixture.rows) assert.equal(rowName(r.headers, r.position, vocab), r.row_name);
});

test('the vocabulary grows incrementally and the leakage check enumerates only normaliser tokens', () => {
  const vocab = new Vocabulary();
  vocab.addScreen(['Amount', 'Save']);
  assert.ok(!vocab.has('amount'), 'one screen is not recurrence');
  vocab.addScreen(['Amount', 'Cancel']);
  assert.ok(vocab.has('amount') && !vocab.has('save') && !vocab.has('cancel'));

  const ctx = recordingContext({ values: ['1,250.00', 'ACME Corp'], titles: ['Bills - ACME Corp'], vocab });
  const ok = checkLeakage({ nodes: [{ key: 'a1b2c3d4e5f60718', signature: ['field:amount'] }], edges: [{ control: 'amount', slot: 'input_1' }, { control: 'cmd+s' }, { control: 'enter' }] }, ctx);
  assert.equal(ok.ok, true, describeFailures(ok));

  const bad = checkLeakage({ a: 'paid 1,250.00', b: 'Bills - ACME Corp', c: '{secret}', edges: [{ control: 'Vendor name' }] }, ctx);
  const reasons = new Set(bad.failures.map((f) => f.reason));
  assert.deepEqual([...reasons].sort(), ['non_vocab_name', 'recorded_value', 'unknown_token', 'window_title']);
  const text = JSON.stringify(bad.failures) + describeFailures(bad);
  for (const s of ['1,250', 'ACME', 'Vendor', 'secret']) assert.ok(!text.includes(s), `report repeats ${s}`);
  assert.match(describeFailures(bad), /recorded_value at \$\.a/);
});
