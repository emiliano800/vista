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


// ---- The API-backed portfolio store -------------------------------------------
// A fake backend so the store's mapping, derived figures and write-through are
// asserted against request/response pairs rather than against seed fixtures.
const COMPANY_A = "11111111-1111-4111-8111-111111111111";
const COMPANY_B = "22222222-2222-4222-8222-222222222222";
const OPP = "33333333-3333-4333-8333-333333333333";
const TASK = "44444444-4444-4444-8444-444444444444";

function fakeApi(overrides = {}) {
  const sent = [];
  const detail = (id, extra) => ({
    id,
    name: id === COMPANY_A ? "Keystone Bearing" : "Ridgeway Fasteners",
    profile: { industry: "Industrial goods", location: "Ohio", acquired: "2026-01-04" },
    as_of: "2026-09-20",
    batch_id: "batch-1",
    customers: [{ client_id: "C-1", client_name: "Acme" }],
    invoices: [{ invoice_id: "INV-1", balance: "1200.00", due_date: "2026-01-01", _row: 2 }],
    vendors: [{ vendor_id: "V-1", vendor_name: "Globex" }],
    purchases: [{ sku: "BRG-204", quantity: "80", unit_price: "15.00", _row: 2 }],
    subscriptions: [],
    policies: [],
    commissions: [],
    unsourced: ["subscriptions", "policies", "commissions"],
    exceptions: [],
    analysis: { findings: [{ id: "f1", title: "Overdue invoice", detail: "", category: "receivable", amount: "1200.00", status: "open" }] },
    ...extra,
  });
  const routes = {
    "GET /portfolio/companies": () => [
      { id: COMPANY_A, name: "Keystone Bearing", profile: {}, created_at: "2026-01-04T00:00:00Z", imports: 1, records: 4, last_run_at: null },
      { id: COMPANY_B, name: "Ridgeway Fasteners", profile: {}, created_at: "2026-02-04T00:00:00Z", imports: 1, records: 4, last_run_at: null },
    ],
    [`GET /portfolio/companies/${COMPANY_A}`]: () => detail(COMPANY_A),
    [`GET /portfolio/companies/${COMPANY_B}`]: () => detail(COMPANY_B),
    "GET /portfolio/tasks": () => [],
    "GET /portfolio/opportunities": () => [
      {
        id: OPP, title: "Shared SKU price gap", category: "Purchasing", deal_ids: [COMPANY_A, COMPANY_B],
        confidence: 0.9, potential_value: "200.00", status: "New", found_at: "2026-09-20T00:00:00Z",
        fact: "", evidence: [{ sku: "BRG-204" }], calculation: [], benefit: "", assumptions: [],
        next_action: "", realized_value: null,
      },
    ],
    "GET /portfolio/activity?limit=200": () => [],
    "GET /agents": () => [],
    "GET /runs?limit=200": () => [],
    ...overrides,
  };
  globalThis.fetch = async (url, options = {}) => {
    const method = options.method ?? "GET";
    const path = String(url).replace(/^\/api/, "");
    const key = `${method} ${path}`;
    sent.push({ key, body: options.body ? JSON.parse(options.body) : null });
    const handler = routes[key];
    if (!handler) return { ok: false, status: 404, json: async () => ({ detail: `no route ${key}` }) };
    return { ok: true, status: method === "POST" ? 201 : 200, json: async () => handler(sent.at(-1)?.body) };
  };
  return sent;
}

test("hydrate maps the API into the workspace vocabulary", async () => {
  fakeApi();
  await store.hydrate();
  const cs = store.companies();
  assert.deepEqual(cs.map((c) => c.name), ["Keystone Bearing", "Ridgeway Fasteners"]);
  const c = cs[0];
  assert.equal(c.industry, "Industrial goods", "profile comes from the deal, not a hardcoded fixture");
  assert.equal(c.invoices[0].outstanding, 1200, "balance maps to outstanding");
  assert.equal(c.purchases[0].total, 1200, "total is derived when the export has no total column");
  assert.equal(store.opportunities()[0].potentialValue, 200);
  assert.equal(store.findings(c.id).length, 1, "findings come from the import analysis");
  assert.equal(store.findings(c.id)[0].companyId, c.id);
});

test("a bucket with no import behind it is null, never a measured zero", async () => {
  fakeApi();
  await store.hydrate();
  const m = store.companyMetrics(store.companies()[0]);
  assert.equal(m.softwareAnnual, null, "no subscriptions were imported");
  assert.equal(m.revenue, null, "invoices carry no issue date, so revenue has no source");
  assert.equal(m.outstandingAr, 1200, "the balance column is sourced, so it is a figure");
  assert.equal(m.customers, 1);
});

test("creating a task from an opportunity writes through and advances it", async () => {
  const sent = fakeApi({
    "POST /portfolio/tasks": (body) => ({
      id: TASK, deal_id: body.deal_id, title: body.title, description: "", category: body.category,
      source_type: body.source_type, source_id: body.source_id, assignee: body.assignee,
      priority: body.priority, due_date: body.due_date, status: "Open", created_by: "sarah",
      created_at: "2026-09-20T00:00:00Z", completed_at: null, outcome: null, outcome_notes: "",
      realized_result: null,
    }),
    [`PATCH /portfolio/opportunities/${OPP}`]: (body) => ({
      id: OPP, title: "Shared SKU price gap", category: "Purchasing", deal_ids: [COMPANY_A, COMPANY_B],
      confidence: 0.9, potential_value: "200.00", status: body.status, found_at: "2026-09-20T00:00:00Z",
      fact: "", evidence: [], calculation: [], benefit: "", assumptions: [], next_action: "",
      realized_value: body.realized_value ?? null,
    }),
  });
  await store.hydrate();
  const task = await store.createTask(
    { title: "Follow up", companyId: COMPANY_A, sourceType: "opportunity", sourceId: OPP, priority: "High" },
    "Sarah Okafor",
  );
  assert.equal(task.companyId, COMPANY_A);
  const post = sent.find((s) => s.key === "POST /portfolio/tasks");
  assert.equal(post.body.deal_id, COMPANY_A, "the company is sent as deal_id");
  assert.equal(post.body.due_date, null);
  assert.equal(store.tasks()[0].id, TASK, "the snapshot is patched, not re-fetched");
  assert.equal(store.opportunities()[0].status, "Task created", "the opportunity advanced");
});

test("only an implemented outcome sends a realized value", async () => {
  const patched = [];
  const taskRow = (status, outcome, realized) => ({
    id: TASK, deal_id: COMPANY_A, title: "Follow up", description: "", category: "Integration",
    source_type: "opportunity", source_id: OPP, assignee: "Sarah", priority: "High", due_date: null,
    status, created_by: "sarah", created_at: "2026-09-20T00:00:00Z",
    completed_at: status === "Complete" ? "2026-09-21T00:00:00Z" : null,
    outcome, outcome_notes: "", realized_result: realized,
  });
  const sent = fakeApi({
    "GET /portfolio/tasks": () => [taskRow("Open", null, null)],
    [`PATCH /portfolio/tasks/${TASK}`]: (body) => {
      patched.push(body);
      return taskRow(body.status, body.outcome, body.realized_result ?? null);
    },
    [`PATCH /portfolio/opportunities/${OPP}`]: (body) => ({
      id: OPP, title: "x", category: "Purchasing", deal_ids: [COMPANY_A], confidence: 0.9,
      potential_value: "200.00", status: body.status, found_at: "2026-09-20T00:00:00Z", fact: "",
      evidence: [], calculation: [], benefit: "", assumptions: [], next_action: "",
      realized_value: body.realized_value ?? null,
    }),
  });
  await store.hydrate();
  await store.updateTask(TASK, { status: "Complete", outcome: "No benefit found", realizedResult: 999 }, "Sarah");
  assert.equal(patched.at(-1).realized_result, undefined, "a non-implemented outcome sends no realized value");
  assert.equal(store.opportunities()[0].status, "Dismissed");

  await store.updateTask(TASK, { status: "Complete", outcome: "Implemented", realizedResult: 200 }, "Sarah");
  assert.equal(patched.at(-1).realized_result, 200);
  assert.equal(store.opportunities()[0].status, "Realized");
  assert.ok(sent.some((s) => s.key === `PATCH /portfolio/opportunities/${OPP}`));
});

test("portfolio analysis merges what the backend created and updated", async () => {
  const opp = (id, status, value) => ({
    id, title: "gap", category: "Purchasing", deal_ids: [COMPANY_A, COMPANY_B], confidence: 0.9,
    potential_value: value, status, found_at: "2026-09-20T00:00:00Z", fact: "", evidence: [],
    calculation: [], benefit: "", assumptions: [], next_action: "", realized_value: null,
  });
  fakeApi({
    "POST /portfolio/analysis": () => ({
      companies: 2,
      skus_compared: 2,
      created: [opp("55555555-5555-4555-8555-555555555555", "New", "300.00")],
      updated: [opp(OPP, "New", "250.00")],
    }),
  });
  await store.hydrate();
  assert.equal(store.opportunities().length, 1);
  const created = await store.runPortfolioAnalysis();
  assert.equal(created.length, 1, "only new opportunities are returned to the page");
  assert.equal(store.opportunities().length, 2, "re-running updated the existing one in place");
  assert.equal(store.opportunities().find((o) => o.id === OPP).potentialValue, 250);
  assert.ok(store.companies().every((c) => c.analysisRunAt), "every company records the run");
});

test("an unreachable backend leaves the workspace empty rather than inventing data", async () => {
  globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => ({}) });
  await assert.rejects(store.hydrate());
  store.setState(null);
  assert.deepEqual(store.companies(), []);
  assert.deepEqual(store.opportunities(), []);
  assert.equal(store.company(COMPANY_A), null);
  assert.equal(store.companyName(COMPANY_A), "Portfolio");
});
