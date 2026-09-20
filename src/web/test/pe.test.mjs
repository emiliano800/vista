import test from "node:test";
import assert from "node:assert/strict";
import { signIn, session, signOut, requireAnalyst } from "../public/lib/auth.js";
import { parseCsv, detectDataset, proposeMappings, transformRows, detectExceptions, cedarSampleFiles, buildCompany, CEDAR_PROFILE, normalizeDate, normalizeMoney, normalizePhone } from "../public/lib/importer.js";
import * as store from "../public/lib/store.js";

const DEMO_KEY = "88c4845687c36379be7086043bc646a37aedecd26b3f72a4f3fc842ec0a9ec95";
const IDENTITY = { email: "sarah@northstarhvac.com", tenant_id: "9f1d0b6a-0000-4000-8000-000000000001" };
// Records what auth.js sent so the tests assert on the request, not on a stub.
function stubFetch(responses) {
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, method: options.method ?? "GET", body: options.body, headers: options.headers ?? {} });
    const next = responses.shift() ?? { ok: true, status: 200, json: async () => IDENTITY };
    return { ok: next.ok, status: next.status, json: next.json ?? (async () => IDENTITY) };
  };
  return calls;
}
const memoryStorage = () => {
  const m = new Map();
  return { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) };
};

test("sign-in exchanges the access key for a backend session cookie", async () => {
  const calls = stubFetch([{ ok: true, status: 200 }]);
  const storage = memoryStorage();
  const identity = await signIn(DEMO_KEY, storage);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/auth/session");
  assert.equal(calls[0].method, "POST");
  assert.deepEqual(JSON.parse(calls[0].body), { token: DEMO_KEY });
  assert.equal(calls[0].headers["X-Vista-Request"], "1", "the proxy requires the same-origin marker");
  assert.equal(identity.email, IDENTITY.email);
  assert.equal(session(storage).email, IDENTITY.email, "identity is cached for the header only");
});

test("sign-in rejects a key the backend refuses, and never stores an identity", async () => {
  stubFetch([{ ok: false, status: 401 }]);
  const storage = memoryStorage();
  await assert.rejects(signIn(DEMO_KEY, storage), /Invalid access key/);
  assert.equal(session(storage), null);
  await assert.rejects(signIn("short", storage), /Invalid access key/);
});

test("requireAnalyst confirms the cookie with the backend and clears a dead session", async () => {
  const storage = memoryStorage();
  stubFetch([{ ok: true, status: 200 }]);
  assert.equal((await requireAnalyst(storage)).email, IDENTITY.email);
  assert.equal(session(storage).email, IDENTITY.email);

  let redirected = null;
  globalThis.window = { VISTA_NAVIGATE: (u) => (redirected = u) };
  globalThis.location = { pathname: "/portfolio/", search: "" };
  stubFetch([{ ok: false, status: 401 }]);
  assert.equal(await requireAnalyst(storage), null);
  assert.equal(session(storage), null, "a dead session drops the cached identity");
  assert.equal(redirected, "/signin/analyst/?next=%2Fportfolio%2F");

  stubFetch([{ ok: true, status: 204 }]);
  storage.setItem("vista.analyst.identity", JSON.stringify(IDENTITY));
  await signOut(storage);
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
