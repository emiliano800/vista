// Write the compiled v2 invoice graph to tests/fixtures/plan_invoice_v2.json (backend + web tests).
//   node test/plan-fixture.mjs
import fs from 'node:fs';

import { compilePlan } from '../src/plan.js';
import { invoiceEvents, invoiceFiles } from './fixtures/invoice-recording.js';

const out = new URL('../../../tests/fixtures/plan_invoice_v2.json', import.meta.url);
const { graph, report } = compilePlan({ recordingId: 'rec-1', events: invoiceEvents(), files: invoiceFiles });
if (!report.leakage.ok) throw new Error(`fixture leaks: ${JSON.stringify(report.leakage.failures)}`);
fs.writeFileSync(out, JSON.stringify(graph, null, 2) + '\n');
console.log(`${out.pathname}: ${graph.nodes.length} states, ${graph.edges.length} moves`);
