import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { sha256Hex, verifyAnalystKey, signIn, session, signOut, ANALYST } from "../public/lib/auth.js";
import { parseCsv, detectDataset, proposeMappings, transformRows, detectExceptions, cedarSampleFiles, buildCompany, CEDAR_PROFILE, normalizeDate, normalizeMoney, normalizePhone } from "../public/lib/importer.js";
import * as store from "../public/lib/store.js";

const DEMO_KEY = "88c4845687c36379be7086043bc646a37aedecd26b3f72a4f3fc842ec0a9ec95";
const demoAccess = fs.readFileSync(new URL("../../../DEMO_ACCESS.md", import.meta.url), "utf8");
const memoryStorage = () => {
  const m = new Map();
  return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) };
};

test("analyst key: documented demo key verifies, anything else is rejected", async () => {
  assert.match(demoAccess, new RegExp(DEMO_KEY));
  assert.equal(await sha256Hex("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  assert.equal(await verifyAnalystKey(DEMO_KEY), true);
  assert.equal(await verifyAnalystKey(`  ${DEMO_KEY.toUpperCase()} `), true, "hex keys are case/whitespace tolerant");
  assert.equal(await verifyAnalystKey(DEMO_KEY.slice(1)), false);
  assert.equal(await verifyAnalystKey("k".repeat(64)), false);
  assert.equal(await verifyAnalystKey(""), false);
});

test("analyst session persists in storage and clears on sign-out", async () => {
  const storage = memoryStorage();
  assert.equal(session(storage), null);
  await assert.rejects(signIn("wrong-key", storage));
  assert.equal(session(storage), null);
  const s = await signIn(DEMO_KEY, storage);
  assert.equal(s.email, ANALYST.email);
  assert.equal(session(storage).firm, ANALYST.firm);
  signOut(storage);
  assert.equal(session(storage), null);
});

test("seed portfolio: two companies, metrics sum from records, potential is never realized", () => {
  store.reset(null);
  const cs = store.companies();
  assert.deepEqual(cs.map((c) => c.id), ["harbor", "summit"]);
  const m = store.portfolioMetrics();
  const sum = (k) => cs.reduce((n, c) => n + store.companyMetrics(c)[k], 0);
  assert.equal(m.companies, 2);
  assert.ok(Math.abs(m.revenue - sum("revenue")) < 0.01);
  assert.ok(Math.abs(m.outstandingAr - sum("outstandingAr")) < 0.01);
  assert.ok(m.overdueAr <= m.outstandingAr);
  assert.ok(m.vendorSpend > 0);
  assert.equal(m.openOpportunities, store.opportunities().filter((o) => !["Dismissed", "Realized"].includes(o.status)).length);
  for (const o of store.opportunities()) if (o.status !== "Realized") assert.equal(o.realizedValue, null, `${o.id} claims realized value`);
  for (const c of cs) {
    const { steps, complete, total, label } = store.integrationSteps(c);
    assert.ok(total >= 5 && steps.length === total);
    assert.equal(complete, steps.filter((s) => s.status === "Complete").length);
    assert.ok(["Complete", "In progress", "Not started"].includes(label));
  }
});

test("csv parsing handles quotes, commas and blank lines", () => {
  const { columns, rows } = parseCsv('Name,Amount\n"Smith, Bob",  "1,250.50"\n\nJane,7\n');
  assert.deepEqual(columns, ["Name", "Amount"]);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].Name, "Smith, Bob");
  assert.equal(normalizeMoney(rows[0].Amount), 1250.5);
});

test("normalisers are deterministic", () => {
  assert.equal(normalizeDate("3/7/2026"), "2026-03-07");
  assert.equal(normalizeDate("2026-03-07"), "2026-03-07");
  assert.equal(normalizeMoney("$1,234.50"), 1234.5);
  assert.equal(normalizeMoney("(120)"), -120);
  assert.equal(normalizePhone("(503) 555-0142"), "(503) 555-0142");
  assert.equal(normalizePhone("503.555.0142"), "(503) 555-0142");
});

test("cedar sample import: detection, mappings, transforms, exceptions, provenance", () => {
  const files = cedarSampleFiles();
  assert.ok(files.length >= 3);
  const parsed = files.map((f) => ({ ...f, ...parseCsv(f.text) }));
  const kinds = parsed.map((f) => detectDataset(f.columns, f.name));
  assert.deepEqual(new Set(kinds.map((k) => k.dataset)), new Set(["customers", "invoices", "vendors", "subscriptions"]));
  assert.ok(kinds.every((k) => k.confidence >= 0.5));

  const datasets = {};
  let reviewCount = 0;
  for (const f of parsed) {
    const { dataset } = detectDataset(f.columns, f.name);
    const mappings = proposeMappings(dataset, f.columns, f.rows);
    assert.ok(mappings.length, `${f.name} has mappings`);
    for (const m of mappings) assert.equal(m.status, m.target && m.confidence >= 0.9 ? "Ready" : "Review", `${m.source} under 90% must be flagged Review`);
    reviewCount += mappings.filter((m) => m.status === "Review").length;
    const out = transformRows(dataset, f.rows, mappings, { file: f.name, job: "imp-test-001" });
    assert.equal(out.length, f.rows.length);
    for (const r of out) {
      assert.equal(r.provenance.file, f.name);
      assert.equal(r.provenance.importJob, "imp-test-001");
      assert.ok(Number.isInteger(r.provenance.row));
      assert.ok(r.provenance.original && r.provenance.normalized);
    }
    datasets[dataset] = { file: f.name, records: out };
  }
  assert.ok(reviewCount >= 1, "the sample set contains at least one ambiguous mapping for the analyst to confirm");
  const exceptions = detectExceptions(datasets, store.companies().flatMap((c) => c.vendors));
  assert.ok(exceptions.length >= 1, "ambiguous records are surfaced, not merged");
  assert.ok(exceptions.every((e) => e.decision === null && e.actions.length >= 2));

  exceptions.forEach((e) => (e.decision = e.actions[0]));
  const c = buildCompany({ id: "cedar", profile: CEDAR_PROFILE, datasets, exceptions, job: "imp-test-001" });
  assert.equal(c.name, CEDAR_PROFILE.name);
  assert.ok(c.customers.length && c.invoices.length && c.purchases.length);
  assert.ok(c.customers.every((r) => ["auto-accepted", "reviewed"].includes(r.provenance.review)));
  assert.equal(c.importJobs[0].id, "imp-test-001");
  assert.equal(c.importExceptions.length, 0, "decided exceptions are closed");
  const undecided = buildCompany({ id: "cedar2", profile: CEDAR_PROFILE, datasets, exceptions: exceptions.map((e) => ({ ...e, decision: null })), job: "imp-test-002" });
  assert.equal(undecided.importExceptions.length, exceptions.length, "undecided exceptions stay open for later review");
});

test("portfolio analysis creates a deterministic purchasing opportunity; task creation moves it along", () => {
  store.reset(null);
  const before = store.opportunities().length;
  const found = store.runPortfolioAnalysis();
  assert.ok(found.length >= 1);
  assert.equal(store.opportunities().length, before + found.length);
  const o = found.find((x) => x.category === "Purchasing");
  assert.ok(o, "purchasing gap between Harbor and Summit is found");
  assert.equal(o.status, "New");
  assert.equal(o.realizedValue, null);
  assert.ok(o.potentialValue > 0);
  assert.ok(o.evidence.length >= 2);
  assert.ok(o.calculation.some((line) => /×/.test(line)), "calculation shows price gap × units");
  assert.ok(store.evidenceRows(o.evidence[0]).length > 0);
  assert.deepEqual(store.runPortfolioAnalysis(), [], "re-running does not duplicate");

  const t = store.createTask({ title: "Follow up", companyId: o.companyIds[0], category: "Opportunity follow-up", sourceType: "opportunity", sourceId: o.id, priority: "High", assignee: "Sarah Okafor", dueDate: "2026-10-03" }, "Sarah Okafor");
  assert.match(t.id, /^T-\d+$/);
  assert.equal(t.status, "Open");
  assert.equal(store.opportunities().find((x) => x.id === o.id).status, "Task created");
  assert.ok(store.activity().some((a) => a.text.includes(t.id)));

  store.updateTask(t.id, { status: "Complete", outcome: "Implemented", realizedResult: 1200 }, "Sarah Okafor");
  const done = store.tasks().find((x) => x.id === t.id);
  assert.equal(done.status, "Complete");
  assert.ok(done.completedAt);
  const realized = store.opportunities().find((x) => x.id === o.id);
  assert.equal(realized.status, "Realized");
  assert.equal(realized.realizedValue, 1200);
});

test("task outcomes other than Implemented never populate realized value", () => {
  store.reset(null);
  const o = store.opportunities().find((x) => x.status === "Under review");
  const t = store.createTask({ title: "Validate", companyId: o.companyIds[0], category: "Opportunity follow-up", sourceType: "opportunity", sourceId: o.id }, "Sarah Okafor");
  store.updateTask(t.id, { status: "Complete", outcome: "Benefit validated", realizedResult: 999 }, "Sarah Okafor");
  const after = store.opportunities().find((x) => x.id === o.id);
  assert.equal(after.status, "Validated");
  assert.equal(after.realizedValue, null);
  assert.equal(store.tasks().find((x) => x.id === t.id).realizedResult, null);
});

test("agents can be paused, resumed and run; runs are recorded", () => {
  store.reset(null);
  const a = store.agents()[0];
  store.setAgentStatus(a.id, "Paused");
  assert.equal(store.agents().find((x) => x.id === a.id).status, "Paused");
  store.setAgentStatus(a.id, "Active");
  const before = store.runs().length;
  const r = store.runAgentNow(a.id);
  assert.equal(store.runs().length, before + 1);
  assert.equal(store.run(r.id).agentId, a.id);
  assert.ok(r.sources.length && r.events.length);
});

test("state round-trips through storage", () => {
  const storage = memoryStorage();
  globalThis.localStorage = storage;
  try {
    store.reset(storage);
    const t = store.createTask({ title: "Persist me", companyId: "harbor", category: "Integration" }, "Sarah Okafor");
    store.setState(null);
    assert.ok(store.load(storage).tasks.some((x) => x.id === t.id));
  } finally {
    delete globalThis.localStorage;
    store.reset(null);
  }
});
