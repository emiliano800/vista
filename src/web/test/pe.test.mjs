import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  normalizeKey,
  signIn,
  session,
  signOut,
  requireAnalyst,
  api,
  ApiError,
} from "../public/lib/auth.js";
import * as store from "../public/lib/store.js";

// Use the analyst key from DEMO_ACCESS.md when that file is present (it is not
// committed); otherwise a fixed 64-hex stand-in, since the tests only need shape.
const demoAccessUrl = new URL("../../../DEMO_ACCESS.md", import.meta.url);
const demoAccess = fs.existsSync(demoAccessUrl)
  ? fs.readFileSync(demoAccessUrl, "utf8")
  : "";
const DEMO_KEY =
  demoAccess.match(/^[0-9a-f]{64}$/m)?.[0] ?? "0123456789abcdef".repeat(4);
const memoryStorage = () => {
  const m = new Map();
  return {
    getItem: (k) => m.get(k) ?? null,
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
};
const json = (status, body) => ({
  ok: status < 400,
  status,
  json: async () => body,
});
const ME = {
  name: "Sarah Okafor",
  email: "sarah@northstarhvac.com",
  role: "analyst",
  firm: { id: "f-1", name: "Northstar HVAC Holdings", slug: "northstar" },
};

// A fetch stub that records calls and answers from a route table.
function mockFetch(routes) {
  const calls = [];
  const fn = async (url, options = {}) => {
    const method = options.method ?? "GET";
    calls.push({
      method,
      url,
      body: options.body ? JSON.parse(options.body) : undefined,
      headers: options.headers,
    });
    const handler = routes[`${method} ${url}`];
    if (!handler) return json(404, { detail: `no route ${method} ${url}` });
    return typeof handler === "function"
      ? handler(calls.at(-1))
      : json(200, handler);
  };
  fn.calls = calls;
  return fn;
}

test("api(): sends same-origin JSON with the CSRF header and maps errors to ApiError", async () => {
  const fetch = mockFetch({
    "GET /api/portfolio/me": ME,
    "POST /api/tasks": () => json(422, { detail: "A title is required" }),
    "GET /api/nope": () => json(500, null),
  });
  assert.deepEqual(await api("/portfolio/me", {}, fetch), ME);
  assert.equal(fetch.calls[0].headers["X-Vista-Request"], "1");
  await assert.rejects(
    api("/tasks", { method: "POST", body: "{}" }, fetch),
    (e) =>
      e instanceof ApiError &&
      e.status === 422 &&
      e.message === "A title is required",
  );
  await assert.rejects(
    api("/nope", {}, fetch),
    (e) => e.status === 500 && /could not complete/.test(e.message),
  );
});

test("analyst key: documented demo key is well-formed; the browser only checks shape", () => {
  assert.equal(DEMO_KEY.length, 64, "analyst key is 64 hex chars");
  assert.equal(normalizeKey(`  ${DEMO_KEY.toUpperCase()} `), DEMO_KEY);
  assert.equal(normalizeKey(DEMO_KEY.slice(1)), null);
  assert.equal(normalizeKey("k".repeat(64)), null);
});

test("signIn exchanges the key for a server session and caches the server identity", async () => {
  const storage = memoryStorage();
  const fetch = mockFetch({
    "POST /api/auth/session": (call) =>
      call.body.token === DEMO_KEY
        ? json(200, { ok: true })
        : json(401, { detail: "Invalid access key" }),
    "GET /api/portfolio/me": ME,
    "DELETE /api/auth/session": () => ({ ok: true, status: 204 }),
  });
  await assert.rejects(
    signIn("wrong-key", storage, fetch),
    /Invalid access key/,
  );
  await assert.rejects(
    signIn("a".repeat(64), storage, fetch),
    /Invalid access key/,
  );
  assert.equal(session(storage), null);
  const s = await signIn(DEMO_KEY, storage, fetch);
  assert.equal(s.email, ME.email);
  assert.equal(session(storage).firm, ME.firm.name);
  await signOut(storage, fetch);
  assert.equal(session(storage), null);
  assert.ok(
    fetch.calls.some(
      (c) => c.method === "DELETE" && c.url === "/api/auth/session",
    ),
  );
});

test("requireAnalyst trusts the server: 401/403 clears the cache and redirects to sign-in", async () => {
  const storage = memoryStorage();
  storage.setItem("vista.analyst.session", JSON.stringify({ name: "stale" }));
  const redirects = [];
  globalThis.window = { VISTA_NAVIGATE: (u) => redirects.push(u) };
  globalThis.location = { pathname: "/portfolio/", search: "" };
  try {
    const denied = mockFetch({
      "GET /api/portfolio/me": () => json(403, { detail: "Not a firm member" }),
    });
    assert.equal(await requireAnalyst(storage, denied), null);
    assert.equal(session(storage), null);
    assert.deepEqual(redirects, ["/signin/analyst/?next=%2Fportfolio%2F"]);
    const ok = await requireAnalyst(
      storage,
      mockFetch({ "GET /api/portfolio/me": ME }),
    );
    assert.equal(ok.firmId, "f-1");
  } finally {
    delete globalThis.window;
    delete globalThis.location;
  }
});

// ---- store: reads come from the snapshot, writes go to the API then refresh ----
const SNAPSHOT = {
  today: "2026-09-19",
  metrics: {
    companies: 2,
    revenue: 1000,
    outstandingAr: 300,
    overdueAr: 100,
    vendorSpend: 400,
    openOpportunities: 1,
  },
  companies: [
    {
      id: "c-harbor",
      slug: "harbor",
      name: "Harbor Heating",
      metrics: { revenue: 600, outstandingAr: 200, overdueAr: 100 },
      summary: {},
      integration: { steps: [], complete: 7, total: 7, label: "Complete" },
      invoices: [
        { id: "i1", outstanding: 100, dueDate: "2026-05-01" },
        { id: "i2", outstanding: 100, dueDate: "2026-10-01" },
        { id: "i3", outstanding: 0, dueDate: "2026-01-01" },
      ],
      purchases: [{ id: "p1", sku: "CAP-45", date: "2026-06-01" }],
      subscriptions: [],
      vendors: [],
      customers: [],
      importJobs: [],
      importExceptions: [
        {
          id: "X-1",
          uuid: "11111111-1111-1111-1111-111111111111",
          open: true,
          actions: ["Merge", "Keep separate"],
        },
      ],
    },
    {
      id: "c-summit",
      slug: "summit",
      name: "Summit Mechanical",
      metrics: { revenue: 400 },
      summary: {},
      integration: { steps: [] },
      invoices: [],
      purchases: [],
      subscriptions: [],
      vendors: [],
      customers: [],
      importJobs: [],
      importExceptions: [],
    },
  ],
  tasks: [{ id: "T-1", companyId: "c-harbor", status: "Open" }],
  opportunities: [
    {
      id: "OP-1",
      status: "New",
      realizedValue: null,
      companyIds: ["c-harbor", "c-summit"],
      evidence: [{ companyId: "c-harbor", entity: "purchase", sku: "CAP-45" }],
    },
  ],
  agents: [
    { id: "file_reviewer-harbor", companyId: "c-harbor", status: "Active", review: 2 },
  ],
  runs: [{ id: "R-1", agentId: "file_reviewer-harbor", companyId: "c-harbor" }],
  findings: [
    { id: "F-001", uuid: "11111111-1111-1111-1111-111111111111", companyId: "c-harbor", status: "Open", severity: "High", runId: "R-1" },
  ],
  activity: [
    { at: "2026-09-01T00:00:00Z", companyId: "c-harbor", text: "old" },
    { at: "2026-09-18T00:00:00Z", companyId: "c-summit", text: "new" },
  ],
  attention: [{ companyId: "c-harbor", title: "2 AR items need review" }],
};

function storeFetch(extra = {}) {
  let snapshot = structuredClone(SNAPSHOT);
  const fetch = mockFetch({
    "GET /api/portfolio": () => json(200, snapshot),
    "POST /api/portfolio/analysis": () => {
      snapshot.opportunities.push({
        id: "OP-2",
        status: "New",
        realizedValue: null,
        companyIds: ["c-harbor"],
      });
      return json(201, { found: [snapshot.opportunities.at(-1)], count: 1 });
    },
    "POST /api/tasks": (call) => {
      const t = { id: "T-2", status: "Open", ...call.body };
      snapshot.tasks.push(t);
      return json(201, t);
    },
    "POST /api/tasks/T-2": (call) => {
      Object.assign(snapshot.tasks[1], call.body);
      if (call.body.outcome !== "Implemented")
        snapshot.tasks[1].realizedResult = null;
      return json(200, snapshot.tasks[1]);
    },
    "POST /api/companies/c-harbor/exceptions/11111111-1111-1111-1111-111111111111/resolve":
      (call) => {
        snapshot.companies[0].importExceptions[0] = {
          ...snapshot.companies[0].importExceptions[0],
          open: false,
          decision: call.body.decision,
        };
        return json(200, snapshot.companies[0].importExceptions[0]);
      },
    "POST /api/findings/F-001/status": (call) => {
      snapshot.findings[0].status = call.body.status;
      return json(200, snapshot.findings[0]);
    },
    ...extra,
  });
  return fetch;
}

test("store reads every figure from the server snapshot; nothing is computed from browser seed data", async () => {
  const fetch = storeFetch();
  globalThis.fetch = fetch;
  try {
    store.setState(null);
    assert.throws(() => store.companies(), /await load\(\)/);
    await store.load();
    assert.deepEqual(
      store.companies().map((c) => c.slug),
      ["harbor", "summit"],
    );
    assert.equal(
      store.company("harbor").id,
      "c-harbor",
      "companies resolve by id or slug",
    );
    assert.equal(store.portfolioMetrics().revenue, 1000);
    assert.equal(store.portfolioMetrics().rows.length, 2);
    const m = store.companyMetrics(store.company("c-harbor"));
    assert.equal(m.revenue, 600);
    assert.deepEqual(
      m.outstandingInvoices.map((i) => i.id),
      ["i1", "i2"],
    );
    assert.deepEqual(
      m.overdueInvoices.map((i) => i.id),
      ["i1"],
    );
    assert.deepEqual(
      m.overdue90.map((i) => i.id),
      ["i1"],
    );
    assert.deepEqual(store.agentStatus("c-harbor"), {
      label: "2 need review",
      tone: "warning",
    });
    assert.deepEqual(store.agentStatus("c-summit"), {
      label: "No agents",
      tone: "",
    });
    assert.deepEqual(
      store.activity().map((a) => a.text),
      ["new", "old"],
    );
    assert.equal(store.attentionQueue("c-summit").length, 0);
    assert.equal(
      store.evidenceRows(SNAPSHOT.opportunities[0].evidence[0]).length,
      1,
    );
    await store.load();
    assert.equal(
      fetch.calls.filter((c) => c.url === "/api/portfolio").length,
      1,
      "snapshot is cached until a mutation",
    );
  } finally {
    delete globalThis.fetch;
  }
});

test("store mutations POST to the API and refresh the snapshot; realized value stays server-owned", async () => {
  const fetch = storeFetch();
  globalThis.fetch = fetch;
  try {
    store.setState(null);
    await store.load();
    const found = await store.runPortfolioAnalysis();
    assert.equal(found[0].id, "OP-2");
    assert.equal(
      store.opportunities().length,
      2,
      "snapshot refreshed after analysis",
    );
    assert.equal(store.opportunities().at(-1).realizedValue, null);

    const t = await store.createTask({
      title: "Follow up",
      companyId: "c-harbor",
      sourceType: "opportunity",
      sourceId: "OP-2",
    });
    assert.equal(t.id, "T-2");
    assert.equal(store.tasks().length, 2);
    await store.updateTask("T-2", {
      status: "Complete",
      outcome: "Benefit validated",
      realizedResult: 999,
    });
    assert.equal(
      store.tasks()[1].realizedResult,
      null,
      "browser cannot assert realized value; server decides",
    );

    await store.resolveException("c-harbor", "X-1", "Merge");
    const x = store.company("c-harbor").importExceptions[0];
    assert.equal(x.open, false);
    assert.equal(x.decision, "Merge");
    assert.ok(
      fetch.calls.some((c) =>
        c.url.endsWith(
          "/exceptions/11111111-1111-1111-1111-111111111111/resolve",
        ),
      ),
      "exception refs resolve to their uuid",
    );

    // Finding triage addresses the ledger row by its display ref and refreshes the snapshot.
    const f = await store.setFindingStatus("F-001", "Reviewed");
    assert.equal(f.status, "Reviewed");
    assert.equal(store.findings("c-harbor")[0].status, "Reviewed");
    assert.equal(store.run("R-1").agentId, "file_reviewer-harbor");
    for (const c of fetch.calls.filter((c) => c.method === "POST"))
      assert.equal(c.headers["X-Vista-Request"], "1");
  } finally {
    delete globalThis.fetch;
    store.setState(null);
  }
});

test("runPortfolioInterpretation queues the chain, polls until done, then refreshes and reports new opportunities", async () => {
  const request = "8f1c2a3e-0b4d-4c5e-9f6a-7b8c9d0e1f2a";
  let polls = 0;
  let snapshot = structuredClone(SNAPSHOT);
  const fetch = mockFetch({
    "GET /api/portfolio": () => json(200, snapshot),
    "POST /api/portfolio/interpretation": () =>
      json(202, {
        request_id: request,
        review: { "c-harbor": "j1" },
        merge: { hvac: "j2" },
      }),
    [`GET /api/portfolio/interpretation/${request}`]: () => {
      polls += 1;
      const done = polls >= 2;
      if (done)
        snapshot.opportunities.push({
          id: "OP-9",
          status: "New",
          realizedValue: null,
          companyIds: ["c-harbor"],
        });
      const status = done ? "succeeded" : "running";
      return json(200, {
        request_id: request,
        done,
        succeeded: done ? 2 : 0,
        failed: 0,
        jobs: [
          { id: "j1", kind: "canonical_review", scope: "c-harbor", status },
          { id: "j2", kind: "portfolio_merge", scope: "hvac", status },
        ],
      });
    },
  });
  globalThis.fetch = fetch;
  try {
    store.setState(null);
    await store.load();
    const result = await store.runPortfolioInterpretation({ pollMs: 1 });
    assert.equal(polls, 2);
    assert.equal(result.done, true);
    assert.equal(result.timedOut, false);
    assert.deepEqual(
      result.found.map((o) => o.id),
      ["OP-9"],
      "new opportunities are those absent from the pre-run snapshot",
    );
    assert.equal(store.opportunities().length, 2, "snapshot refreshed");
    const post = fetch.calls.find((c) => c.method === "POST");
    assert.equal(post.url, "/api/portfolio/interpretation");
    assert.ok(
      !fetch.calls.some((c) => c.url === "/api/portfolio/analysis"),
      "legacy analysis route is not used",
    );
  } finally {
    delete globalThis.fetch;
    store.setState(null);
  }
});

test("store surfaces API errors to the caller and keeps the last good snapshot", async () => {
  globalThis.fetch = storeFetch({
    "POST /api/opportunities/OP-1/status": () =>
      json(422, { detail: "'Bogus' is not a valid status" }),
  });
  try {
    store.setState(null);
    await store.load();
    await assert.rejects(
      store.setOpportunityStatus("OP-1", "Bogus"),
      /not a valid status/,
    );
    assert.equal(store.opportunities()[0].status, "New");
  } finally {
    delete globalThis.fetch;
    store.setState(null);
  }
});

// ---- fixture generator (feeds scripts/export_portfolio_fixture.mjs) ----
test("the workspace clock follows the server snapshot, not the browser", async () => {
  const { TODAY, PERIOD, trailingTwelveMonths } =
    await import("../public/lib/format.js");
  assert.deepEqual(trailingTwelveMonths("2026-03-31"), {
    label: "TTM ending Mar. 2026",
    start: "2025-04-01",
    end: "2026-03-31",
  });
  const fetch = storeFetch();
  globalThis.fetch = fetch;
  try {
    await store.load();
    assert.equal(TODAY.toISOString().slice(0, 10), SNAPSHOT.today);
    // No period on the snapshot: the period is the trailing twelve months to `today`.
    assert.deepEqual({ ...PERIOD }, trailingTwelveMonths(SNAPSHOT.today));
    store.setState({
      ...SNAPSHOT,
      today: "2026-03-31",
      period: { label: "FY", start: "2025-01-01", end: "2025-12-31" },
    });
    assert.equal(TODAY.toISOString().slice(0, 10), "2026-03-31");
    assert.equal(PERIOD.label, "FY");
  } finally {
    delete globalThis.fetch;
  }
});
